"""Repair legacy model defaults for ledger-verified ChatGPT projects only.

Default is read-only. --apply takes a consistent SQLite backup before updating
only folder.data.model_ids. Existing names, timestamps, instructions, files,
chats, ordinary folders and the relay ledger are never modified.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import subprocess
from typing import Any

ROOT = Path(r'C:\Code\open-webui-custom')
DATA = Path(r'C:\Program Files\Python311\Lib\site-packages\open_webui\data')
BACKUPS = Path(r'C:\ProgramData\ABiT\OpenWebUI\backups')
MODEL = 'omniroute.abit-chatgpt-relay/project-management'


def plan_repairs(database: Path, ledger: Path) -> list[dict[str, Any]]:
    """Read only; require exact owner, folder, project and account agreement."""
    if not database.is_file() or not ledger.is_file():
        raise RuntimeError('Application database and relay ledger must already exist.')
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=30)) as db:
        db.row_factory = sqlite3.Row
        db.execute('ATTACH DATABASE ? AS relay', (ledger.resolve().as_uri() + '?mode=ro',))
        rows = db.execute(
            'SELECT f.id, f.user_id, f.meta, f.data, p.project_id, p.account '
            'FROM folder AS f JOIN relay.projects AS p '
            'ON p.folder_id=f.id AND p.owner=f.user_id'
        ).fetchall()
    updates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        meta = json.loads(row['meta']) if row['meta'] else {}
        data = json.loads(row['data']) if row['data'] else {}
        if not isinstance(meta, dict) or not isinstance(data, dict):
            raise RuntimeError('Mapped project metadata must be JSON objects; no updates made.')
        if (not re.fullmatch(r'g-p-[A-Za-z0-9_-]{5,190}', row['project_id'])
                or not re.fullmatch(r'[a-f0-9]{64}', row['account'])
                or meta.get('chatgpt_project_id') != row['project_id']
                or meta.get('chatgpt_account') != row['account']):
            continue
        if data.get('model_ids') == [MODEL]:
            continue
        if row['id'] in seen:
            raise RuntimeError('Ambiguous project mapping; no updates made.')
        seen.add(row['id'])
        updates.append({
            'id': row['id'], 'owner': row['user_id'], 'meta_before': row['meta'],
            'data_before': row['data'],
            'data_after': json.dumps({**data, 'model_ids': [MODEL]}, ensure_ascii=False),
        })
    return updates


def apply_updates(database: Path, updates: list[dict[str, Any]]) -> None:
    """Compare-and-swap in one transaction; roll back all rows on any conflict."""
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=rw', uri=True, timeout=30)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        for row in updates:
            cursor = db.execute(
                'UPDATE folder SET data=? '
                'WHERE id=? AND user_id=? AND meta IS ? AND data IS ?',
                (row['data_after'], row['id'], row['owner'],
                 row['meta_before'], row['data_before']),
            )
            if cursor.rowcount != 1:
                raise RuntimeError('A project changed during repair; all updates rolled back.')
            saved = db.execute('SELECT data FROM folder WHERE id=?', (row['id'],)).fetchone()
            if not saved or json.loads(saved[0]) != json.loads(row['data_after']):
                raise RuntimeError('Model repair verification failed; all updates rolled back.')


def repair(database: Path, ledger: Path, backup_root: Path, *, apply: bool = False) -> dict[str, Any]:
    updates = plan_repairs(database, ledger)
    result: dict[str, Any] = {'affected_projects': len(updates), 'applied': False}
    if not apply or not updates:
        return result
    backup = backup_root / ('chatgpt-model-defaults-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    backup.mkdir(parents=True, exist_ok=False)
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=30)) as source:
        with closing(sqlite3.connect(backup / 'webui.db')) as target:
            source.backup(target)
            if target.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise RuntimeError('Database backup integrity check failed; no updates made.')
    apply_updates(database, updates)
    result.update(applied=True, backup=str(backup), remaining_projects=len(plan_repairs(database, ledger)))
    # Counts only; never put project names, instructions or credentials in logs.
    (backup / 'manifest.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Back up and repair confirmed legacy model defaults.')
    args = parser.parse_args()
    def git(*arguments: str) -> str:
        return subprocess.check_output(['git', *arguments], cwd=ROOT, text=True).strip()
    if Path(git('rev-parse', '--show-toplevel')).resolve() != ROOT.resolve():
        raise RuntimeError('Unexpected source repository.')
    if git('branch', '--show-current') != 'abit-projects':
        raise RuntimeError('Unexpected branch.')
    if git('remote', 'get-url', 'abit-origin') != 'https://github.com/ABiT-Consulting/abit-ai-platform.git':
        raise RuntimeError('Unexpected project remote.')
    result = repair(DATA / 'webui.db', DATA / 'aibit-chatgpt-relay.sqlite', BACKUPS, apply=args.apply)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
