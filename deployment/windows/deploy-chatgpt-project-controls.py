"""Scoped AIBIT deployment; preserves unrelated installed customizations and ACLs.

--check validates task-only patches; --apply backs up before replacing anything.
Restart/readiness checks are performed separately by the dedicated app services.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess

ROOT = Path(r'C:\Code\open-webui-custom')
INSTALLED = Path(r'C:\Program Files\Python311\Lib\site-packages\open_webui')
RUNTIME = Path(r'C:\ProgramData\ABiT\OpenWebUI')
BASE = '6a826a177'
BACKEND = ['routers/chatgpt_relay.py', 'routers/folders.py', 'main.py', 'utils/audit.py']

def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)

def normalized(raw):
    return raw.decode('utf-8-sig').replace('\r\n', '\n')

def scoped_patch(base, desired, installed):
    if installed == desired: return installed, 'already-current'
    if installed == base: return desired, 'source-matches-baseline'
    before, after = base.splitlines(keepends=True), desired.splitlines(keepends=True)
    matcher = difflib.SequenceMatcher(None, before, after, autojunk=False)
    # Narrow context only when an unrelated adjacent customization differs.
    # Each old/new hunk must still have exactly one unambiguous match.
    for context in (4, 3, 2, 1):
        result = installed
        valid = True
        groups = list(matcher.get_grouped_opcodes(context))
        for group in reversed(groups):
            old = ''.join(before[group[0][1]:group[-1][2]])
            new = ''.join(after[group[0][3]:group[-1][4]])
            # An insertion can contain the complete old context. Check the
            # already-applied hunk first so repeat deployments never duplicate it.
            if new and result.count(new) == 1:
                continue
            elif old and result.count(old) == 1:
                result = result.replace(old, new, 1)
            else:
                valid = False
                break
        if valid: return result, 'task-only-context-patch'
    raise RuntimeError('Installed file differs inside a task patch. Refusing to overwrite unrelated changes.')


def sha(raw): return hashlib.sha256(raw).hexdigest()

def plan():
    if Path(git('rev-parse','--show-toplevel').decode().strip()).resolve() != ROOT.resolve():
        raise RuntimeError('Unexpected source root')
    if git('branch','--show-current').decode().strip() != 'abit-projects': raise RuntimeError('Unexpected branch')
    if git('remote','get-url','abit-origin').decode().strip() != 'https://github.com/ABiT-Consulting/abit-ai-platform.git':
        raise RuntimeError('Unexpected project remote')
    changes = []
    for relative in BACKEND:
        source = ROOT/'backend/open_webui'/relative
        target = INSTALLED/relative
        original = target.read_bytes()
        desired = normalized(source.read_bytes())
        base = normalized(git('show', BASE+':backend/open_webui/'+relative))
        result, mode = scoped_patch(base, desired, normalized(original))
        # Validate the exact deployment payload, not just the source checkout.
        compile(result, str(target), 'exec')
        eol = '\r\n' if b'\r\n' in original else '\n'
        payload = result.replace('\n',eol).encode('utf-8')
        changes.append((relative, target, original, payload, mode))
    return changes

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');parser.add_argument('--check',action='store_true');args=parser.parse_args()
    changes = plan()
    for relative, _, _, _, mode in changes: print('PATCH_READY',relative,mode)
    if not args.apply: return
    build = ROOT/'build'
    if not (build/'index.html').is_file() or not (build/'_app/immutable').is_dir():
        raise RuntimeError('Production build is missing')
    exit_file = ROOT/'.chatgpt-validation-build.exit'
    if not exit_file.is_file() or exit_file.read_text().strip() != '0':
        raise RuntimeError('Production build has not succeeded')
    newest_source = max(p.stat().st_mtime for p in (ROOT/'src').rglob('*.svelte'))
    if (build/'index.html').stat().st_mtime < newest_source:
        raise RuntimeError('Build is older than frontend source changes')
    backup = RUNTIME/'backups'/('chatgpt-controls-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(parents=True,exist_ok=False)
    manifest={'baseline':BASE,'backend':[]}
    for relative,target,original,payload,mode in changes:
        saved=backup/'backend'/relative;saved.parent.mkdir(parents=True,exist_ok=True);saved.write_bytes(original)
        manifest['backend'].append({'path':relative,'before':sha(original),'after':sha(payload),'method':mode})
    shutil.copy2(INSTALLED/'frontend/index.html',backup/'index.html')
    shutil.copytree(INSTALLED/'frontend/_app',backup/'frontend/_app')
    # SQLite backup API captures a consistent snapshot, including pending WAL.
    for name in ['webui.db','aibit-chatgpt-relay.sqlite']:
        database=INSTALLED/'data'/name
        if database.is_file():
            with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True,timeout=30) as source_db:
                with sqlite3.connect(backup/name) as backup_db: source_db.backup(backup_db)
    relay_backup=backup/'relay';relay_backup.mkdir()
    (relay_backup/'aibit_relay.previous.cjs').write_bytes(git('show',BASE+':deployment/windows/aibit-relay/aibit_relay.cjs'))
    existing_journal=RUNTIME/'aibit-created-projects.json'
    if existing_journal.is_file(): shutil.copy2(existing_journal,backup/existing_journal.name)
    for relative,target,original,payload,mode in changes:
        if target.read_bytes()!=original: raise RuntimeError('Installed file changed concurrently; aborting deployment: '+relative)
    for _,target,_,payload,_ in changes:
        target.write_bytes(payload)  # Overwrite existing files: preserve their ACLs.
    shutil.copytree(build/'_app',INSTALLED/'frontend/_app',dirs_exist_ok=True)
    shutil.copyfile(build/'index.html',INSTALLED/'frontend/index.html')
    # Never copy /static or branding assets; keep old hashed chunks for open tabs.
    for relative,target,_,payload,_ in changes:
        if target.read_bytes()!=payload: raise RuntimeError('Backend verification failed: '+relative)
    manifest['frontend_index_sha256']=sha((build/'index.html').read_bytes())
    manifest['frontend_assets']=sum(1 for p in (build/'_app').rglob('*') if p.is_file())
    (backup/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    (ROOT/'.chatgpt-controls-deploy-backup.txt').write_text(str(backup),encoding='utf-8')
    print('BACKUP',backup)
    print('DEPLOYED_BACKEND_FILES',len(changes))
    print('DEPLOYED_FRONTEND_ASSETS',manifest['frontend_assets'])
    print('BRANDING_DATA_AND_EXISTING_PERMISSIONS_PRESERVED')

if __name__=='__main__': main()
