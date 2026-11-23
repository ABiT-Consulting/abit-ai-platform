#!/usr/bin/env python3
"""Root-only CLI for the separately reviewed expired-owner recovery control."""
import argparse
import importlib.util
import json
import os
import socket
import sys
from pathlib import Path

MODULE = Path(__file__).resolve().with_name('operator_ownership_recovery.py')
SPEC = importlib.util.spec_from_file_location('abit_operator_recovery', MODULE)
recovery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recovery)
RecoveryDenied = recovery.RecoveryDenied
recover_expired_operator = recovery.recover_expired_operator
acknowledge_recovered_operator = recovery.acknowledge_recovered_operator


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='action', required=True)
    recover = sub.add_parser('recover')
    recover.add_argument('--request', required=True, type=Path)
    ack = sub.add_parser('acknowledge')
    ack.add_argument('--receiver-id', required=True)
    ack.add_argument('--expected-record-sha256', required=True)
    ack.add_argument('--token-stdin', action='store_true', required=True)
    args = parser.parse_args()
    if os.geteuid() != 0 or os.environ.get('SUDO_USER') != 'jimran@abitcons.com' or socket.gethostname() != 'abit-dev-linux':
        raise RecoveryDenied('run through the established sudo administrator boundary on abit-dev-linux')
    if args.action == 'recover':
        request = json.loads(args.request.read_text())
        result = recover_expired_operator(admin_id='jimran@abitcons.com', admin_role='admin', **request)
    else:
        token = sys.stdin.readline().strip()
        result = acknowledge_recovered_operator(
            receiver_id=args.receiver_id, receiver_token=token,
            expected_record_sha256=args.expected_record_sha256,
        )
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (RecoveryDenied, OSError, ValueError) as error:
        print(json.dumps({'status': 'DENIED', 'reason': str(error)}), file=sys.stderr)
        raise SystemExit(2)
