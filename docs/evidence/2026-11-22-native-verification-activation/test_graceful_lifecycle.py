"""Finite isolated lifecycle checks; no systemd, sudo, network or production DB."""
import ast
from contextlib import ExitStack
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SOURCE = ROOT / 'deployment/linux/native-verification-activation-20261122/release_core.py'
spec = importlib.util.spec_from_file_location('reviewed_graceful_candidate', SOURCE)
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)

OLD = {'pid': 101, 'start_ticks': 100, 'uid': 999, 'cmdline_sha256': 'c' * 64}
NEW = {'pid': 202, 'start_ticks': 10001, 'uid': 999, 'cmdline_sha256': 'c' * 64}
OPEN = {'state': 'open', 'epoch': 88, 'command_id': 'prior', 'actor': 'prior-operator'}

def cycle(mode='apply'):
    return {'mode': mode, 'previous_identity': dict(OLD), 'gate_before': dict(OPEN), 'intents': {}}

def receipt(c):
    return {'state': 'prepared', 'cycles': [c], 'manifest_sha256': 'f' * 64}

def close_intent():
    return {'action': 'close', 'expected_epoch': 88, 'command_id': h.COMMAND_PREFIX + ':apply:core-close',
            'command_phase': 'invocation_may_have_started', 'requested_tick': 9000}

def closed(i=None):
    i = i or close_intent()
    return {'state': 'draining', 'epoch': i['expected_epoch'] + 1,
            'command_id': i['command_id'], 'actor': h.ACTOR}

def model():
    return {'core_before': dict(OLD), 'app_version': '0.11.4', 'baseline': {},
            'changes': {}, 'preserved_processes': {},
            'graceful': {'maintenance_module_sha256': 'd' * 64,
                         'unit_effective': dict(h.ORIGINAL_EFFECTIVE),
                         'historical': {'relay': {}, 'dot': {}}}}

def active(identity=OLD, temporary=True):
    return dict({'MainPID': str(identity['pid']), 'ActiveState': 'active', 'SubState': 'running',
                 'User': 'mcpdev', 'FragmentPath': h.UNIT_FILES[0], 'ControlGroup': h.CGROUP,
                 'DropInPaths': ' '.join(h.UNIT_FILES[1:] + ([str(h.OVERRIDE)] if temporary else []))},
                **(h.TEMP_EFFECTIVE if temporary else h.ORIGINAL_EFFECTIVE))

def inactive():
    return dict(active(), MainPID='0', ActiveState='inactive', SubState='dead', ControlGroup='')

class LifecycleChecks(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(subprocess, 'run',
            side_effect=AssertionError('Unapproved external process in isolated test')))
        self.stack.enter_context(patch.object(socket, 'create_connection',
            side_effect=AssertionError('Network forbidden')))
        self.stack.enter_context(patch.object(sqlite3, 'connect',
            side_effect=AssertionError('Production database forbidden')))
        self.stack.enter_context(patch.object(h, 'update', side_effect=lambda r: None))
        self.stack.enter_context(patch.object(h, 'owner', return_value=None))
        self.stack.enter_context(patch.object(h, 'config_binding', return_value=None))
        self.stack.enter_context(patch.object(h, 'preserve', return_value=None))

    def use(self, **values):
        for name, value in values.items():
            self.stack.enter_context(patch.object(h, name, value))

    def test_original_verification_primitives_preserved(self):
        original = (HERE / 'reference/release-core-graceful.py').read_text()
        self.assertEqual(hashlib.sha256(original.encode()).hexdigest(), 'd98a77389a7cf3300aedd4f336ab5557d3f28e042c469e425e18fb87fb234a8a')
        left = {n.name: n for n in ast.parse(original).body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
        right = {n.name: n for n in ast.parse(SOURCE.read_text()).body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
        names = ['NoRedirect', 'acquire_release_lock', 'begin_intent', 'begin_rollback', 'canonical', 'cgroup_empty', 'check_override', 'check_prepared', 'command_once', 'completed', 'control_receipt', 'db_rows', 'file_metadata', 'filehash', 'finished', 'fsyncdir', 'gate', 'gate_before_matches', 'held', 'install', 'main', 'now', 'original_absent', 'override_exists', 'override_on', 'owner', 'prepare', 'preserve', 'process', 'protected', 'raw', 'require', 'restore_override', 'run_cycle', 'save', 'savejson', 'service', 'sha', 'stop_core', 'stopped', 'unit', 'unit_files', 'update']
        self.assertEqual(len(names), 43)
        for name in names:
            self.assertEqual(ast.dump(left[name]), ast.dump(right[name]), name)

    def test_unbound_candidate_cannot_execute(self):
        self.assertEqual(h.MANIFEST_SHA, 'UNBOUND')
        with patch.object(h, 'raw', side_effect=AssertionError('Must fail before any manifest read')):
            with self.assertRaisesRegex(RuntimeError, 'Unbound release'):
                h.base_manifest()

    def test_source_contains_no_restart_or_kill_command(self):
        tree = ast.parse(SOURCE.read_text())
        strings = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        self.assertNotIn('restart', strings)
        self.assertNotIn('kill', strings)
        self.assertNotIn('SIGKILL', strings)
        self.assertEqual(h.OVERRIDE_BYTES, b'[Service]\nTimeoutStopSec=infinity\nSendSIGKILL=no\nKillMode=mixed\nRestart=no\n')

    def test_supported_close_cli_uses_exact_actor_and_service_module(self):
        m = model(); c = cycle(); r = receipt(c); calls = []
        self.use(check_override=Mock(), service=Mock(return_value=active()),
                 gate=Mock(return_value=dict(OPEN)), filehash=Mock(return_value='d' * 64),
                 control_receipt=Mock(return_value=closed()))
        def run(argv, **kwargs):
            calls.append(argv)
            return types.SimpleNamespace(returncode=0, stdout=b'{}', stderr=b'')
        with patch.object(subprocess, 'run', side_effect=run):
            h.close_gate(m, r, c)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][:8], ['/usr/bin/sudo', '-n', '-u', 'mcpdev', '--',
                                      '/usr/bin/python3', '-I', '-S'])
        self.assertEqual(calls[0][8:11], ['-B', str(h.LIVE / 'utils/agentic_maintenance.py'), 'close'])
        self.assertEqual(calls[0][-2:], ['--actor', h.ACTOR])
        self.assertTrue(h.completed(c, 'close'))

    def test_control_receipt_requires_exact_audit_actor_and_gate_epoch(self):
        intent = close_intent(); m = model()
        self.use(gate=Mock(return_value=closed(intent)))
        good = {'action': 'close', 'expected_epoch': 88, 'actor': h.ACTOR}
        self.use(db_rows=Mock(return_value=[[{'kind': 'operator_control', 'payload': json.dumps(good)}]]))
        self.assertEqual(h.control_receipt(m, intent), closed(intent))
        for change in ({'actor': 'another-operator'}, {'expected_epoch': 87}):
            altered = dict(good, **change)
            with patch.object(h, 'db_rows', return_value=[[{'kind': 'operator_control', 'payload': json.dumps(altered)}]]):
                with self.assertRaisesRegex(RuntimeError, 'Exact maintenance control receipt'):
                    h.control_receipt(m, intent)

    def test_unknown_close_is_reconciled_without_reissuing(self):
        m = model(); c = cycle(); r = receipt(c)
        c['intents']['close'] = close_intent()
        self.use(control_receipt=Mock(return_value=closed()))
        with patch.object(subprocess, 'run', side_effect=AssertionError('Must not issue again')):
            h.close_gate(m, r, c)
        self.assertTrue(h.completed(c, 'close'))

    def test_unknown_close_without_receipt_holds(self):
        c = cycle(); c['intents']['close'] = close_intent()
        self.use(control_receipt=Mock(side_effect=RuntimeError('Exact receipt absent')))
        with self.assertRaisesRegex(RuntimeError, 'receipt absent'):
            h.close_gate(model(), receipt(c), c)
        self.assertFalse(h.completed(c, 'close'))

    def test_known_precondition_failure_can_resume_but_unknown_invocation_cannot(self):
        c = cycle(); r = receipt(c); calls = []
        before = Mock(side_effect=RuntimeError('precondition drift'))
        with self.assertRaisesRegex(RuntimeError, 'precondition drift'):
            h.command_once(r, c, 'test', {'action': 'test'}, ['fixed'], before)
        self.assertEqual(c['intents']['test']['command_phase'], 'precondition_failed')
        def run(argv, **kwargs):
            calls.append(argv)
            raise subprocess.TimeoutExpired(argv, 30)
        with patch.object(subprocess, 'run', side_effect=run):
            with self.assertRaises(subprocess.TimeoutExpired):
                h.command_once(r, c, 'test', {'action': 'test'}, ['fixed'], Mock())
            h.command_once(r, c, 'test', {'action': 'test'}, ['fixed'], Mock())
        self.assertEqual(calls, [['fixed']])
        self.assertEqual(c['intents']['test']['command_phase'], 'invocation_may_have_started')

    def test_stop_is_once_and_requires_actual_cessation(self):
        m = model(); c = cycle(); r = receipt(c); state = {'stopped': False}; calls = []
        self.use(held=Mock(), check_override=Mock(), check_prepared=Mock(),
                 source=Mock(), ledger_snapshot=Mock(), process=Mock(return_value=dict(OLD)),
                 service=Mock(side_effect=lambda *args: inactive() if state['stopped'] else active()),
                 original_absent=Mock(side_effect=lambda identity: state['stopped']),
                 cgroup_empty=Mock(side_effect=lambda: state['stopped']))
        def run(argv, **kwargs):
            calls.append(argv); state['stopped'] = True
            return types.SimpleNamespace(returncode=0, stdout=b'', stderr=b'')
        with patch.object(subprocess, 'run', side_effect=run):
            h.stop_core(m, r, c)
            h.stop_core(m, r, c)
        self.assertEqual(calls, [['/usr/bin/systemctl', 'stop', '--no-block', 'abit-ai.service']])
        self.assertTrue(c['intents']['stop']['proof']['cgroup_empty'])

    def test_stop_timeout_never_forces_or_repeats(self):
        c = cycle(); r = receipt(c)
        c['intents']['stop'] = {'command_phase': 'invocation_may_have_started'}
        with patch.object(h, 'OBSERVE_SECONDS', 0):
            with self.assertRaisesRegex(RuntimeError, 'no signal or repeated stop'):
                h.stop_core(model(), r, c)
        self.assertFalse(h.completed(c, 'stop'))

    def test_nonempty_descendant_cgroup_prevents_install(self):
        m = model(); c = cycle(); r = receipt(c)
        install = Mock(side_effect=AssertionError('Source replacement forbidden'))
        self.use(held=Mock(), service=Mock(return_value=inactive()), original_absent=Mock(return_value=True),
                 cgroup_empty=Mock(return_value=False), install=install)
        with self.assertRaisesRegex(RuntimeError, 'has not fully ceased'):
            h.install_source(m, r, c)
        install.assert_not_called()

    def test_active_or_reused_core_prevents_install(self):
        self.use(held=Mock(), service=Mock(return_value=active()), install=Mock())
        c = cycle()
        with self.assertRaisesRegex(RuntimeError, 'has not fully ceased'):
            h.install_source(model(), receipt(c), c)
        h.install.assert_not_called()

    def test_post_stop_cohort_change_preserves_source_and_rows(self):
        install = Mock(); c = cycle()
        self.use(held=Mock(), stopped=Mock(), check_prepared=Mock(),
                 ledger_snapshot=Mock(side_effect=RuntimeError('Provider cohort changed')),
                 install=install)
        with self.assertRaisesRegex(RuntimeError, 'Provider cohort changed'):
            h.install_source(model(), receipt(c), c)
        install.assert_not_called()
        self.assertNotIn('install', c['intents'])

    def test_post_stop_dead_owner_lease_is_still_a_hold(self):
        self.use(db_rows=Mock(return_value=[[{'state': 'draining', 'epoch': 89}], [{'state': 'unresolved', 'n': 1}]]),
                 process=Mock(side_effect=AssertionError('Dead original must not be treated as current live process')))
        with self.assertRaisesRegex(RuntimeError, 'active/unresolved leases'):
            h.ledger_snapshot(model(), dict(OLD), check_identity=False)
        h.process.assert_not_called()

    def test_unknown_start_is_not_reissued(self):
        c = cycle(); c['intents']['start'] = {'command_phase': 'invocation_may_have_started'}
        with patch.object(h, 'OBSERVE_SECONDS', 0):
            with self.assertRaisesRegex(RuntimeError, 'never start again'):
                h.start_core(model(), receipt(c), c)
        self.assertFalse(h.completed(c, 'start'))

    def test_successful_one_use_start_binds_new_process_and_health(self):
        m = model(); c = cycle(); r = receipt(c); calls = []
        self.use(held=Mock(), stopped=Mock(), source=Mock(), check_prepared=Mock(),
                 service=Mock(return_value=active(NEW)), process=Mock(return_value=dict(NEW)),
                 http=Mock(side_effect=lambda path: {'status': True} if path == '/health' else {'version': '0.11.4'}))
        def run(argv, **kwargs):
            calls.append(argv)
            return types.SimpleNamespace(returncode=0, stdout=b'', stderr=b'')
        with patch.object(h.time, 'clock_gettime', return_value=100), patch.object(h.os, 'sysconf', return_value=100), patch.object(subprocess, 'run', side_effect=run):
            h.start_core(m, r, c)
            h.start_core(m, r, c)
        self.assertEqual(calls, [['/usr/bin/systemctl', 'start', '--no-block', 'abit-ai.service']])
        self.assertEqual(c['core_after'], NEW)

    def test_wrong_new_process_signature_is_rejected(self):
        c = cycle(); c['intents']['start'] = {'requested_tick': 9000}
        self.use(service=Mock(return_value=active(NEW)), process=Mock(return_value=dict(NEW, uid=0)))
        with self.assertRaisesRegex(h.StartIdentityPending, 'same-generation runtime exec identity'):
            h.core_from_start(model(), c)

    def test_effective_override_must_disable_force_and_restarts(self):
        self.use(unit_files=Mock(), unit=Mock(return_value=active()))
        self.assertEqual(h.service(model(), True)['Restart'], 'no')
        for field, value in [('SendSIGKILL', 'yes'), ('KillMode', 'control-group'), ('Restart', 'on-failure')]:
            with patch.object(h, 'unit', return_value=dict(active(), **{field: value})):
                with self.assertRaisesRegex(RuntimeError, 'Effective unit setting'):
                    h.service(model(), True)

    def test_cgroup_checks_descendant_population(self):
        with tempfile.TemporaryDirectory(prefix='graceful-cgroup-') as directory:
            root = Path(directory); child = root / 'unit'; child.mkdir()
            (child / 'cgroup.procs').write_text('')
            (child / 'cgroup.events').write_text('populated 1\nfrozen 0\n')
            def mapped(value):
                if value == '/sys/fs/cgroup':
                    return root
                raise AssertionError('Unexpected cgroup path')
            with patch.object(h, 'CGROUP', '/unit'), patch.object(h, 'Path', side_effect=mapped):
                self.assertFalse(h.cgroup_empty())
                (child / 'cgroup.events').write_text('populated 0\nfrozen 0\n')
                self.assertTrue(h.cgroup_empty())

    def test_restore_reconciles_removed_dropin_and_completed_reload(self):
        c = cycle(); r = receipt(c)
        c['core_after'] = dict(NEW)
        c['intents']['start'] = {'requested_tick': 9000, 'fulfilled_utc': 'done'}
        c['intents']['override_remove'] = {'requested_tick': 9001}
        c['intents']['override_off'] = {'command_phase': 'invocation_may_have_started'}
        self.use(held=Mock(), service=Mock(return_value=active(NEW, False)),
                 process=Mock(return_value=dict(NEW)), override_exists=Mock(return_value=False),
                 http=Mock(side_effect=lambda path: {'status': True} if path == '/health' else {'version': '0.11.4'}))
        h.restore_override(model(), r, c)
        self.assertTrue(h.completed(c, 'override_off'))

    def test_open_reconciles_committed_unknown_command_without_close_recheck(self):
        c = cycle(); r = receipt(c)
        c['intents']['open'] = {'action': 'open', 'expected_epoch': 89,
                                'command_id': 'own-open', 'command_phase': 'invocation_may_have_started'}
        self.use(healthy=Mock(), held=Mock(side_effect=AssertionError('Close is no longer current')),
                 control_receipt=Mock(return_value={'epoch': 90}))
        h.open_gate(model(), r, c)
        self.assertTrue(h.completed(c, 'open'))

    def test_open_without_original_receipt_does_not_retry(self):
        c = cycle()
        c['intents']['open'] = {'command_phase': 'invocation_may_have_started'}
        self.use(healthy=Mock(), control_receipt=Mock(side_effect=RuntimeError('Missing original audit')))
        with self.assertRaisesRegex(RuntimeError, 'Missing original audit'):
            h.open_gate(model(), receipt(c), c)

    def test_explicit_rollback_borrows_original_pending_stop(self):
        c = cycle(); r = receipt(c)
        c['intents']['close'] = close_intent()
        c['intents']['stop'] = {'action': 'stop', 'command_phase': 'invocation_may_have_started'}
        self.use(check_prepared=Mock(), source=Mock(), service=Mock(return_value=dict(active(), ActiveState='deactivating', SubState='stop-sigterm')),
                 gate=Mock(return_value=closed()), control_receipt=Mock(return_value=closed()))
        rollback = h.begin_rollback(model(), r)
        self.assertEqual(rollback['borrowed_stop'], c['intents']['stop'])
        self.assertEqual(rollback['borrowed_close']['command_id'], close_intent()['command_id'])
        self.assertEqual(rollback['previous_identity'], OLD)

    def test_rollback_restores_availability_but_cannot_open_changed_cohort(self):
        c = cycle('rollback'); r = receipt(c)
        c['intents']['close'] = close_intent(); c['core_after'] = dict(NEW)
        self.use(held=Mock(return_value=closed()), healthy=Mock(return_value=dict(NEW)),
                 ledger_snapshot=Mock(side_effect=RuntimeError('Provider cohort changed')))
        with self.assertRaisesRegex(RuntimeError, 'Provider cohort changed'):
            h.open_gate(model(), r, c)
        self.assertEqual(r['state'], 'rolled_back_health_verified_admission_held')
        self.assertNotIn('open', c['intents'])

    def test_resume_after_start_does_not_repeat_stop_or_install(self):
        c = cycle(); r = receipt(c); c['core_after'] = dict(NEW)
        for key in ('override_on', 'close', 'stop', 'install', 'start'):
            c['intents'][key] = {'fulfilled_utc': 'done'}
        def finish(name):
            return lambda m, r, c: c['intents'].update({name: {'fulfilled_utc': 'done'}})
        self.use(override_on=Mock(side_effect=AssertionError('No repeat override')),
                 close_gate=Mock(side_effect=AssertionError('No repeat close')),
                 stop_core=Mock(side_effect=AssertionError('No repeat stop')),
                 install_source=Mock(side_effect=AssertionError('No repeat install')),
                 start_core=Mock(side_effect=AssertionError('No repeat start')),
                 restore_override=Mock(side_effect=finish('override_off')),
                 open_gate=Mock(side_effect=finish('open')), healthy=Mock())
        h.run_cycle(model(), r, c)
        self.assertEqual(r['state'], 'deployed_health_verified')
        self.assertFalse(r['databases_restored'])
        self.assertFalse(r['original_jobs_resubmitted'])

    def test_open_proven_never_invoked_resumes_once(self):
        m = model(); c = cycle(); r = receipt(c); calls = []
        c['intents']['open'] = {'action': 'open', 'expected_epoch': 89,
            'command_id': h.OWNER + ':apply:core-open', 'command_phase': 'precondition_failed'}
        self.use(held=Mock(return_value=closed()), healthy=Mock(return_value=dict(NEW)),
                 ledger_snapshot=Mock(return_value={'cohort_preserved': True}),
                 filehash=Mock(return_value='d' * 64), control_receipt=Mock(return_value={'epoch': 90}))
        def run(argv, **kwargs):
            calls.append(argv)
            return types.SimpleNamespace(returncode=0, stdout=b'{}', stderr=b'')
        with patch.object(subprocess, 'run', side_effect=run):
            h.open_gate(m, r, c)
            h.open_gate(m, r, c)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][10], 'open')
        self.assertTrue(h.completed(c, 'open'))

    def test_contended_global_lock_prevents_manifest_and_any_action(self):
        self.use(acquire_release_lock=Mock(side_effect=BlockingIOError('Global release held')),
                 validate_manifest=Mock(side_effect=AssertionError('Must not proceed past global lock')))
        with patch.object(h.os, 'geteuid', return_value=0), patch.dict(h.os.environ, {'SUDO_USER': 'jimran@abitcons.com'}), patch.object(h.socket, 'gethostname', return_value='abit-dev-linux'), patch.object(h.sys, 'argv', ['helper', 'apply']):
            with self.assertRaises(BlockingIOError):
                h.main()
        h.acquire_release_lock.assert_called_once_with(h.GLOBAL_LOCK)
        h.validate_manifest.assert_not_called()

    def test_global_lock_rejects_nonroot_metadata_without_rewriting(self):
        with tempfile.TemporaryDirectory(prefix='graceful-lock-') as directory:
            path = Path(directory) / 'existing.lock'
            path.write_bytes(b'existing-lock-evidence')
            path.chmod(0o600)
            before = path.stat()
            with self.assertRaisesRegex(RuntimeError, 'Release lock metadata'):
                h.acquire_release_lock(path)
            after = path.stat()
            self.assertEqual(path.read_bytes(), b'existing-lock-evidence')
            self.assertEqual((before.st_ino, before.st_uid, before.st_gid, before.st_mode),
                             (after.st_ino, after.st_uid, after.st_gid, after.st_mode))

    def test_local_override_remove_resumes_before_unlink_without_broad_removal(self):
        m = model(); c = cycle(); r = receipt(c); c['core_after'] = dict(NEW)
        c['intents']['start'] = {'requested_tick': 9000, 'fulfilled_utc': 'done'}
        c['intents']['override_remove'] = {'requested_tick': 9001}
        with tempfile.TemporaryDirectory(prefix='graceful-override-') as directory:
            own = Path(directory) / 'owned.conf'; other = Path(directory) / 'unrelated.conf'
            own.write_bytes(h.OVERRIDE_BYTES); other.write_bytes(b'preserved')
            self.use(OVERRIDE=own, held=Mock(), unit_files=Mock(),
                     service=Mock(return_value=active(NEW, False)), process=Mock(return_value=dict(NEW)),
                     override_exists=lambda: own.exists(), fsyncdir=Mock(),
                     check_override=Mock(side_effect=lambda: self.assertEqual(own.read_bytes(), h.OVERRIDE_BYTES)),
                     http=Mock(side_effect=lambda path: {'status': True} if path == '/health' else {'version': '0.11.4'}))
            with patch.object(subprocess, 'run', return_value=types.SimpleNamespace(returncode=0, stdout=b'', stderr=b'')) as run:
                h.restore_override(m, r, c)
            self.assertFalse(own.exists())
            self.assertEqual(other.read_bytes(), b'preserved')
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0], ['/usr/bin/systemctl', 'daemon-reload'])

    def test_real_temporary_source_only_rollback_preserves_unknown_work(self):
        c = cycle('rollback'); r = receipt(c); m = model()
        rel = 'utils/agentic_transport_retry.py'
        old = b'old-reviewed-source\n'; new = b'new-reviewed-source\n'
        m['changes'] = {rel: {'before_sha256': h.sha(old), 'after_sha256': h.sha(new)}}
        m['baseline'] = {rel: {'uid': h.os.getuid(), 'gid': h.os.getgid(), 'mode': '0o644'}}
        with tempfile.TemporaryDirectory(prefix='graceful-source-') as directory:
            live = Path(directory) / 'live'; stage = Path(directory) / 'stage'
            target = live / rel; target.parent.mkdir(parents=True); target.write_bytes(new)
            (stage / 'source').mkdir(parents=True)
            (stage / 'source' / rel.replace('/', '--')).write_bytes(old)
            preserved = stage / 'unknown-original-evidence'; preserved.write_bytes(b'unknown remains unknown')
            self.use(LIVE=live, STAGE=stage, held=Mock(), stopped=Mock(), check_prepared=Mock(),
                     protected=Mock(), source=Mock(),
                     ledger_snapshot=Mock(return_value={'current_work_changed': True, 'historical_outcomes_completed': False}))
            h.install_source(m, r, c)
            self.assertEqual(target.read_bytes(), old)
            self.assertEqual(preserved.read_bytes(), b'unknown remains unknown')
            self.assertEqual(h.ledger_snapshot.call_args.kwargs,
                             {'strict': False, 'idle': False, 'check_identity': False})
            self.assertFalse(c['intents']['install']['proof']['databases_restored'])

if __name__ == '__main__':
    unittest.main(verbosity=2)
