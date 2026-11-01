#!/usr/bin/env python3
"""Switch only ABiT AI's saved MCP development connection to local ABIT-DEV-LINUX.

Usage: python3 route_mcp_to_linux.py            # dry run
       python3 route_mcp_to_linux.py --apply
       python3 route_mcp_to_linux.py --rollback /path/to/backup.db

The rollback restores ONLY the saved tool-server config, never a database snapshot.
Backups contain private configuration and must stay outside Git.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import socket
import sqlite3
import time
from urllib.request import Request, urlopen
from urllib.parse import urlparse

DB = Path("/srv/abit-dev-linux/abit-ai/data/webui.db")
LOCAL_MCP = "http://127.0.0.1:8795/mcp"
OLD_ID = "development-server-dev-02"
NEW_ID = "abit-dev-linux"
KEY = "tool_server.connections"


def switch_connection(connections):
    if not isinstance(connections, list):
        raise ValueError("tool-server configuration is not a list")
    result = copy.deepcopy(connections)
    old = [i for i, item in enumerate(result)
           if item.get("info", {}).get("id") == OLD_ID]
    new = [i for i, item in enumerate(result)
           if item.get("info", {}).get("id") == NEW_ID]
    if not old and len(new) == 1 and result[new[0]].get("url") == LOCAL_MCP:
        return result, False
    if len(old) != 1 or new:
        raise ValueError("expected exactly one legacy server and no Linux duplicate")
    item = result[old[0]]
    if item.get("type") != "mcp" or item.get("auth_type") != "none":
        raise ValueError("unexpected legacy MCP connection scheme")
    if urlparse(item.get("url", "")).hostname != "development-server-dev-02.abit.ai":
        raise ValueError("unexpected legacy MCP server hostname")
    # Keep access grants, feature filters, and all unrelated tool servers intact.
    item["url"] = LOCAL_MCP
    item["info"].update({
        "id": NEW_ID,
        "name": "ABIT-DEV-LINUX",
        "description": "Linux development workspace MCP server (mcpdev; no sudo by default)"
    })
    return result, True


def check_local_mcp():
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "abit-ai-linux-cutover-check", "version": "1"}
        }
    }
    req = Request(LOCAL_MCP, data=json.dumps(payload).encode("utf-8"),
                  headers={"Content-Type": "application/json",
                           "Accept": "application/json, text/event-stream"},
                  method="POST")
    with urlopen(req, timeout=8) as res:
        response = json.load(res)
    if response.get("result", {}).get("serverInfo", {}).get("name") != "ABIT-DEV-LINUX":
        raise RuntimeError("local MCP did not confirm the ABIT-DEV-LINUX identity")


def read_config(db):
    row = db.execute("SELECT value FROM config WHERE key = ?", (KEY,)).fetchone()
    if not row:
        raise ValueError("production MCP config key missing")
    return row[0], json.loads(row[0])


def backup_db(db, destination):
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    with sqlite3.connect(str(destination)) as snapshot:
        db.backup(snapshot)
    return destination


def update_config(db, old_raw, value):
    db.execute("BEGIN IMMEDIATE")
    current = db.execute("SELECT value FROM config WHERE key=?", (KEY,)).fetchone()
    if not current or current[0] != old_raw:
        db.rollback()
        raise RuntimeError("MCP config changed during migration; no write performed")
    db.execute("UPDATE config SET value=?, updated_at=? WHERE key=?",
               (json.dumps(value, ensure_ascii=False, separators=(",", ":")),
                int(time.time()), KEY))
    db.commit()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_mutually_exclusive_group()
    commands.add_argument("--apply", action="store_true")
    commands.add_argument("--rollback", type=Path)
    args = parser.parse_args()
    if socket.gethostname().lower() != "abit-dev-linux":
        raise RuntimeError("refusing to change configuration outside ABIT-DEV-LINUX")
    if not DB.is_file():
        raise RuntimeError("production SQLite database is missing")
    with sqlite3.connect(str(DB), timeout=15) as db:
        db.execute("PRAGMA busy_timeout=15000")
        old_raw, old_config = read_config(db)
        if args.rollback:
            with sqlite3.connect(f"file:{args.rollback}?mode=ro", uri=True) as snapshot:
                _, restore = read_config(snapshot)
            _, old_present = switch_connection(restore)
            if not old_present:
                raise RuntimeError("rollback snapshot does not contain the original server")
            current, needs_change = switch_connection(old_config)
            if needs_change:
                raise RuntimeError("current config was not migrated; refusing rollback")
            changed_snapshot = DB.parent / "backups" / f"pre-linux-mcp-rollback-{int(time.time())}.db"
            backup_db(db, changed_snapshot)
            update_config(db, old_raw, restore)
            print("ROLLED_BACK_CONFIG_ONLY backup=", changed_snapshot)
            return
        new_config, changed = switch_connection(old_config)
        if not changed:
            print("ALREADY_MIGRATED local_mcp=", LOCAL_MCP)
            return
        check_local_mcp()
        if not args.apply:
            print("DRY_RUN_READY target=", LOCAL_MCP, "preserved_other_servers=", len(old_config) - 1)
            return
        destination = DB.parent / "backups" / f"pre-linux-mcp-{int(time.time())}.db"
        backup_db(db, destination)
        update_config(db, old_raw, new_config)
        _, verified = read_config(db)
        if verified != new_config:
            raise RuntimeError("post-write readback differs from requested configuration")
        print("MIGRATED target=", LOCAL_MCP, "server_id=", NEW_ID,
              "preserved_other_servers=", len(old_config) - 1, "backup=", destination)


if __name__ == "__main__":
    main()
