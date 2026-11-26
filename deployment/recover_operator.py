#!/usr/bin/env python3
"""Root-only CLI for the separately approved expired-owner recovery control."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import stat
import sys
from pathlib import Path

EXPECTED_HELPER_SHA256 = 'f09d40a577d180ec4183a046e5dd2a98bcdd5f306a9ab216e72845eed51d5a3c'


def verify_protected_installation() -> None:
    script = Path(__file__)
    module = script.with_name('operator_ownership_recovery.py')
    directory = script.parent
    if script.is_symlink() or module.is_symlink() or directory.is_symlink() or directory.resolve() != directory.absolute():
        raise RuntimeError('recovery installation path must not contain symlinks')
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
        raise RuntimeError('recovery installation directory must be root-owned mode 0700')
    for path in (script, module):
        item = path.lstat()
        if not stat.S_ISREG(item.st_mode) or item.st_nlink != 1 or item.st_uid != 0 or stat.S_IMODE(item.st_mode) != 0o500:
            raise RuntimeError(f'protected recovery source metadata differs: {path.name}')
    if hashlib.sha256(module.read_bytes()).hexdigest() != EXPECTED_HELPER_SHA256:
        raise RuntimeError('protected recovery helper SHA-256 differs from the reviewed package')


def load_recovery_module():
    module_path = Path(__file__).resolve().with_name('operator_ownership_recovery.py')
    spec = importlib.util.spec_from_file_location('operator_ownership_recovery', module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError('protected recovery module could not be loaded')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='action', required=True)
    recover = sub.add_parser('recover')
    recover.add_argument('--request', required=True, type=Path)
    reconcile = sub.add_parser('reconcile-commit')
    reconcile.add_argument('--expected-record-sha256', required=True)
    acknowledge = sub.add_parser('acknowledge')
    acknowledge.add_argument('--recovery-id', required=True)
    args = parser.parse_args()

    verify_protected_installation()
    recovery = load_recovery_module()
    recovery._admin_identity()
    if args.action == 'recover':
        raw, info = recovery._read_regular(args.request, max_bytes=16 * 1024)
        if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
            raise recovery.RecoveryDenied('recovery request must be root-owned mode 0600')
        request = json.loads(raw)
        if not isinstance(request, dict):
            raise recovery.RecoveryDenied('recovery request must be a JSON object')
        result = recovery.recover_expired_operator(**request)
    elif args.action == 'reconcile-commit':
        result = recovery.reconcile_commit(args.expected_record_sha256)
    else:
        result = recovery.apply_receiver_acknowledgment(args.recovery_id)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        if error.__class__.__name__ == 'RecoveryDenied' or isinstance(error, (OSError, ValueError, TypeError, RuntimeError)):
            print(json.dumps({'status': 'DENIED', 'reason': str(error)}), file=sys.stderr)
            raise SystemExit(2)
        raise
