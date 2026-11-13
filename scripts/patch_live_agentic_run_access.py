"""Apply a surgical ABIT.AI live router guard without overwriting live-only code.

The ABiT Linux runtime contains preserved Dev-02/ERP fixes not yet merged into
the Git branch. Do NOT copy the entire source automations.py over live.
This patch only adds a governance-managed mutation denial after the existing
ERP-managed automation restriction. No secrets, DB writes or service restart.
"""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

ANCHOR = (
    "    if mutation and is_erp_managed(automation):\n"
    "        raise HTTPException(409, 'This agent is managed by its ERP task')\n"
)
ADDITION = (
    "    if mutation and (automation.meta or {}).get('governance_managed') is True:\n"
    "        raise HTTPException(409, 'Project-governed agent runs require ERP Project authorization')\n"
)


def apply(source: str) -> str:
    if source.count(ADDITION) == 1:
        return source
    if source.count(ANCHOR) != 1:
        raise ValueError("Expected unique live ERP-managed automation access guard")
    return source.replace(ANCHOR, ANCHOR + ADDITION, 1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--check", action="store_true", help="Validate exact patch without modifying")
    args = parser.parse_args()
    old = args.path.read_text(encoding="utf-8")
    new = apply(old)
    compile(new, str(args.path), "exec")
    if args.check:
        print("SAFE_PATCH_CHECK_OK changed=", new != old)
        return 0
    if new == old:
        print("ALREADY_PATCHED")
        return 0
    stat = args.path.stat()
    fd, tmp = tempfile.mkstemp(prefix="agov-access-", suffix=".py", dir=str(args.path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(new)
        os.chmod(tmp, stat.st_mode & 0o777)
        os.chown(tmp, stat.st_uid, stat.st_gid)
        os.replace(tmp, args.path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    print("APPLIED_MINIMAL_GOVERNANCE_RUN_GUARD")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
