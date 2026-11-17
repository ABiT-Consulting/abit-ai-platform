"""Pure guard fixtures. No network, service, process signals, live files or databases."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('relay_leaf_guard_subject', HERE / 'apply_relay_feed.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def original_fixture():
    def receipt(event, status):
        return json.dumps({'durable': True, 'duplicate': False, 'event_id': event,
                           'status': status, 'run_id': m.RUN, 'task_id': 'TASK-2026-06200'})
    value = {
        'invocation': [{'invocation_id': m.INVOCATION, 'automation_run_id': m.RUN,
                        'erp_project': 'PROJ-0198', 'erp_task': 'TASK-2026-06200',
                        'state': 'execution_finished', 'submitted_at': 1794732638,
                        'dispatch_attempts': 1, 'reason': None}],
        'start': [{'invocation_id': m.INVOCATION, 'run_id': m.RUN,
                   'event_sha256': m.START_SHA,
                   'receipt_json': receipt('agov-start-ff4f815af2fd97f9f9423cd2b84c2772fc8f51ae5dbb3d10', 'Working')}],
        'automation': [{'id': m.RUN, 'status': 'success', 'chat_id': 'original-chat', 'error': None}],
        'leases': [],
        'original_leases': [
            {'id': 'old-admission', 'kind': 'governed', 'reference': m.INVOCATION,
             'state': 'finished', 'epoch': 17, 'pid': 505071, 'process_start': '68273241'},
            {'id': 'resumed-admission', 'kind': 'governed', 'reference': m.INVOCATION,
             'state': 'finished', 'epoch': 19, 'pid': 923235, 'process_start': '68517753'}],
        'gate': [{'state': 'draining', 'epoch': 20}],
        'gate_full': [{'id': 1, 'state': 'draining', 'epoch': 20}],
        'active_invocations': [], 'running_automations': [],
        'result': [{'invocation_id': m.INVOCATION, 'run_id': m.RUN, 'phase': 'published',
                    'result_sha256': m.RESULT_SHA, 'result_json': '{"outcome":"blocked"}',
                    'receipt_json': receipt('agov-result-98c7fd3b1f4391b5e0fe09cfb89cf03d6ac9f0295025703c', 'Blocked')}],
        'local_result': [{'invocation_id': m.INVOCATION, 'run_id': m.RUN,
                          'result_json': '{"outcome":"blocked"}'}],
        'gateway_sessions': [{'invocation_id': m.INVOCATION, 'state': 'admitted'}],
        'gateway_calls': [{'operation': op, 'state': 'completed'} for op in
                          ['context.read', 'repository.deliver', 'repository.delivery_status',
                           'repository.delivery_status']],
        'dot': [{'original_run_id': m.RUN, 'status': 'delegated'}], 'relay': [],
        'action_count': 0, 'current_original_links': 0, 'legacy_qa_work': 0,
    }
    return value, {'original_rows': m.original_row_pins(value)}


def gate_fixture():
    before = {'gate': [{'id': 1, 'state': 'draining', 'epoch': 20}],
              'lease': [{'id': 'governed-original', 'state': 'finished'}],
              'audit': [{'seq': 1, 'kind': 'old', 'payload': '{}'}]}
    qa = {'pid': 923245, 'start_ticks': '68517762'}
    after = copy.deepcopy(before)
    after['lease'].append({'id': 'passive-qa', 'kind': 'qa', 'state': 'finished',
                           'epoch': 20, 'reference': m.QA_REFERENCE, 'receipt_sha256': m.QA_RECEIPT,
                           'pid': qa['pid'], 'process_start': qa['start_ticks'], 'parent_id': None})
    for seq, kind, extra in [(2, 'admitted_original_recovery',
                              {'epoch': 20, 'parent': None, 'original_receipt': m.QA_RECEIPT}),
                             (3, 'original_receipt_settled', {'receipt_sha256': m.QA_RECEIPT})]:
        after['audit'].append({'seq': seq, 'kind': kind,
                               'payload': json.dumps({'lease': 'passive-qa', 'kind': 'qa',
                                                      'reference': m.QA_REFERENCE, **extra})})
    return before, after, qa


class RelayLeafGuards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.network = patch.object(socket.socket, 'connect', side_effect=AssertionError('NETWORK_DENIED'))
        cls.network.start()

    @classmethod
    def tearDownClass(cls):
        cls.network.stop()

    def assert_denied(self, call, message=None):
        with self.assertRaises(RuntimeError) as caught:
            call()
        if message:
            self.assertIn(message, str(caught.exception))

    def test_positive_idle_health(self):
        value = dict(ok=True, ready=True, authenticated=True, target_ready=True,
                     active_relay_turns=0, browser_busy=False, isolated_chat_tabs=16)
        self.assertEqual(m.health_check(value), value)

    def test_missing_activity_counter_is_not_idle(self):
        self.assert_denied(lambda: m.health_check(dict(ok=True, ready=True, authenticated=True,
                           target_ready=True, browser_busy=False, isolated_chat_tabs=16)))

    def test_busy_or_unauthenticated_health_is_denied(self):
        base = dict(ok=True, ready=True, authenticated=True, target_ready=True,
                    active_relay_turns=0, browser_busy=False, isolated_chat_tabs=16)
        for change in ({'active_relay_turns': 1}, {'browser_busy': True}, {'authenticated': False},
                       {'ready': False}, {'isolated_chat_tabs': 15}, {'active_relay_turns': False}):
            with self.subTest(change=change):
                self.assert_denied(lambda: m.health_check({**base, **change}))

    def test_only_exact_settled_published_blocked_original_is_allowed(self):
        value, baseline = original_fixture()
        result = m.validate_original(value, baseline)
        self.assertEqual(result['active_admissions'], 0)
        self.assertEqual(result['settled_original_admissions'], 2)
        self.assertEqual(result['completed_gateway_calls'], 4)
        self.assertEqual(result['outcome'], 'blocked')
        self.assertTrue(result['submitted'])

    def test_missing_or_ambiguous_original_rows_deny(self):
        for group in ('invocation', 'start', 'automation', 'result', 'local_result',
                      'gateway_sessions', 'dot'):
            with self.subTest(group=group):
                value, baseline = original_fixture()
                value[group] = []
                self.assert_denied(lambda: m.validate_original(value, baseline), 'EXACT_SINGLE')
                value, baseline = original_fixture()
                value[group].append(copy.deepcopy(value[group][0]))
                self.assert_denied(lambda: m.validate_original(value, baseline), 'EXACT_SINGLE')

    def test_missing_receipt_or_submission_marker_denies(self):
        for group, field, changed in [('start', 'receipt_json', None),
                                     ('result', 'receipt_json', None),
                                     ('invocation', 'submitted_at', None)]:
            with self.subTest(group=group, field=field):
                value, baseline = original_fixture()
                value[group][0][field] = changed
                self.assert_denied(lambda: m.validate_original(value, baseline))

    def test_different_original_state_run_task_or_dispatch_denies(self):
        for field, changed in [('state', 'running'), ('state', 'uncertain'), ('state', 'handed_off'),
                               ('automation_run_id', 'another-run'), ('dispatch_attempts', 2),
                               ('erp_project', 'another-project'), ('erp_task', 'another-task')]:
            with self.subTest(field=field, changed=changed):
                value, baseline = original_fixture()
                value['invocation'][0][field] = changed
                self.assert_denied(lambda: m.validate_original(value, baseline))

    def test_settled_admissions_cannot_be_rebound_or_reactivated(self):
        for index, change in [(0, {'pid': 923235, 'process_start': '68517753'}),
                              (1, {'state': 'active'}), (0, {'state': 'unresolved'}),
                              (1, {'epoch': 20})]:
            with self.subTest(index=index, change=change):
                value, baseline = original_fixture()
                value['original_leases'][index].update(change)
                self.assert_denied(lambda: m.validate_original(value, baseline), 'ADMISSIONS_CHANGED')

    def test_any_running_or_admitted_work_denies(self):
        for group, changed in [('leases', {'id': 'other'}), ('active_invocations', m.INVOCATION),
                               ('running_automations', m.RUN)]:
            with self.subTest(group=group):
                value, baseline = original_fixture()
                value[group].append(changed)
                self.assert_denied(lambda: m.validate_original(value, baseline), 'RUNNING_OR_ADMITTED')

    def test_any_action_or_current_link_denies(self):
        for key in ('action_count', 'current_original_links', 'legacy_qa_work'):
            with self.subTest(key=key):
                value, baseline = original_fixture()
                value[key] = 1
                self.assert_denied(lambda: m.validate_original(value, baseline), 'PROVIDER_OR_LINKED')

    def test_open_sealed_or_changed_epoch_denies(self):
        for gate in ({'state': 'open', 'epoch': 20}, {'state': 'sealed', 'epoch': 20},
                     {'state': 'draining', 'epoch': 18}, {'state': 'draining', 'epoch': 21}):
            with self.subTest(gate=gate):
                value, baseline = original_fixture()
                value['gate'] = [gate]
                self.assert_denied(lambda: m.validate_original(value, baseline), 'BLOCKED_GATE')

    def test_unlisted_immutable_original_field_change_denies(self):
        for group in ('invocation', 'start', 'automation', 'original_leases', 'result',
                      'local_result', 'gateway_sessions', 'gateway_calls', 'dot'):
            with self.subTest(group=group):
                value, baseline = original_fixture()
                value[group][0]['unreviewed_field'] = 'changed'
                self.assert_denied(lambda: m.validate_original(value, baseline), 'IMMUTABLE_ROW')

    def test_incomplete_or_additional_gateway_operation_denies(self):
        value, baseline = original_fixture()
        value['gateway_calls'][0]['state'] = 'uncertain'
        self.assert_denied(lambda: m.validate_original(value, baseline), 'GATEWAY_WORK')
        value, baseline = original_fixture()
        value['gateway_calls'].append({'operation': 'artifact.put', 'state': 'completed'})
        self.assert_denied(lambda: m.validate_original(value, baseline), 'GATEWAY_WORK')

    def test_nonterminal_dot_or_new_relay_record_denies(self):
        value, baseline = original_fixture()
        value['dot'][0]['status'] = 'unknown'
        self.assert_denied(lambda: m.validate_original(value, baseline), 'PROVIDER_OR_LINKED')
        value, baseline = original_fixture()
        value['relay'].append({'status': 'running'})
        self.assert_denied(lambda: m.validate_original(value, baseline), 'PROVIDER_OR_LINKED')

    def test_publication_phase_hash_or_outcome_change_denies(self):
        for field, changed in [('phase', 'prepared'), ('result_sha256', '0' * 64),
                               ('result_json', '{"outcome":"ready_for_review"}')]:
            with self.subTest(field=field):
                value, baseline = original_fixture()
                value['result'][0][field] = changed
                self.assert_denied(lambda: m.validate_original(value, baseline), 'PUBLISHED_RESULT')

    def test_native_receipt_identity_and_durability_are_exact(self):
        for group in ('start', 'result'):
            for field, changed in [('durable', False), ('duplicate', True),
                                   ('event_id', 'other'), ('run_id', 'other'),
                                   ('task_id', 'other'), ('status', 'Closed')]:
                with self.subTest(group=group, field=field):
                    value, baseline = original_fixture()
                    receipt = json.loads(value[group][0]['receipt_json'])
                    receipt[field] = changed
                    value[group][0]['receipt_json'] = json.dumps(receipt)
                    self.assert_denied(lambda: m.validate_original(value, baseline), 'DURABLE_RECEIPT')

    def test_missing_or_additional_settled_admission_denies(self):
        for change in ('remove', 'duplicate'):
            value, baseline = original_fixture()
            if change == 'remove':
                value['original_leases'].pop()
            else:
                value['original_leases'].append(copy.deepcopy(value['original_leases'][1]))
            self.assert_denied(lambda: m.validate_original(value, baseline), 'ADMISSIONS_CHANGED')

    def test_unsuccessful_or_unlinked_automation_denies(self):
        for change in ({'status': 'running'}, {'chat_id': None}, {'error': 'failure'}):
            with self.subTest(change=change):
                value, baseline = original_fixture()
                value['automation'][0].update(change)
                self.assert_denied(lambda: m.validate_original(value, baseline), 'AUTOMATION_CHANGED')

    def test_no_history_change_passes(self):
        before, _, qa = gate_fixture()
        self.assertEqual(m.preserve_gate_history(before, copy.deepcopy(before), qa),
                         {'prior_rows_unchanged': True, 'passive_qa_pairs_appended': 0})

    def test_exact_passive_qa_pair_can_append_without_editing_old_rows(self):
        before, after, qa = gate_fixture()
        self.assertEqual(m.preserve_gate_history(before, after, qa)['passive_qa_pairs_appended'], 1)
        self.assertEqual(before['lease'], [{'id': 'governed-original', 'state': 'finished'}])

    def test_history_deletion_or_mutation_denies(self):
        for table in ('lease', 'audit'):
            with self.subTest(table=table):
                before, after, qa = gate_fixture()
                after[table][0]['unexpected'] = True
                self.assert_denied(lambda: m.preserve_gate_history(before, after, qa), 'HISTORICAL_GATE_ROW')
                after[table].pop(0)
                self.assert_denied(lambda: m.preserve_gate_history(before, after, qa), 'HISTORICAL_GATE_ROW')

    def test_unfinished_or_foreign_qa_addition_denies(self):
        for change in ({'state': 'active'}, {'pid': 1}, {'process_start': 'other'},
                       {'reference': 'another-task'}, {'receipt_sha256': '0' * 64},
                       {'kind': 'governed'}, {'epoch': 19}):
            with self.subTest(change=change):
                before, after, qa = gate_fixture()
                after['lease'][1].update(change)
                self.assert_denied(lambda: m.preserve_gate_history(before, after, qa), 'ADMISSION_ADDITION')

    def test_missing_duplicate_or_unmatched_audit_denies(self):
        before, after, qa = gate_fixture()
        after['audit'].pop()
        self.assert_denied(lambda: m.preserve_gate_history(before, after, qa), 'INCOMPLETE')
        before, after, qa = gate_fixture()
        extra = copy.deepcopy(after['audit'][1])
        extra['seq'] = 4
        after['audit'].append(extra)
        self.assert_denied(lambda: m.preserve_gate_history(before, after, qa), 'DUPLICATE')
        before, after, qa = gate_fixture()
        after['lease'].pop()
        self.assert_denied(lambda: m.preserve_gate_history(before, after, qa), 'AUDIT_ADDITION')

    def test_changed_gate_row_denies_even_with_finished_pair(self):
        before, after, qa = gate_fixture()
        after['gate'][0]['epoch'] = 19
        self.assert_denied(lambda: m.preserve_gate_history(before, after, qa), 'GATE_CHANGED')

    def test_term_intent_precedes_exactly_one_pidfd_signal(self):
        events = []
        def record(name, value):
            events.append(('record', name))
        def sender(fd, sig, info, flags):
            events.append(('signal', fd, sig, info, flags))
        m.send_term_once(77, record, sender)
        self.assertEqual(events, [('record', 'node-term-intent.json'),
                                   ('signal', 77, signal.SIGTERM, None, 0),
                                   ('record', 'node-term-call-returned.json')])

    def test_signal_error_does_not_resend_or_claim_ack(self):
        events = []
        def record(name, value):
            events.append(('record', name))
        def lost_ack(*args):
            events.append(('signal', args[0]))
            raise OSError('lost acknowledgement')
        with self.assertRaises(OSError):
            m.send_term_once(77, record, lost_ack)
        self.assertEqual(events, [('record', 'node-term-intent.json'), ('signal', 77)])

    def test_intent_write_error_sends_nothing(self):
        calls = []
        def fail(*args):
            raise OSError('intent cannot persist')
        with self.assertRaises(OSError):
            m.send_term_once(77, fail, lambda *args: calls.append(args))
        self.assertEqual(calls, [])

    def test_input_symlink_and_wrong_hash_deny(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / 'real'
            path.write_bytes(b'proof')
            (root / 'link').symlink_to(path)
            self.assert_denied(lambda: m.read(root / 'link'), 'CANONICAL')
            self.assert_denied(lambda: m.read(path, '0' * 64), 'PINNED')

    def test_atomic_leaf_preserves_exact_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'detector.cjs'
            path.write_bytes(b'old')
            path.chmod(0o640)
            os.utime(path, ns=(1_700_000_000_000_000_000, 1_700_000_001_000_000_000))
            try:
                os.setxattr(path, 'user.test_relay_leaf', b'fixture')
            except OSError:
                pass
            prior = m.file_proof(path)
            raw = b'reviewed candidate'
            baseline = json.loads(json.dumps({'relay_code': {str(path): prior}}))
            with patch.object(m, 'TARGET', path), patch.object(m, 'AFTER', m.sha(raw)):
                m.atomic_leaf(raw, baseline)
                self.assertEqual(m.file_proof(path), {**prior, 'sha256': m.sha(raw), 'size': len(raw)})

    def test_atomic_leaf_denies_drift_before_replace(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'detector.cjs'
            path.write_bytes(b'old')
            baseline = {'relay_code': {str(path): m.file_proof(path)}}
            path.write_bytes(b'changed')
            with patch.object(m, 'TARGET', path), patch.object(m, 'AFTER', m.sha(b'candidate')):
                self.assert_denied(lambda: m.atomic_leaf(b'candidate', baseline), 'CAS_PRECONDITION')
            self.assertEqual(path.read_bytes(), b'changed')

    def test_watchdog_replacement_requires_new_birth_and_same_identity(self):
        old = {'pid': 10, 'start_ticks': '100', 'uid': 999, 'gid': 988,
               'parent': 2, 'exe': '/usr/local/bin/node', 'argv_sha256': 'a' * 64,
               'cgroup': ['relay']}
        good = {**old, 'pid': 11, 'start_ticks': '101'}
        with patch.object(m, 'read', return_value=b'11'), patch.object(m, 'process', return_value=good):
            self.assertEqual(m.replacement_node({}, old), good)
        for changed in ({'start_ticks': '99'}, {'parent': 3}, {'argv_sha256': 'b' * 64}):
            with self.subTest(changed=changed), patch.object(m, 'read', return_value=b'11'), \
                 patch.object(m, 'process', return_value={**good, **changed}):
                self.assert_denied(lambda: m.replacement_node({}, old), 'REPLACEMENT_IDENTITY')


if __name__ == '__main__':
    unittest.main(verbosity=2)
