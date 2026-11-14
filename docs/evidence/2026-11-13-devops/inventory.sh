#!/usr/bin/env bash
# REVIEW ONLY: do not create/queue a release until Root approves proposal.json.
# Metadata only. No checkout, configuration reads, migrations or service changes.
set -euo pipefail
/usr/bin/timeout --signal=TERM 35s /usr/bin/python3 -I -S -B -u - <<'PY'
import json
import os
import re
import socket
import subprocess
import sys

EXPECTED_HOST = 'UBUNTU-CODEX'
TARGET = 'erp.abit.ai'
BACKEND = 'erpnext-abit-backend-1'
PROJECT = 'erpnext-abit'
BENCH = '/home/frappe/frappe-bench'
sys.excepthook = lambda *_: print(json.dumps({'schema_version': 1, 'ok': False, 'error_code': 'UNEXPECTED_METADATA_ERROR'}))
MAX_OUTPUT = 16384
DOCKER_ENV = {'PATH': '/usr/bin:/bin:/usr/local/bin', 'LANG': 'C.UTF-8'}

def fail(code):
    print(json.dumps({'schema_version': 1, 'ok': False, 'error_code': code}))
    raise SystemExit(64)

observed_host = socket.gethostname()
if observed_host != EXPECTED_HOST:
    fail('HOST_FENCE_MISMATCH')
if not os.path.isfile('/usr/bin/docker'):
    fail('EXPECTED_DOCKER_BINARY_UNAVAILABLE')

def docker(args, limit=8192):
    try:
        value = subprocess.run(
            ['/usr/bin/docker', '--host', 'unix:///var/run/docker.sock', *args], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=5, check=False, cwd='/', env=DOCKER_ENV,
        )
    except (OSError, subprocess.TimeoutExpired):
        fail('METADATA_COMMAND_UNAVAILABLE')
    if value.returncode != 0 or len(value.stdout) > limit:
        fail('METADATA_COMMAND_FAILED_OR_EXCEEDED_BOUND')
    return value.stdout.decode('utf-8', errors='strict').strip()

if docker(['info', '--format', '{{.Name}}']) != EXPECTED_HOST:
    fail('LOCAL_DOCKER_DAEMON_HOST_MISMATCH')

def safe_name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_./:@+-]{1,200}', value):
        fail('INVALID_METADATA_IDENTIFIER')
    return value

# Go template explicitly excludes Config.Env, command arguments, mount contents
# and every label value except the two Compose identity fields.
FORMAT = (
    '{"id":{{json .Id}},"name":{{json .Name}},'
    '"status":{{json .State.Status}},'
    '"compose_project":{{json (index .Config.Labels "com.docker.compose.project")}},'
    '"compose_service":{{json (index .Config.Labels "com.docker.compose.service")}}}'
)
backend = json.loads(docker(['inspect', '--format', FORMAT, BACKEND]))
if (backend.get('name') != '/' + BACKEND or backend.get('status') != 'running'
        or backend.get('compose_project') != PROJECT or backend.get('compose_service') != 'backend'):
    fail('ERP_BACKEND_IDENTITY_UNVERIFIED')
for key in ('id', 'name', 'status', 'compose_project', 'compose_service'):
    safe_name(backend[key])

# Fixed Python invocation reads only directory names/existence, never any file.
SITE_CHECK = "import json,os\nfrom pathlib import Path\nb=Path('/home/frappe/frappe-bench')\ns=b/'sites'/'erp.abit.ai'\nok=b.is_dir() and s.is_dir() and s.resolve()==s\ncount=0\nif ok:\n    with os.scandir(b/'sites') as stream:\n        for index,entry in enumerate(stream):\n            if index>=200:raise SystemExit(66)\n            if entry.is_dir(follow_symlinks=False) and entry.name not in {'assets','logs','backups','__pycache__'}:count+=1\nprint(json.dumps({'bench':str(b),'site':'erp.abit.ai','site_directory_exists':bool(ok),'verified_target_directories':['erp.abit.ai'] if ok else [],'potential_site_directory_count':count,'multiple_potential_sites':count>1}))\nraise SystemExit(0 if ok else 65)\n"
site = json.loads(docker(['exec', '--user', 'frappe', BACKEND, '/usr/bin/timeout', '--signal=TERM', '3s',
                          '/home/frappe/frappe-bench/env/bin/python', '-I', '-S', '-B', '-c', SITE_CHECK]))
if not site.get('site_directory_exists') or site.get('site') != TARGET or site.get('bench') != BENCH:
    fail('ERP_SITE_DIRECTORY_UNVERIFIED')

ids = docker(['ps', '--all', '--filter', 'label=com.docker.compose.project=' + PROJECT,
              '--format', '{{.ID}}']).splitlines()
if not ids or len(ids) > 12 or any(not re.fullmatch(r'[a-f0-9]{12,64}', value) for value in ids):
    fail('PROJECT_CONTAINER_BOUND_EXCEEDED_OR_UNVERIFIED')
containers = []
ingress = []
rule_reads = 0
for ident in ids:
    metadata = json.loads(docker(['inspect', '--format', FORMAT, ident]))
    if metadata.get('compose_project') != PROJECT:
        fail('CROSS_PROJECT_METADATA_REJECTED')
    for key in ('id', 'name', 'status', 'compose_project', 'compose_service'):
        safe_name(metadata[key])
    containers.append(metadata)
    # Only label names are enumerated. Values are read solely for router-rule
    # keys, parsed privately and reduced to an exact target-domain match.
    keys = docker(['inspect', '--format', '{{range $k,$v := .Config.Labels}}{{println $k}}{{end}}', ident]).splitlines()
    for key in keys:
        if not re.fullmatch(r'traefik\.http\.routers\.[A-Za-z0-9_-]{1,80}\.rule', key):
            continue
        rule_reads += 1
        if rule_reads > 8:
            fail('INGRESS_METADATA_BOUND_EXCEEDED')
        template = '{{json (index .Config.Labels "' + key + '")}}'
        rule = json.loads(docker(['inspect', '--format', template, ident], limit=2048))
        hosts = re.findall(r'Host\(\s*[`\"]([A-Za-z0-9.-]+)[`\"]\s*\)', rule or '')
        if TARGET in hosts:
            ingress.append({'container_id': metadata['id'], 'router_label': key, 'hostname': TARGET})

result = {
    'schema_version': 1, 'ok': True, 'purpose': 'erp-route-readonly-inventory',
    'host': observed_host, 'target': TARGET, 'backend': backend, 'site': site,
    'containers': containers, 'ingress_hostname_label_matches': ingress,
    'ingress_label_match_found': bool(ingress),
    'external_route_verified': False,
    'ingress_note': 'Even matching router labels do not verify external routing or backend association; separate ingress readback is required.',
    'reads_only': True,
}
encoded = json.dumps(result, sort_keys=True, separators=(',', ':'))
if len(encoded.encode()) > MAX_OUTPUT:
    fail('OUTPUT_BOUND_EXCEEDED')
print(encoded)
PY
