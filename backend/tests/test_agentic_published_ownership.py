"""Actual ownership guard with real temporary SQLite ledgers; no application imports."""
import copy
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid

SOURCE = Path(__file__).parents[1] / 'open_webui/utils/agentic_ownership.py'
spec = importlib.util.spec_from_file_location('actual_published_ownership', SOURCE)
ownership = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ownership)


class PublishedOwnership(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.task, self.project, self.agent, self.owner = 'TASK-TEST', 'PROJ-TEST', 'developer-test', 'owner-test'
        self.invocation = 'agov-test-published-original'
        self.run = str(uuid.uuid5(uuid.NAMESPACE_URL, 'https://abit.ai/agentic/run/' + self.invocation))
        self.binding = {'project_id': self.project, 'role': 'developer', 'agent_id': self.agent,
                        'erp_user': 'ai-developer@example.invalid'}
        self.packet = {'schema_version': 1, 'project_id': self.project, 'task_id': self.task,
                       'task_revision': 'revision', 'binding': self.binding}
        self.packet['content_sha256'] = ownership._digest(self.packet)
        self.result = {'invocation_id': self.invocation, 'project_id': self.project, 'task_id': self.task,
                       'task_revision': 'revision', 'work_packet_digest': self.packet['content_sha256'],
                       'actor': self.binding, 'outcome': 'ready_for_review'}
        self.event = {'event_id': 'agov-result-' + ownership._digest([self.invocation, self.result])[:48],
                      'type': 'run.ready', 'invocation_id': self.invocation, 'run_id': self.run,
                      'project_id': self.project, 'task_id': self.task, 'task_revision': 'revision',
                      'work_packet_digest': self.packet['content_sha256'], 'agent_id': self.agent,
                      'role': 'developer', 'result_contract': self.result}
        self.receipt = {'event_id': self.event['event_id'], 'run_id': self.run, 'task_id': self.task,
                        'durable': True, 'status': 'Pending Review', 'modified': 'synthetic-modified'}
        self.meta = {'governance_managed': True, 'agentic_project': self.project,
                     'agentic_role': 'developer', 'erp_role_user': self.binding['erp_user']}
        self.envelope = {'owner_id': self.owner, 'automation_id': self.agent, 'run_id': self.run,
                         'idempotency_key': __import__('hashlib').sha256(
                             json.dumps([self.owner, self.agent, self.run], separators=(',', ':')).encode()).hexdigest(),
                         'task': {'meta': {'governance_managed': True, 'erp_task': self.task,
                                         'governance_project': self.project, 'erp_role': 'developer',
                                         'governance_packet_digest': self.packet['content_sha256']}}}
        with self.connect('webui.db') as d:
            d.executescript('CREATE TABLE automation(id TEXT,user_id TEXT,meta TEXT);'
                            'CREATE TABLE automation_run(id TEXT,status TEXT,automation_id TEXT);')
            d.execute('INSERT INTO automation VALUES(?,?,?)', (self.agent, self.owner, json.dumps(self.meta)))
            d.execute('INSERT INTO automation_run VALUES(?,?,?)', (self.run, 'success', self.agent))
        with self.connect('abitdot-primary.sqlite') as d:
            d.execute('CREATE TABLE dot_primary_runs(owner TEXT,automation_id TEXT,original_run_id TEXT,envelope_json TEXT,status TEXT,response_text TEXT,receipt TEXT)')
            d.execute('INSERT INTO dot_primary_runs VALUES(?,?,?,?,?,?,?)',
                      (self.owner, self.agent, self.run, json.dumps(self.envelope), 'unknown', None, None))
        with self.connect('agentic-governance.sqlite') as d:
            d.executescript('''CREATE TABLE governance_invocation(invocation_id TEXT,automation_run_id TEXT,state TEXT,
              target_role TEXT,erp_task TEXT,erp_project TEXT,agent_id TEXT,submitted_at INTEGER,
              dispatch_attempts INTEGER,work_packet TEXT,packet_hash TEXT,task_revision TEXT);
              CREATE TABLE governance_result(invocation_id TEXT,run_id TEXT,phase TEXT,result_json TEXT,
              result_sha256 TEXT,event_json TEXT,receipt_json TEXT);
              CREATE TABLE governance_gateway_session(session_id TEXT,invocation_id TEXT);
              CREATE TABLE governance_gateway_call(session_id TEXT,state TEXT);''')
            d.execute('INSERT INTO governance_invocation VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                      (self.invocation, self.run, 'handed_off', 'developer', self.task, self.project, self.agent,
                       1, 1, json.dumps(self.packet), ownership._digest(self.packet), 'revision'))
            d.execute('INSERT INTO governance_result VALUES(?,?,?,?,?,?,?)',
                      (self.invocation, self.run, 'published', json.dumps(self.result),
                       ownership._digest(self.result), json.dumps(self.event), json.dumps(self.receipt)))
            d.execute('INSERT INTO governance_gateway_session VALUES(?,?)', ('session', self.invocation))
            d.execute('INSERT INTO governance_gateway_call VALUES(?,?)', ('session', 'completed'))

    def connect(self, name):
        return sqlite3.connect(self.root / name)

    def update(self, database, sql, args=()):
        with self.connect(database) as d:
            d.execute(sql, args)

    def blocked(self):
        self.assertEqual(ownership.external_execution(self.task, self.root),
                         'An original ERP/Dot request is active or unresolved')

    def test_settled_original_releases_guard_without_changing_provider_or_ledgers(self):
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        self.assertIsNone(ownership.external_execution(self.task, self.root))
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})
        with self.connect('abitdot-primary.sqlite') as d:
            self.assertEqual(d.execute('SELECT status,response_text,receipt FROM dot_primary_runs').fetchone(),
                             ('unknown', None, None))
        for claims in (2, 3):
            self.update('agentic-governance.sqlite', 'UPDATE governance_invocation SET dispatch_attempts=?', (claims,))
            self.assertIsNone(ownership.external_execution(self.task, self.root))

    def test_mismatched_or_nondurable_native_receipts_keep_veto(self):
        for field, value in [('durable', False), ('status', 'Working'), ('event_id', 'foreign'),
                             ('run_id', 'foreign'), ('task_id', 'TASK-OTHER'), ('modified', '')]:
            with self.subTest(field=field):
                receipt = {**self.receipt, field: value}
                self.update('agentic-governance.sqlite', 'UPDATE governance_result SET receipt_json=?',
                            (json.dumps(receipt),))
                self.blocked()

    def test_active_transport_governance_and_unsettled_call_still_block(self):
        for status in ('prepared', 'dispatching'):
            self.update('abitdot-primary.sqlite', 'UPDATE dot_primary_runs SET status=?', (status,))
            self.blocked()
        self.update('abitdot-primary.sqlite', "UPDATE dot_primary_runs SET status='unknown'")
        for state in ('running', 'uncertain', 'cancel_requested', 'execution_finished', 'blocked'):
            self.update('agentic-governance.sqlite', 'UPDATE governance_invocation SET state=?', (state,))
            self.blocked()
        self.update('agentic-governance.sqlite', "UPDATE governance_invocation SET state='handed_off'")
        self.update('agentic-governance.sqlite', "UPDATE governance_gateway_call SET state='uncertain'")
        self.blocked()

    def test_absent_or_incomplete_proof_database_fails_closed(self):
        governance = self.root / 'agentic-governance.sqlite'
        backup = self.root / 'retained-governance'
        governance.rename(backup)
        self.blocked()
        governance.touch()
        self.blocked()
        governance.unlink()
        backup.rename(governance)
        (self.root / 'webui.db').unlink()
        self.blocked()

    def test_unrelated_task_cannot_supply_proof_and_own_run_exclusion_is_exact(self):
        self.assertIsNone(ownership.external_execution('TASK-OTHER', self.root))
        self.update('agentic-governance.sqlite', "UPDATE governance_invocation SET erp_task='TASK-OTHER'")
        self.blocked()
        self.assertIsNone(ownership.external_execution(self.task, self.root, own_run=self.run))
        other = copy.deepcopy(self.envelope)
        other['run_id'] = 'other-unresolved-run'
        self.update('abitdot-primary.sqlite', 'INSERT INTO dot_primary_runs VALUES(?,?,?,?,?,?,?)',
                    (self.owner, self.agent, other['run_id'], json.dumps(other), 'dispatching', None, None))
        self.assertIsNotNone(ownership.external_execution(self.task, self.root, own_run=self.run))

    def test_envelope_owner_automation_and_registration_cannot_be_borrowed(self):
        for field in ('owner_id', 'automation_id', 'run_id', 'idempotency_key'):
            with self.subTest(field=field):
                value = {**self.envelope, field: 'foreign'}
                self.update('abitdot-primary.sqlite', 'UPDATE dot_primary_runs SET envelope_json=?', (json.dumps(value),))
                self.blocked()
        self.update('abitdot-primary.sqlite', 'UPDATE dot_primary_runs SET envelope_json=?', (json.dumps(self.envelope),))
        for field in ('governance_managed', 'agentic_project', 'agentic_role', 'erp_role_user'):
            with self.subTest(registration=field):
                value = {**self.meta, field: 'foreign'}
                self.update('webui.db', 'UPDATE automation SET meta=?', (json.dumps(value),))
                self.blocked()
        self.update('webui.db', 'UPDATE automation SET meta=?', (json.dumps(self.meta),))
        self.update('webui.db', "UPDATE automation_run SET status='running'")
        self.blocked()

    def test_result_event_packet_and_original_run_drift_denied(self):
        cases = [('UPDATE governance_result SET phase=?', ('pending',)),
                 ('UPDATE governance_result SET result_sha256=?', ('f' * 64,)),
                 ('UPDATE governance_result SET event_json=?', (json.dumps({**self.event, 'run_id': 'foreign'}),)),
                 ('UPDATE governance_invocation SET packet_hash=?', ('f' * 64,)),
                 ('UPDATE governance_invocation SET dispatch_attempts=?', (4,)),
                 ('UPDATE governance_invocation SET submitted_at=?', (None,))]
        with self.connect('agentic-governance.sqlite') as d:
            for query, args in cases:
                d.execute('SAVEPOINT mutation')
                d.execute(query, args)
                d.commit()
                self.blocked()
                # Restore only the controlled fixture, never any production row.
                d.execute('UPDATE governance_result SET phase=?,result_sha256=?,event_json=?',
                          ('published', ownership._digest(self.result), json.dumps(self.event)))
                d.execute('UPDATE governance_invocation SET packet_hash=?,dispatch_attempts=?,submitted_at=?',
                          (ownership._digest(self.packet), 1, 1))
                d.commit()

    def test_existing_legacy_webui_and_managed_guards_remain(self):
        self.update('webui.db', 'UPDATE automation SET meta=?', (json.dumps({'erp_task': self.task}),))
        self.update('webui.db', "UPDATE automation_run SET status='running'")
        self.assertEqual(ownership.external_execution(self.task, self.root), 'An existing ERP automation owns this task')
        self.update('webui.db', "UPDATE automation_run SET status='success'")
        self.update('webui.db', 'UPDATE automation SET meta=?', (json.dumps(self.meta),))
        with self.connect('erp-task-resolution.sqlite') as d:
            d.execute('CREATE TABLE erp_runs(task TEXT,state TEXT)')
            d.execute('INSERT INTO erp_runs VALUES(?,?)', (self.task, json.dumps({'attempts': 1, 'synced': False})))
        self.assertEqual(ownership.external_execution(self.task, self.root),
                         'An existing managed ERP attempt requires original-run reconciliation')


if __name__ == '__main__':
    unittest.main(verbosity=2)
