"""Read-only source/overlay verification; no service, data or execution changes."""
from pathlib import Path
import argparse, ast, hashlib, json, sys

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--installed-root', type=Path)
    args=parser.parse_args()
    package=Path(__file__).resolve().parent
    manifest=json.loads((package/'manifest.json').read_text(encoding='utf-8'))
    allowed={'utils/automations.py','models/automations.py','routers/automations.py',
             'utils/agentic_maintenance.py','utils/automation_admission.py',
             'models/automation_admission.py','frontend/agent-activity.js'}
    if {x['path'] for x in manifest['files']} != allowed or len(manifest['files']) != len(allowed):
        raise RuntimeError('Unexpected overlay allowlist')
    errors=[]
    for entry in manifest['files']:
        relative=Path(entry['path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise RuntimeError('Unsafe manifest path')
        candidate=package/'candidate/open_webui'/relative
        if not candidate.is_file() or candidate.is_symlink() or digest(candidate)!=entry['after_sha256']:
            errors.append({'path':entry['path'],'error':'candidate bytes changed'})
            continue
        if relative.suffix=='.py':
            ast.parse(candidate.read_text(encoding='utf-8'),filename=entry['path'])
        before=package/'baseline/open_webui'/relative
        expected=entry['expected_before_sha256']
        if expected is not None and (not before.is_file() or digest(before)!=expected):
            errors.append({'path':entry['path'],'error':'rollback preimage changed'})
        if args.installed_root is not None:
            installed=args.installed_root/relative
            if installed.is_symlink():
                errors.append({'path':entry['path'],'error':'target symlink is not authorized'})
            elif expected is None:
                if installed.exists():
                    errors.append({'path':entry['path'],'error':'new target already exists; rebase required'})
            elif not installed.is_file() or digest(installed)!=expected:
                errors.append({'path':entry['path'],'error':'installed preimage drift; abort before every write'})
    print(json.dumps({'verified':not errors,'files':len(allowed),'installed_checked':args.installed_root is not None,
                      'live_mutations':0,'errors':errors},indent=2))
    return 1 if errors else 0

if __name__=='__main__':
    sys.exit(main())
