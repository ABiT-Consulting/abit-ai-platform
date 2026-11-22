"""Finite source-only checks with actual temporary files/SQLite and no host control."""
from pathlib import Path
from contextlib import ExitStack
import ast
import copy
import hashlib
import importlib.util
import io
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
import test_graceful_lifecycle as legacy

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
P = ROOT / 'deployment/linux/native-verification-activation-20261122'
h = legacy.h


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


b = load('native_backup_preparation', P / 'prepare_release.py')
s = load('disabled_broker_stager', P / 'stage_broker.py')


class DeltaCases(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(subprocess, 'run',
            side_effect=AssertionError('External process forbidden')))
        self.stack.enter_context(patch.object(socket, 'create_connection',
            side_effect=AssertionError('Network forbidden')))
        self.stack.enter_context(patch.object(h, 'update', side_effect=lambda r: None))
        self.stack.enter_context(patch.object(h, 'owner', return_value=None))
        self.stack.enter_context(patch.object(h, 'preserve', return_value=None))
        self.temp = tempfile.TemporaryDirectory(prefix='native-activation-isolated-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def use(self, module=h, **values):
        for key, value in values.items():
            self.stack.enter_context(patch.object(module, key, value))

    def metadata(self):
        return {'uid': os.getuid(), 'gid': os.getgid(), 'mode': '0o644'}

    def source_fixture(self):
        base = self.root / 'base'
        live = base / 'releases/runtime/open_webui'
        live.mkdir(parents=True)
        (base / 'current').symlink_to(base / 'releases')
        config = base / 'config/resolver.json'
        config.parent.mkdir()
        config.write_text('{"existing_integration":true}')
        existing = ['routers/agentic_governance.py', 'utils/agentic_contracts/validator.py',
                    'utils/agentic_maintenance.py']
        existing += ['fixtures/source_' + str(i) + '.py' for i in range(370)]
        existing += ['utils/agentic_contracts/schemas/governance_v1.schema.json',
                     'utils/agentic_contracts/schemas/second.schema.json']
        rows = {}
        for rel in existing:
            path = live / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(('source:' + rel).encode())
            path.chmod(0o644)
            rows[rel] = {**self.metadata(), 'sha256': h.filehash(path)}
        for rel in h.NEW_LEAVES:
            rows[rel] = {**self.metadata(), 'sha256': None}
        changes = {}
        for rel in h.ALLOWED:
            parent = (live / rel).parent.stat()
            changes[rel] = {'before_sha256': rows[rel]['sha256'],
                            'after_sha256': h.sha(('candidate:' + rel).encode()),
                            'parent_device': parent.st_dev, 'parent_inode': parent.st_ino}
        m = {'baseline': rows, 'changes': changes,
             'configuration': [h.file_metadata(config)],
             'native_registration_enabled': False, 'native_configuration_path': str(config)}
        self.use(BASE=base, LIVE=live, CONFIG_PATH=config)
        return m, live, config

    def test_complete_current_source_inventory_and_three_absence_preconditions(self):
        m, live, config = self.source_fixture()
        h.source(m, 'before')
        rel = sorted(h.NEW_LEAVES)[0]
        (live / rel).write_bytes(b'unreviewed preexisting file')
        with self.assertRaisesRegex(RuntimeError, 'Installed source changed'):
            h.source(m, 'before')

    def test_unrelated_added_source_or_modified_schema_blocks(self):
        m, live, config = self.source_fixture()
        addition = live / 'unreviewed.py'
        addition.write_bytes(b'extra')
        with self.assertRaisesRegex(RuntimeError, 'inventory'):
            h.source(m, 'before')
        addition.unlink()
        schema = live / 'utils/agentic_contracts/schemas/second.schema.json'
        schema.write_bytes(b'changed schema')
        with self.assertRaisesRegex(RuntimeError, 'source changed'):
            h.source(m, 'before')

    def test_actual_new_leaf_install_is_exclusive_and_exact(self):
        path = self.root / 'new.py'
        h.install_new(path, b'reviewed bytes', self.metadata())
        self.assertEqual(path.read_bytes(), b'reviewed bytes')
        self.assertEqual(path.stat().st_nlink, 1)
        self.assertEqual(path.stat().st_mode & 0o777, 0o644)
        with self.assertRaises(FileExistsError):
            h.install_new(path, b'overwrite forbidden', self.metadata())
        self.assertEqual(path.read_bytes(), b'reviewed bytes')
        self.assertEqual(list(self.root.iterdir()), [path])

    def test_new_leaf_link_race_preserves_other_writer(self):
        path = self.root / 'new.py'
        actual_link = os.link
        def race(source, destination, **kwargs):
            path.write_bytes(b'concurrent owner')
            return actual_link(source, destination, **kwargs)
        with patch.object(os, 'link', side_effect=race):
            with self.assertRaises(FileExistsError):
                h.install_new(path, b'our candidate', self.metadata())
        self.assertEqual(path.read_bytes(), b'concurrent owner')
        self.assertEqual(list(self.root.iterdir()), [path])

    def rollback_fixture(self, content=b'reviewed new source'):
        rel = sorted(h.NEW_LEAVES)[0]
        live = self.root / 'live'
        path = live / rel
        path.parent.mkdir(parents=True)
        path.write_bytes(content)
        path.chmod(0o644)
        m = legacy.model()
        m['baseline'] = {rel: self.metadata()}
        m['changes'] = {rel: {'before_sha256': None,
                             'after_sha256': h.sha(b'reviewed new source')}}
        c = legacy.cycle('rollback')
        r = legacy.receipt(c)
        self.use(LIVE=live, held=Mock(), stopped=Mock(), check_prepared=Mock(),
                 source=Mock(), ledger_snapshot=Mock(return_value={'unknown_preserved': True}))
        return m, c, r, path

    def test_rollback_removes_only_owned_new_postimage_and_preserves_unknown_record(self):
        m, c, r, path = self.rollback_fixture()
        original = path.parent / 'original-uncertain.json'
        original.write_bytes(b'unknown remains unknown')
        h.install_source(m, r, c)
        self.assertFalse(path.exists())
        self.assertEqual(original.read_bytes(), b'unknown remains unknown')
        self.assertFalse(c['intents']['install']['proof']['databases_restored'])

    def test_rollback_refuses_changed_new_source_or_symlink(self):
        m, c, r, path = self.rollback_fixture(b'other owner bytes')
        with self.assertRaisesRegex(RuntimeError, 'Concurrent source'):
            h.install_source(m, r, c)
        self.assertEqual(path.read_bytes(), b'other owner bytes')
        path.unlink()
        other = path.parent / 'other'
        other.write_bytes(b'reviewed new source')
        path.symlink_to(other)
        with self.assertRaises(OSError):
            h.install_source(m, r, c)
        self.assertEqual(other.read_bytes(), b'reviewed new source')

    def test_default_off_rejects_true_missing_enabled_and_non_object(self):
        path = self.root / 'resolver.json'
        self.use(CONFIG_PATH=path)
        m = {'native_registration_enabled': False, 'native_configuration_path': str(path)}
        for value in ({}, {'agentic_native_verification': {'enabled': False}}):
            path.write_text(json.dumps(value))
            h.default_off(m)
        for value in ({'agentic_native_verification': {'enabled': True}},
                      {'agentic_native_verification': {}}, []):
            path.write_text(json.dumps(value))
            with self.assertRaises(RuntimeError):
                h.default_off(m)

    def test_actual_process_environment_binds_only_fixed_config_path(self):
        self.use(process=Mock(return_value=legacy.OLD))
        good = b'PRIVATE_TOKEN=do-not-export\0ABIT_ERP_TASK_RESOLUTION_CONFIG=' + str(h.CONFIG_PATH).encode() + b'\0'
        self.use(raw_proc_environment=Mock(return_value=good))
        h.config_binding(legacy.OLD)
        self.use(raw_proc_environment=Mock(return_value=good.replace(str(h.CONFIG_PATH).encode(), b'/other/resolver.json')))
        with self.assertRaisesRegex(RuntimeError, 'configuration differs') as caught:
            h.config_binding(legacy.OLD)
        self.assertNotIn('PRIVATE_TOKEN', str(caught.exception))
        self.assertNotIn('do-not-export', str(caught.exception))

    def test_relay_idle_is_only_runtime_status_and_requires_exact_integer_zero(self):
        m = {'preserved_processes': {'relay': legacy.OLD}}
        value = {'ok': True, 'protocol': 'v1_graceful_relay_drain',
                 'pid': legacy.OLD['pid'], 'draining': False, 'server_closed': False,
                 'active_http_handlers': 0, 'active_relay_turns': 0,
                 'erp_active_turns': 0, 'erp_inspections': 0}
        self.use(process=Mock(return_value=legacy.OLD), http=Mock(return_value=value))
        h.relay_idle(m)
        h.http.assert_called_once_with('/runtime-status', 8810)
        for changed in ({'active_http_handlers': True}, {'erp_inspections': 1},
                        {'draining': True}, {'pid': 999}):
            h.http.return_value = {**value, **changed}
            with self.assertRaises(RuntimeError):
                h.relay_idle(m)

    def test_relay_process_replacement_during_read_is_rejected(self):
        value = {'ok': True, 'protocol': 'v1_graceful_relay_drain',
                 'pid': legacy.OLD['pid'], 'draining': False, 'server_closed': False,
                 'active_http_handlers': 0, 'active_relay_turns': 0,
                 'erp_active_turns': 0, 'erp_inspections': 0}
        self.use(process=Mock(side_effect=[legacy.OLD, legacy.NEW]),
                 http=Mock(return_value=value))
        with self.assertRaisesRegex(RuntimeError, 'changed during'):
            h.relay_idle({'preserved_processes': {'relay': legacy.OLD}})

    def test_status_read_enforces_bound_and_never_uses_old_relay_health(self):
        class Response(io.BytesIO):
            status = 200
        seen = []
        def open_request(request, timeout):
            seen.append(request)
            return Response(b' ' * 4097)
        opener = types.SimpleNamespace(open=open_request)
        with patch.object(h.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaisesRegex(RuntimeError, 'bound'):
                h.http('/runtime-status', 8810)
        self.assertEqual(seen[0].full_url, 'http://127.0.0.1:8810/runtime-status')
        self.assertEqual(seen[0].get_header('Connection'), 'close')
        with self.assertRaisesRegex(RuntimeError, 'Unsupported'):
            h.http('/health', 8810)

    def start_fixture(self):
        m = legacy.model()
        c = legacy.cycle()
        r = legacy.receipt(c)
        self.use(held=Mock(), stopped=Mock(), check_prepared=Mock(), source=Mock(),
                 service=Mock(return_value=legacy.active(legacy.NEW)), OBSERVE_SECONDS=5,
                 http=Mock(side_effect=lambda path: {'status': True} if path == '/health' else {'version': '0.11.4'}))
        return m, c, r

    def test_launcher_same_generation_exec_waits_then_verifies_one_start(self):
        m, c, r = self.start_fixture()
        launcher = {**legacy.NEW, 'cmdline_sha256': 'launcher-not-runtime'}
        self.use(process=Mock(side_effect=[launcher, legacy.NEW]))
        result = types.SimpleNamespace(returncode=0, stdout=b'', stderr=b'')
        with patch.object(subprocess, 'run', return_value=result) as run, \
             patch.object(h.time, 'clock_gettime', return_value=1), \
             patch.object(h.time, 'sleep', return_value=None):
            h.start_core(m, r, c)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0], ['/usr/bin/systemctl', 'start', '--no-block', 'abit-ai.service'])
        self.assertEqual(c['core_after'], legacy.NEW)
        self.assertEqual(c['start_process'], {k: legacy.NEW[k] for k in ('pid', 'start_ticks')})

    def test_launcher_replacement_generation_denies_without_second_start(self):
        m, c, r = self.start_fixture()
        launcher = {**legacy.NEW, 'cmdline_sha256': 'launcher'}
        replaced = {**legacy.NEW, 'pid': 203, 'start_ticks': legacy.NEW['start_ticks'] + 1}
        self.use(process=Mock(side_effect=[launcher, replaced]))
        result = types.SimpleNamespace(returncode=0, stdout=b'', stderr=b'')
        with patch.object(subprocess, 'run', return_value=result) as run, \
             patch.object(h.time, 'clock_gettime', return_value=1), \
             patch.object(h.time, 'sleep', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'generation changed'):
                h.start_core(m, r, c)
        self.assertEqual(run.call_count, 1)
        self.assertNotIn('core_after', c)
        self.assertEqual(c['intents']['start']['command_phase'], 'invocation_may_have_started')

    def test_unverified_launcher_timeout_preserves_one_start_and_finalize_observes_it(self):
        m, c, r = self.start_fixture()
        self.use(process=Mock(return_value={**legacy.NEW, 'cmdline_sha256': 'launcher'}))
        result = types.SimpleNamespace(returncode=0, stdout=b'', stderr=b'')
        with patch.object(subprocess, 'run', return_value=result) as run, \
             patch.object(h.time, 'clock_gettime', return_value=1), \
             patch.object(h.time, 'monotonic', side_effect=[0, 1, 6]), \
             patch.object(h.time, 'sleep', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'timed out'):
                h.start_core(m, r, c)
            self.assertEqual(run.call_count, 1)
        self.use(process=Mock(return_value=legacy.NEW))
        with patch.object(subprocess, 'run', side_effect=AssertionError('Start must not repeat')):
            h.start_core(m, r, c)
        self.assertTrue(h.completed(c, 'start'))
        self.assertEqual(c['core_after'], legacy.NEW)

    def test_new_command_namespace_cannot_reuse_previous_close_receipt(self):
        self.assertIn('native-verification-20261122T1615', h.COMMAND_PREFIX)
        self.assertNotEqual(h.COMMAND_PREFIX, h.OWNER)
        self.assertIn(h.COMMAND_PREFIX, h.ACTOR)

    def test_unbound_preparation_denies_before_input_read(self):
        self.assertEqual(b.PREPARATION_SHA, 'UNBOUND')
        with patch.object(b, 'raw', side_effect=AssertionError('Unbound read forbidden')):
            with self.assertRaisesRegex(RuntimeError, 'Unbound preparation'):
                b.read_preparation()

    def test_actual_eleven_online_backups_and_isolated_restores_preserve_originals(self):
        base = self.root / 'sqlite-base'
        stage = self.root / 'sqlite-stage'
        for folder in ('database', 'restore-check'):
            (stage / folder).mkdir(parents=True)
        real_connect = sqlite3.connect
        originals = {}
        for name in b.DATABASES:
            path = base / 'data' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with real_connect(path) as db:
                db.execute('CREATE TABLE synthetic(id INTEGER PRIMARY KEY, private_text TEXT)')
                db.execute('INSERT INTO synthetic VALUES (1,?)', ('CANARY_PRIVATE_ROW_NEVER_EXPORTED',))
            originals[path] = hashlib.sha256(path.read_bytes()).hexdigest()
        def confined_connect(database, *args, **kwargs):
            text = str(database)
            if text.startswith('file:'):
                text = text[5:].split('?', 1)[0]
            self.assertTrue(Path(text).resolve().is_relative_to(self.root))
            return real_connect(database, *args, **kwargs)
        report = {'databases': []}
        self.use(module=b, BASE=base, STAGE=stage, deadline=Mock())
        with patch.object(sqlite3, 'connect', side_effect=confined_connect):
            b.backup_databases(report)
        self.assertEqual(len(report['databases']), 11)
        self.assertTrue(all(row['integrity'] == 'ok' and row['isolated_restore_schema_and_counts_match']
                            for row in report['databases']))
        self.assertNotIn('CANARY_PRIVATE_ROW_NEVER_EXPORTED', json.dumps(report))
        self.assertEqual(list((stage / 'restore-check').iterdir()), [])
        for path, expected in originals.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected)
        self.assertEqual(len(list((stage / 'database').iterdir())), 11)

    def test_backup_failure_retains_existing_completed_and_partial_artifacts(self):
        base = self.root / 'failing-base'
        stage = self.root / 'failing-stage'
        for folder in ('database', 'restore-check'):
            (stage / folder).mkdir(parents=True)
        for i, name in enumerate(b.DATABASES):
            path = base / 'data' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if i == 1:
                path.write_bytes(b'not sqlite')
            else:
                with sqlite3.connect(path) as db:
                    db.execute('CREATE TABLE fixture(id INTEGER)')
        self.use(module=b, BASE=base, STAGE=stage, deadline=Mock())
        report = {'databases': []}
        with self.assertRaises(sqlite3.DatabaseError):
            b.backup_databases(report)
        self.assertEqual(len(report['databases']), 1)
        self.assertTrue((stage / 'database' / b.DATABASES[0]).is_file())
        self.assertTrue((stage / 'database' / b.DATABASES[1]).is_file())
        self.assertEqual((base / 'data' / b.DATABASES[1]).read_bytes(), b'not sqlite')

    def test_backup_original_database_loop_and_thirteen_functions_are_ast_exact(self):
        old = ast.parse((HERE / 'reference/backup-current-core.py').read_text())
        new = ast.parse((P / 'prepare_release.py').read_text())
        left = {n.name: n for n in old.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
        right = {n.name: n for n in new.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
        names = ['require','now','sha','regular','save','json_save','checkpoint','table_summary',
                 'deadline','sql_deadline','progress','filehash','validate_owner']
        self.assertEqual(len(names), 13)
        for name in names:
            self.assertEqual(ast.dump(left[name]), ast.dump(right[name]), name)
        find_loop = lambda node: next(n for n in ast.walk(node) if isinstance(n, ast.For)
            and isinstance(n.iter, ast.Name) and n.iter.id == 'DATABASES')
        self.assertEqual(ast.dump(find_loop(left['main'])), ast.dump(find_loop(right['backup_databases'])))

    def test_source_stager_has_only_systemctl_show_no_keys_or_authority_command(self):
        tree = ast.parse((P / 'stage_broker.py').read_text())
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute)
                 and isinstance(node.func.value, ast.Name)
                 and node.func.value.id == 'subprocess']
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].func.attr, 'run')
        argv = ast.literal_eval(calls[0].args[0])
        self.assertEqual(argv[:3], ['/usr/bin/systemctl', 'show', 'abit-native-verification-broker.service'])
        strings = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        for forbidden in ('enable', 'start', 'restart', 'daemon-reload', 'systemd-run'):
            self.assertNotIn(forbidden, strings)

    def test_broker_default_off_denies_enabled_config_without_mutation(self):
        config = self.root / 'config.json'
        config.write_text('{"agentic_native_verification":{"enabled":true}}')
        self.use(module=s, CONFIG=config)
        original = config.read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'disabled'):
            s.default_off()
        self.assertEqual(config.read_bytes(), original)


    def stager_fixture(self):
        stage = self.root / 'stage'
        stage.mkdir()
        bundle = self.root / 'var-lib-abit/native-verification/service'
        unit = self.root / 'etc-systemd/native.service'
        unit.parent.mkdir()
        payload = {bundle / 'one.py': (b'first reviewed module', 0o400),
                   bundle / 'registration.json': (b'{"enabled":false}', 0o400),
                   unit: (b'[Service]\nRestart=no\n', 0o644)}
        self.use(module=s, STAGE=stage, BUNDLE=bundle, UNIT=unit,
                 JOURNAL=stage / 'journal.json', operator=Mock(), inactive=Mock(),
                 default_off=Mock(), inputs=Mock(return_value=payload),
                 trusted_directory=Mock())
        self.stack.enter_context(patch.object(os, 'fchown', return_value=None))
        # The local test account cannot manufacture uid0. Root ownership remains
        # enforced in product owned_file and is separately checked by its AST/
        # source review; here only the real file/mode/content lifecycle is run.
        def fixture_owned(path, data, mode):
            info = path.lstat()
            self.assertTrue(path.is_file() and not path.is_symlink())
            self.assertEqual(info.st_nlink, 1)
            self.assertEqual(info.st_mode & 0o777, mode)
            self.assertEqual(path.read_bytes(), data)
        self.use(module=s, owned_file=fixture_owned)
        return stage, bundle, payload

    def test_preexisting_broker_prefix_is_not_adopted(self):
        stage, bundle, payload = self.stager_fixture()
        bundle.parent.mkdir(parents=True)
        preserved = bundle.parent / 'unknown'
        preserved.write_bytes(b'other owner')
        with self.assertRaisesRegex(RuntimeError, 'already exists'):
            s.apply()
        self.assertFalse(s.JOURNAL.exists())
        self.assertEqual(preserved.read_bytes(), b'other owner')

    def test_partial_local_broker_stage_reconciles_without_replacing_completed_inode(self):
        stage, bundle, payload = self.stager_fixture()
        actual = s.create_exact
        first = bundle / 'one.py'
        def fail_after_first(path, data, mode):
            actual(path, data, mode)
            if path == first:
                raise TimeoutError('fixture interrupted after durable file creation')
        with patch.object(s, 'create_exact', side_effect=fail_after_first):
            with self.assertRaises(TimeoutError):
                s.apply()
        inode = first.stat().st_ino
        self.assertEqual(json.loads(s.JOURNAL.read_text())['phase'], 'intent_saved')
        with patch('sys.stdout', new=io.StringIO()):
            s.apply()
        record = json.loads(s.JOURNAL.read_text())
        self.assertEqual(record['phase'], 'staged_disabled')
        self.assertFalse(record['service_started'])
        self.assertFalse(record['keys_created'])
        self.assertFalse(record['authority_issued'])
        self.assertEqual(first.stat().st_ino, inode)
        self.assertEqual(first.read_bytes(), payload[first][0])

    def test_partial_stage_unknown_bundle_file_holds_without_removal(self):
        stage, bundle, payload = self.stager_fixture()
        actual = s.create_exact
        first = bundle / 'one.py'
        def fail_after_first(path, data, mode):
            actual(path, data, mode)
            if path == first:
                raise TimeoutError('fixture pause')
        with patch.object(s, 'create_exact', side_effect=fail_after_first):
            with self.assertRaises(TimeoutError):
                s.apply()
        unknown = bundle / 'unknown.py'
        unknown.write_bytes(b'preserve')
        with self.assertRaisesRegex(RuntimeError, 'Unexpected broker bundle'):
            s.apply()
        self.assertEqual(unknown.read_bytes(), b'preserve')

    def test_active_broker_prevents_staging_before_intent(self):
        stage, bundle, payload = self.stager_fixture()
        self.use(module=s, inactive=Mock(side_effect=RuntimeError('already active')))
        with self.assertRaisesRegex(RuntimeError, 'already active'):
            s.apply()
        self.assertFalse(s.JOURNAL.exists())
        self.assertFalse(bundle.parent.exists())

    def test_actual_untrusted_ancestry_is_rejected_read_only(self):
        # This is ordinary metadata inspection, with no ownership/mode changes.
        with self.assertRaisesRegex(RuntimeError, 'exclusively administered'):
            s.trusted_directory(self.root)


if __name__ == '__main__':
    unittest.main(verbosity=2)
