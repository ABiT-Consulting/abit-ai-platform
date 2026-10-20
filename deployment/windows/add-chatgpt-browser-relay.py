#!/usr/bin/env python3
"""Safely expose the existing ERP ChatGPT browser relay in ABiT Inhouse AI.

Run from C:\\Code\\open-webui-custom on branch abit-projects.
Dry-run by default. --apply backs up SQLite online before making two
small persistent configuration changes. No restart, new provider, or secrets.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[2]
DEFAULT_DB = Path(r"C:\Program Files\Python311\Lib\site-packages\open_webui\data\webui.db")
DEFAULT_BACKUPS = Path(r"C:\Code\open-webui-live-backups")
UPSTREAM = "https://omniroute.abit.ai/v1"
RELAY_ID = "abit-chatgpt-relay/project-management"
DISPLAY_NAME = "ChatGPT Browser Relay (Project Management)"
DESCRIPTION = "ChatGPT browser-session relay used for ERP project management, via existing OmniRoute provider."


def connect_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=15)


def read_config(db: sqlite3.Connection) -> tuple[list[str], dict, str]:
    def load(key: str):
        row = db.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        if row is None:
            raise ValueError(f"Missing persistent Open WebUI configuration: {key}")
        return json.loads(row[0]), row[0]

    urls, _ = load("openai.api_base_urls")
    configs, original = load("openai.api_configs")
    if urls.count(UPSTREAM) != 1:
        raise ValueError("Expected exactly one existing OmniRoute connection; refusing to change providers.")
    idx = str(urls.index(UPSTREAM))
    if not isinstance(configs.get(idx), dict):
        raise ValueError("Missing OmniRoute provider settings; refusing to replace them.")
    return urls, configs, original


def model_state(db: sqlite3.Connection) -> tuple[bool, bool, str]:
    _, configs, _ = read_config(db)
    url_idx = next(idx for idx, value in configs.items() if isinstance(value, dict) and
                   idx.isdigit() and idx == str(db_urls_index(db)))
    cfg = configs[url_idx]
    model_ids = cfg.get("model_ids", [])
    if not isinstance(model_ids, list) or not all(isinstance(item, str) for item in model_ids):
        raise ValueError("Unexpected OmniRoute model_ids format")
    public_id = f"{cfg['prefix_id']}.{RELAY_ID}" if cfg.get("prefix_id") else RELAY_ID
    current = db.execute("SELECT name FROM model WHERE id=?", (public_id,)).fetchone()
    return RELAY_ID in model_ids, current is not None, public_id


def db_urls_index(db: sqlite3.Connection) -> int:
    urls, _, _ = read_config(db)
    return urls.index(UPSTREAM)


def check_repo() -> None:
    if REPO.resolve() != Path(r"C:\Code\open-webui-custom").resolve():
        raise ValueError("Refusing to operate outside the canonical ABiT Inhouse AI source tree")
    branch = subprocess.check_output(
        ["git", "-C", str(REPO), "branch", "--show-current"], text=True
    ).strip()
    if branch != "abit-projects":
        raise ValueError(f"Wrong Git branch: {branch}")


def choose_admin(db: sqlite3.Connection) -> str:
    row = db.execute(
        "SELECT m.user_id FROM model m JOIN user u ON u.id=m.user_id "
        "WHERE u.role='admin' ORDER BY m.created_at LIMIT 1"
    ).fetchone()
    if not row:
        raise ValueError("No existing admin-owned model; cannot assign a safe owner")
    return row[0]


def backup_database(src_path: Path, backup_root: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    folder = backup_root / f"chatgpt-browser-relay-{stamp}"
    folder.mkdir(parents=True, exist_ok=False)
    destination = folder / "webui.db"
    with closing(connect_readonly(src_path)) as src, closing(sqlite3.connect(destination)) as dst:
        src.backup(dst, pages=512, sleep=0.05)
        result = dst.execute("PRAGMA quick_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"Backup integrity check failed: {result}")
    return destination


def run(path: Path, backups: Path, apply: bool) -> None:
    check_repo()
    if not path.is_file():
        raise FileNotFoundError(path)
    with closing(connect_readonly(path)) as db:
        registered, alias_present, public_id = model_state(db)
        # Existing model overrides are intentionally left untouched.
        admin_id = choose_admin(db) if not alias_present else None
    print(f"provider={UPSTREAM}")
    print(f"upstream_model={RELAY_ID}")
    print(f"inhouse_model={public_id}")
    print(f"registered={registered}; display_alias_exists={alias_present}")
    if not apply or (registered and alias_present):
        print("result=already_configured" if registered and alias_present else "result=dry_run")
        return
    backup = backup_database(path, backups)
    print(f"verified_database_backup={backup}")
    with closing(sqlite3.connect(path, timeout=30)) as db:
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("BEGIN IMMEDIATE")
        try:
            _, configs, original = read_config(db)
            idx = str(db_urls_index(db))
            ids = configs[idx]["model_ids"]
            if RELAY_ID not in ids:
                ids.append(RELAY_ID)
                new_value = json.dumps(configs, ensure_ascii=False)
                db.execute(
                    "UPDATE config SET value=?,updated_at=? WHERE key=? AND value=?",
                    (new_value, int(time.time()), "openai.api_configs", original),
                )
                if db.execute("SELECT changes()").fetchone()[0] != 1:
                    raise RuntimeError("Provider settings changed concurrently; aborting")
            if db.execute("SELECT 1 FROM model WHERE id=?", (public_id,)).fetchone() is None:
                now = int(time.time())
                db.execute(
                    "INSERT INTO model (id,user_id,base_model_id,name,params,meta,"
                    "updated_at,created_at,is_active) VALUES (?,?,NULL,?,?,?,?,?,1)",
                    (
                        public_id, admin_id or choose_admin(db), DISPLAY_NAME,
                        json.dumps({}), json.dumps({"description": DESCRIPTION}),
                        now, now,
                    ),
                )
            db.commit()
        except Exception:
            db.rollback()
            raise
    with closing(connect_readonly(path)) as db:
        registered, alias_present, _ = model_state(db)
        assert registered and alias_present, "Live database verification failed"
        name = db.execute("SELECT name FROM model WHERE id=?", (public_id,)).fetchone()[0]
        assert name, "Display name missing"
    print(f"result=applied_and_database_verified; display_name={name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Back up database and apply config")
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument("--backup-root", type=Path, default=DEFAULT_BACKUPS)
    args = parser.parse_args()
    try:
        run(args.database, args.backup_root, args.apply)
    except Exception as error:
        print(f"ERROR: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1)
