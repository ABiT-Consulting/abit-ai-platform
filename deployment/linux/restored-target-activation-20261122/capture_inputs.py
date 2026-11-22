"""Root read-only draft inputs. Does not bind, stage, approve, probe /health or apply.

Output contains process/source identities and aggregate private-data hashes only.
The operator reviews fresh backup/owner/gate bindings before setting authorization
and installing the separately bound root-only release script.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent

def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value

def collect():
    if os.getresuid() != (0,0,0) or os.getresgid() != (0,0,0):
        raise RuntimeError('Root read-only operator required')
    a = load('relay_release_input_capture', 'release_relay.py')
    b = load('relay_bootstrap_input_capture', 'relay_bootstrap.py')
    package = json.loads(a.raw(a.SOURCE_PACKAGE/'manifest.json'))
    if a.filehash(a.SOURCE_PACKAGE/'manifest.json') != a.SOURCE_MANIFEST_SHA:
        raise RuntimeError('Reviewed source package changed')
    lease = json.loads(a.raw(a.LEASE))
    units = {}
    for name in a.UNIT_NAMES:
        result = subprocess.run(['/usr/bin/systemctl','show',name,'--no-pager',
                                 '--property='+','.join(a.UNIT_FIELDS)],
                                check=True,capture_output=True,text=True,timeout=10)
        units[name] = dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
    def pidfile(name):
        value=a.raw(a.STATE/(name+'.pid'),64).strip()
        if not value.isdigit():
            raise RuntimeError('Fixed process PID file invalid')
        return a.process(int(value))
    processes={name:pidfile(name) for name in ('chrome','egress','xvfb','tunnel')}
    for name,unit in (('core','abit-ai.service'),('qa','abit-ai-erp-review-qa.service'),
                      ('watchdog','aibit-linux-relay-watchdog.service')):
        processes[name]=a.process(int(units[unit]['MainPID']))
    m={'purpose':'RESTORED_TARGET_DIAGNOSTIC_GUARDED_NODE_V2',
       'draft_observed_utc':a.now(),'live_activation_authorized':False,
       'source_base_commit':a.SOURCE_BASE_COMMIT,
       'source_manifest_sha256':a.SOURCE_MANIFEST_SHA,
       'release_helper_unbound_sha256':a.filehash(HERE/'release_relay.py'),
       'operator_source':{name:a.filehash(HERE/name) for name in
          ('relay_bootstrap.py','relay_runtime_guard.py','browser_lock_keeper.cjs')},
       'operator':{'owner':lease['owner'],'actor':lease['owner']+':restored-target-relay'},
       'preserved_processes':processes,'relay_before':pidfile('relay'),
       'units':units,'unchanged_files':package['unchanged_files'],
       'unchanged_core':{name:a.filehash(a.CORE/name) for name in
           ('utils/agentic_transport_retry.py','utils/agentic_relay_scope.py',
            'routers/agentic_governance.py','utils/agentic_maintenance.py')},
       'runtime_files':{},'allow_rebuildable_catalog_expiry':True}
    a.owner(m)
    a.listener_owned(m['relay_before']['pid'])
    m['gate_before']=a.gate(m)
    if m['gate_before']['state']!='open':
        raise RuntimeError('An existing maintenance gate is not open; do not replace it')
    parent=a.LIVE.stat()
    for row in package['runtime_files']:
        target=a.LIVE/row['path']
        if a.filehash(target)!=row['before_sha256']:
            raise RuntimeError('Runtime preimage drift')
        m['runtime_files'][row['path']]={key:row[key] for key in ('before_sha256','after_sha256')}
        m['runtime_files'][row['path']].update(
            parent_identity=[parent.st_dev,parent.st_ino],metadata=[999,988,'0o644'])
    m.update(a.ledger_snapshot(m,verify=False))
    m['private_preservation']=a.private_preservation(m,verify=False)
    m['browser_inventory']=a.browser_snapshot(m,verify=False)
    catalog=a.STATE/'aibit-relay-projects.json'
    m['catalog_cache_before']=a.file_proof(catalog) if catalog.exists() else None
    m['hermes_transport']=b.source_timeout(m['relay_before']['pid'])
    existing,owned=b.split_ruleset(b.read_ruleset())
    if owned:
        raise RuntimeError('An owned bootstrap table already exists; reconcile that original first')
    m['nft_before_sha256']=b.digest(existing)
    backup_stage=a.STAGE/'backup'
    if not backup_stage.exists():
        backup_stage=Path('/var/lib/abit-ai-orchestration-recovery-20261122T1015')
    m['backup']={'stage':str(backup_stage),
                 'receipt_sha256':a.filehash(backup_stage/'backup-receipt.json')}
    a.consistent(m,'before')
    a.backup(m)
    if a.gate(m)!=m['gate_before'] or a.process(m['relay_before']['pid'])!=m['relay_before']:
        raise RuntimeError('Draft inputs changed during read-only collection')
    return m

if __name__=='__main__':
    try:
        print(json.dumps(collect(),sort_keys=True,indent=2))
    except BaseException as error:
        if isinstance(error,SystemExit):
            raise
        print(json.dumps({'state':'draft_capture_held','error_type':type(error).__name__,
                          'no_live_actions':True}),file=sys.stderr)
        raise SystemExit(78) from None
