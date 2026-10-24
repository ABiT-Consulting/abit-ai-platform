"""Deploy only the project-deletion policy and current built UI, preserving ACLs.

The dedicated application runtime is restarted and health-checked separately.
No chats, folders, credentials, branding, or application configuration are changed.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSTALLED = Path(r'C:\Program Files\Python311\Lib\site-packages\open_webui')
RUNTIME = Path(r'C:\ProgramData\ABiT\OpenWebUI')
BASELINE = 'e2a4bca1c902f75f5a530d76147370944cf4f33e'
BACKEND = ['models/folders.py', 'routers/folders.py', 'utils/chatgpt_project_protection.py']


def sha(data):
    return hashlib.sha256(data).hexdigest()


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)


def plan():
    if git('rev-parse', '--show-toplevel').decode().strip().lower() != str(ROOT).replace('\\', '/').lower():
        raise RuntimeError('Unexpected source path')
    if git('branch', '--show-current').decode().strip() != 'abit-projects':
        raise RuntimeError('Unexpected branch')
    if git('remote', 'get-url', 'abit-origin').decode().strip() != 'https://github.com/ABiT-Consulting/abit-ai-platform.git':
        raise RuntimeError('Unexpected project remote')
    spec = importlib.util.spec_from_file_location('scoped_deploy', ROOT/'deployment/windows/deploy-chatgpt-project-controls.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    planned=[]
    for relative in BACKEND:
        desired=(ROOT/'backend/open_webui'/relative).read_text(encoding='utf-8-sig')
        destination=INSTALLED/relative
        original=destination.read_bytes() if destination.is_file() else None
        if relative=='utils/chatgpt_project_protection.py':
            if original is not None and original.decode('utf-8-sig').replace('\r\n','\n')!=desired:
                raise RuntimeError('Unexpected existing deletion policy; review before overwriting')
            output=desired.encode('utf-8');mode='new-policy' if original is None else 'already-current'
        else:
            if original is None:raise RuntimeError('Installed backend file missing: '+relative)
            base=git('show',BASELINE+':backend/open_webui/'+relative).decode('utf-8-sig').replace('\r\n','\n')
            current=original.decode('utf-8-sig').replace('\r\n','\n')
            output,mode=helper.scoped_patch(base,desired,current)
            output=output.encode('utf-8')
        compile(output,relative,'exec')
        planned.append((relative,destination,original,output,mode))
    return planned


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    planned=plan()
    print('BACKEND_PLAN='+json.dumps([{'path':r,'mode':m} for r,_,_,_,m in planned]),flush=True)
    if not args.apply:return
    if (ROOT/'.project-deletion-build.exit').read_text().strip()!='0':
        raise RuntimeError('Production build did not pass')
    build=ROOT/'build'
    if not (build/'index.html').is_file() or not (build/'_app/immutable').is_dir():
        raise RuntimeError('Production frontend output missing')
    files=[build/'index.html', *sorted((build/'_app').rglob('*'))]
    files=[p for p in files if p.is_file()]
    if not any(b'ChatGPT-linked projects cannot be deleted from ABIT.ai.' in p.read_bytes()
               for p in files if p.suffix=='.js'):
        raise RuntimeError('Deletion controls absent from production output')
    backup=RUNTIME/'backups'/('project-deletion-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    backup.mkdir(parents=True,exist_ok=False)
    manifest={'baseline':BASELINE,'backend':[],'frontend':[]}
    frontend_changes=[]
    for relative,destination,original,output,mode in planned:
        if original is not None:
            saved=backup/'backend'/relative;saved.parent.mkdir(parents=True,exist_ok=True);saved.write_bytes(original)
        manifest['backend'].append({'path':relative,'before':sha(original) if original is not None else None,
                                    'after':sha(output),'mode':mode})
    for source in files:
        relative=source.relative_to(build);destination=INSTALLED/'frontend'/relative
        output=source.read_bytes();original=destination.read_bytes() if destination.is_file() else None
        changed=original!=output
        if changed:
            if original is not None:
                saved=backup/'frontend'/relative;saved.parent.mkdir(parents=True,exist_ok=True);saved.write_bytes(original)
            frontend_changes.append((destination,output))
        manifest['frontend'].append({'path':relative.as_posix(),'before':sha(original) if original is not None else None,
                                     'after':sha(output),'changed':changed})
    (backup/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print('BACKUP_READY='+str(backup),flush=True)
    for relative,destination,original,output,mode in planned:
        if original!=output:
            destination.parent.mkdir(parents=True,exist_ok=True)
            destination.write_bytes(output)  # Existing file ACLs are preserved.
    # Keep old immutable assets available for already-open clients. Switch the
    # index last; never mirror/delete branding, uploads or static customizations.
    for destination,output in sorted(frontend_changes,key=lambda item:item[0].name=='index.html'):
        destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(output)
    for relative,destination,original,output,mode in planned:
        if sha(destination.read_bytes())!=sha(output):raise RuntimeError('Backend hash mismatch')
    for entry in manifest['frontend']:
        if sha((INSTALLED/'frontend'/entry['path']).read_bytes())!=entry['after']:
            raise RuntimeError('Frontend hash mismatch: '+entry['path'])
    (RUNTIME/'last-project-deletion-backup.txt').write_text(str(backup),encoding='utf-8')
    print('DEPLOYED_BACKEND_FILES='+str(len(planned)),flush=True)
    print('VERIFIED_FRONTEND_FILES='+str(len(files)),flush=True)
    print('NO_DATABASE_OR_BRANDING_CHANGES',flush=True)


if __name__=='__main__':main()
