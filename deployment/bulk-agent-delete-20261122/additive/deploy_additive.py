"""Add or roll back only the reviewed bulk-agent UI asset and its script tag.

The default action is read-only preflight. Production --apply/--rollback require
the user's release authorization; this file does not grant it.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import tempfile
import urllib.request

FRONTEND = Path("/srv/abit-dev-linux/abit-ai/current/runtime/open_webui/frontend")
ASSET_NAME = "agent-bulk-delete-20261122.js"
EXPECTED = {
    "index.html": "766cf5aa0a442b58c227df5ea833d0910d7f2863e896782a0311ba7b78e2303f",
    "agent-card-delete-20261115.js": "c084951378a744474e4e5c15e7a04ebf06938ad49a01dc96b9570c1b1147009b",
    "_app/immutable/nodes/22.AbitErpRuns20261108.js": "8435211e5ca4dc59c8f5aee38047bf643649494cfc484ad8ccc5c793a9a6c1d8",
}
TAG = ('\t\t<script src="/' + ASSET_NAME + '" defer crossorigin="use-credentials"></script>\n').encode()

def sha(data):
    return hashlib.sha256(data).hexdigest()

def add_tag(before):
    if TAG in before or ASSET_NAME.encode() in before:
        raise ValueError("Bulk-delete inclusion already exists; inspect before replacing it")
    if before.count(b"</head>") != 1:
        raise ValueError("Expected one closing head tag")
    after = before.replace(b"</head>", TAG + b"</head>", 1)
    if after.replace(TAG, b"", 1) != before:
        raise ValueError("Shell change exceeds the exact additive tag")
    return after

def remove_tag(current):
    if current.count(TAG) != 1:
        raise ValueError("Expected exactly one owned bulk-delete tag")
    return current.replace(TAG, b"", 1)

def atomic_replace(path, data, expected):
    if path.read_bytes() != expected:
        raise RuntimeError("Concurrent frontend change; no write performed")
    fd, name = tempfile.mkstemp(prefix=".bulk-agent-delete-", dir=str(path.parent))
    temp = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        shutil.copystat(path, temp)
        if path.read_bytes() != expected:
            raise RuntimeError("Concurrent frontend change; no write performed")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)

def get(path):
    with urllib.request.urlopen("http://127.0.0.1:8888" + path, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError("HTTP verification failed")
        return response.read()

def assert_identity():
    if socket.gethostname() != "abit-dev-linux":
        raise RuntimeError("Unexpected deployment host")
    identity = subprocess.check_output(["id", "-un"], text=True).strip()
    if identity != "jimran@abitcons.com":
        raise RuntimeError("Unexpected deployment identity")

def main():
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--apply", action="store_true")
    action.add_argument("--rollback", action="store_true")
    args = parser.parse_args()
    assert_identity()
    base = Path(__file__).resolve().parent
    frontend = FRONTEND.resolve()
    index = frontend / "index.html"
    before = index.read_bytes()

    if args.rollback:
        after = remove_tag(before)
        atomic_replace(index, after, before)
        if TAG in get("/automations"):
            raise RuntimeError("Owned tag remains in the served shell")
        print(json.dumps({"rolled_back": True, "index_sha256": sha(after), "service_restart": False}))
        return

    manifest = json.loads((base / "release-manifest.json").read_text())
    asset = (base / ASSET_NAME).read_bytes()
    if sha(asset) != manifest["asset_sha256"] or not 0 < len(asset) < 300_000:
        raise RuntimeError("Asset differs from the reviewed manifest")
    subprocess.run(["node", "--check", str(base / ASSET_NAME)], check=True)
    for name, expected_hash in EXPECTED.items():
        if sha((frontend / name).read_bytes()) != expected_hash:
            raise RuntimeError("Installed source drift: " + name)
    if not json.loads(get("/health")).get("status"):
        raise RuntimeError("Application is not healthy")
    after = add_tag(before)
    target = frontend / ASSET_NAME
    if target.exists():
        raise RuntimeError("Versioned target asset already exists; reconcile before retrying")
    report = {
        "release": "agent-bulk-delete-20261122",
        "host": socket.gethostname(),
        "frontend": str(frontend),
        "asset_sha256": sha(asset),
        "index_before_sha256": sha(before),
        "index_after_sha256": sha(after),
        "apply_requested": args.apply,
        "backend_changed": False,
        "service_restart": False,
        "live_agents_deleted_during_verification": False,
    }
    if not args.apply:
        print(json.dumps(report, indent=2))
        return

    backup = base / "index.before.html"
    if backup.exists() and backup.read_bytes() != before:
        raise RuntimeError("Existing backup belongs to a different source")
    if not backup.exists():
        backup.write_bytes(before)
    # Never replace existing assets or any generated application chunk.
    with target.open("xb") as stream:
        stream.write(asset)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(target, 0o644)
    try:
        atomic_replace(index, after, before)
        if get("/" + ASSET_NAME) != asset or TAG not in get("/automations"):
            raise RuntimeError("Served frontend differs from the reviewed package")
        if not json.loads(get("/health")).get("status"):
            raise RuntimeError("Health failed after frontend inclusion")
    except BaseException:
        current = index.read_bytes()
        if current.count(TAG) == 1:
            atomic_replace(index, remove_tag(current), current)
        raise
    report["activated"] = True
    report["http_verified"] = True
    (base / "release-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    main()
