"""Deletion protection for ChatGPT-linked projects and their containing folders.

No upstream calls or writes are made. The durable, owner-scoped relay mapping
also protects legacy folders whose display metadata is missing.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Iterable

PROJECT_DELETE_MESSAGE = 'ChatGPT-linked projects cannot be deleted from ABIT.ai.'


class ProjectDeletionProtectedError(ValueError):
    def __init__(self):
        super().__init__(PROJECT_DELETE_MESSAGE)


class ProjectProtectionUnavailableError(RuntimeError):
    def __init__(self):
        super().__init__('Project deletion protection could not be verified. No folders were deleted.')


def linked_project_folder_ids(owner_id: str, ledger_path: Path | None = None) -> set[str]:
    """Read mappings for every linked account, not just the current browser login."""
    if ledger_path is None:
        from open_webui.env import DATA_DIR
        ledger_path = Path(DATA_DIR) / 'aibit-chatgpt-relay.sqlite'
    path = Path(ledger_path)
    try:
        # A fresh installation has no relay ledger; folder metadata still applies.
        if not path.exists():
            return set()
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
            return {
                row[0] for row in db.execute(
                    "SELECT folder_id FROM projects WHERE owner=? AND project_id<>''",
                    (owner_id,),
                ) if isinstance(row[0], str) and row[0]
            }
    except (OSError, sqlite3.Error) as exc:
        # Never downgrade a failed lookup to permission to delete a project.
        raise ProjectProtectionUnavailableError() from exc


def protected_project_folder_ids(folders: Iterable, mapped_ids: Iterable[str] = ()) -> set[str]:
    """Protect the project and all ancestors, without locking ordinary children."""
    def value(row, key):
        return row.get(key) if isinstance(row, dict) else getattr(row, key, None)

    rows = list(folders)
    parents = {value(row, 'id'): value(row, 'parent_id') for row in rows}
    protected = set(mapped_ids) & parents.keys()
    for row in rows:
        meta = value(row, 'meta')
        if isinstance(meta, dict) and meta.get('chatgpt_project_id'):
            protected.add(value(row, 'id'))
    pending = list(protected)
    while pending:
        parent = parents.get(pending.pop())
        if parent in parents and parent not in protected:
            protected.add(parent)
            pending.append(parent)
    return protected
