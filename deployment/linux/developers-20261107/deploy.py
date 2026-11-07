#!/usr/bin/env python3
"""Deploy the scoped ABiT Developers module without replacing the existing frontend.

Run with the existing production sudo authorization. The payload is built in the
authoritative Dev-02 checkout. This script never copies or restores the live data
directory, changes service configuration, or modifies existing compiled assets.
"""
from __future__ import annotations

import argparse
import ast
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

ROOT = Path("/srv/abit-dev-linux/abit-ai")
RUNTIME = ROOT / "current/runtime/open_webui"
SERVICE = "abit-ai.service"
SOURCE_FILES = {
    "backend/open_webui/routers/developer.py": "routers/developer.py",
    "backend/open_webui/utils/developer_api.py": "utils/developer_api.py",
    "backend/open_webui/utils/developer_ui.py": "utils/developer_ui.py",
}


def patch_main(text: str) -> str:
    if "    developer,\n" not in text:
        needle = "    configs,\n"
        assert text.count(needle) == 1, "Router import anchor changed"
        text = text.replace(needle, needle + "    developer,\n", 1)
    if "app.include_router(developer.router" not in text:
        needle = "app.include_router(auths.router,"
        assert text.count(needle) == 1, "Router registration anchor changed"
        text = text.replace(needle, "app.include_router(developer.router, prefix='/api/v1/developer', tags=['developer'])\n" + needle, 1)
    if "from open_webui.utils.developer_ui import" not in text:
        needle = "class SPAStaticFiles(StaticFiles):\n"
        assert text.count(needle) == 1, "SPA class anchor changed"
        replacement = (
            "from open_webui.utils.developer_ui import register_developer_ui, render_developer_navigation\n\n\n"
            + needle
            + "    def file_response(self, full_path, stat_result, scope, status_code=200):\n"
            + "        response = render_developer_navigation(\n"
            + "            full_path, scope, status_code, frontend_build_dir=self.directory\n"
            + "        )\n"
            + "        if response is not None:\n"
            + "            return response\n"
            + "        return super().file_response(full_path, stat_result, scope, status_code)\n\n"
        )
        text = text.replace(needle, replacement, 1)
    if "register_developer_ui(app, FRONTEND_BUILD_DIR)" not in text:
        needle = "if os.path.exists(FRONTEND_BUILD_DIR):\n"
        assert text.count(needle) == 1, "Frontend mount anchor changed"
        text = text.replace(needle, needle + "    register_developer_ui(app, FRONTEND_BUILD_DIR)\n", 1)
    ast.parse(text)
    return text


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def request(path: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8888" + path, timeout=8) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def relay_idle() -> bool:
    with urllib.request.urlopen("http://127.0.0.1:8810/health", timeout=5) as response:
        state = json.load(response)
    return state.get("active_relay_turns") == 0


def wait_healthy(timeout: int = 90) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status, body = request("/health")
            if status == 200 and json.loads(body).get("status") is True:
                return
        except (OSError, ValueError):
            pass
        time.sleep(1)
    raise RuntimeError("Production health did not recover")


def atomic_write(path: Path, data: bytes, *, mode: int, uid: int, gid: int) -> None:
    handle, temporary = tempfile.mkstemp(prefix=".developer-release-", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.chown(temporary, uid, gid)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def counts(database: Path) -> dict[str, int]:
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=10) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return {name: connection.execute('SELECT count(*) FROM "' + name + '"').fetchone()[0]
                for name in ("user", "chat", "folder", "automation", "config") if name in tables}


def deploy(stage: Path, expected_main: str) -> dict:
    assert os.geteuid() == 0, "Use existing sudo authorization for controlled service management"
    main_file = RUNTIME / "main.py"
    frontend = RUNTIME / "frontend"
    assert main_file.is_file() and (ROOT / "data/webui.db").is_file()
    assert digest(main_file) == expected_main, "Runtime main changed; inspect the new version first"
    assert not (frontend / "developer-ui").exists(), "Developer UI already exists; inspect before updating"
    for destination in SOURCE_FILES.values():
        assert not (RUNTIME / destination).exists(), f"Unexpected existing module: {destination}"
    manifest = json.loads((stage / "manifest.json").read_text())
    for relative, expected in manifest["files"].items():
        path = (stage / relative).resolve()
        assert path.is_relative_to(stage.resolve()) and path.is_file()
        assert digest(path) == expected, f"Staged file changed: {relative}"
    for source in SOURCE_FILES:
        ast.parse((stage / source).read_text(encoding="utf-8"))
    original_main = main_file.read_text(encoding="utf-8")
    changed_main = patch_main(original_main)
    assert changed_main != original_main
    unit = subprocess.check_output(["systemctl", "show", SERVICE, "--property=ExecStart", "--property=WorkingDirectory", "--property=ActiveState"], text=True)
    assert "run-production.py" in unit and str(ROOT) in unit and "ActiveState=active" in unit
    assert request("/health")[0] == 200 and request("/api/config")[0] == 200
    assert relay_idle(), "BUSY: wait for active relay work to finish before restarting"

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = ROOT / "deployment" / "developers-20261107" / "backups" / stamp
    backup.mkdir(parents=True, mode=0o700, exist_ok=False)
    os.chmod(backup, 0o700)
    shutil.copy2(main_file, backup / "main.py")
    os.chmod(backup / "main.py", 0o600)
    database = ROOT / "data/webui.db"
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=30) as live:
        with sqlite3.connect(backup / "webui.db") as snapshot:
            live.backup(snapshot, pages=1024, sleep=0.01)
            assert snapshot.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    os.chmod(backup / "webui.db", 0o600)
    before_counts = counts(backup / "webui.db")
    # Keep a digest of every existing frontend file. No existing asset is replaced.
    frontend_before = {str(path.relative_to(frontend)): digest(path)
                       for path in frontend.rglob("*") if path.is_file()}
    report = {
        "snapshot_method": "SQLite backup API, including committed WAL contents",
        "backup": str(backup), "before_main_sha256": expected_main,
        "before_counts": before_counts, "source_commit": manifest.get("source_commit"),
        "frontend_files_preserved": len(frontend_before), "result": "preflight",
    }
    (backup / "report.json").write_text(json.dumps(report, indent=2))
    assert digest(main_file) == expected_main, "Concurrent backend deployment detected"
    assert relay_idle(), "BUSY: relay work started while backup ran; live files unchanged"
    metadata = main_file.stat()
    added = []
    portal = frontend / "developer-ui"
    try:
        added.append(portal)
        shutil.copytree(stage / "build-developers", portal)
        for path in [portal, *portal.rglob("*")]:
            os.chmod(path, 0o755 if path.is_dir() else 0o644)
            os.chown(path, metadata.st_uid, metadata.st_gid)
        for source, destination in SOURCE_FILES.items():
            target = RUNTIME / destination
            atomic_write(target, (stage / source).read_bytes(), mode=0o660, uid=metadata.st_uid, gid=metadata.st_gid)
            added.append(target)
        atomic_write(main_file, changed_main.encode(), mode=metadata.st_mode & 0o777, uid=metadata.st_uid, gid=metadata.st_gid)
        subprocess.run(["systemctl", "restart", SERVICE], check=True, timeout=100)
        wait_healthy()
        assert request("/api/config")[0] == 200
        for path in ("/api/v1/developer/", "/api/v1/developer/openapi.json", "/api/v1/developer/postman.json"):
            status, body = request(path)
            assert status in (401, 403) and b"<!doctype" not in body.lower(), f"Anonymous access check failed: {path}"
        status, html = request("/developers")
        assert status == 200 and b"/developer-ui/" in html, "Standalone module shell failed"
        status, navigation = request("/developer-ui/navigation.js")
        assert status == 200 and b"sidebar-developers-button" in navigation
        status, app_html = request("/")
        assert status == 200 and b"/developer-ui/navigation.js" in app_html, "Navigation injection failed"
        changed = [relative for relative, checksum in frontend_before.items()
                   if not (frontend / relative).is_file() or digest(frontend / relative) != checksum]
        assert not changed, "Existing frontend changed concurrently: " + ", ".join(changed[:5])
        report.update({
            "result": "deployed", "after_main_sha256": digest(main_file),
            "after_counts": counts(database), "health": 200, "config": 200,
            "anonymous_exports": "denied", "developer_shell": 200, "navigation": 200,
            "existing_frontend_unchanged": True,
        })
    except BaseException as error:
        atomic_write(main_file, (backup / "main.py").read_bytes(), mode=metadata.st_mode & 0o777, uid=metadata.st_uid, gid=metadata.st_gid)
        for path in reversed(added):
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        subprocess.run(["systemctl", "restart", SERVICE], check=True, timeout=100)
        wait_healthy()
        report.update({"result": "rolled_back", "error": str(error)})
        (backup / "report.json").write_text(json.dumps(report, indent=2))
        raise
    (backup / "report.json").write_text(json.dumps(report, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--expected-main-sha", required=True)
    args = parser.parse_args()
    with open("/tmp/abit-ai-deploy.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print(json.dumps(deploy(args.stage.resolve(), args.expected_main_sha), indent=2))


if __name__ == "__main__":
    main()
