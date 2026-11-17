"""Linux Active Directory validation through a dedicated administrator-owned PAM service.

Passwords are supplied only on stdin. Both authentication and account policy must
succeed. No login session is opened and no authentication token is changed.
"""
from __future__ import annotations

import os
import re
import stat
import subprocess
import threading
from pathlib import Path

PAM_EXECUTABLE = Path("/usr/bin/pamtester")
PAM_SERVICE_FILE = Path("/etc/pam.d/abit-ai")
_PAM_SLOTS = threading.BoundedSemaphore(4)
_UPN = re.compile(r"[A-Za-z0-9._-]{1,64}@[A-Za-z0-9][A-Za-z0-9.-]{0,251}[A-Za-z0-9]")
# Preserve the existing caller's result contract; account failures stay uniform.
_INVALID_CREDENTIALS = 1326
_UNAVAILABLE = -1
_AUTHENTICATION_FAILED = -2


def _trusted_file(path: Path, *, executable: bool = False) -> bool:
    try:
        info = path.lstat()
        parent = path.parent.stat()
        return (
            stat.S_ISREG(info.st_mode)
            and info.st_uid == 0
            and not (info.st_mode & 0o022)
            and parent.st_uid == 0
            and not (parent.st_mode & 0o022)
            and info.st_size > 0
            and (not executable or os.access(path, os.X_OK))
        )
    except OSError:
        return False


def validate_linux_ad_credentials(upn: str, password: str) -> tuple[bool, int]:
    """Fail closed unless the dedicated PAM authentication and account checks pass."""
    if (
        not isinstance(upn, str)
        or not _UPN.fullmatch(upn)
        or not isinstance(password, str)
        or not password.strip()
        or len(password) > 4096
        or any(value in password for value in ("\x00", "\r", "\n"))
    ):
        return False, _INVALID_CREDENTIALS
    if not _trusted_file(PAM_SERVICE_FILE) or not _trusted_file(
        PAM_EXECUTABLE, executable=True
    ):
        return False, _UNAVAILABLE
    if not _PAM_SLOTS.acquire(blocking=False):
        return False, _UNAVAILABLE
    try:
        result = subprocess.run(
            [
                str(PAM_EXECUTABLE),
                "abit-ai",
                upn,
                "authenticate(PAM_SILENT|PAM_DISALLOW_NULL_AUTHTOK)",
                "acct_mgmt",
            ],
            input=password + "\n",
            text=True,
            encoding="utf-8",
            stdin=None,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=6,
            check=False,
            close_fds=True,
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
        )
        return (True, 0) if result.returncode == 0 else (False, _AUTHENTICATION_FAILED)
    except (OSError, subprocess.TimeoutExpired):
        return False, _UNAVAILABLE
    finally:
        _PAM_SLOTS.release()
