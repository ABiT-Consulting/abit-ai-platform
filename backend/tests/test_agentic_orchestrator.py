"""Lifecycle tests use the real transactional SQLite ledger; no model/network."""

import importlib.util, json, sys, tempfile, types, unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch, AsyncMock

ROOT = Path(__file__).resolve().parents[1] / 'open_webui/utils'


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


P = 'PROJ-0197'
T = 'TASK-2026-anchor'
USERS = {
    'solution_architect': 'ai-projects@abitcons.com',
    'project_manager': 'ai-projects@abitcons.com',
    'developer': 'ai-developer@abitcons.com',
    'qa': 'ai-qa@abitcons.com',
    'it_manager': 'ai-operations@abitcons.com',
    'operations': 'ai-operations@abitcons.com',
}
GATE = {
    'enabled': True,
    'pm_task_automation_enabled': True,
    'project_status': 'Open',
    'assigned_user': 'ai-projects@abitcons.com',
    'revision': 'project-1',
}


class FakeERP:
    def __init__(self, test):
        self.test = test
        self.tasks = {
            T: {
                'name': T,
                'project': P,
                'status': 'Open',
                'revision': 'inputs-1',
                'depends_on': [],
                'custom_agentic_managed': 1,
                'custom_agov_dispatch_released': 1,
            }
        }
        self.creates = []
        self.releases = []
        self.lose_ack = False

    async def get(self, dt, name):
        return deepcopy(self.tasks[name])

    async def rows(self, dt, fields, filters):
        parent = next(c[2] for c in filters if c[0] == 'parent_task')
        for t in self.tasks.values():
            if t.get('parent_task') == parent:
                yield deepcopy(t)

    async def request(self, method, url, **kw):
        if url.endswith('get_project_backlog'):
            rows=[{'task_id':ident,'task_revision':value.get('revision','untracked'),'owner_context_sha256':'b'*64,'managed':bool(value.get('custom_agentic_managed')),'source_run_id':value.get('custom_agov_source_run',''),'status':value.get('status'),'priority':value.get('priority','Medium'),'due_date':value.get('exp_end_date','')} for ident,value in sorted(self.tasks.items())]
            snap={'project_id':P,'complete':False,'capture_complete':True,'page_complete':True,'offset':0,'next_offset':None,'total_count':len(rows),'page_size':50,'tasks':rows,'context_excluded_task_ids':[]}
            snap['snapshot_sha256']=self.test.b.digest({'project_id':P,'tasks':rows});snap['context_sha256']=self.test.b.digest({'project_id':P,'tasks':[{k:row[k] for k in ('task_id','task_revision','owner_context_sha256')} for row in rows]})
            return {'message':snap}
        if url.endswith('release_backlog'):
            self.releases.append(kw['json'])
            return {'message': {'released': True, **kw['json']}}
        if url.endswith('get_project_task_snapshot'):
            value=self.tasks[kw['params']['task_id']]
            return {'message':{'task_id':value['name'],'task_revision':value.get('revision'),'status':value['status']}}
        if url.endswith('get_backlog_release_receipt'):
            matches=[row for row in self.releases if row.get('source_run_id')==kw['params']['source_run_id'] and row.get('plan_digest')==kw['params']['plan_digest']]
            return {'message':{'exists':True,'released':True,**matches[0]} if matches else {'exists':False}}
        if url.endswith('get_task_request_receipt'):
            matches = [(p, t) for p, t in self.creates if p['idempotency_key'] == kw['params']['idempotency_key']]
            return {
                'message': (
                    {'exists': True, 'task_id': matches[0][1], 'content_sha256': self.test.b.digest(matches[0][0])}
                    if matches
                    else {'exists': False}
                )
            }
        raise AssertionError(url)


class FakeBridge:
    secret = 'offline-not-a-real-token'

    def __init__(self, test):
        self.test = test
        self.erp = FakeERP(test)

    async def packet(self, project, task, role, agent, revision=None):
        return self.test.packet(task, role)

    async def close(self):
        pass

    async def create_task(self, p):
        ident = 'TASK-repair-' + str(len(self.erp.creates) + 1)
        self.erp.tasks[ident] = {
            'name': ident,
            'project': p['project_id'],
            'parent_task': p['parent_task'],
            'status': 'Open',
            'revision': 'repair-inputs',
            'depends_on': [],
            'custom_agentic_managed': 1,
            'custom_agov_dispatch_released': 1,
            'custom_agov_source_run': p['source_run_id'],
        }
        self.erp.creates.append((deepcopy(p), ident))
        if self.erp.lose_ack:
            self.erp.lose_ack = False
            raise TimeoutError('Committed creation, lost acknowledgment')
        return {'task_id': ident}


class Lifecycle(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'state.sqlite'
        env = types.ModuleType('open_webui.env')
        env.DATA_DIR = self.tmp.name
        utils = types.ModuleType('open_webui.utils')
        utils.__path__ = [str(ROOT)]
        self.p = patch.dict(sys.modules, {'open_webui.env': env, 'open_webui.utils': utils})
        self.p.start()
        self.addCleanup(self.p.stop)
        self.g = load('orchestrator_test_ledger', 'agentic_governance.py')
        utils.agentic_governance = self.g
        self.b = load('orchestrator_test_bridge', 'agentic_bridge.py')
        self.p2 = patch.dict(
            sys.modules, {'open_webui.utils.agentic_governance': self.g, 'open_webui.utils.agentic_bridge': self.b}
        )
        self.p2.start()
        self.addCleanup(self.p2.stop)
        self.s = load('orchestrator_test_store', 'agentic_result_store.py')
        self.p3 = patch.dict(sys.modules, {'open_webui.utils.agentic_result_store': self.s})
        self.p3.start()
        self.addCleanup(self.p3.stop)
        self.pm_review=load('orchestrator_test_pm_review','agentic_pm_review.py')
        self.pm_patch=patch.dict(sys.modules,{'open_webui.utils.agentic_pm_review':self.pm_review})
        self.pm_patch.start();self.addCleanup(self.pm_patch.stop)
        self.o = load('orchestrator_test_module', 'agentic_orchestrator.py')
        self.g.enroll(P, 'owner', USERS, self.db)
        self.g.project_control(P, 'enable', actor='operator', policy_revision='project-1', db_path=self.db)
        self.bridge = FakeBridge(self)
        self.sent = []

    async def gate(self, *a):
        return {}, GATE

    async def dispatch(self, ident):
        self.sent.append(ident)

    def packet(self, task=T, role='developer'):
        value = self.bridge.erp.tasks[task]
        p = {
            'schema_version': 1,
            'project_id': P,
            'task_id': task,
            'task_revision': value['revision'],
            'srs': {'revision': 'srs-1', 'sha256': 'b' * 64},
            'repository': 'https://github.com/example/repo',
            'environment': 'canary',
            'instructions': 'scoped',
            'activity': [],
            'requirement_ids': ['REQ-ONE'],
            'binding': {'project_id': P, 'role': role, 'agent_id': self.g.agent_id(P, role), 'erp_user': USERS[role]},
        }
        if value.get('parent_task'):
            p['parent_task'] = value['parent_task']
        if value.get('atomic_adoption'):
            p['atomic_adoption'] = deepcopy(value['atomic_adoption'])
        if role=='project_manager' and value.get('status')=='QA APPROVED':
            with self.g.connect(self.db) as db:
                latest=db.execute('SELECT v.qa_invocation_id FROM governance_verification v JOIN governance_invocation i ON i.invocation_id=v.invocation_id WHERE i.erp_project=? AND i.erp_task=? ORDER BY v.rowid DESC LIMIT 1',(P,task)).fetchone()
                if latest:p['qa_review']=self.pm_review._verified_source(db,latest[0])[1]
        p['content_sha256'] = self.b.packet_digest(p)
        return p

    def finish(
        self,
        role='developer',
        ident='original-invocation-001',
        task=T,
        outcome='ready_for_review',
        source=None,
        extras=None,
    ):
        packet = self.packet(task, role)
        if source:
            self.g.handoff(
                source_invocation_id=source['invocation_id'],
                source_run_id=source['automation_run_id'],
                source_lease_token=source['lease_token'],
                invocation_id=ident,
                project=P,
                task=task,
                from_role=source['target_role'],
                target_role=role,
                task_revision=packet['task_revision'],
                context='verified successor',
                work_packet=packet,
                gate=GATE,
                actor='server',
                db_path=self.db,
            )
        else:
            self.g.enqueue(
                invocation_id=ident,
                project=P,
                task=task,
                from_role='erp' if role in {'solution_architect', 'project_manager'} else 'project_manager',
                target_role=role,
                task_revision=packet['task_revision'],
                context='scoped',
                work_packet=packet,
                gate=GATE,
                actor='server',
                db_path=self.db,
            )
        run = 'run-' + ident
        claim = self.g.claim_invocation(ident, run, gate=GATE, db_path=self.db)
        self.g.mark_submitted(ident, run, claim['lease_token'], gate=GATE, db_path=self.db)
        self.g.finish_execution(
            ident,
            run,
            claim['lease_token'],
            qa_verdict=outcome if role == 'qa' and outcome in {'pass', 'fail', 'blocked'} else None,
            db_path=self.db,
        )
        result = {
            'invocation_id': ident,
            'actor': packet['binding'],
            'outcome': outcome,
            'evidence': [{'id': 'proof', 'sha256': 'a' * 64}],
            'defects': [],
            **(extras or {}),
        }
        receipt = {
            'event_id': 'event-' + ident,
            'task_id': task,
            'run_id': run,
            'status': 'QA APPROVED' if outcome == 'pass' else 'Pending Review',
            'modified': 'accepted-' + ident,'durable':True,
            'type':'qa.passed' if outcome=='pass' else 'run.ready','artifacts':[],
        }
        if role in {'developer','operations','it_manager'}:self.bridge.erp.tasks[task]['custom_agov_implementation_run']=run
        self.s.save_result(ident, run, result, receipt, self.db)
        self.s.publication_receipt(ident, receipt, self.db)
        return self.g.find(ident, self.db, include_private=True), receipt

    async def advance(self, row, receipt):
        return await self.o.advance(
            row['invocation_id'],
            receipt,
            config={},
            project_gate=self.gate,
            dispatch=self.dispatch,
            db_path=self.db,
            bridge=self.bridge,
        )

    async def finish_pm_review(self):
        ident=self.sent[-1];row=self.g.find(ident,self.db,include_private=True)
        packet=json.loads(row['work_packet']);self.assertIn('qa_review',packet)
        task_before=deepcopy(self.bridge.erp.tasks[row['erp_task']])
        review,receipt=self.finish_existing(ident,'reviewed',{'qa_review_ack':self.b.digest(packet['qa_review'])})
        await self.advance(review,receipt)
        self.assertEqual(self.bridge.erp.tasks[row['erp_task']],task_before)
        return review,receipt

    async def test_architect_hands_exact_plan_to_pm_once(self):
        row, receipt = self.finish(
            'solution_architect', extras={'architect_plan': {'requirements': ['REQ-ONE'], 'artifact': 'verified'}}
        )
        await self.advance(row, receipt)
        await self.advance(row, receipt)
        self.assertEqual(len(self.sent), 1)
        nextrow = self.g.find(self.sent[0], self.db, include_private=True)
        self.assertEqual(nextrow['target_role'], 'project_manager')
        self.assertIn('verified', nextrow['context'])
        self.assertEqual(nextrow['source_invocation_id'], row['invocation_id'])

    async def test_developer_to_qa_once(self):
        row, receipt = self.finish()
        await self.advance(row, receipt)
        await self.advance(row, receipt)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.g.find(self.sent[0], self.db)['target_role'], 'qa')

    async def test_receipt_mismatch_cannot_trigger_handoff(self):
        row, r = self.finish()
        with self.assertRaisesRegex(ValueError, 'receipt'):
            await self.advance(row, {**r, 'status': 'Completed'})
        self.assertFalse(self.sent)

    async def test_unpublished_result_cannot_handoff(self):
        row, r = self.finish()
        with self.g.connect(self.db) as d:
            d.execute("update governance_result set phase='uncertain'")
        with self.assertRaisesRegex(ValueError, 'published'):
            await self.advance(row, r)

    async def test_qa_pass_verifies_exact_implementation(self):
        dev, _ = self.finish()
        qa, r = self.finish(
            'qa',
            'independent-qa-0001',
            outcome='pass',
            source=dev,
            extras={'implementation_invocation_id': dev['invocation_id'], 'implementation_agent_id': dev['agent_id']},
        )
        self.bridge.erp.tasks[T]['status'] = 'QA APPROVED'
        await self.advance(qa, r)
        await self.advance(qa, r)
        self.assertEqual(self.g.find(dev['invocation_id'], self.db)['state'], 'verified')
        self.assertEqual(len(self.sent),1)
        self.assertEqual(self.g.find(self.sent[-1],self.db)['target_role'],'project_manager')
        self.assertFalse(self.pm_review.review_complete(P,T,self.db))
        await self.finish_pm_review()
        self.assertTrue(self.pm_review.review_complete(P,T,self.db))

    async def test_qa_cannot_verify_different_implementation(self):
        dev, _ = self.finish()
        qa, r = self.finish(
            'qa',
            'independent-qa-0001',
            outcome='pass',
            source=dev,
            extras={'implementation_invocation_id': dev['invocation_id'], 'implementation_agent_id': 'different-agent'},
        )
        with self.assertRaisesRegex(ValueError, 'implementation'):
            await self.advance(qa, r)

    async def test_qa_failure_returns_same_task_to_developer(self):
        dev, _ = self.finish()
        qa, r = self.finish('qa', 'independent-qa-0001', outcome='fail', source=dev)
        await self.advance(qa, r)
        self.assertEqual(len(self.sent), 1)
        nextrow = self.g.find(self.sent[0], self.db)
        self.assertEqual(nextrow['target_role'], 'developer')
        self.assertEqual(nextrow['erp_task'], T)

    async def test_blocked_requires_concrete_issues(self):
        row, r = self.finish(outcome='blocked')
        with self.assertRaisesRegex(ValueError, 'concrete'):
            await self.advance(row, r)
        self.assertFalse(self.bridge.erp.creates)

    async def test_repair_child_fanout_never_closes_parent(self):
        defect = {
            'id': 'fix-api',
            'requirement_ids': ['REQ-ONE'],
            'reproduction': 'Call endpoint',
            'observed': 'HTTP 503',
            'expected': 'HTTP 200',
        }
        row, r = self.finish(outcome='blocked', extras={'defects': [defect]})
        await self.advance(row, r)
        await self.advance(row, r)
        self.assertEqual(len(self.bridge.erp.creates), 1)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.g.find(row['invocation_id'], self.db)['state'], 'execution_finished')

    async def test_lost_repair_ack_recovers_original_creation(self):
        defect = {
            'id': 'fix-api',
            'requirement_ids': ['REQ-ONE'],
            'reproduction': 'Call endpoint',
            'observed': '503',
            'expected': '200',
        }
        row, r = self.finish(outcome='blocked', extras={'defects': [defect]})
        self.bridge.erp.lose_ack = True
        with self.assertRaises(TimeoutError):
            await self.advance(row, r)
        await self.advance(row, r)
        self.assertEqual(len(self.bridge.erp.creates), 1)
        self.assertEqual(len(self.sent), 1)

    async def test_external_erp_approval_cannot_fake_managed_child_verification(self):
        self.bridge.erp.tasks['TASK-child'] = {
            'name': 'TASK-child',
            'project': P,
            'status': 'QA APPROVED',
            'custom_agentic_managed': 1,
        }
        self.assertFalse(await self.o._task_done(self.bridge, P, 'TASK-child', self.db))

    async def test_cross_project_ancestry_rejected(self):
        self.bridge.erp.tasks['TASK-child'] = {'name': 'TASK-child', 'project': 'PROJ-other', 'parent_task': T}
        with self.assertRaisesRegex(ValueError, 'Cross-project'):
            await self.o._ancestry(self.bridge, P, 'TASK-child', T)

    def finish_existing(self, ident, outcome='ready_for_review', extras=None):
        row = self.g.find(ident, self.db, include_private=True)
        task = row['erp_task']
        role = row['target_role']
        run = 'run-' + ident
        claim = self.g.claim_invocation(ident, run, gate=GATE, db_path=self.db)
        self.g.mark_submitted(ident, run, claim['lease_token'], gate=GATE, db_path=self.db)
        self.g.finish_execution(
            ident,
            run,
            claim['lease_token'],
            qa_verdict=outcome if role == 'qa' and outcome in {'pass', 'fail', 'blocked'} else None,
            db_path=self.db,
        )
        packet = json.loads(row['work_packet'])
        result = {
            'invocation_id': ident,
            'actor': packet['binding'],
            'outcome': outcome,
            'evidence': [{'id': 'actual-proof', 'sha256': 'a' * 64}],
            'defects': [],
            **(extras or {}),
        }
        status = 'QA APPROVED' if outcome=='pass' or packet.get('qa_review') else 'Pending Review'
        receipt = {
            'event_id': 'event-' + ident,
            'task_id': task,
            'run_id': run,
            'status': status,
            'modified': 'version-' + ident,'durable':True,
            'type':'qa.passed' if outcome=='pass' else 'run.ready','artifacts':[],
        }
        if packet.get('qa_review'):
            receipt['pm_review_state']='reviewed' if outcome=='reviewed' else 'held'
            receipt['type']='pm.reviewed' if outcome=='reviewed' else 'pm.review.checkpoint'
        self.bridge.erp.tasks[task]['status'] = status
        if role in {'developer','operations','it_manager'}:self.bridge.erp.tasks[task]['custom_agov_implementation_run']=run
        self.s.save_result(ident, run, result, receipt, self.db)
        self.s.publication_receipt(ident, receipt, self.db)
        return self.g.find(ident, self.db, include_private=True), receipt

    async def test_parent_resumes_own_acceptance_only_after_actual_child_qa(self):
        defect = {
            'id': 'fix-api',
            'requirement_ids': ['REQ-ONE'],
            'reproduction': 'Call endpoint',
            'observed': '503',
            'expected': '200',
        }
        parent, r = self.finish(outcome='blocked', extras={'defects': [defect]})
        await self.advance(parent, r)
        child, cr = self.finish_existing(self.sent[-1])
        await self.advance(child, cr)
        qa, qr = self.finish_existing(
            self.sent[-1],
            'pass',
            {'implementation_invocation_id': child['invocation_id'], 'implementation_agent_id': child['agent_id']},
        )
        await self.advance(qa, qr)
        self.assertEqual(self.g.find(self.sent[-1],self.db)['target_role'],'project_manager')
        await self.finish_pm_review()
        self.assertEqual(len(self.sent), 4)
        resumed = self.g.find(self.sent[-1], self.db)
        self.assertEqual(resumed['erp_task'], T)
        self.assertEqual(resumed['target_role'], 'developer')
        self.assertEqual(resumed['state'], 'queued')
        self.assertNotEqual(self.bridge.erp.tasks[T]['status'], 'QA APPROVED')

    async def test_old_qa_and_pm_proof_cannot_complete_human_edited_child(self):
        defect={'id':'fix-api','requirement_ids':['REQ-ONE'],'reproduction':'Call API','observed':'503','expected':'200'}
        parent,r=self.finish(outcome='blocked',extras={'defects':[defect]});await self.advance(parent,r)
        child,cr=self.finish_existing(self.sent[-1]);await self.advance(child,cr)
        qa,qr=self.finish_existing(self.sent[-1],'pass',{'implementation_invocation_id':child['invocation_id'],'implementation_agent_id':child['agent_id']});await self.advance(qa,qr)
        self.bridge.erp.tasks[child['erp_task']]['revision']='human-new-business-scope'
        await self.finish_pm_review()
        self.assertFalse(await self.o._task_done(self.bridge,P,child['erp_task'],self.db))
        self.assertEqual(self.g.find(parent['invocation_id'],self.db)['state'],'execution_finished')
        self.assertEqual(len(self.sent),3)

    async def test_repair_parent_cannot_resume_after_human_scope_edit(self):
        defect = {
            'id': 'fix-api',
            'requirement_ids': ['REQ-ONE'],
            'reproduction': 'Call endpoint',
            'observed': '503',
            'expected': '200',
        }
        parent, r = self.finish(outcome='blocked', extras={'defects': [defect]})
        await self.advance(parent, r)
        child, cr = self.finish_existing(self.sent[-1])
        await self.advance(child, cr)
        qa, qr = self.finish_existing(
            self.sent[-1],
            'pass',
            {'implementation_invocation_id': child['invocation_id'], 'implementation_agent_id': child['agent_id']},
        )
        self.bridge.erp.tasks[T]['revision'] = 'human-new-scope'
        await self.advance(qa,qr)
        with self.assertRaisesRegex(ValueError,'Parent scope'):
            await self.finish_pm_review()
        self.assertEqual(len(self.sent),3)

    async def test_unsupported_it_action_source_cannot_dispatch(self):
        row, r = self.finish(
            'qa',
            outcome='blocked',
            extras={
                'action_requests': [
                    {
                        'request_key': 'pkg',
                        'operation': 'install_official_package',
                        'risk': 'routine',
                        'parameters': {'package': 'safe'},
                        'reason': 'dependency',
                        'acceptance_criteria': ['installed'],
                    }
                ]
            },
        )
        with self.assertRaisesRegex(ValueError, 'cannot delegate'):
            await self.advance(row, r)
        self.assertFalse(self.bridge.erp.creates)

    async def test_it_dependency_is_structured_and_parent_waits(self):
        row, r = self.finish(
            outcome='blocked',
            extras={
                'action_requests': [
                    {
                        'request_key': 'pkg',
                        'operation': 'install_official_package',
                        'risk': 'routine',
                        'parameters': {'package': 'safe'},
                        'reason': 'dependency',
                        'acceptance_criteria': ['installed'],
                    }
                ]
            },
        )
        await self.advance(row, r)
        proposal, _ = self.bridge.erp.creates[0]
        self.assertEqual(proposal['target_role'], 'it_manager')
        self.assertEqual(proposal['tool_action']['operation'], 'install_official_package')
        self.assertFalse(proposal['repair'])
        self.assertEqual(self.g.find(self.sent[-1], self.db)['target_role'], 'it_manager')

    async def test_arch_pm_backlog_dependency_order_and_final_parent_acceptance(self):
        arch, ar = self.finish('solution_architect', extras={'architect_plan': {'tasks': ['A', 'B']}})
        await self.advance(arch, ar)
        pm, pr = self.finish_existing(self.sent[-1])
        planner = types.ModuleType('open_webui.utils.agentic_planning')

        async def materialize(*args, **kw):
            for name, deps in [('TASK-A', []), ('TASK-B', [{'task': 'TASK-A'}])]:
                self.bridge.erp.tasks[name] = {
                    'name': name,
                    'project': P,
                    'status': 'Open',
                    'revision': name + '-inputs',
                    'parent_task': T,
                    'depends_on': deps,
                    'custom_agentic_managed': 1,
                    'custom_agov_dispatch_released': 1,
                    'custom_agov_source_run': pm['automation_run_id'],
                    'custom_agov_plan_digest':self.b.digest({'tasks':['A','B']}),
                }
            with self.g.connect(self.db) as db:
                db.execute('CREATE TABLE governance_backlog_task(erp_task TEXT,request_json TEXT,phase TEXT)')
                for ident in ('TASK-A', 'TASK-B'):
                    db.execute(
                        'INSERT INTO governance_backlog_task VALUES(?,?,?)',
                        (ident, json.dumps({'target_role':'developer','source_invocation_id':pm['invocation_id'],'source_run_id':pm['automation_run_id'],'project_id':P,'plan_digest':self.b.digest({'tasks':['A','B']})}),'confirmed'),
                    )
            return {'A': 'TASK-A', 'B': 'TASK-B'}

        planner.materialize_backlog = materialize
        from open_webui.utils.agentic_planning import complete_project_backlog
        planner.complete_project_backlog=complete_project_backlog
        with patch.dict(sys.modules, {'open_webui.utils.agentic_planning': planner}):
            await self.advance(pm, pr)
        self.assertEqual(len(self.bridge.erp.releases), 1)
        self.assertEqual(self.g.find(self.sent[-1], self.db)['erp_task'], 'TASK-A')
        for ident in ('TASK-A', 'TASK-B'):
            dev, dr = self.finish_existing(self.sent[-1])
            self.assertEqual(dev['erp_task'], ident)
            await self.advance(dev, dr)
            qa, qr = self.finish_existing(
                self.sent[-1],
                'pass',
                {'implementation_invocation_id': dev['invocation_id'], 'implementation_agent_id': dev['agent_id']},
            )
            await self.advance(qa, qr)
            await self.finish_pm_review()
        parent = self.g.find(self.sent[-1], self.db)
        self.assertEqual(parent['erp_task'], T)
        self.assertEqual(parent['target_role'], 'developer')
        self.assertEqual(parent['state'], 'queued')
        self.assertFalse(self.o._verified(P, T, self.db))
        final, fr = self.finish_existing(self.sent[-1])
        await self.advance(final, fr)
        qa, qr = self.finish_existing(
            self.sent[-1],
            'pass',
            {'implementation_invocation_id': final['invocation_id'], 'implementation_agent_id': final['agent_id']},
        )
        await self.advance(qa, qr)
        await self.finish_pm_review()
        self.assertTrue(self.o._verified(P, T, self.db))

    async def test_duplicate_published_result_sweep_does_not_rematerialize_finished_plan(self):
        await self.test_arch_pm_backlog_dependency_order_and_final_parent_acceptance()
        before = (len(self.sent), len(self.bridge.erp.creates), len(self.bridge.erp.releases))
        with self.g.connect(self.db) as db:
            ids = [row[0] for row in db.execute("SELECT invocation_id FROM governance_result WHERE phase='published'")]
        self.assertEqual(len(ids), 11)
        for ident in ids:
            saved = self.s.get_result(ident, self.db)
            await self.advance(self.g.find(ident, self.db, include_private=True), saved['receipt'])
        self.assertEqual((len(self.sent), len(self.bridge.erp.creates), len(self.bridge.erp.releases)), before)

    async def test_continue_is_a_new_fenced_work_unit_without_replay(self):
        original, r = self.finish(outcome='continue')
        await self.advance(original, r)
        await self.advance(original, r)
        self.assertEqual(len(self.sent), 1)
        successor = self.g.find(self.sent[-1], self.db)
        self.assertEqual(successor['erp_task'], T)
        self.assertNotEqual(successor['invocation_id'], original['invocation_id'])
        self.assertEqual(successor['state'], 'queued')

    async def test_pm_continuation_preserves_exact_architecture_plan_and_provenance(self):
        arch, ar = self.finish('solution_architect', extras={'architect_plan': {'exact': 'original-approved-source'}})
        await self.advance(arch, ar)
        pm, pr = self.finish_existing(self.sent[-1], 'continue')
        await self.advance(pm, pr)
        resumed = self.g.find(self.sent[-1], self.db, include_private=True)
        self.assertIn('original-approved-source', resumed['context'])
        self.assertEqual(resumed['source_invocation_id'], pm['invocation_id'])
        ready, rr = self.finish_existing(resumed['invocation_id'])
        planner = types.ModuleType('open_webui.utils.agentic_planning')
        seen = []

        async def materialize(bridge, source, result, reviewer, packet, path):
            seen.append((source['invocation_id'], reviewer['invocation_id']))
            self.bridge.erp.tasks['TASK-A'] = {
                'name': 'TASK-A',
                'project': P,
                'status': 'Open',
                'revision': 'child-inputs',
                'custom_agov_source_run':ready['automation_run_id'],
                'custom_agov_plan_digest':self.b.digest({'exact':'original-approved-source'}),
                'parent_task': T,
                'depends_on': [],
                'custom_agentic_managed': 1,
                'custom_agov_dispatch_released': 1,
            }
            with self.g.connect(self.db) as d:
                d.execute('CREATE TABLE governance_backlog_task(erp_task TEXT,request_json TEXT,phase TEXT)')
                d.execute(
                    'INSERT INTO governance_backlog_task VALUES(?,?,?)',
                    ('TASK-A', json.dumps({'target_role':'developer','source_invocation_id':ready['invocation_id'],'source_run_id':ready['automation_run_id'],'project_id':P,'plan_digest':self.b.digest({'exact':'original-approved-source'})}),'confirmed'),
                )
            return {'A': 'TASK-A'}

        planner.materialize_backlog = materialize
        from open_webui.utils.agentic_planning import complete_project_backlog
        planner.complete_project_backlog=complete_project_backlog
        with patch.dict(sys.modules, {'open_webui.utils.agentic_planning': planner}):
            await self.advance(ready, rr)
        self.assertEqual(seen, [(arch['invocation_id'], ready['invocation_id'])])
        self.assertEqual(self.g.find(self.sent[-1], self.db)['erp_task'], 'TASK-A')

    async def test_human_atomic_pm_continuation_then_same_task_delegation(self):
        self.bridge.erp.tasks[T]['atomic_adoption'] = {
            'adoption_id': 'AGOV-human-verified',
            'executor_role': 'developer',
            'task_revision': 'inputs-1',
            'source_modified': 'adoption-version',
            'requirement_ids': ['REQ-ONE'],
        }
        row, r = self.finish('project_manager', outcome='continue')
        await self.advance(row, r)
        ready, rr = self.finish_existing(self.sent[-1])
        await self.advance(ready, rr)
        self.assertEqual(self.g.find(self.sent[-1], self.db)['target_role'], 'developer')
        self.assertFalse(self.bridge.erp.creates)

    async def test_operations_qa_rejection_preserves_accountable_role(self):
        row, r = self.finish('operations')
        await self.advance(row, r)
        qa, qr = self.finish_existing(
            self.sent[-1],
            'fail',
            {'implementation_invocation_id': row['invocation_id'], 'implementation_agent_id': row['agent_id']},
        )
        await self.advance(qa, qr)
        self.assertEqual(self.g.find(self.sent[-1], self.db)['target_role'], 'operations')

    async def test_blocked_report_preserves_both_defects_and_it_dependencies(self):
        row, r = self.finish(
            outcome='blocked',
            extras={
                'defects': [
                    {
                        'id': 'fix-code',
                        'requirement_ids': ['REQ-ONE'],
                        'reproduction': 'call',
                        'observed': '503',
                        'expected': '200',
                    }
                ],
                'action_requests': [
                    {
                        'request_key': 'pkg',
                        'operation': 'install_official_package',
                        'risk': 'routine',
                        'parameters': {'package': 'safe'},
                        'reason': 'dependency',
                        'acceptance_criteria': ['installed'],
                    }
                ],
            },
        )
        await self.advance(row, r)
        self.assertEqual(len(self.bridge.erp.creates), 2)
        self.assertEqual({p['target_role'] for p, _ in self.bridge.erp.creates}, {'developer', 'it_manager'})

    async def test_it_completion_receives_independent_qa(self):
        row, r = self.finish('it_manager')
        await self.advance(row, r)
        qa, qr = self.finish_existing(
            self.sent[-1],
            'pass',
            {'implementation_invocation_id': row['invocation_id'], 'implementation_agent_id': row['agent_id']},
        )
        self.assertEqual(qa['target_role'], 'qa')
        await self.advance(qa, qr)
        self.assertTrue(self.o._verified(P, T, self.db))

    async def test_it_qa_rejection_persists_intervention_without_action_replay(self):
        row, r = self.finish('it_manager')
        await self.advance(row, r)
        qa, qr = self.finish_existing(
            self.sent[-1],
            'fail',
            {
                'implementation_invocation_id': row['invocation_id'],
                'implementation_agent_id': row['agent_id'],
                'defects': [{'id': 'wrong-service'}],
            },
        )
        await self.advance(qa, qr)
        await self.advance(qa, qr)
        self.assertEqual(len(self.sent), 1)
        self.assertFalse(self.o._verified(P, T, self.db))
        with self.o._db(self.db) as d:
            held = d.execute('SELECT * FROM governance_lifecycle_hold').fetchall()
        self.assertEqual(len(held), 1)
        self.assertIn(row['invocation_id'], held[0]['details'])

    async def test_atomic_human_adoption_pm_delegates_exact_same_task_once(self):
        self.bridge.erp.tasks[T]['atomic_adoption'] = {
            'adoption_id': 'AGOV-human-verified',
            'executor_role': 'developer',
            'task_revision': 'inputs-1',
            'source_modified': 'adoption-version',
            'requirement_ids': ['REQ-ONE'],
        }
        row, r = self.finish('project_manager')
        await self.advance(row, r)
        await self.advance(row, r)
        self.assertEqual(len(self.sent), 1)
        successor = self.g.find(self.sent[-1], self.db)
        self.assertEqual(successor['target_role'], 'developer')
        self.assertEqual(successor['erp_task'], T)
        self.assertFalse(self.bridge.erp.creates)

    async def test_atomic_adoption_stale_human_scope_denied(self):
        self.bridge.erp.tasks[T]['atomic_adoption'] = {
            'adoption_id': 'AGOV-human-verified',
            'executor_role': 'developer',
            'task_revision': 'inputs-1',
            'source_modified': 'adoption-version',
            'requirement_ids': ['REQ-ONE'],
        }
        row, r = self.finish('project_manager')
        self.bridge.erp.tasks[T]['revision'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'changed'):
            await self.advance(row, r)
        self.assertFalse(self.sent)

    async def test_disabled_or_absent_pm_checkbox_blocks_handoff(self):
        row, r = self.finish()
        for gate in (
            {**GATE, 'pm_task_automation_enabled': False},
            {k: v for k, v in GATE.items() if k != 'pm_task_automation_enabled'},
        ):

            async def rejected(*a):
                return {}, gate

            with self.assertRaisesRegex(ValueError, 'opt-in'):
                await self.o.advance(
                    row['invocation_id'],
                    r,
                    config={},
                    project_gate=rejected,
                    dispatch=self.dispatch,
                    db_path=self.db,
                    bridge=self.bridge,
                )
        self.assertFalse(self.sent)

    async def test_paused_project_cannot_create_repair_children(self):
        row, r = self.finish(
            outcome='blocked',
            extras={
                'defects': [
                    {
                        'id': 'fix',
                        'requirement_ids': ['REQ-ONE'],
                        'reproduction': 'call',
                        'observed': '503',
                        'expected': '200',
                    }
                ]
            },
        )
        self.g.project_control(P, 'pause', actor='owner', db_path=self.db)
        with self.assertRaisesRegex(ValueError, 'paused'):
            await self.advance(row, r)
        self.assertFalse(self.bridge.erp.creates)

    async def test_cancelled_blocker_cannot_create_children(self):
        row, r = self.finish(outcome='blocked')
        self.g.request_cancel(row['invocation_id'], 'owner', self.db)
        with self.assertRaisesRegex(ValueError, 'no longer owns'):
            await self.o._repair_children(
                row,
                {
                    'defects': [
                        {
                            'id': 'fix',
                            'requirement_ids': ['REQ-ONE'],
                            'reproduction': 'call',
                            'observed': '503',
                            'expected': '200',
                        }
                    ]
                },
                self.packet(),
                self.bridge,
                self.db,
                GATE,
            )
        self.assertFalse(self.bridge.erp.creates)

    async def test_paused_task_cannot_publish_new_handoff(self):
        row, r = self.finish()
        with self.g.connect(self.db) as d:
            self.g._task_gate(d, P, T)
            d.execute('INSERT INTO governance_task_control VALUES(?,?,?,?,?)', (P, T, 'paused', 'owner', 1))
        await self.advance(row, r)
        self.assertFalse(self.sent)

    async def test_paused_repair_child_does_not_block_unrelated_ready_sibling(self):
        defects = [
            {'id': name, 'requirement_ids': ['REQ-ONE'], 'reproduction': 'call', 'observed': '503', 'expected': '200'}
            for name in ('a', 'b')
        ]
        row, r = self.finish(outcome='blocked', extras={'defects': defects})
        actual = await self.o._repair_children(row, {'defects': defects}, self.packet(), self.bridge, self.db, GATE)
        self.o._wait(row, 'repair', actual, self.packet(), self.db)
        with self.g.connect(self.db) as d:
            self.g._task_gate(d, P, actual[0])
            d.execute('INSERT INTO governance_task_control VALUES(?,?,?,?,?)', (P, actual[0], 'paused', 'owner', 1))
        await self.o.scan_project(
            P, config={}, project_gate=self.gate, dispatch=self.dispatch, db_path=self.db, bridge=self.bridge
        )
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.g.find(self.sent[-1], self.db)['erp_task'], actual[1])

    async def scan(self):
        await self.o.scan_project(P,config={},project_gate=self.gate,dispatch=self.dispatch,db_path=self.db,bridge=self.bridge)

    def reference_wait(self,source,ident='TASK-existing-human',managed=False,status='Working'):
        task={'name':ident,'project':P,'status':status,'revision':'human-inputs','depends_on':[],'parent_task':'TASK-unrelated-parent','custom_agentic_managed':int(managed)}
        self.bridge.erp.tasks[ident]=task
        proposal={'project_id':P,'source_invocation_id':source['invocation_id'],'target_role':'developer' if managed else 'human','reference_only':True,'external_dependency':not managed,'existing_task_revision':'human-inputs','owner_context_sha256':'b'*64}
        with self.g.connect(self.db) as db:
            db.execute('CREATE TABLE governance_backlog_task(erp_task TEXT,request_json TEXT,phase TEXT)')
            db.execute('INSERT INTO governance_backlog_task VALUES(?,?,?)',(ident,json.dumps(proposal),'referenced'))
        self.o._wait(source,'plan',[ident],self.packet(T,'project_manager'),self.db)
        return task

    async def test_full_project_scan_observes_unmanaged_tasks_without_adoption(self):
        self.bridge.erp.tasks['TASK-manual']={'name':'TASK-manual','project':P,'status':'Open','revision':'manual-inputs','depends_on':[],'_assign':'["human@abitcons.com"]'}
        before=deepcopy(self.bridge.erp.tasks)
        await self.scan();await self.scan()
        self.assertFalse(self.sent);self.assertFalse(self.bridge.erp.creates);self.assertEqual(self.bridge.erp.tasks,before)

    async def test_existing_human_reference_waits_then_resumes_parent_without_reassigning(self):
        source,_=self.finish('project_manager')
        task=self.reference_wait(source)
        await self.scan();self.assertFalse(self.sent)
        task['status']='Completed';before=deepcopy(task)
        await self.scan();await self.scan()
        self.assertEqual(len(self.sent),1);self.assertEqual(self.g.find(self.sent[0],self.db)['erp_task'],T)
        self.assertEqual(task,before);self.assertEqual(task['parent_task'],'TASK-unrelated-parent')
        with self.g.connect(self.db) as db:
            event=db.execute("SELECT payload FROM governance_event WHERE kind='parent_acceptance_resumed'").fetchone()
        audit=json.loads(event[0]);self.assertEqual(audit['externally_completed_dependencies'],[task['name']]);self.assertEqual(audit['independently_verified_children'],[])

    async def test_existing_managed_reference_is_not_dispatched_or_falsely_verified(self):
        source,_=self.finish('project_manager')
        task=self.reference_wait(source,managed=True,status='Open')
        await self.scan();task['status']='QA APPROVED';await self.scan()
        self.assertFalse(self.sent);self.assertFalse(self.bridge.erp.creates)

    async def test_existing_reference_business_edit_requires_explicit_pm_review(self):
        source,_=self.finish('project_manager');task=self.reference_wait(source);task['revision']='human-edited-inputs'
        with self.assertRaisesRegex(ValueError,'reference changed'):await self.scan()
        self.assertFalse(self.sent)

    async def test_full_scan_recovers_only_exact_durable_released_plan_without_model_replay(self):
        plan={'tasks':['atomic-A']};arch,_=self.finish('solution_architect',extras={'architect_plan':plan})
        await self.advance(arch,self.s.get_result(arch['invocation_id'],self.db)['receipt'])
        pm,_=self.finish_existing(self.sent[-1]);self.sent.clear()
        ident='TASK-recovered-child';planhash=self.b.digest(plan)
        self.bridge.erp.tasks[ident]={'name':ident,'project':P,'status':'Open','revision':'atomic-inputs','parent_task':T,'depends_on':[],'custom_agentic_managed':1,'custom_agov_dispatch_released':1,'custom_agov_source_run':pm['automation_run_id'],'custom_agov_plan_digest':planhash}
        proposal={'project_id':P,'source_invocation_id':pm['invocation_id'],'source_run_id':pm['automation_run_id'],'target_role':'developer','plan_digest':planhash}
        with self.g.connect(self.db) as db:
            db.execute('CREATE TABLE governance_backlog_plan(plan_hash TEXT,architect_invocation TEXT,pm_invocation TEXT,project TEXT,anchor_task TEXT,source_hash TEXT,pm_packet_hash TEXT)')
            db.execute('CREATE TABLE governance_backlog_task(plan_hash TEXT,task_key TEXT,request_hash TEXT,request_json TEXT,phase TEXT,erp_task TEXT)')
            db.execute('INSERT INTO governance_backlog_plan VALUES(?,?,?,?,?,?,?)',(planhash,arch['invocation_id'],pm['invocation_id'],P,T,self.b.digest(self.s.get_result(arch['invocation_id'],self.db)['result']),self.b.digest(json.loads(pm['work_packet']))))
            db.execute('INSERT INTO governance_backlog_task VALUES(?,?,?,?,?,?)',(planhash,'A',self.b.digest(proposal),json.dumps(proposal),'confirmed',ident))
        # An absent/unknown release acknowledgement grants no dispatch authority.
        await self.scan();self.assertFalse(self.sent)
        self.bridge.erp.releases.append({'project_id':P,'source_run_id':pm['automation_run_id'],'plan_digest':planhash,'task_ids':[ident],'existing_task_refs':[]})
        await self.scan();await self.scan()
        self.assertEqual(len(self.sent),1);self.assertEqual(self.g.find(self.sent[0],self.db)['erp_task'],ident)
        self.assertFalse(self.bridge.erp.creates);self.assertEqual(len(self.bridge.erp.releases),1)

    async def test_cancelled_source_cannot_fanout(self):
        row, r = self.finish()
        self.g.request_cancel(row['invocation_id'], 'operator', self.db)
        self.bridge.erp.tasks['TASK-child'] = {
            'name': 'TASK-child',
            'project': P,
            'revision': 'child',
            'parent_task': T,
        }
        with self.assertRaisesRegex(ValueError, 'authority'):
            self.o._fanout(row, self.packet('TASK-child'), ['TASK-child', T], GATE, self.db, purpose='test')


if __name__ == '__main__':
    unittest.main()
