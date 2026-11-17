#!/usr/bin/env python3
"""Run only finite source/controller fixtures with network/process/live I/O denied."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.dont_write_bytecode=True
HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]
DENIED=(Path('/srv/abit-dev-linux/abit-ai'),Path('/var/lib/abit-ai-agentic-gateway'),
        Path('/var/lib/abit-ai-erp-rollouts'),Path('/proc'),Path('/run'))
PINS=[HERE/'apply_scope_repair.py',HERE/'preserved_scope.py',HERE/'test_scope_repair.py',
      Path(__file__).resolve(),REPO/'deployment/linux/core-hardening-20261115/apply_core_hardening.py',
      REPO/'deployment/linux/core-command-capacity-20261115/apply_command_capacity.py',
      REPO/'backend/open_webui/utils/agentic_relay_scope.py']
sha=lambda raw:hashlib.sha256(raw).hexdigest()
def audit(event,args):
    if event in {'socket.connect','socket.getaddrinfo','socket.gethostbyname','socket.gethostbyaddr',
                 'subprocess.Popen','os.system','os.posix_spawn','os.fork'}:
        raise RuntimeError('FIXTURE_NETWORK_OR_PROCESS_DENIED')
    if event in {'open','sqlite3.connect'} and args and isinstance(args[0],(str,bytes,os.PathLike)):
        value=os.fsdecode(args[0])
        if value.startswith('file:'):value=value[5:].split('?',1)[0]
        path=Path(value).absolute()
        if any(path==root or root in path.parents for root in DENIED):
            raise RuntimeError('FIXTURE_LIVE_INPUT_DENIED')
def flatten(value):
    for item in value:
        if isinstance(item,unittest.TestSuite):yield from flatten(item)
        else:yield item
class Results(unittest.TextTestResult):
    def __init__(self,*args,**kw):
        super().__init__(*args,**kw);self.ids=[];self.passed=[]
    def startTest(self,test):
        self.ids.append(test.id());super().startTest(test)
    def addSuccess(self,test):
        self.passed.append(test.id());super().addSuccess(test)
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--name',required=True)
    parser.add_argument('--select',action='append');args=parser.parse_args()
    assert args.name.replace('-','').isalnum()
    out=HERE/'validation';out.mkdir(exist_ok=True)
    logfile=out/(args.name+'.log');receipt=out/(args.name+'-receipt.json')
    assert not logfile.exists() and not receipt.exists()
    pins={str(p.relative_to(REPO)):sha(p.read_bytes())for p in PINS}
    started=datetime.now(timezone.utc).isoformat();clock=time.monotonic()
    sys.addaudithook(audit)
    spec=importlib.util.spec_from_file_location('test_scope_repair',HERE/'test_scope_repair.py')
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    if args.select:
        suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name,module)for name in args.select)
    else:suite=unittest.defaultTestLoader.loadTestsFromModule(module)
    ids=[test.id()for test in flatten(suite)]
    assert len(ids)==len(set(ids)) and ids
    with logfile.open('x')as stream:
        result=unittest.TextTestRunner(stream=stream,verbosity=2,resultclass=Results).run(suite)
    current={str(p.relative_to(REPO)):sha(p.read_bytes())for p in PINS}
    value={'schema_version':1,'started_utc':started,'finished_utc':datetime.now(timezone.utc).isoformat(),
        'elapsed_seconds':time.monotonic()-clock,'successful':result.wasSuccessful(),
        'tests':result.testsRun,'unique_ids':len(set(result.ids)),'test_ids':result.ids,'passed_ids':result.passed,
        'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),
        'source_pins':pins,'source_unchanged':current==pins,'log_sha256':sha(logfile.read_bytes()),
        'network_and_subprocess_denied':True,'live_file_open_and_sqlite_denied':True,
        'real_lifecycle_calls':0,'real_runtime_writes':0,'real_model_calls':0,
        'limits':'Finite simulated lifecycle, actual temporary SQLite/CAS/cache operations; no OS-level sandbox or live lifecycle verification claimed.'}
    with receipt.open('x')as stream:json.dump(value,stream,sort_keys=True,indent=2);stream.write('\n')
    print(json.dumps({k:value[k]for k in ('successful','tests','unique_ids','failures','errors','skipped','source_unchanged','elapsed_seconds')}))
    return 0 if result.wasSuccessful()and current==pins else 1
if __name__=='__main__':raise SystemExit(main())
