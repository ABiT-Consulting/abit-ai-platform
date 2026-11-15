"""Finite release guards with temporary files and fake lifecycle/gate objects only."""
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import py_compile
import socket
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

FILE = Path(__file__).with_name('apply_core_hardening.py')
spec = importlib.util.spec_from_file_location('core_release_guard_subject', FILE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def plan():
    return {'schema_version': 1, 'state': 'FROZEN_TESTED_CORE_INCREMENT',
            'source_commit': 'a' * 40, 'package': str(m.REPO / '.git/combined-core-hardening-20261115-fixture'),
            'target_sha256': {name: hashlib.sha256(name.encode()).hexdigest() for name in m.TARGETS},
            'new_files': sorted(m.NEW_FILES), 'manifest_sha256': 'b' * 64,
            'runtime_snapshot_sha256': 'c' * 64, 'tests_sha256': 'd' * 64,
            'live_baseline_sha256': 'e' * 64, 'test_count': 172,
            'live_baseline': str(m.REPO / '.git/final-core-fixture.json'),
            'initial_gate': {'state': 'draining', 'epoch': 20, 'active_admissions': 0}}


def manifest():
    value = plan()
    parent = {'dev': 1, 'ino': 2, 'uid': 999, 'gid': 988, 'mode': 0o2770, 'xattrs': {}}
    rows = []
    for name, pin in value['target_sha256'].items():
        new = name in m.NEW_FILES
        metadata = {'uid': 999, 'gid': 988, 'mode': 0o644, 'mtime_ns': 1_700_000_000_000_000_000,
                    'xattrs': {}}
        rows.append({'relative_path': name, 'live_path': str(m.RUNTIME / name),
                     'candidate_sha256': pin, 'candidate_bytes': 20, 'replacement_required': True,
                     'new_file': new, 'original_sha256': None if new else 'f' * 64,
                     'live_metadata': None if new else metadata,
                     'new_metadata': metadata if new else None, 'parent_metadata': deepcopy(parent)})
    return {'source_commit': value['source_commit'], 'status': 'OFFLINE_VERIFIED_PENDING_ROOT_RELEASE_GATES',
            'live_runtime': str(m.RUNTIME), 'replacement_count': 11, 'new_file_count': 2, 'files': rows}


def browser():
    now = datetime(2026, 11, 15, 9, 0, tzinfo=timezone.utc)
    value = {'source': 'authenticated_browser_ui', 'url': 'https://abit.ai/api/tasks',
             'observed_at': now.isoformat(), 'actual_task_count': 0,
             'main_pid': 100, 'main_process_start_ticks': '1000', 'credential_extracted': False}
    return value, {'pid': 100, 'process_start_ticks': '1000'}, now


def units():
    return {name: {'fixed_properties_sha256': 'a' * 64, 'dropins': ['/etc/original.conf'],
                   'SendSIGKILL': 'yes', 'RefuseManualStart': 'no',
                   'KillSignal': '15', 'KillMode': 'control-group'} for name in (m.MAIN, m.QA)}


def gate_rows():
    return {'gate': [{'id': 1, 'state': 'draining', 'epoch': 20, 'command_id': 'old', 'actor': 'old'}],
            'lease': [{'id': 'historical', 'state': 'finished'}],
            'audit': [{'seq': 1, 'event_id': 'old', 'kind': 'old', 'payload': '{}'}]}


class NoNetwork(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.network = patch.object(socket.socket, 'connect', side_effect=AssertionError('NETWORK_DENIED'))
        cls.dns = patch.object(socket, 'getaddrinfo', side_effect=AssertionError('DNS_DENIED'))
        cls.network.start(); cls.dns.start()

    @classmethod
    def tearDownClass(cls):
        cls.network.stop(); cls.dns.stop()

    def denied(self, callback, code=None):
        with self.assertRaises(RuntimeError) as found:
            callback()
        if code:
            self.assertIn(code, str(found.exception))


class InputAndBrowserGuards(NoNetwork):
    def test_exact_plan_and_manifest_preserve_inputs(self):
        a, b = plan(), manifest()
        before = deepcopy((a, b))
        self.assertIs(m.validate_plan(a), a)
        self.assertIs(m.validate_manifest(b, a), b)
        self.assertEqual((a, b), before)

    def test_unfrozen_plan_or_added_target_denies(self):
        a = plan(); a['state'] = 'PENDING'
        self.denied(lambda: m.validate_plan(a))
        a = plan(); a['target_sha256']['utils/unreviewed.py'] = 'a' * 64
        self.denied(lambda: m.validate_plan(a), 'ELEVEN_TARGET')
        a = plan(); a['new_files'].append('utils/another.py')
        self.denied(lambda: m.validate_plan(a), 'ELEVEN_TARGET')

    def test_package_and_baseline_cannot_escape_private_repository_scope(self):
        for field, value in [('package', '/tmp/candidate'),
                             ('package', str(m.REPO / '.git/../candidate')),
                             ('live_baseline', '/tmp/baseline.json')]:
            a = plan(); a[field] = value
            self.denied(lambda: m.validate_plan(a))

    def test_open_uninitialised_or_active_gate_plan_denies(self):
        for change in ({'state': 'open'}, {'state': 'uninitialised'},
                       {'active_admissions': 1}, {'active_admissions': False},
                       {'epoch': True}, {'epoch': 19}):
            a = plan(); a['initial_gate'].update(change)
            self.denied(lambda: m.validate_plan(a), 'BLOCKED_GATE')

    def test_missing_duplicate_or_added_manifest_target_denies(self):
        for mode in ('remove', 'duplicate', 'add'):
            a = manifest()
            if mode == 'remove':
                a['files'].pop()
            elif mode == 'duplicate':
                a['files'][0] = deepcopy(a['files'][1])
            else:
                a['files'].append(deepcopy(a['files'][0]))
            self.denied(lambda: m.validate_manifest(a, plan()), 'RUNTIME_MANIFEST')

    def test_existing_and_absent_classifications_cannot_be_swapped(self):
        for new in (True, False):
            a = manifest(); row = next(v for v in a['files'] if v['new_file'] is new)
            row['new_file'] = not new
            self.denied(lambda: m.validate_manifest(a, plan()), 'CLASSIFICATION')

    def test_existing_bytes_and_new_absence_require_separate_proofs(self):
        a = manifest(); row = next(v for v in a['files'] if v['new_file'])
        row['original_sha256'] = 'a' * 64
        self.denied(lambda: m.validate_manifest(a, plan()), 'PROVED_ABSENCE')
        a = manifest(); row = next(v for v in a['files'] if not v['new_file'])
        row['original_sha256'] = None
        self.denied(lambda: m.validate_manifest(a, plan()), 'EXISTING_ORIGINAL')

    def test_wrong_owner_mode_parent_or_candidate_denies(self):
        for field, change in [('candidate_sha256', '0' * 64), ('live_path', '/tmp/foreign.py')]:
            a = manifest(); a['files'][0][field] = change
            self.denied(lambda: m.validate_manifest(a, plan()), 'PINNED_TARGET')
        a = manifest(); row = next(v for v in a['files'] if not v['new_file'])
        row['live_metadata']['mode'] = 0o777
        self.denied(lambda: m.validate_manifest(a, plan()), 'SOURCE_METADATA')
        a = manifest(); a['files'][0]['parent_metadata']['uid'] = 0
        self.denied(lambda: m.validate_manifest(a, plan()), 'SOURCE_PARENT')

    def test_actual_fresh_browser_zero_tasks_passes(self):
        value, original, now = browser()
        self.assertIsNone(m.validate_browser(value, original, now))

    def test_anonymous_wrong_url_and_boolean_task_count_deny(self):
        for change in ({'source': 'anonymous_health'}, {'url': 'https://abit.ai/health'},
                       {'actual_task_count': False}, {'actual_task_count': 1},
                       {'credential_extracted': True}):
            value, original, now = browser(); value.update(change)
            self.denied(lambda: m.validate_browser(value, original, now))

    def test_stale_future_or_wrong_process_browser_proof_denies(self):
        for change in ({'observed_at': '2026-11-15T08:57:59+00:00'},
                       {'observed_at': '2026-11-15T09:00:06+00:00'},
                       {'main_pid': 101}, {'main_process_start_ticks': '1001'}):
            value, original, now = browser(); value.update(change)
            self.denied(lambda: m.validate_browser(value, original, now))

    def test_missing_browser_proof_blocks_before_helpers_or_locks(self):
        args = types.SimpleNamespace(execute=True, preflight_only=False,
            controller_sha256=hashlib.sha256(FILE.read_bytes()).hexdigest(),
            main_task_receipt=None, main_task_receipt_sha256=None)
        loader = Mock(side_effect=AssertionError('MUST_NOT_LOAD'))
        with patch.object(m, 'PLAN_SHA', 'a' * 64), patch.object(m.os, 'geteuid', return_value=0), \
             patch.object(m, 'module', loader):
            self.denied(lambda: m.main(args), 'AUTHENTICATED_BROWSER')
        loader.assert_not_called()

    def test_unfrozen_controller_blocks_all_helpers(self):
        args = types.SimpleNamespace(execute=True, preflight_only=False,
            controller_sha256=hashlib.sha256(FILE.read_bytes()).hexdigest())
        loader = Mock(side_effect=AssertionError('MUST_NOT_LOAD'))
        with patch.object(m, 'PLAN_SHA', 'UNFROZEN'), patch.object(m.os, 'geteuid', return_value=0), \
             patch.object(m, 'module', loader):
            self.denied(lambda: m.main(args), 'NOT_FROZEN')
        loader.assert_not_called()

    def test_symlink_hardlink_and_changed_hash_inputs_deny(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / 'source'; p.write_bytes(b'original')
            link = Path(temp) / 'link'; link.symlink_to(p)
            self.denied(lambda: m.read(link), 'CANONICAL')
            self.denied(lambda: m.read(p, '0' * 64), 'PINNED')
            os.link(p, Path(temp) / 'hard')
            self.denied(lambda: m.read(p), 'SINGLE_LINK')


class NewLeafAndBytecodeGuards(NoNetwork):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'utils').mkdir()
        (self.root / 'archive').mkdir(mode=0o700)
        scope = patch.object(m, 'RUNTIME', self.root); scope.start(); self.addCleanup(scope.stop)
        self.target = self.root / 'utils/agentic_delivery_control.py'
        self.parent = m.parent_proof(self.target.parent)
        self.raw = b'VALUE = "new"\n'
        self.metadata = {'uid': os.getuid(), 'gid': os.getgid(), 'mode': 0o644,
                         'mtime_ns': 1_700_000_000_000_000_000, 'xattrs': {}}

    def create(self):
        m.atomic_new_leaf(self.target, self.raw, self.parent, self.metadata, m.sha(self.raw))

    def test_existing_install_preserves_deliberately_old_timestamp_before_readback(self):
        target = self.root / 'utils/agentic_executor.py'
        target.write_bytes(b'VALUE = "old"\\n')
        target.chmod(0o640)
        old_ns = 1_400_000_000_123_456_789
        os.utime(target, ns=(old_ns, old_ns))
        try:
            os.setxattr(target, 'user.existing_fixture', b'keep')
        except OSError:
            pass
        before = m.file_proof(target)
        m.atomic_existing_leaf(target, self.raw, self.parent, before, m.sha(self.raw))
        self.assertEqual(m.file_proof(target),
                         {**before, 'sha256': m.sha(self.raw), 'size': len(self.raw)})
        self.assertEqual(target.stat().st_mtime_ns, old_ns)
        self.assertEqual(list(target.parent.glob('.core-source-*')), [])

    def test_existing_install_rejects_source_drift_or_changed_parent_without_replacement(self):
        target = self.root / 'utils/agentic_executor.py'
        target.write_bytes(b'original')
        before = m.file_proof(target)
        target.write_bytes(b'concurrent original')
        self.denied(lambda: m.atomic_existing_leaf(target, self.raw, self.parent, before, m.sha(self.raw)),
                    'CAS_CHANGED')
        self.assertEqual(target.read_bytes(), b'concurrent original')
        before = m.file_proof(target)
        changed_parent = {**self.parent, 'ino': self.parent['ino'] + 1}
        self.denied(lambda: m.atomic_existing_leaf(target, self.raw, changed_parent, before, m.sha(self.raw)),
                    'CAS_CHANGED')

    def test_both_exact_new_helpers_create_without_replacing_any_file(self):
        for name in m.NEW_FILES:
            self.target = self.root / name
            self.create()
            self.assertEqual(self.target.read_bytes(), self.raw)
            self.assertEqual(m.file_proof(self.target),
                             {**self.metadata, 'sha256': m.sha(self.raw), 'size': len(self.raw)})

    def test_existing_file_symlink_and_foreign_new_path_deny(self):
        self.target.write_bytes(b'foreign')
        self.denied(self.create, 'ABSENCE')
        self.assertEqual(self.target.read_bytes(), b'foreign')
        self.target.unlink(); other = self.root / 'outside'; other.write_bytes(b'preserve')
        self.target.symlink_to(other)
        self.denied(self.create, 'PARENT')
        self.assertEqual(other.read_bytes(), b'preserve')
        self.target = self.root / 'utils/foreign.py'
        self.denied(self.create, 'EXACT_NEW_HELPER')

    def test_changed_parent_identity_or_candidate_hash_denies(self):
        self.parent['ino'] += 1
        self.denied(self.create, 'PARENT')
        self.parent = m.parent_proof(self.target.parent)
        self.denied(lambda: m.atomic_new_leaf(self.target, self.raw, self.parent,
                                              self.metadata, '0' * 64), 'HASH')
        self.assertFalse(self.target.exists())

    def test_arriving_file_wins_and_is_never_overwritten(self):
        original_link = os.link
        def race(source, target, **kwargs):
            Path(target).write_bytes(b'concurrent original')
            return original_link(source, target, **kwargs)
        with patch.object(m.os, 'link', side_effect=race), self.assertRaises(FileExistsError):
            self.create()
        self.assertEqual(self.target.read_bytes(), b'concurrent original')
        self.assertEqual(list(self.target.parent.glob('.core-source-*')), [])

    def test_failed_temporary_metadata_write_cleans_only_own_temp(self):
        with patch.object(m.os, 'fchmod', side_effect=OSError('fixture failure')), self.assertRaises(OSError):
            self.create()
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.target.parent.glob('.core-source-*')), [])

    def test_exact_created_helper_rollback_preserves_other_files(self):
        self.create(); proof = m.file_proof(self.target)
        unrelated = self.target.parent / 'other.py'; unrelated.write_bytes(b'keep')
        m.remove_new_leaf_for_reviewed_rollback(self.target, proof, self.parent)
        self.assertFalse(self.target.exists())
        self.assertEqual(unrelated.read_bytes(), b'keep')

    def test_changed_new_helper_or_foreign_rollback_cannot_unlink(self):
        self.create(); proof = m.file_proof(self.target)
        self.target.write_bytes(b'changed')
        self.denied(lambda: m.remove_new_leaf_for_reviewed_rollback(self.target, proof, self.parent),
                    'ROLLBACK_NEW_LEAF')
        self.assertEqual(self.target.read_bytes(), b'changed')
        other = self.target.parent / 'other.py'; other.write_bytes(b'keep')
        self.denied(lambda: m.remove_new_leaf_for_reviewed_rollback(other, m.file_proof(other), self.parent),
                    'EXACT_NEW_HELPER_ROLLBACK')
        self.assertTrue(other.exists())

    def test_matching_timestamp_and_size_really_load_stale_cache_until_quarantined(self):
        self.target.write_bytes(b'VALUE = "old"\n')
        os.utime(self.target, ns=(self.metadata['mtime_ns'], self.metadata['mtime_ns']))
        py_compile.compile(str(self.target), doraise=True,
                           invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
        self.target.write_bytes(self.raw)
        os.utime(self.target, ns=(self.metadata['mtime_ns'], self.metadata['mtime_ns']))
        def load_value():
            spec = importlib.util.spec_from_file_location('controlled_bytecode_fixture', self.target)
            module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
            return module.VALUE
        self.assertEqual(load_value(), 'old')
        records = []
        rows = m.quarantine_bytecode([self.target], self.root / 'saved-cache',
                                     lambda n, v: records.append((n, v)))
        self.assertEqual(len(rows), 1)
        self.assertTrue(Path(rows[0]['archive_path']).exists())
        self.assertEqual(m.selected_caches([self.target]), [])
        self.assertEqual(load_value(), 'new')
        self.assertEqual(len(records), 1)

    def test_bytecode_quarantine_leaves_other_module_cache_untouched(self):
        self.create()
        other = self.target.parent / 'other.py'; other.write_bytes(b'VALUE=1\n')
        for p in (self.target, other):
            py_compile.compile(str(p), doraise=True)
        other_caches = m.selected_caches([other])
        before = [m.file_proof(p) for p in other_caches]
        m.quarantine_bytecode([self.target], self.root / 'cache-backup', lambda *args: None)
        self.assertEqual([m.file_proof(p) for p in other_caches], before)

    def test_orphan_new_helper_cache_is_also_quarantined(self):
        cache = self.target.parent / '__pycache__'; cache.mkdir()
        p = cache / 'agentic_delivery_control.cpython-312.pyc'
        p.write_bytes(b'old orphan cache')
        self.assertFalse(self.target.exists())
        rows = m.quarantine_bytecode([self.target], self.root / 'orphan-backup', lambda *args: None)
        self.assertEqual(len(rows), 1)
        self.assertFalse(p.exists())

    def test_bytecode_symlink_or_hardlink_denies_without_removal(self):
        cache = self.target.parent / '__pycache__'; cache.mkdir()
        foreign = self.root / 'foreign'; foreign.write_bytes(b'preserve')
        p = cache / 'agentic_delivery_control.cpython-312.pyc'; p.symlink_to(foreign)
        self.denied(lambda: m.selected_caches([self.target]), 'BYTECODE_SCOPE')
        p.unlink(); os.link(foreign, p)
        self.denied(lambda: m.selected_caches([self.target]), 'BYTECODE_SCOPE')
        self.assertEqual(foreign.read_bytes(), b'preserve')

    def test_bytecode_parent_symlink_denies(self):
        other = self.root / 'foreign-directory'; other.mkdir()
        (self.target.parent / '__pycache__').symlink_to(other)
        self.denied(lambda: m.selected_caches([self.target]), 'BYTECODE_PARENT')


class LifecycleAndGateGuards(NoNetwork):
    def test_original_configuration_and_guarded_policy_are_separate_exact_states(self):
        original = units()
        with patch.object(m, 'static_unit_state', side_effect=lambda u: deepcopy(original[u])):
            m.check_units(original)
            self.denied(lambda: m.check_units(original, guards=True), 'GRACEFUL_POLICY')
        guarded = deepcopy(original)
        for unit, row in guarded.items():
            row['dropins'].append(str(m.NOKILL[unit])); row['SendSIGKILL'] = 'no'
        with patch.object(m, 'static_unit_state', side_effect=lambda u: deepcopy(guarded[u])):
            m.check_units(original, guards=True)
            self.denied(lambda: m.check_units(original), 'GRACEFUL_POLICY')

    def test_changed_security_property_missing_guard_or_effective_fence_denies(self):
        original = units(); guarded = deepcopy(original)
        for unit, row in guarded.items():
            row['dropins'].append(str(m.NOKILL[unit])); row['SendSIGKILL'] = 'no'
        guarded[m.QA]['dropins'].append(str(m.QA_FENCE))
        with patch.object(m, 'static_unit_state', side_effect=lambda u: deepcopy(guarded[u])):
            self.denied(lambda: m.check_units(original, guards=True, qa_fence=True), 'GRACEFUL_POLICY')
        guarded[m.QA]['RefuseManualStart'] = 'yes'
        with patch.object(m, 'static_unit_state', side_effect=lambda u: deepcopy(guarded[u])):
            m.check_units(original, guards=True, qa_fence=True)
        guarded[m.MAIN]['fixed_properties_sha256'] = 'b' * 64
        with patch.object(m, 'static_unit_state', side_effect=lambda u: deepcopy(guarded[u])):
            self.denied(lambda: m.check_units(original, guards=True, qa_fence=True))

    def test_one_service_call_follows_durable_intent_even_with_unknown_ack(self):
        events = []
        def record(name, value): events.append(('record', name))
        def attempt(): events.append(('service', 1)); return {'acknowledgement': 'unknown'}
        result = m.once_service(record, 'main-stop', 'stop', m.MAIN, attempt,
                                on_attempt=lambda: events.append(('count', 1)))
        self.assertEqual(result['acknowledgement'], 'unknown')
        self.assertEqual(events, [('record', 'main-stop-intent.json'), ('count', 1),
                                  ('service', 1), ('record', 'main-stop-ack.json')])

    def test_intent_failure_sends_no_service_action_and_no_count(self):
        sent = []; counted = []
        def record(*args): raise OSError('intent failure')
        with self.assertRaises(OSError):
            m.once_service(record, 'qa-stop', 'stop', m.QA, lambda: sent.append(1),
                           on_attempt=lambda: counted.append(1))
        self.assertEqual(sent, []); self.assertEqual(counted, [])

    def test_duplicate_intent_blocks_second_service_call(self):
        names = set(); sent = []
        def record(name, value):
            if name in names: raise FileExistsError(name)
            names.add(name)
        m.once_service(record, 'main-start', 'start', m.MAIN, lambda: sent.append(1))
        with self.assertRaises(FileExistsError):
            m.once_service(record, 'main-start', 'start', m.MAIN, lambda: sent.append(1))
        self.assertEqual(sent, [1])

    def test_service_scope_cannot_expand_to_relay_or_force_action(self):
        self.denied(lambda: m.once_service(lambda *a: None, 'x', 'kill', m.MAIN, lambda: None))
        self.denied(lambda: m.once_service(lambda *a: None, 'x', 'stop', 'relay.service', lambda: None))

    def test_new_process_requires_same_executable_arguments_identity_and_later_birth(self):
        old = {'pid': 10, 'start_ticks': '100', 'uid': 999, 'gid': 988, 'parent': 1,
               'argv_sha256': 'a' * 64, 'exe': '/python', 'cgroup': ['service']}
        new = {**old, 'pid': 11, 'start_ticks': '101'}
        self.assertEqual(m.replacement_process(old, new), new)
        for change in ({'pid': 10}, {'start_ticks': '99'}, {'uid': 0},
                       {'argv_sha256': 'b' * 64}, {'exe': '/foreign'}, {'cgroup': ['other']}):
            self.denied(lambda: m.replacement_process(old, {**new, **change}))

    def test_every_nested_gate_load_reuses_verified_module_without_sourcefileloader(self):
        original = Mock(side_effect=AssertionError('Unchecked disk loader must never run'))
        hold = types.SimpleNamespace(load_gate=original)
        pinned = types.SimpleNamespace(status=lambda path: {'state': 'sealed'})
        with patch.object(m, 'load_gate_pinned', return_value=pinned) as loader:
            self.assertIs(m.bind_gate_loader(hold), pinned)
            self.assertIs(hold.load_gate(), pinned)
            self.assertIs(hold.load_gate(), pinned)
            self.assertEqual(hold.load_gate().status('fixture'), {'state': 'sealed'})
        loader.assert_called_once_with(hold)
        original.assert_not_called()

    def test_only_one_exact_seal_audit_and_gate_transition_pass(self):
        before = gate_rows(); after = deepcopy(before)
        after['gate'][0].update(state='sealed', epoch=21, command_id='core-fixture-seal',
                               actor='reviewed-release-operator')
        after['audit'].append({'seq': 2, 'event_id': 'core-fixture-seal', 'kind': 'operator_control',
            'payload': json.dumps({'action': 'seal', 'expected_epoch': 20,
                                   'actor': 'reviewed-release-operator'})})
        m.validate_gate_history(before, after, 'core-fixture-seal', 'reviewed-release-operator', 20)
        for group in ('gate', 'lease', 'audit'):
            changed = deepcopy(after); changed[group][0]['foreign'] = True
            self.denied(lambda: m.validate_gate_history(before, changed, 'core-fixture-seal',
                                                        'reviewed-release-operator', 20))
        after['audit'].append({'seq': 3, 'event_id': 'foreign', 'kind': 'foreign', 'payload': '{}'})
        self.denied(lambda: m.validate_gate_history(before, after, 'core-fixture-seal',
                                                    'reviewed-release-operator', 20))

    def fake_seal(self, apply=True, lost_ack=False):
        gate_file = patch.object(m, 'gate_file_check', return_value=None)
        gate_file.start(); self.addCleanup(gate_file.stop)
        loader = patch.object(m, 'load_gate_pinned', side_effect=lambda hold: hold.load_gate())
        loader.start(); self.addCleanup(loader.stop)
        fixture = types.SimpleNamespace(rows=gate_rows(), calls=0)
        class Gate:
            def path(self, root): return 'fixture'
            def status(self, path):
                row = fixture.rows['gate'][0]
                return {'state': row['state'], 'epoch': row['epoch'], 'active_admissions': 0}
            def control(self, action, **kwargs):
                fixture.calls += 1
                if apply:
                    fixture.rows['gate'][0].update(state='sealed', epoch=21,
                        command_id=kwargs['command_id'], actor=kwargs['actor'])
                    fixture.rows['audit'].append({'seq': 2, 'event_id': kwargs['command_id'],
                        'kind': 'operator_control', 'payload': json.dumps({
                            'action': 'seal', 'expected_epoch': kwargs['expected_epoch'],
                            'actor': kwargs['actor']})})
                if lost_ack: raise OSError('lost acknowledgement')
        gate = Gate()
        hold = types.SimpleNamespace(as_service=nullcontext, load_gate=lambda: gate)
        relay = types.SimpleNamespace(gate_history=lambda _: deepcopy(fixture.rows))
        return fixture, hold, relay

    def test_lost_seal_ack_reconciles_original_by_readback_without_retry(self):
        fixture, hold, relay = self.fake_seal(lost_ack=True)
        events = []
        result, count = m.seal_once(hold, relay, plan()['initial_gate'], 'core-fixture',
                                   lambda n, v: events.append(n))
        self.assertEqual(result, {'state': 'sealed', 'epoch': 21, 'active_admissions': 0})
        self.assertEqual((fixture.calls, count), (1, 1))
        self.assertEqual(events, ['seal-intent.json', 'seal-call-ack.json', 'seal-readback.json'])

    def test_unknown_unapplied_seal_holds_without_second_control(self):
        fixture, hold, relay = self.fake_seal(apply=False, lost_ack=True)
        self.denied(lambda: m.seal_once(hold, relay, plan()['initial_gate'], 'core-fixture',
                                       lambda *a: None), 'NO_RETRY')
        self.assertEqual(fixture.calls, 1)

    def test_already_exact_sealed_gate_sends_no_control(self):
        fixture, hold, relay = self.fake_seal()
        fixture.rows['gate'][0].update(state='sealed', epoch=21)
        result, count = m.seal_once(hold, relay,
            {'state': 'sealed', 'epoch': 21, 'active_admissions': 0}, 'core-fixture', lambda *a: None)
        self.assertEqual((fixture.calls, count), (0, 0))

    def test_missing_gate_file_never_loads_initializer_or_creates_a_database(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(m, 'DATA', Path(temporary)):
            loader = Mock(side_effect=AssertionError('MUST_NOT_LOAD'))
            with patch.object(m, 'load_gate_pinned', loader), self.assertRaises(FileNotFoundError):
                m.current_gate(types.SimpleNamespace(as_service=nullcontext))
            loader.assert_not_called()
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_seal_intent_failure_sends_nothing(self):
        fixture, hold, relay = self.fake_seal()
        def failed(*args): raise OSError('intent failed')
        with self.assertRaises(OSError):
            m.seal_once(hold, relay, plan()['initial_gate'], 'core-fixture', failed)
        self.assertEqual(fixture.calls, 0)



class CohortAndEnvironmentGuards(NoNetwork):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = self.root / 'runtime'
        (self.runtime / 'utils').mkdir(parents=True); (self.runtime / 'routers').mkdir()
        (self.root / 'current/runtime').mkdir(parents=True)
        (self.root / 'current/runtime/open_webui').symlink_to(self.runtime, target_is_directory=True)
        for name, value in [('RUNTIME', self.runtime), ('ROOT', self.root)]:
            item = patch.object(m, name, value); item.start(); self.addCleanup(item.stop)
        names = sorted(m.TARGETS - m.NEW_FILES) + ['utils/fixture_' + str(i) + '.py' for i in range(138)]
        for name in names:
            p = self.runtime / name; p.write_bytes(b'VALUE = 1\n')
        self.baseline = {'core_files': {str(self.runtime / name): m.file_proof(self.runtime / name) for name in names}}
        rows = []
        for name in sorted(m.TARGETS):
            new = name in m.NEW_FILES
            p = self.runtime / name
            metadata = ({'uid': os.getuid(), 'gid': os.getgid(), 'mode': 0o644,
                         'mtime_ns': 1_700_000_000_000_000_000, 'xattrs': {}}
                        if new else {k: v for k, v in m.file_proof(p).items() if k not in {'sha256', 'size'}})
            rows.append({'relative_path': name, 'new_file': new,
                         'new_metadata': metadata if new else None,
                         'live_metadata': None if new else metadata,
                         'candidate_sha256': m.sha(b'VALUE = 2\n'), 'candidate_bytes': 10,
                         'parent_metadata': m.parent_proof(p.parent)})
        self.manifest = {'files': rows}

    def test_complete_original147_cohort_passes(self):
        self.assertTrue(m.source_cohort(self.baseline, self.manifest))

    def test_added_removed_or_modified_dependency_denies(self):
        p = self.runtime / 'utils/unreviewed.py'; p.write_bytes(b'new')
        self.denied(lambda: m.source_cohort(self.baseline, self.manifest), 'INVENTORY')
        p.unlink()
        p = self.runtime / 'utils/fixture_0.py'; p.write_bytes(b'changed')
        self.denied(lambda: m.source_cohort(self.baseline, self.manifest), 'BYTES')
        p.unlink()
        self.denied(lambda: m.source_cohort(self.baseline, self.manifest), 'INVENTORY')

    def test_complete_after149_cohort_requires_both_new_helpers_and_exact_metadata(self):
        for item in self.manifest['files']:
            p = self.runtime / item['relative_path']
            metadata = item['new_metadata'] if item['new_file'] else item['live_metadata']
            p.write_bytes(b'VALUE = 2\n')
            os.chmod(p, metadata['mode'])
            os.utime(p, ns=(metadata['mtime_ns'], metadata['mtime_ns']))
        self.assertTrue(m.source_cohort(self.baseline, self.manifest, installed=True))
        p = self.runtime / next(iter(m.NEW_FILES)); p.chmod(0o600)
        self.denied(lambda: m.source_cohort(self.baseline, self.manifest, installed=True), 'METADATA')
        p.unlink()
        self.denied(lambda: m.source_cohort(self.baseline, self.manifest, installed=True), 'INVENTORY')

    def test_parent_mode_or_acl_identity_change_denies_even_with_identical_source(self):
        parent = self.runtime / 'utils'; old = parent.stat().st_mode & 0o7777
        parent.chmod(0o700 if old != 0o700 else 0o750)
        self.denied(lambda: m.source_cohort(self.baseline, self.manifest), 'PARENT')

    def test_process_environment_excludes_only_documented_generation_values(self):
        first = b'PATH=/controlled\0INVOCATION_ID=old\0JOURNAL_STREAM=old\0SYSTEMD_EXEC_PID=100\0'
        second = b'PATH=/controlled\0INVOCATION_ID=new\0JOURNAL_STREAM=new\0SYSTEMD_EXEC_PID=101\0'
        with patch.object(Path, 'read_bytes', return_value=first):
            old = m.environment_proof(100)
        with patch.object(Path, 'read_bytes', return_value=second):
            new = m.environment_proof(101)
        self.assertEqual(old, new)
        with patch.object(Path, 'read_bytes', return_value=second.replace(b'/controlled', b'/changed')):
            self.assertNotEqual(m.environment_proof(101), old)

    def test_nondefault_bytecode_prefix_or_duplicate_environment_key_denies(self):
        for raw in (b'PYTHONPYCACHEPREFIX=/unreviewed\0',
                    b'PATH=a\0PATH=b\0', b'bad_environment_entry\0'):
            with patch.object(Path, 'read_bytes', return_value=raw):
                self.denied(lambda: m.environment_proof(100))

    def test_all_preserved_processes_and_completed_relay_are_required(self):
        processes = {name: {'pid': index + 20, 'start_ticks': '100'}
                     for index, name in enumerate(sorted(m.PROCESS_NAMES))}
        processes['node'] = {'pid': 1273362, 'start_ticks': '68729026'}
        value = {'processes': processes, 'detector_before':
                 'ddf85a4edd9fc3e71ff41c28c76f670906cb4a7bf11c1185dfd0b9de07e51f53',
                 'core_files': {str(i): {} for i in range(147)},
                 'environments': {m.MAIN: {}, m.QA: {}}, 'static_units': {m.MAIN: {}, m.QA: {}}}
        self.assertIs(m.validate_live_baseline(value), value)
        other = deepcopy(value); other['processes'].pop('mcp_auth')
        self.denied(lambda: m.validate_live_baseline(other), 'PRESERVED_PROCESS')
        other = deepcopy(value); other['processes']['node']['pid'] = 3593239
        self.denied(lambda: m.validate_live_baseline(other), 'RELAY_DEPLOYMENT')
        other = deepcopy(value); other['environments'].pop(m.QA)
        self.denied(lambda: m.validate_live_baseline(other), 'ENVIRONMENT')


if __name__ == '__main__':
    unittest.main(verbosity=2)
