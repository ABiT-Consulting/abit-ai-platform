#!/usr/bin/env python3
"""Read-only Codex-first release preimage/source verification.

This is not an installer, admission client, authenticator or release authorization.
Run from the source repository checkout on ABIT-DEV-LINUX.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath

HERE=Path(__file__).resolve().parent

def digest(path):
    if not path.is_file() or path.is_symlink():return None
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--runtime',default='/srv/abit-dev-linux/abit-ai/current/runtime')
    p.add_argument('--source',default=str(HERE.parents[1]))
    args=p.parse_args()
    root=Path(args.runtime);source=Path(args.source)
    manifest=json.loads((HERE/'release-manifest.json').read_text())
    records=[]
    for entry in manifest['files']:
        relative=PurePosixPath(entry['path'])
        if relative.is_absolute() or '..' in relative.parts or not str(relative).startswith('open_webui/'):
            raise ValueError('Untrusted package path')
        staged=digest(source/'backend'/str(relative))
        installed=digest(root/str(relative))
        before=entry['before_sha256']
        records.append({'path':str(relative),'source_matches':staged==entry['candidate_sha256'],
                        'preimage_matches':installed==before,
                        'operation':entry['operation']})
    c=manifest['config_files'][0]
    dropin=digest(HERE/'50-agentic-codex.conf')
    config_ok=dropin==c['candidate_sha256']
    parity=json.loads((HERE/'source-parity-manifest.json').read_text())
    source_dependencies=[]
    for entry in parity['modules']:
        relative=PurePosixPath(entry['path'])
        if relative.is_absolute() or '..' in relative.parts or not str(relative).startswith('open_webui/'):
            raise ValueError('Untrusted dependency path')
        source_dependencies.append({'path':str(relative),
            'source_matches':digest(source/'backend'/str(relative))==entry['sha256'],
            'installed_matches':digest(root/str(relative))==entry['sha256']})
    result={'schema_version':1,'probe':'read_only_only','candidate_sources_valid':all(x['source_matches'] for x in records),
            'installed_preimages_valid':all(x['preimage_matches'] for x in records),
            'installed_lineage_source_valid':all(x['source_matches'] and x['installed_matches'] for x in source_dependencies),
            'systemd_dropin_candidate_valid':config_ok,
            'file_count':len(records),'lineage_file_count':len(source_dependencies),
            'file_checks':records,'source_lineage_checks':source_dependencies,'production_installed_by_probe':False,
            'production_approval_granted_by_probe':False}
    print(json.dumps(result,indent=2,sort_keys=True))
    if not result['candidate_sources_valid'] or not config_ok:raise SystemExit(2)
    if not result['installed_preimages_valid'] or not result['installed_lineage_source_valid']:raise SystemExit(3)

if __name__=='__main__':main()
