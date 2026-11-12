#!/usr/bin/env python3
"""Enable native Open WebUI Google OAuth signup on ABIT-DEV-LINUX.

A Google Cloud OAuth *Web application* client with the authorized redirect URI
https://abit.ai/oauth/google/callback must exist before --apply can run.
Credentials are read from a root-protected JSON file and never printed.
"""
import argparse
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import stat
import subprocess
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from urllib.request import HTTPRedirectHandler, build_opener, urlopen

ROOT = Path("/srv/abit-dev-linux/abit-ai")
ENV_FILE = ROOT / "config" / "google-signup.env"
DROP_IN = Path("/etc/systemd/system/abit-ai.service.d/60-google-signup.conf")
DB_FILE = ROOT / "data" / "webui.db"
BACKUP_DIR = ROOT / "data" / "backups" / "google-signup"
REDIRECT_URI = "https://abit.ai/oauth/google/callback"
PUBLIC_CONFIG = "https://abit.ai/api/config"
LOCAL_HEALTH = "http://127.0.0.1:8888/health"
CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9._-]+\.apps\.googleusercontent\.com$")
SECRET_RE = re.compile(r"^[A-Za-z0-9._-]{10,}$")


def validate_credentials(source: dict) -> tuple[str, str]:
    client_id = source.get("client_id")
    client_secret = source.get("client_secret")
    if not isinstance(client_id, str) or not CLIENT_ID_RE.fullmatch(client_id):
        raise ValueError("Invalid Google OAuth Web application client_id")
    if not isinstance(client_secret, str) or not SECRET_RE.fullmatch(client_secret):
        raise ValueError("Invalid Google OAuth client_secret")
    return client_id, client_secret


def render_env(client_id: str, client_secret: str) -> str:
    # Strict validation above prevents EnvironmentFile injection.
    validate_credentials({"client_id": client_id, "client_secret": client_secret})
    return (
        f"GOOGLE_CLIENT_ID={client_id}\n"
        f"GOOGLE_CLIENT_SECRET={client_secret}\n"
        f"GOOGLE_REDIRECT_URI={REDIRECT_URI}\n"
        "GOOGLE_OAUTH_SCOPE=openid email profile\n"
        "WEBUI_URL=https://abit.ai\n"
        "ENABLE_OAUTH=true\n"
        "ENABLE_OAUTH_SIGNUP=true\n"
        "ENABLE_OAUTH_PERSISTENT_CONFIG=false\n"
        "OAUTH_MERGE_ACCOUNTS_BY_EMAIL=false\n"
        "OAUTH_AUTO_REDIRECT=false\n"
    )


def load_credentials(path: Path) -> tuple[str, str]:
    if path.is_symlink():
        raise ValueError("Credentials file cannot be a symlink")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise ValueError("Credentials file must be a regular file with mode 0600")
    return validate_credentials(json.loads(path.read_text(encoding="utf-8")))


def atomic_write(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".google-signup-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as out:
            os.fchmod(out.fileno(), mode)
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def backup_database() -> Path:
    BACKUP_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not DB_FILE.is_file():
        raise RuntimeError("Production database missing; refusing deployment")
    target = BACKUP_DIR / f"webui-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.sqlite3"
    with sqlite3.connect(f"file:{DB_FILE}?mode=ro", uri=True, timeout=30) as source:
        with sqlite3.connect(str(target), timeout=30) as destination:
            source.backup(destination)
    os.chmod(target, 0o600)
    return target


def url_json(url: str) -> dict:
    with urlopen(url, timeout=6) as response:
        if response.status != 200:
            raise RuntimeError(f"Endpoint returned {response.status}: {url}")
        return json.loads(response.read())


def google_redirect_ok() -> bool:
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    try:
        build_opener(NoRedirect).open("https://abit.ai/oauth/google/login", timeout=8)
    except HTTPError as exc:
        if exc.code not in (302, 303, 307):
            return False
        location = exc.headers.get("Location", "")
        parsed = urlparse(location)
        params = parse_qs(parsed.query)
        return (
            parsed.scheme == "https"
            and parsed.hostname == "accounts.google.com"
            and params.get("redirect_uri") == [REDIRECT_URI]
        )
    return False


def status() -> tuple[bool, bool]:
    try:
        with urlopen(LOCAL_HEALTH, timeout=6) as resp:
            healthy = resp.status == 200
    except Exception:
        healthy = False
    try:
        config = url_json(PUBLIC_CONFIG)
        google_enabled = "google" in config.get("oauth", {}).get("providers", {})
    except Exception:
        google_enabled = False
    return healthy, google_enabled


def run(command: list[str]) -> None:
    subprocess.run(command, check=True, timeout=60, stdout=subprocess.DEVNULL)


def restart_and_verify() -> bool:
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "restart", "abit-ai.service"])
    for _ in range(35):
        time.sleep(2)
        healthy, enabled = status()
        if healthy and enabled and google_redirect_ok():
            return True
    return False


def restore(path: Path, original: bytes | None, mode: int) -> None:
    if original is None:
        path.unlink(missing_ok=True)
    else:
        atomic_write(path, original, mode)


def verify_service_identity() -> None:
    result = subprocess.run(
        ["systemctl", "show", "abit-ai.service", "-p", "MainPID", "-p", "ExecStart"],
        capture_output=True,
        text=True,
        check=True,
        timeout=12,
    )
    pid = next((line.partition("=")[2] for line in result.stdout.splitlines()
                if line.startswith("MainPID=")), "0")
    if not pid.isdigit() or int(pid) < 1 or (
        "/srv/abit-dev-linux/abit-ai/deployment/run-production.py" not in result.stdout
    ):
        raise RuntimeError("Unexpected ABiT AI service process; refusing restart")


def apply(credentials_path: Path) -> None:
    if os.geteuid() != 0 or socket.gethostname().lower() != "abit-dev-linux":
        raise RuntimeError("Run with sudo on verified host abit-dev-linux only")
    if not ROOT.is_dir() or not DB_FILE.is_file():
        raise RuntimeError("Expected live ABiT AI install not found")
    verify_service_identity()
    client_id, client_secret = load_credentials(credentials_path)
    healthy, _ = status()
    if not healthy:
        raise RuntimeError("Application is unhealthy before change; refusing restart")
    old_env = ENV_FILE.read_bytes() if ENV_FILE.exists() else None
    old_drop = DROP_IN.read_bytes() if DROP_IN.exists() else None
    old_env_mode = stat.S_IMODE(ENV_FILE.stat().st_mode) if old_env is not None else 0o600
    old_drop_mode = stat.S_IMODE(DROP_IN.stat().st_mode) if old_drop is not None else 0o644

    backup = backup_database()
    print(f"Production SQLite backup completed: {backup}")
    try:
        atomic_write(ENV_FILE, render_env(client_id, client_secret).encode(), 0o600)
        atomic_write(
            DROP_IN,
            (f"[Service]\nEnvironmentFile={ENV_FILE}\n").encode(),
            0o644,
        )
        if not restart_and_verify():
            raise RuntimeError("Google OAuth redirect or post-restart health verification failed")
    except BaseException:
        restore(ENV_FILE, old_env, old_env_mode)
        restore(DROP_IN, old_drop, old_drop_mode)
        try:
            run(["systemctl", "daemon-reload"])
            run(["systemctl", "restart", "abit-ai.service"])
        finally:
            print("Rolled back Google OAuth service configuration; existing database untouched.")
        raise
    print("Google OAuth login endpoint and public provider enabled.")
    print("Final real-user Google consent, callback and pending-account approval test still required.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="Read-only application check")
    group.add_argument("--apply", metavar="CREDENTIALS_JSON", type=Path)
    args = parser.parse_args()
    if args.check:
        healthy, google = status()
        print(f"Local application healthy: {healthy}; Google provider active: {google}")
    else:
        apply(args.apply)


if __name__ == "__main__":
    main()