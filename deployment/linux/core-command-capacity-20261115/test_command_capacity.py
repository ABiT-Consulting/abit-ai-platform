"""Finite offline guards. No live DB, network, process or service access is allowed."""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


m = load('capacity_subject', HERE / 'apply_command_capacity.py')
p = load('preservation_subject', HERE / 'preserved_original.py')
core = m.pinned_module('finite_reviewed_support', m.SUPPORT, m.SUPPORT_SHA)


def audit_guard(event, args):
    if event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
        value = os.fsdecode(args[0])
        forbidden = ('/srv/abit-dev-linux/abit-ai/data/',
                     '/srv/abit-dev-linux/abit-ai/releases/',
                     '/run/', '/proc/', '/sys/')
        if value.startswith(forbidden):
            raise AssertionError('LIVE_STATE_OPEN_DENIED')
    if event in {'subprocess.Popen', 'os.system', 'os.posix_spawn', 'socket.connect', 'socket.getaddrinfo'}:
        raise AssertionError('EXTERNAL_EFFECT_DENIED')


sys.addaudithook(audit_guard)


def plan():
    return {'schema_version': 1, 'state': 'FROZEN_CAPACITY_ONLY_RETAINED_ORIGINAL',
            'source_commit': 'a' * 40,
            'package': str(m.REPO / '.git/core-command-capacity-20261115-fixture'),
            'manifest_sha256': 'b' * 64, 'tests_sha256': 'c' * 64, 'test_count': 2,
            'live_baseline': str(m.REPO / '.git/capacity-live-fixture.json'),
            'live_baseline_sha256': 'd' * 64, 'initial_gate': deepcopy(m.INITIAL_GATE),
            'support_sha256': m.SUPPORT_SHA, 'preservation_sha256': m.PRESERVATION_SHA,
            'native_publication': {'path': '/var/lib/abit-fixture/native.json',
                'sha256': 'e' * 64, 'expected': {}, 'channel_path': '/var/lib/abit-fixture/channel.json',
                'channel_sha256': 'f' * 64, 'channel_expected': {}}}


def manifest():
    return {'schema_version': 1, 'source_commit': plan()['source_commit'], 'runtime': str(m.RUNTIME),
            'files': [{'relative_path': name, 'kind': value['kind'],
                       'candidate_sha256': value['sha256'], 'candidate_bytes': 20,
                       'original': {'sha256': '1' * 64, 'uid': 999, 'gid': 988,
                           'mode': 0o644, 'size': 10, 'mtime_ns': 1700000000000000000, 'xattrs': {}},
                       'parent': {'dev': 1, 'ino': 2, 'uid': 999, 'gid': 988, 'mode': 0o2770, 'xattrs': {}}}
                      for name, value in m.TARGETS.items()]}


def browser():
    now = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
    return ({'source': 'authenticated_browser_ui', 'url': 'https://abit.ai/api/tasks',
             'observed_at': now.isoformat(), 'actual_task_count': 0, 'main_pid': 100,
             'main_process_start_ticks': '1000', 'credential_extracted': False,
             'admin_authenticated': True}, {'pid': 100, 'process_start_ticks': '1000'}, now)


def units():
    return {unit: {'fixed_properties_sha256': 'a' * 64, 'dropins': ['/etc/original.conf'],
                   'SendSIGKILL': 'yes', 'RefuseManualStart': 'no',
                   'KillSignal': '15', 'KillMode': 'control-group'} for unit in (m.MAIN, m.QA)}


def native():
    value = plan()
    services = {role: {'id': char * 64, 'image': 'sha256:' + char * 64, 'running': True}
                for role, char in [('backend', '1'), ('governance', '2'), ('scheduler', '3')]}
    expected = {**m.NATIVE_FIXED, 'services': services}
    channel = deepcopy(m.CHANNEL_FIXED)
    channel_raw = m.canonical(channel)
    publication = {**deepcopy(expected), 'original_f33': {'invocation_id': p.INVOCATION,
        'run_id': p.RUN, 'preserved': True},
        'channel_new_start_receipt_path': value['native_publication']['channel_path'],
        'channel_new_start_receipt_sha256': m.sha(channel_raw)}
    raw = m.canonical(publication)
    value['native_publication'].update({'expected': expected, 'sha256': m.sha(raw),
        'channel_expected': deepcopy(channel), 'channel_sha256': m.sha(channel_raw)})
    contents = {value['native_publication']['path']: raw,
                value['native_publication']['channel_path']: channel_raw}
    def read(path, pin, private=False):
        assert private is True
        result = contents[str(path)]
        if m.sha(result) != pin:
            raise RuntimeError('PIN_CHANGED')
        return result
    return value, publication, channel, contents, types.SimpleNamespace(read=read)


class Offline(unittest.TestCase):
    def setUp(self):
        self.effects = [patch.object(socket.socket, 'connect', side_effect=AssertionError('NETWORK_DENIED')),
                        patch.object(socket, 'getaddrinfo', side_effect=AssertionError('DNS_DENIED')),
                        patch.object(subprocess, 'Popen', side_effect=AssertionError('PROCESS_DENIED'))]
        for effect in self.effects:
            effect.start()
            self.addCleanup(effect.stop)

    def denied(self, fn, code=None):
        with self.assertRaises(RuntimeError) as found:
            fn()
        if code:
            self.assertIn(code, str(found.exception))


class TypedInputsAndBrowser(Offline):
    def test_unfrozen_program_denies_before_loading_or_action(self):
        args = types.SimpleNamespace(execute=True, preflight_only=False)
        with patch.object(m.os, 'geteuid', return_value=0), patch.object(m.os, 'uname',
             return_value=types.SimpleNamespace(nodename='abit-dev-linux')), patch.object(m, 'pinned_module') as imported:
            self.denied(lambda: m.main(args), 'NOT_FROZEN')
        imported.assert_not_called()

    def test_exact_two_existing_typed_targets(self):
        a, b = plan(), manifest()
        before = deepcopy((a, b))
        self.assertIs(m.validate_plan(a), a)
        self.assertIs(m.validate_manifest(b, a), b)
        self.assertEqual((a, b), before)

    def test_added_missing_duplicate_and_new_helper_denied(self):
        for mode in ('added', 'missing', 'duplicate', 'helper'):
            value = manifest()
            if mode == 'added':
                value['files'].append(deepcopy(value['files'][0]))
            elif mode == 'missing':
                value['files'].pop()
            elif mode == 'duplicate':
                value['files'][0] = deepcopy(value['files'][1])
            else:
                value['files'][0]['relative_path'] = 'utils/agentic_delivery_control.py'
            self.denied(lambda: m.validate_manifest(value, plan()), 'ONLY_TWO')

    def test_candidate_kind_pin_metadata_and_parent_must_match(self):
        for category, key, value in [('row', 'kind', 'RuntimeJSON'), ('row', 'candidate_sha256', '9' * 64),
                                     ('original', 'uid', 0), ('original', 'mode', 0o666),
                                     ('parent', 'gid', 0), ('original', 'sha256', None)]:
            item = manifest()
            row = item['files'][0]
            (row if category == 'row' else row[category])[key] = value
            self.denied(lambda: m.validate_manifest(item, plan()))

    def test_already_current_pair_or_single_current_leaf_is_not_a_replacement(self):
        for count in (1, 2):
            value = manifest()
            for row in value['files'][:count]:
                row['original']['sha256'] = row['candidate_sha256']
            self.denied(lambda: m.validate_manifest(value, plan()), 'EXISTING_SOURCE')

    def test_open_sealed_idle_wrong_epoch_and_boolean_count_denied(self):
        for delta in ({'state': 'open'}, {'state': 'sealed'}, {'active_admissions': 0},
                      {'active_admissions': True}, {'epoch': 25}, {'epoch': True}):
            value = plan()
            value['initial_gate'].update(delta)
            self.denied(lambda: m.validate_plan(value), 'DRAINING24')

    def test_package_escape_and_changed_support_denied(self):
        for field, value in [('package', '/tmp/foreign'),
                             ('package', str(m.REPO / '.git/../foreign')),
                             ('live_baseline', '/tmp/private.json'),
                             ('support_sha256', 'a' * 64), ('test_count', True)]:
            data = plan()
            data[field] = value
            self.denied(lambda: m.validate_plan(data))

    def test_duplicate_nonfinite_and_wrong_schema_type_denied(self):
        self.denied(lambda: m.strict_json(b'{"value":1,"value":2}'), 'DUPLICATE')
        self.denied(lambda: m.strict_json(b'{"value":NaN}'), 'NONFINITE')
        value = manifest()
        value['schema_version'] = True
        self.denied(lambda: m.validate_manifest(value, plan()))

    def test_fresh_administrative_browser_proof(self):
        value, main, now = browser()
        m.validate_browser(core, value, main, now=now)

    def test_stale_anonymous_nonadmin_wrong_birth_tasks_or_credentials_denied(self):
        for delta in ({'admin_authenticated': False}, {'source': 'http_probe'},
                      {'main_pid': 99}, {'main_process_start_ticks': 'old'}, {'actual_task_count': 1},
                      {'credential_extracted': True}, {'observed_at': '2026-11-15T11:57:59+00:00'}):
            value, main, now = browser()
            value.update(delta)
            self.denied(lambda: m.validate_browser(core, value, main, now=now))


class NativePublication(Offline):
    def test_exact_positive_receipts_bind_original_services_and_channel_counts(self):
        plan_value, publication, channel, contents, reader = native()
        self.assertEqual(m.native_positive(reader, plan_value), {
            'publication_sha256': plan_value['native_publication']['sha256'],
            'channel_sha256': plan_value['native_publication']['channel_sha256']})

    def test_build_only_or_queued_native_result_cannot_pass(self):
        for delta in ({'ok': False}, {'purpose': 'BUILD_ONLY'}, {'command_receipt_max_chars': 2000},
                      {'native_source_commit': '0' * 40}):
            value, publication, channel, contents, reader = native()
            publication.update(delta)
            raw = m.canonical(publication)
            contents[value['native_publication']['path']] = raw
            value['native_publication']['sha256'] = m.sha(raw)
            self.denied(lambda: m.native_positive(reader, value), 'PUBLICATION_DRIFT')

    def test_schema_cannot_omit_native_configuration_or_expected_service(self):
        for key in ('site_config_sha256', 'common_config_sha256', 'native_source_commit', 'services'):
            value, publication, channel, contents, reader = native()
            del value['native_publication']['expected'][key]
            self.denied(lambda: m.native_positive(reader, value), 'NATIVE_CONTRACT')

    def test_unrunning_changed_service_or_original_receipt_denied(self):
        for mode in ('service', 'original', 'channel'):
            value, publication, channel, contents, reader = native()
            if mode == 'service':
                publication['services']['backend']['running'] = False
            elif mode == 'original':
                publication['original_f33']['preserved'] = False
            else:
                publication['channel_new_start_receipt_sha256'] = '0' * 64
            raw = m.canonical(publication)
            contents[value['native_publication']['path']] = raw
            value['native_publication']['sha256'] = m.sha(raw)
            self.denied(lambda: m.native_positive(reader, value))

    def test_channel_start_stop_counts_cannot_be_reset_or_invented(self):
        for field, changed in [('physical_explicit_Start_calls_total', 1),
                               ('prior_old_Stop_calls', 0), ('explicit_Start_calls', True)]:
            value, publication, channel, contents, reader = native()
            value['native_publication']['channel_expected'][field] = changed
            self.denied(lambda: m.native_positive(reader, value), 'CHANNEL_RECEIPT')

    def test_wrong_three_service_roles_denied_even_when_root_projection_matches(self):
        for roles in (('backend', 'frontend', 'websocket'),
                      ('backend', 'scheduler', 'agentic-governance-worker')):
            value, publication, channel, contents, reader = native()
            services = dict(zip(roles, publication['services'].values()))
            publication['services'] = services
            value['native_publication']['expected']['services'] = deepcopy(services)
            raw = m.canonical(publication)
            contents[value['native_publication']['path']] = raw
            value['native_publication']['sha256'] = m.sha(raw)
            self.denied(lambda: m.native_positive(reader, value), 'THREE_PUBLISHED')

    def test_missing_native_receipt_is_not_filled_from_current_state(self):
        value, publication, channel, contents, reader = native()
        contents.clear()
        with self.assertRaises(KeyError):
            m.native_positive(reader, value)


class TemporaryLedger:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.trace = []
        self.packet = {'content_sha256': '2' * 64, 'fixture': True}
        self.packet_hash = p.digest(self.packet)
        self.event = {'invocation_id': p.INVOCATION, 'run_id': p.RUN, 'type': 'run.started',
                      'work_packet_digest': self.packet['content_sha256'], 'event_id': 'fixture-start'}
        event_json = p.canonical(self.event).decode()
        receipt = {'durable': True, 'status': 'Working', 'run_id': p.RUN,
                   'event_id': self.event['event_id'], 'task_id': p.TASK}
        with self.write('agentic-governance.sqlite') as db:
            self.table(db, 'governance_invocation', {
                'invocation_id': p.INVOCATION, 'automation_run_id': p.RUN,
                'erp_project': p.PROJECT, 'erp_task': p.TASK, 'target_role': 'developer',
                'state': 'uncertain', 'dispatch_attempts': 1, 'submitted_at': 1794734924,
                'lease_fence': 5, 'packet_hash': self.packet_hash,
                'work_packet': p.canonical(self.packet).decode(), 'lease_token': 'fixture-only',
                'agent_id': 'fixture-agent'})
            self.table(db, 'governance_task_lease', {'erp_project': p.PROJECT, 'erp_task': p.TASK,
                'invocation_id': p.INVOCATION, 'fence': 5})
            self.table(db, 'governance_start', {'invocation_id': p.INVOCATION, 'run_id': p.RUN,
                'packet_hash': self.packet_hash, 'lease_fence': 5,
                'lease_token_sha256': hashlib.sha256(b'fixture-only').hexdigest(),
                'event_json': event_json, 'event_sha256': hashlib.sha256(event_json.encode()).hexdigest(),
                'receipt_json': p.canonical(receipt).decode()})
            for table in ('governance_local_result', 'governance_result', 'governance_action',
                          'governance_developer_delivery', 'governance_delivery_step'):
                self.table(db, table, {'invocation_id': 'empty'}, insert=False)
            self.table(db, 'governance_gateway_session', {'session_id': 'fixture-session',
                'invocation_id': p.INVOCATION})
            self.table(db, 'governance_gateway_call', {'session_id': 'fixture-session',
                'operation': 'context.read', 'state': 'completed'})
            for operation in ('repository.deliver', 'repository.delivery_status'):
                db.execute('INSERT INTO governance_gateway_call VALUES (?,?,?)',
                           ('fixture-session', operation, 'completed'))
            self.table(db, 'governance_native_control', {'project': p.PROJECT, 'task': '',
                'sequence': 14, 'command_id': p.CONTROL, 'action': 'retry',
                'applied_state': 'enabled', 'phase': 'acknowledged'})
            self.table(db, 'governance_project_control', {'erp_project': p.PROJECT, 'state': 'enabled'})
            self.table(db, 'governance_task_control', {'project': p.PROJECT, 'task': p.TASK,
                'state': 'enabled'})
            payload = {'policy': 'returned-reply-format.v1', 'protected_file_sha256': p.CAPTURE_SHA,
                'corrected_reply_sha256': p.CORRECTED_SHA, 'control_sequence': 14, 'control_command': p.CONTROL}
            selected = {'seq': 150, 'event_id': 'fixture-selection', 'erp_project': p.PROJECT,
                'invocation_id': p.INVOCATION, 'kind': 'returned_reply_format_selected',
                'actor': 'coordinator', 'payload': p.canonical(payload).decode(),
                'created_at': 1794739534, 'previous_hash': ''}
            selected['event_hash'] = p.digest([selected[key] for key in ('event_id', 'erp_project',
                'invocation_id', 'kind', 'actor', 'payload', 'created_at', 'previous_hash')])
            self.selection_hash = selected['event_hash']
            self.table(db, 'governance_event', selected)
        with self.write('abitdot-primary.sqlite') as db:
            self.table(db, 'dot_primary_runs', {'original_run_id': p.RUN, 'status': 'unknown',
                'response_text': '', 'receipt': ''})
        with self.write('webui.db') as db:
            self.table(db, 'automation_run', {'id': p.RUN, 'automation_id': 'fixture-agent',
                'status': 'success', 'chat_id': '6a7b943a-4ebd-5684-a2d8-d612350a9b71'})
        with self.write('erp-pending-review-qa.sqlite') as db:
            task, run = p.QA_REFERENCE.split(':')
            terminal = {'task': task, 'run': run, 'binding': 'fixture', 'phase': 'done', 'status': 'blocked'}
            self.table(db, 'review', terminal)
            self.qa_hash = hashlib.sha256(json.dumps([terminal], sort_keys=True,
                separators=(',', ':'), default=str).encode()).hexdigest()
        with self.write('maintenance-admission/gate.sqlite') as db:
            self.table(db, 'gate', {'id': 1, 'state': 'draining', 'epoch': 24})
            self.table(db, 'lease', {'id': p.ADMISSION, 'kind': 'governed', 'reference': p.INVOCATION,
                'epoch': 21, 'pid': p.ORIGINAL_OWNER[0], 'process_start': p.ORIGINAL_OWNER[1],
                'state': 'unresolved', 'parent_id': None, 'receipt_sha256': '', 'created_ns': 100})
            self.table(db, 'audit', {'seq': 1, 'kind': 'old', 'payload': '{}'})
        self.patches = [patch.object(p, 'PACKET_SHA', self.packet_hash),
                        patch.object(p, 'SELECTION_HASH', self.selection_hash),
                        patch.object(p, 'QA_RECEIPT_SHA', self.qa_hash),
                        patch.object(p, 'original_owner_gone', return_value=True)]
        for item in self.patches:
            item.start()

    def close(self):
        for item in reversed(self.patches):
            item.stop()

    def write(self, name):
        path = self.directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(path)

    def table(self, db, name, row, insert=True):
        db.execute('CREATE TABLE ' + name + ' (' + ','.join('"' + key + '" ' +
            ('INTEGER' if type(value) is int else 'TEXT') for key, value in row.items()) + ')')
        if insert:
            db.execute('INSERT INTO ' + name + ' VALUES (' + ','.join('?' for _ in row) + ')', tuple(row.values()))

    def db(self, name, write=False):
        assert write is False
        result = sqlite3.connect((self.directory / name).as_uri() + '?mode=ro', uri=True)
        result.row_factory = sqlite3.Row
        result.execute('PRAGMA query_only=ON')
        result.set_trace_callback(lambda sql: self.trace.append((name, sql)))
        return result


class PreservedOriginal(Offline):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledger = TemporaryLedger(self.temp.name)
        self.addCleanup(self.ledger.close)
        self.before = p.capture(self.ledger)

    def test_actual_query_only_transactions_capture_exact_original_without_writes(self):
        self.assertEqual(p.preserve(self.before, p.capture(self.ledger)), 0)
        begun = {name for name, sql in self.ledger.trace if sql == 'BEGIN'}
        self.assertEqual(len(begun), 5)
        self.assertTrue(all(sql.startswith(('SELECT', 'BEGIN')) for _, sql in self.ledger.trace))
        self.assertEqual(self.before['facts']['gate'], p.GATE)
        self.assertEqual(self.before['facts']['selection']['protected_file_sha256'], p.CAPTURE_SHA)

    def test_still_live_original_owner_denied(self):
        with patch.object(p, 'original_owner_gone', return_value=False):
            self.denied(lambda: p.capture(self.ledger), 'OWNER_STILL_LIVE')

    def test_recovery_submission_result_or_scope_change_denied(self):
        for field, value in [('state', 'running'), ('dispatch_attempts', 2), ('lease_fence', 6),
                             ('erp_task', 'foreign-task')]:
            facts = deepcopy(self.before['facts'])
            translated = {'erp_task': 'task'}.get(field, field)
            facts['original'][translated] = value
            self.denied(lambda: p.validate_facts(facts, owner_gone=True), 'SUBMITTED_ORIGINAL')

    def test_mutated_work_packet_bytes_denied_even_with_same_declared_packet_hash(self):
        with self.ledger.write('agentic-governance.sqlite') as db:
            packet = deepcopy(self.ledger.packet)
            packet['fixture'] = False
            db.execute('UPDATE governance_invocation SET work_packet=?', (p.canonical(packet).decode(),))
        self.denied(lambda: p.capture(self.ledger), 'PACKET_CHANGED')

    def test_original_native_receipt_and_v1_selection_drift_denied(self):
        for key in ('protected_file_sha256', 'corrected_reply_sha256', 'control_command'):
            facts = deepcopy(self.before['facts'])
            facts['selection'][key] = 'foreign'
            self.denied(lambda: p.validate_facts(facts, owner_gone=True), 'V1_SELECTION')
        with self.ledger.write('agentic-governance.sqlite') as db:
            db.execute('UPDATE governance_start SET receipt_json=?', ('{"durable":false}',))
        with self.assertRaises((RuntimeError, KeyError)):
            p.capture(self.ledger)

    def test_entire_business_table_audit_provider_or_automation_change_denied(self):
        for key in ('table_hashes', 'audit', 'provider_sha256', 'qa_sha256', 'automation_sha256', 'gate'):
            after = deepcopy(self.before)
            after[key] = {'changed': True}
            self.denied(lambda: p.preserve(self.before, after), 'BUSINESS_OR_GATE')

    def test_original_lease_may_not_settle_transfer_or_disappear(self):
        for mode in ('settled', 'transferred', 'removed'):
            after = deepcopy(self.before)
            if mode == 'removed':
                after['leases'].clear()
            elif mode == 'settled':
                after['leases'][0]['state'] = 'finished'
            else:
                after['leases'][0]['pid'] = 999
            self.denied(lambda: p.preserve(self.before, after))

    def bookkeeping(self, owner=(100, '1000'), created=200):
        after = deepcopy(self.before)
        row = {'id': 'fixture-finished-qa', 'kind': 'qa', 'reference': p.QA_REFERENCE,
            'state': 'finished', 'epoch': 24, 'parent_id': None, 'receipt_sha256': p.QA_RECEIPT_SHA,
            'pid': owner[0], 'process_start': owner[1], 'created_ns': created}
        after['leases'].append(row)
        after['maintenance_audit'].extend([
            {'seq': 2, 'kind': 'admitted_original_recovery', 'payload': json.dumps({
                'lease': row['id'], 'kind': 'qa', 'reference': p.QA_REFERENCE,
                'epoch': 24, 'parent': None, 'original_receipt': p.QA_RECEIPT_SHA})},
            {'seq': 3, 'kind': 'original_receipt_settled', 'payload': json.dumps({
                'lease': row['id'], 'kind': 'qa', 'reference': p.QA_REFERENCE,
                'receipt_sha256': p.QA_RECEIPT_SHA})}])
        return after

    def test_exact_old_and_replacement_qa_terminal_bookkeeping_only(self):
        old = (100, '1000')
        self.assertEqual(p.preserve(self.before, self.bookkeeping(old), allowed_qa_owners={old},
            old_qa_owner=old, old_qa_stop_ns=200), 1)
        new = (101, '1001')
        self.assertEqual(p.preserve(self.before, self.bookkeeping(new),
            allowed_qa_owners={old, new}, old_qa_owner=old, old_qa_stop_ns=150), 1)

    def test_old_qa_bookkeeping_after_stop_or_foreign_owner_denied(self):
        old = (100, '1000')
        self.denied(lambda: p.preserve(self.before, self.bookkeeping(old, 201),
            allowed_qa_owners={old}, old_qa_owner=old, old_qa_stop_ns=200), 'AFTER_STOP')
        self.denied(lambda: p.preserve(self.before, self.bookkeeping((999, '999')),
            allowed_qa_owners={old}), 'KNOWN_FINISHED')

    def test_active_qa_other_task_receipt_epoch_or_new_governed_admission_denied(self):
        for field, value in [('state', 'active'), ('reference', 'other'), ('receipt_sha256', '0' * 64),
                             ('epoch', 25), ('kind', 'governed'), ('parent_id', 'other')]:
            after = self.bookkeeping()
            after['leases'][-1][field] = value
            self.denied(lambda: p.preserve(self.before, after,
                allowed_qa_owners={(100, '1000')}), 'KNOWN_FINISHED')

    def test_missing_altered_or_unrelated_maintenance_audit_denied(self):
        for mode in ('missing', 'unrelated', 'changed'):
            after = self.bookkeeping()
            if mode == 'missing':
                after['maintenance_audit'].pop()
            elif mode == 'unrelated':
                after['maintenance_audit'].append({'seq': 4, 'kind': 'foreign', 'payload': '{}'})
            else:
                after['maintenance_audit'][-1]['payload'] = '{}'
            self.denied(lambda: p.preserve(self.before, after,
                allowed_qa_owners={(100, '1000')}))


class LifecycleAndFilesystem(Offline):
    def test_graceful_loaded_policy_must_precede_stop(self):
        old = units()
        guarded = deepcopy(old)
        for unit, value in guarded.items():
            value['dropins'].append(str(core.NOKILL[unit]))
            value['SendSIGKILL'] = 'no'
        with patch.object(core, 'static_unit_state', side_effect=lambda unit: guarded[unit]):
            core.check_units(old, guards=True)
            guarded[m.MAIN]['SendSIGKILL'] = 'yes'
            self.denied(lambda: core.check_units(old, guards=True), 'GRACEFUL_POLICY')

    def test_changed_unit_environment_or_extra_dropin_denied(self):
        for field, value in [('fixed_properties_sha256', 'b' * 64), ('dropins', ['/etc/foreign.conf']),
                             ('KillMode', 'process'), ('KillSignal', '9')]:
            old = units()
            actual = deepcopy(old)
            actual[m.MAIN][field] = value
            with patch.object(core, 'static_unit_state', side_effect=lambda unit: actual[unit]):
                self.denied(lambda: core.check_units(old), 'UNIT_CONFIGURATION')

    def test_unknown_stop_ack_never_resends_and_readback_requires_gone_empty_group(self):
        records = {}
        def record(name, value):
            if name in records:
                raise RuntimeError('ORIGINAL_INTENT_EXISTS')
            records[name] = value
        send = Mock(return_value={'ack_unknown': True})
        core.once_service(record, 'main-stop', 'stop', m.MAIN, send)
        self.denied(lambda: core.once_service(record, 'main-stop', 'stop', m.MAIN, send), 'INTENT_EXISTS')
        self.assertEqual(send.call_count, 1)
        self.assertFalse(m.stopped_state({'ActiveState': 'inactive', 'MainPID': '0'}, True, True))
        self.assertFalse(m.stopped_state({'ActiveState': 'inactive', 'MainPID': '0'}, False, False))
        self.assertTrue(m.stopped_state({'ActiveState': 'inactive', 'MainPID': '0'}, False, True))

    def test_readback_denial_prevents_next_mutation(self):
        records, sent = [], []
        changed = {'state': 'open', 'epoch': 25, 'active_admissions': 1}
        def step():
            m.exact_gate(changed)
            core.once_service(lambda *args: records.append(args), 'main-start', 'start',
                              m.MAIN, lambda: sent.append('start'))
        self.denied(step, 'DRAINING24')
        self.assertEqual((records, sent), ([], []))

    def test_replacement_process_cgroup_argv_uid_and_birth_preserved(self):
        original = {'pid': 10, 'start_ticks': '100', 'uid': 999, 'gid': 988,
            'parent': 1, 'exe': '/fixture/python', 'argv_sha256': 'a' * 64,
            'cgroup': 'fixture-slice'}
        new = {**original, 'pid': 11, 'start_ticks': '101'}
        core.replacement_process(original, new)
        for field, value in [('pid', 10), ('start_ticks', '100'), ('uid', 0),
                             ('argv_sha256', 'b' * 64), ('cgroup', 'other')]:
            wrong = {**new, field: value}
            self.denied(lambda: core.replacement_process(original, wrong))

    def test_atomic_existing_leaf_uses_metadata_and_parent_cas(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'utils/agentic_contracts/developer_delivery.py'
            target.parent.mkdir(parents=True)
            target.write_bytes(b'old')
            original, parent = core.file_proof(target), core.parent_proof(target.parent)
            with patch.object(core, 'RUNTIME', root), patch.object(core, 'TARGETS',
                 {'utils/agentic_contracts/developer_delivery.py'}), patch.object(core, 'NEW_FILES', set()):
                core.atomic_existing_leaf(target, b'new', parent, original, core.sha(b'new'))
            expected = {**original, 'sha256': core.sha(b'new'), 'size': 3}
            self.assertEqual(core.file_proof(target), expected)

    def test_source_byte_or_parent_drift_denies_before_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'utils/agentic_contracts/developer_delivery.py'
            target.parent.mkdir(parents=True)
            target.write_bytes(b'old')
            original, parent = core.file_proof(target), core.parent_proof(target.parent)
            target.write_bytes(b'changed')
            with patch.object(core, 'RUNTIME', root), patch.object(core, 'TARGETS',
                 {'utils/agentic_contracts/developer_delivery.py'}), patch.object(core, 'NEW_FILES', set()):
                self.denied(lambda: core.atomic_existing_leaf(target, b'new', parent, original,
                    core.sha(b'new')), 'CAS_CHANGED')
                fresh = core.file_proof(target)
                wrong = {**parent, 'ino': parent['ino'] + 1}
                self.denied(lambda: core.atomic_existing_leaf(target, b'new', wrong, fresh,
                    core.sha(b'new')), 'CAS_CHANGED')
            self.assertEqual(target.read_bytes(), b'changed')

    def test_cache_scope_skips_json_and_rejects_symlink_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'utils/agentic_contracts/developer_delivery.py'
            target.parent.mkdir(parents=True)
            target.write_text('pass')
            schema = root / core.RUNTIME_JSON_PATH
            schema.parent.mkdir(parents=True)
            schema.write_text('{}')
            cache = target.parent / '__pycache__'
            cache.mkdir()
            one = cache / 'developer_delivery.cpython-311.pyc'
            one.write_bytes(b'fixture')
            foreign = cache / 'other.cpython-311.pyc'
            foreign.write_bytes(b'preserved')
            with patch.object(core, 'RUNTIME', root):
                self.assertEqual(core.selected_caches([target, schema]), [one])
                one.unlink()
                foreign.unlink()
                cache.rmdir()
                cache.symlink_to(root / 'foreign')
                self.denied(lambda: core.selected_caches([target, schema]), 'BYTECODE_PARENT')

    def test_pinned_source_rejects_drift_and_symlink_without_import(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / 'safe.py'
            file.write_bytes(b'VALUE = 1\n')
            module = m.pinned_module('fixture_source', file, m.sha(file.read_bytes()))
            self.assertEqual(module.VALUE, 1)
            self.denied(lambda: m.pinned_module('wrong_pin', file, '0' * 64), 'SOURCE_CHANGED')
            link = file.with_name('link.py')
            link.symlink_to(file)
            self.denied(lambda: m.pinned_module('symlink', link, m.sha(file.read_bytes())),
                        'EXACT_SUPPORT')

    def test_storage_reserves_eleven_backups_plus_sources_caches_and_private_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / 'protected'
            file.write_bytes(b'12345')
            fake_core = types.SimpleNamespace(
                protected_history_archive=lambda baseline: {'files': {str(file): {}}},
                protected_format_capture=lambda baseline: {'path': str(file)},
                selected_caches=lambda paths: [])
            fake_relay = types.SimpleNamespace(capacity=lambda hold, parent: {
                'database_count': 11, 'backup_reserve_bytes': 500,
                'minimum_remaining_bytes': 1000})
            baseline = {'protected_configuration': {str(file): {}}}
            with patch.object(m.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=100000000)):
                result = m.storage_budget(fake_core, fake_relay, None, baseline, manifest(),
                                          Path(directory), Path(directory) / 'operation')
            self.assertEqual(result['database_count'], 11)
            self.assertGreater(result['source_cache_protected_evidence_reserve_bytes'], 67108864)
            with patch.object(m.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=1500)):
                self.denied(lambda: m.storage_budget(fake_core, fake_relay, None, baseline,
                    manifest(), Path(directory), Path(directory) / 'operation'), 'EVIDENCE_RESERVE')



class CompleteCohortAndBackup(Offline):
    def fixture(self, directory):
        root = Path(directory)
        release = root / 'releases/fixture'
        runtime = release / 'runtime/open_webui'
        (root / 'current').symlink_to(release, target_is_directory=True)
        names = ['utils/agentic_contracts/developer_delivery.py', 'routers/agentic_governance.py']
        names.extend('utils/fixture_' + str(i) + '.py' for i in range(142))
        names.extend(['utils/agentic_contracts/schemas/governance_v1.schema.json',
                      'utils/agentic_contracts/fixture_a.json', 'utils/agentic_contracts/fixture_b.json'])
        for name in names:
            file = runtime / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(b'{}' if file.suffix == '.json' else b'pass\n')
        base = {'core_files': {str(runtime / name): core.file_proof(runtime / name) for name in names}}
        value = manifest()
        for item in value['files']:
            file = runtime / item['relative_path']
            item['original'] = core.file_proof(file)
            item['parent'] = core.parent_proof(file.parent)
            candidate = b'changed = True\n' if file.suffix == '.py' else b'{"changed":true}'
            item['candidate_sha256'] = core.sha(candidate)
            item['candidate_bytes'] = len(candidate)
        return root, runtime, base, value

    def test_all_147_files_and_each_selected_partial_install_are_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            root, runtime, base, value = self.fixture(directory)
            with patch.object(m, 'ROOT', root), patch.object(m, 'RUNTIME', runtime):
                m.source_cohort(core, base, value)
                item = value['files'][0]
                target = runtime / item['relative_path']
                target.write_bytes(b'changed = True\n')
                os.utime(target, ns=(item['original']['mtime_ns'], item['original']['mtime_ns']))
                m.source_cohort(core, base, value, {item['relative_path']})
                self.denied(lambda: m.source_cohort(core, base, value), 'SOURCE_DRIFT')

    def test_extra_file_unselected_drift_or_release_pointer_change_denied(self):
        for mode in ('extra', 'unselected', 'release'):
            with tempfile.TemporaryDirectory() as directory:
                root, runtime, base, value = self.fixture(directory)
                if mode == 'extra':
                    (runtime / 'utils/foreign.py').write_text('pass')
                elif mode == 'unselected':
                    (runtime / 'utils/fixture_1.py').write_text('changed = True')
                else:
                    (root / 'current').unlink()
                    (root / 'current').symlink_to(root / 'releases/foreign')
                with patch.object(m, 'ROOT', root), patch.object(m, 'RUNTIME', runtime):
                    self.denied(lambda: m.source_cohort(core, base, value))

    def test_unselected_parent_symlink_denied_without_following_foreign_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root, runtime, base, value = self.fixture(directory)
            parent = runtime / 'utils/agentic_contracts'
            moved = runtime / 'utils/foreign_contracts'
            parent.rename(moved)
            parent.symlink_to(moved)
            with patch.object(m, 'ROOT', root), patch.object(m, 'RUNTIME', runtime):
                self.denied(lambda: m.source_cohort(core, base, value))

    def test_eleven_actual_sqlite_backup_api_copies_are_checked_without_source_writes(self):
        relay = m.pinned_module('finite_backup_support', core.RELAY_HELPER, core.RELAY_HELPER_SHA)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data, destination = root / 'data', root / 'operation'
            data.mkdir()
            destination.mkdir()
            for name in relay.DATABASES:
                file = data / name
                file.parent.mkdir(parents=True, exist_ok=True)
                with sqlite3.connect(file) as db:
                    db.execute('CREATE TABLE fixture(value TEXT)')
                    db.execute('INSERT INTO fixture VALUES (?)', (name,))
            before = {name: core.sha((data / name).read_bytes()) for name in relay.DATABASES}
            def db(name):
                connection = sqlite3.connect((data / name).as_uri() + '?mode=ro', uri=True)
                connection.execute('PRAGMA query_only=ON')
                return connection
            def private_new(path, raw):
                with path.open('xb') as stream:
                    stream.write(raw)
                path.chmod(0o600)
            with patch.object(relay, 'DATA', data):
                result = relay.backups(types.SimpleNamespace(db=db),
                    types.SimpleNamespace(private_new=private_new), destination)
            self.assertEqual(result['backup_count'], 11)
            self.assertEqual(result['database_restores'], 0)
            self.assertEqual(before, {name: core.sha((data / name).read_bytes()) for name in relay.DATABASES})
            for row in result['databases']:
                file = destination / 'consistent-databases' / row['database'].replace('/', '__')
                self.assertEqual(core.sha(file.read_bytes()), row['sha256'])
                with sqlite3.connect(file.as_uri() + '?mode=ro', uri=True) as copy:
                    self.assertEqual(copy.execute('PRAGMA quick_check').fetchone()[0], 'ok')
                    self.assertEqual(copy.execute('SELECT value FROM fixture').fetchone()[0], row['database'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
