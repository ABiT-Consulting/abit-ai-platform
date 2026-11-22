"""Narrow bootstrap admission fence. Importing this module performs no action.

Only the one owned IPv4 table may be created/deleted. Established connections
are not reset. The fixed root probe is an explicit trusted operator exception.
All other rules, original process identities and business records remain pinned.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time

NFT = '/usr/sbin/nft'
TABLE = 'abit_relay_bootstrap_20261122'
CHAIN = 'output'
PORT = 8810
MARKER = 'abit-restored-target-bootstrap:'
MAX_NFT = 8 * 1024 * 1024
# All socket states except CLOSED, TIME_WAIT, LISTEN retain potential work.
QUIESCENT_STATES = {'06', '07', '0A'}


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def objects(manifest_sha):
    require(re.fullmatch('[a-f0-9]{64}', manifest_sha or '') is not None, 'Exact activation identity required')
    match = lambda left, right, op='==': {'match': {'op': op, 'left': left, 'right': right}}
    return [
        {'table': {'family': 'ip', 'name': TABLE, 'comment': MARKER + manifest_sha}},
        {'chain': {'family': 'ip', 'table': TABLE, 'name': CHAIN, 'type': 'filter',
                   'hook': 'output', 'prio': -300, 'policy': 'accept'}},
        {'rule': {'family': 'ip', 'table': TABLE, 'chain': CHAIN, 'expr': [
            match({'payload': {'protocol': 'ip', 'field': 'daddr'}}, '127.0.0.1'),
            match({'payload': {'protocol': 'tcp', 'field': 'dport'}}, PORT),
            match({'&': [{'payload': {'protocol': 'tcp', 'field': 'flags'}}, ['syn', 'ack']]}, 'syn'),
            match({'meta': {'key': 'skuid'}}, 0, '!='),
            {'reject': {'type': 'tcp reset'}},
        ], 'comment': MARKER + manifest_sha}},
    ]


def create_document(manifest_sha):
    table, chain, rule = objects(manifest_sha)
    return {'nftables': [{'create': table}, {'add': chain}, {'add': rule}]}


def expected_readback(manifest_sha):
    # Exact nft 1.0.9 readback, verified in an isolated kernel network namespace.
    return objects(manifest_sha)


def split_ruleset(value):
    require(type(value) is dict and set(value) == {'nftables'} and
            type(value['nftables']) is list, 'Unexpected nft JSON')
    other, owned = [], []
    for item in value['nftables']:
        require(type(item) is dict and len(item) == 1, 'Unexpected nft object')
        key, row = next(iter(item.items()))
        if key == 'metainfo':
            continue
        require(type(row) is dict, 'Unexpected nft object body')
        is_owned = row.get('family') == 'ip' and (
            key == 'table' and row.get('name') == TABLE or row.get('table') == TABLE)
        (owned if is_owned else other).append(item)
    return other, owned


def without_handles(items):
    return [{key: {field: value for field, value in row.items() if field != 'handle'}}
            for item in items for key, row in item.items()]


def verify_owned(items, manifest_sha):
    require(without_handles(items) == expected_readback(manifest_sha),
            'Owned admission table changed; no automatic removal')
    require(all(type(row.get('handle')) is int and row['handle'] > 0
                for item in items for row in item.values()), 'Owned nft handles missing')
    return {'objects_sha256': digest(items), 'table_handle': items[0]['table']['handle']}


def read_ruleset():
    result = subprocess.run([NFT, '-s', '-j', '-a', 'list', 'ruleset'],
                            capture_output=True, timeout=10, check=False)
    require(result.returncode == 0 and len(result.stdout) <= MAX_NFT, 'nft readback unavailable')
    return json.loads(result.stdout)


def tcp_rows(text):
    rows = []
    for line in text.splitlines()[1:]:
        parts = line.split()
        require(len(parts) >= 10, 'Unexpected TCP inventory')
        rows.append({'local': parts[1], 'remote': parts[2], 'state': parts[3],
                     'uid': int(parts[7]), 'inode': parts[9]})
    return rows


def usable_ingress(rows):
    endpoint = '0100007F:' + format(PORT, '04X')
    return [row for row in rows if row['state'] not in QUIESCENT_STATES
            and (row['local'] == endpoint or row['remote'] == endpoint)]


def process_socket_inodes(pid):
    paths = list((Path('/proc') / str(pid) / 'fd').iterdir())
    require(len(paths) <= 4096, 'Node socket inventory exceeded')
    found = set()
    for path in paths:
        try:
            target = os.readlink(path)
        except FileNotFoundError:
            continue
        match = re.fullmatch(r'socket:\[([0-9]+)\]', target)
        if match:
            found.add(match.group(1))
    return found


def network_state(pid):
    rows = tcp_rows(Path('/proc/net/tcp').read_text())
    rows6 = tcp_rows(Path('/proc/net/tcp6').read_text())
    owned = process_socket_inodes(pid)
    active = [row for row in rows + rows6 if row['inode'] in owned
              and row['state'] not in QUIESCENT_STATES]
    return {'usable_ingress': len(usable_ingress(rows)), 'node_network_work': len(active)}


RUNTIME_PATHS = {
    'ABIT_ERP_CHATGPT_RELAY_HOST': '127.0.0.1',
    'ABIT_ERP_CHATGPT_RELAY_PORT': '8810',
    'ABIT_ERP_CHATGPT_CDP_URL': 'http://127.0.0.1:9228',
    'ABIT_ERP_CHATGPT_TARGET_FILE': '/srv/abit-dev-linux/relay-runtime/state/aibit-relay-target-id.txt',
    'ABIT_ERP_CHATGPT_PROJECT_CACHE': '/srv/abit-dev-linux/relay-runtime/state/aibit-relay-projects.json',
    'ABIT_ERP_CHATGPT_RATE_LIMIT_STATE_FILE': '/srv/abit-dev-linux/relay-runtime/state/aibit-relay-rate-limit.json',
    'ABIT_SHARED_CHATGPT_LOCK_FILE': '/srv/abit-dev-linux/relay-runtime/state/browser.lock',
    'ABIT_ERP_RELAY_STATE_DIR': '/srv/abit-dev-linux/relay-runtime/state/erp-assignment',
}


def selected_environment(pid):
    # Select fixed nonsensitive settings only. The process environment is never
    # serialized, copied to evidence, printed, or accepted as command input.
    values = {}
    selected = set(RUNTIME_PATHS) | {'ABIT_ERP_CHATGPT_THREAD_TARGET_FILE',
        'CHATGPT_EXISTING_CHROME_USER_DATA_DIR', 'ABIT_HERMES_TIMEOUT_MS', 'ABIT_HERMES_PROXY_URL'}
    with (Path('/proc') / str(pid) / 'environ').open('rb') as stream:
        data = stream.read(2 * 1024 * 1024 + 1)
    require(len(data) <= 2 * 1024 * 1024, 'Process environment exceeded bound')
    for entry in data.split(b'\0'):
        key, sep, value = entry.partition(b'=')
        if sep and key.decode(errors='replace') in selected:
            values[key.decode()] = value.decode()
    return values


def validate_runtime_values(values):
    require(all(values.get(key) == value for key, value in RUNTIME_PATHS.items()),
            'Actual Node transport or state path differs from the guarded bootstrap')
    require(not values.get('CHATGPT_EXISTING_CHROME_USER_DATA_DIR') and
            values.get('ABIT_ERP_CHATGPT_THREAD_TARGET_FILE', '') in ('',
                '/srv/abit-dev-linux/relay-runtime/state/chat-thread-targets.json'),
            'Actual browser backend or original selector path differs')
    timeout = int(values.get('ABIT_HERMES_TIMEOUT_MS', '') or '45000')
    require(1 <= timeout <= 90000, 'Hermes drain timeout outside reviewed bound')
    from urllib.parse import urlsplit
    url = values.get('ABIT_HERMES_PROXY_URL', '') or 'http://127.0.0.1:8645'
    parsed = urlsplit(url)
    require(parsed.scheme == 'http' and parsed.hostname == '127.0.0.1'
            and not parsed.username and not parsed.password and not parsed.query
            and not parsed.fragment, 'Hermes needs a reviewed finite local transport')
    return {'timeout_ms': timeout, 'origin_sha256': hashlib.sha256(url.rstrip('/').encode()).hexdigest()}


def source_timeout(pid):
    return validate_runtime_values(selected_environment(pid))


class Bootstrap:
    def __init__(self, api, manifest, record, cycle):
        self.a, self.m, self.r, self.c = api, manifest, record, cycle

    def snapshot(self, required):
        other, owned = split_ruleset(read_ruleset())
        require(digest(other) == self.m['nft_before_sha256'], 'Existing firewall changed')
        if required:
            proof = verify_owned(owned, self.a.MANIFEST_SHA)
            old = self.c['intents'].get('nft_seal', {}).get('proof')
            if old:
                require(proof == old, 'Owned admission barrier identity changed')
            return proof
        require(not owned, 'Owned admission barrier is not absent')
        return {'owned_table_absent': True, 'preexisting_rules_sha256': digest(other)}

    def seal(self, issue=True):
        intent = self.c['intents'].get('nft_seal')
        if intent and intent.get('command_phase') not in ('intent_saved', 'precondition_failed'):
            proof = self.snapshot(True)
            if not intent.get('fulfilled_utc'):
                self.a.finished(self.r, self.c, 'nft_seal', proof)
            return proof
        require(issue, 'Admission seal has no observed command')
        document = canonical(create_document(self.a.MANIFEST_SHA)) + b'\n'
        path = self.a.STAGE / 'inputs' / ('nft-' + self.c['mode'] + '-seal.json')
        self.a.save_or_verify(path, document)
        def before():
            self.a.owner(self.m)
            self.a.preserve(self.m)
            self.a.control_receipt(self.m, self.c['intents']['close'])
            self.snapshot(False)
            self.a.require(self.a.filehash(path) == hashlib.sha256(document).hexdigest(), 'nft input changed')
        self.a.command_once(self.r, self.c, 'nft_seal', {'document_sha256': hashlib.sha256(document).hexdigest()},
                            [NFT, '-j', '-f', str(path)], before)
        proof = self.snapshot(True)
        self.a.finished(self.r, self.c, 'nft_seal', proof)
        return proof

    def unseal(self, issue=True):
        intent = self.c['intents'].get('nft_unseal')
        if intent and intent.get('command_phase') not in ('intent_saved', 'precondition_failed'):
            proof = self.snapshot(False)
            if not intent.get('fulfilled_utc'):
                self.a.finished(self.r, self.c, 'nft_unseal', proof)
            return proof
        require(issue, 'Admission removal has not been invoked')
        proof = self.snapshot(True)
        document = canonical({'nftables': [{'delete': {'table': {
            'family': 'ip', 'handle': proof['table_handle']}}}]}) + b'\n'
        path = self.a.STAGE / 'inputs' / ('nft-' + self.c['mode'] + '-unseal.json')
        self.a.save_or_verify(path, document)
        def before():
            self.a.owner(self.m)
            self.a.preserve(self.m)
            require(self.snapshot(True) == proof, 'Owned barrier changed before deletion')
            self.a.verify_replacement(self.m, self.c)
        self.a.command_once(self.r, self.c, 'nft_unseal', {'owned_table': proof},
                            [NFT, '-j', '-f', str(path)], before)
        observed = self.snapshot(False)
        self.a.finished(self.r, self.c, 'nft_unseal', observed)
        return observed

    def quiet(self, expected, lock_check, deadline_seconds=110):
        timeout = source_timeout(expected['pid'])
        require(timeout == self.m['hermes_transport'], 'Hermes transport changed')
        # Start after all ingress connections have ended naturally. Wait beyond
        # the existing bounded headers wait; a still-streaming body owns a socket.
        needed = timeout['timeout_ms'] / 1000 + 2
        deadline = time.monotonic() + deadline_seconds
        quiet_since = None
        while True:
            self.a.owner(self.m)
            self.a.preserve(self.m)
            self.snapshot(True)
            lock_check()
            require(self.a.process(expected['pid']) == expected, 'Node changed during bootstrap drain')
            state = network_state(expected['pid'])
            current = time.monotonic()
            if state == {'usable_ingress': 0, 'node_network_work': 0}:
                quiet_since = quiet_since if quiet_since is not None else current
                if current - quiet_since >= needed:
                    return {'quiet_seconds': current - quiet_since, 'hermes': timeout,
                            'network': state, 'connections_forcibly_closed': False}
            else:
                quiet_since = None
            require(current < deadline, 'Existing connections or detached work remain; no signal')
            time.sleep(0.25)

    def zero_connections(self, expected, deadline_seconds=12):
        deadline = time.monotonic() + deadline_seconds
        while True:
            self.snapshot(True)
            require(self.a.process(expected['pid']) == expected, 'Node changed after fixed probe')
            value = network_state(expected['pid'])
            if value == {'usable_ingress': 0, 'node_network_work': 0}:
                return value
            require(time.monotonic() < deadline, 'Root probe or original transport remains open')
            time.sleep(0.25)
