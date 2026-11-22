"""Fixed relay bootstrap proof and actual shared-lock lifetime; no import actions."""
import datetime
import hashlib
import http.client
import json
import os
from pathlib import Path
import subprocess
import time

NODE = '/usr/local/bin/node'
LOCK_SOURCE = Path('/srv/abit-dev-linux/relay-runtime/service/shared_chatgpt_browser_lock.cjs')
LOCK_FILE = Path('/srv/abit-dev-linux/relay-runtime/state/browser.lock')
CATALOG = Path('/srv/abit-dev-linux/relay-runtime/state/aibit-relay-projects.json')
LOCK_SOURCE_SHA = 'a3625394a8cfb684d742434f17feb0af66292279801e7012eb2ddb7419ba8081'


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def valid_cache_expiry(data, started, ended):
    value = json.loads(data)
    require(type(value) is dict and set(value) == {'updated_at', 'projects'}
            and value['projects'] == [], 'Only the rebuildable catalog expiry is authorized')
    stamp = datetime.datetime.fromisoformat(value['updated_at'].replace('Z', '+00:00')).timestamp()
    require(started - 1 <= stamp <= ended + 1, 'Catalog write is outside this bounded probe')
    return value


class RuntimeGuard:
    def __init__(self, api, manifest, record, cycle, bootstrap):
        self.a, self.m, self.r, self.c, self.b = api, manifest, record, cycle, bootstrap
        self.label = cycle.get('keeper_cycle', cycle['mode'])
        self.prefix = api.STAGE / ('lock-' + self.label)
        self.child = None

    def keeper_path(self, suffix):
        return self.prefix.with_name(self.prefix.name + '-' + suffix + '.json')

    def keeper_argv(self):
        return [NODE, str(self.a.STAGE / 'browser_lock_keeper.cjs'),
                self.a.MANIFEST_SHA, self.label]

    def keeper_check(self):
        self.a.require(self.a.filehash(LOCK_SOURCE) == LOCK_SOURCE_SHA, 'Shared lock module changed')
        held_path = self.keeper_path('held')
        self.a.protected(held_path)
        encoded = self.a.raw(held_path)
        held = json.loads(encoded)
        require(held['manifest_sha256'] == self.a.MANIFEST_SHA and held['cycle'] == self.label,
                'Shared lock receipt belongs to another operator')
        expected_cmd = hashlib.sha256(b'\0'.join(
            value.encode() for value in self.keeper_argv()) + b'\0').hexdigest()
        require(held['keeper']['uid'] == 0 and held['keeper']['cmdline_sha256'] == expected_cmd
                and self.a.process(held['keeper']['pid']) == held['keeper'], 'Shared lock keeper changed')
        proof = self.a.file_proof(LOCK_FILE)
        require(proof['sha256'] == held['lock_sha256']
                and proof['device'] == held['lock_device'] and proof['inode'] == held['lock_inode'],
                'Actual shared browser lock is no longer held')
        return {'held_sha256': hashlib.sha256(encoded).hexdigest(), 'keeper': held['keeper'],
                'lock_sha256': held['lock_sha256']}

    def acquire(self, issue=True):
        if self.c.get('keeper_cycle'):
            return self.keeper_check()
        intent = self.c['intents'].get('browser_lock')
        if not intent or intent.get('command_phase') in ('intent_saved', 'precondition_failed'):
            require(issue, 'Shared lock has no observed acquisition')
            source = self.a.PACKAGE / 'browser_lock_keeper.cjs'
            source_bytes = self.a.raw(source)
            require(hashlib.sha256(source_bytes).hexdigest() == self.m['operator_source']['browser_lock_keeper.cjs'],
                    'Reviewed lock keeper changed')
            protected_source = self.a.STAGE / 'browser_lock_keeper.cjs'
            self.a.save_or_verify(protected_source, source_bytes)
            self.a.protected(protected_source)
            input_value = {'manifest_sha256': self.a.MANIFEST_SHA, 'cycle': self.label,
                           'original_node': self.c['node_before']}
            self.a.save_or_verify(self.keeper_path('input'), self.a.canonical(input_value) + b'\n')
            intent, fresh = self.a.begin_intent(self.r, self.c, 'browser_lock',
                                               {'command_phase': 'intent_saved'})
            if fresh or intent.get('command_phase') in ('intent_saved', 'precondition_failed'):
                try:
                    self.a.owner(self.m)
                    self.a.preserve(self.m)
                    self.b.snapshot(True)
                    require(self.a.process(self.c['node_before']['pid']) == self.c['node_before'],
                            'Original Node changed before shared lock')
                except BaseException:
                    intent['command_phase'] = 'precondition_failed'
                    self.a.update(self.r)
                    raise
                intent['command_phase'] = 'invocation_may_have_started'
                self.a.update(self.r)
                # Fixed root script and clean environment, no credentials or caller code.
                log = self.a.STAGE / ('lock-' + self.label + '.log')
                fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                try:
                    self.child = subprocess.Popen(self.keeper_argv(), stdin=subprocess.DEVNULL,
                        stdout=fd, stderr=fd, start_new_session=True, close_fds=True,
                        env={'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8',
                             'ABIT_SHARED_CHATGPT_LOCK_FILE': str(LOCK_FILE)},
                        cwd=self.a.STAGE)
                    intent['spawn_pid'] = self.child.pid
                    self.a.update(self.r)
                finally:
                    os.close(fd)
        deadline = time.monotonic() + 35
        while True:
            self.a.owner(self.m)
            self.b.snapshot(True)
            if self.keeper_path('held').exists():
                proof = self.keeper_check()
                if not intent.get('fulfilled_utc'):
                    self.a.finished(self.r, self.c, 'browser_lock', proof)
                return proof
            if self.child is not None and self.child.poll() is not None:
                raise RuntimeError('Shared lock acquisition ended; never spawn a duplicate unknown keeper')
            require(time.monotonic() < deadline, 'Shared lock acquisition unresolved; reconcile original')
            time.sleep(0.1)

    def release(self, issue=True):
        path = self.keeper_path('release')
        if not path.exists():
            require(issue, 'Shared lock release has not been requested')
            proof = self.keeper_check()
            self.a.verify_replacement(self.m, self.c)
            no_transition = bool(self.c.get('no_node_transition'))
            request = {'manifest_sha256': self.a.MANIFEST_SHA, 'cycle': self.label,
                       'held_sha256': proof['held_sha256'],
                       'reason': 'rollback_without_signal' if no_transition else 'original_ceased',
                       'original_node_unchanged': no_transition}
            self.a.savejson(path, request)
        self.a.protected(path)
        request = json.loads(self.a.raw(path))
        require(request['manifest_sha256'] == self.a.MANIFEST_SHA and request['cycle'] == self.label
                and request['held_sha256'] == self.a.filehash(self.keeper_path('held')),
                'Shared lock release binding changed')
        deadline = time.monotonic() + 5
        while True:
            released = self.keeper_path('released')
            if released.exists():
                self.a.protected(released)
                value = json.loads(self.a.raw(released))
                require(value['manifest_sha256'] == self.a.MANIFEST_SHA and value['cycle'] == self.label
                        and value['held_sha256'] == request['held_sha256'], 'Shared lock release proof differs')
                if self.child is not None:
                    self.child.wait(timeout=2)
                return {'released_sha256': self.a.filehash(released), 'owner_only_unlock': True}
            require(time.monotonic() < deadline, 'Original lock release unresolved; do not remove it manually')
            time.sleep(0.1)

    def cache_check(self):
        expected = self.r.get('catalog_cache_after', self.m['catalog_cache_before'])
        actual = self.a.file_proof(CATALOG) if CATALOG.exists() else None
        require(actual == expected, 'Catalog changed outside the bounded expiry probe')
        return actual

    def prepare_cache(self):
        original = self.m['catalog_cache_before']
        if original is None:
            self.a.save_or_verify(self.a.STAGE / 'catalog-before.absent.json',
                                  self.a.canonical({'catalog_originally_absent': True}) + b'\n')
        else:
            path = self.a.STAGE / 'catalog-before.json'
            if not path.exists():
                require(self.a.file_proof(CATALOG) == original, 'Original catalog backup unavailable')
                self.a.save(path, self.a.raw(CATALOG))
            self.a.protected(path)
            require(self.a.filehash(path) == original['sha256'], 'Protected catalog backup differs')
        return self.cache_check()

    def readiness(self, expected):
        self.keeper_check()
        self.b.snapshot(True)
        self.a.private_preservation(self.m)
        self.a.browser_snapshot(self.m)
        before = self.prepare_cache()
        require(self.b.zero_connections(expected) == {'usable_ingress': 0, 'node_network_work': 0},
                'A usable original connection remains')
        require(self.m['allow_rebuildable_catalog_expiry'] is True, 'Catalog expiry must be explicitly bound')
        started = time.time()
        connection = http.client.HTTPConnection('127.0.0.1', 8810, timeout=30)
        try:
            connection.request('GET', '/health', headers={'Connection': 'close'})
            response = connection.getresponse()
            raw = response.read(4097)
            require(response.status == 200 and len(raw) <= 4096, 'Bounded root readiness probe unavailable')
            value = json.loads(raw)
        finally:
            connection.close()
        ended = time.time()
        after = self.a.file_proof(CATALOG) if CATALOG.exists() else None
        expiry = before != after
        if expiry:
            require(after is not None, 'Catalog expiry must not remove its cache')
            valid_cache_expiry(self.a.raw(CATALOG), started, ended)
            self.r['catalog_cache_after'] = after
            self.a.update(self.r)
        self.cache_check()
        self.a.private_preservation(self.m)
        self.a.browser_snapshot(self.m)
        self.keeper_check()
        self.b.snapshot(True)
        require(self.a.process(expected['pid']) == expected, 'Node changed during readiness probe')
        fields = {key: value.get(key) for key in ('ready', 'authenticated', 'target_ready',
                                                'active_relay_turns', 'browser_busy')}
        require(all(fields[key] is True for key in ('ready', 'authenticated', 'target_ready'))
                and fields['active_relay_turns'] == 0 and fields['browser_busy'] is False,
                'Original provider work or readiness remains unresolved')
        result = {'manifest_sha256': self.a.MANIFEST_SHA, 'node': expected,
                  'observed_utc': self.a.now(), 'observed_at': ended, 'fixed_path': '/health',
                  'root_only_probe': True, 'connection_closed': True, 'health': fields,
                  'catalog_expired': expiry, 'catalog_before': before, 'catalog_after': after,
                  'authoritative_state_unchanged': True, 'original_targets_unchanged': True,
                  'no_submission': True, 'universally_read_only': False}
        history = self.r.setdefault('readiness_observations', [])
        require(len(history) < 8, 'Bounded observation history exhausted')
        history.append(result)
        self.a.update(self.r)
        self.b.zero_connections(expected)
        return result

    def before_signal(self, expected, proof):
        self.b.snapshot(True)
        self.keeper_check()
        self.cache_check()
        self.a.private_preservation(self.m)
        self.a.browser_snapshot(self.m)
        require(proof['manifest_sha256'] == self.a.MANIFEST_SHA and proof['node'] == expected
                and 0 <= time.time() - proof['observed_at'] <= 60,
                'The sealed idle proof expired')
        require(self.b.zero_connections(expected, deadline_seconds=1) ==
                {'usable_ingress': 0, 'node_network_work': 0}, 'Work resumed before the owned signal')
