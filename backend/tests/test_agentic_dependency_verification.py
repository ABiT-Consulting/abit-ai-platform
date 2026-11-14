"""Real SQLite/AF_UNIX/owned-file bytes, synthetic native bridge, zero models.

Reuses the existing controlled IT protocol fixture, then closes its actual QA
and seeds a named completed PM fixture. It never calls GitHub, ERP or a host
service. The only executed process is the existing temporary-file test writer.
"""

from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import AsyncMock, patch

SOURCE = Path(__file__).resolve().parents[2]
ROOT = SOURCE / 'backend/open_webui/utils'
sys.path.insert(0, str(SOURCE / 'backend/tests'))
import test_agentic_it_qa as qa_fixture
from test_agentic_controls_events import P, T, SECOND


class Runtime(qa_fixture.IndependentPhysicalQA):
    # Parent runtime cases below are distinct from the inherited child QA tests.
    # Suppress inherited discovery, retaining its controlled fixture constructor.
    test_independent_current_host_read_has_own_artifact_and_exact_final_gate = None
    test_model_only_claim_or_old_implementer_artifact_cannot_pass = None
    test_native_other_source_or_self_qa_proof_denies_before_host_evidence = None
    test_current_host_changed_after_qa_read_cannot_publish_pass = None
    test_control_stop_or_missing_existing_bridge_never_grants_new_read = None

    def load(self, name):
        chosen = ROOT / (name + '.py')
        if not chosen.exists():
            return super().load(name)
        full = 'open_webui.utils.' + name
        spec = importlib.util.spec_from_file_location(full, chosen)
        module = importlib.util.module_from_spec(spec)
        sys.modules[full] = module
        setattr(self.utils, name, module)
        spec.loader.exec_module(module)
        return module

    def setUp(self):
        super().setUp()
        # The actual parent and descendant hash revision is stable and SHA-256.
        previous = self.packet

        def packet(*args, **kwargs):
            value = previous(*args, **kwargs)
            value['task_revision'] = 'b' * 64
            value['srs']['features'] = [
                {'requirement_id': ident, 'acceptance_criteria': 'Synthetic ' + ident}
                for ident in ('REQ-ONE', 'REQ-SVC')
            ]
            value['content_sha256'] = self.bridge_module.packet_digest(value)
            return value

        self.packet = packet

    async def asyncSetUp(self):
        full = 'open_webui.utils.agentic_contracts.dependency_verification'
        spec = importlib.util.spec_from_file_location(full, ROOT / 'agentic_contracts/dependency_verification.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[full] = module
        spec.loader.exec_module(module)
        self.policy_module = module
        await super().asyncSetUp()
        self.dependency = self.load('agentic_dependency_verification')
        self.readback = self.load('agentic_it_dependency_readback')
        self.load('agentic_gateway_operations')
        # Publish actual child QA fixture and verify it through the existing ledger.
        qa = self.row
        qa_packet = deepcopy(self.packet_qa)
        qa_result = {'outcome': 'pass', 'evidence': [{'id': 'qa-file', 'sha256': 'd' * 64}]}
        qa_event = {
            'event_id': 'child-qa-passed',
            'task_id': T,
            'invocation_id': qa['invocation_id'],
            'run_id': qa['automation_run_id'],
        }
        qa_receipt = {
            'event_id': qa_event['event_id'],
            'run_id': qa['automation_run_id'],
            'task_id': T,
            'modified': 'qa-modified',
            'status': 'QA APPROVED',
            'durable': True,
        }
        self.store.save_result(qa['invocation_id'], qa['automation_run_id'], qa_result, qa_event, self.path)
        self.store.publication_receipt(qa['invocation_id'], qa_receipt, self.path)
        self.g.finish_execution(
            qa['invocation_id'], qa['automation_run_id'], qa['lease_token'], qa_verdict='pass', db_path=self.path
        )
        self.g.verify_completion(
            self.source['invocation_id'], qa['invocation_id'], 'c' * 64, 'd' * 64, 'server-fixture', db_path=self.path
        )
        review = {'qa_invocation_id': qa['invocation_id']}
        pm_packet = deepcopy(qa_packet)
        pm_packet['binding'] = {
            'project_id': P,
            'role': 'project_manager',
            'agent_id': self.g.agent_id(P, 'project_manager'),
            'erp_user': self.users['project_manager'],
        }
        pm_packet['qa_review'] = review
        pm_packet['content_sha256'] = self.bridge_module.packet_digest(pm_packet)
        pm = dict(qa)
        pm.update(
            invocation_id='child-pm-completed',
            target_role='project_manager',
            agent_id=pm_packet['binding']['agent_id'],
            state='completed',
            automation_run_id='child-pm-run',
            work_packet=json.dumps(pm_packet),
        )
        with self.g.connect(self.path) as db:
            columns = list(pm)
            db.execute(
                'INSERT INTO governance_invocation('
                + ','.join(columns)
                + ') VALUES('
                + ','.join('?' for _ in columns)
                + ')',
                [pm[key] for key in columns],
            )
        pm_module = self.load('agentic_pm_review')
        with self.g.connect(self.path) as db:
            pm_module._schema(db)
            db.execute(
                'INSERT INTO governance_pm_review VALUES(?,?,?,?,?,?,?,?)',
                (
                    qa['invocation_id'],
                    P,
                    T,
                    pm['invocation_id'],
                    self.bridge_module.digest(review),
                    'completed',
                    'e' * 64,
                    int(time.time()),
                ),
            )
        pm_result = {
            'outcome': 'reviewed',
            'qa_review_ack': self.bridge_module.digest(review),
            'evidence': [{'id': 'pm-file', 'sha256': 'f' * 64}],
        }
        pm_event = {
            'event_id': 'child-pm-reviewed',
            'task_id': T,
            'invocation_id': pm['invocation_id'],
            'run_id': pm['automation_run_id'],
        }
        pm_receipt = {
            'event_id': pm_event['event_id'],
            'run_id': pm['automation_run_id'],
            'task_id': T,
            'modified': 'qa-modified',
            'status': 'QA APPROVED',
            'durable': True,
            'pm_review_state': 'reviewed',
        }
        self.store.save_result(pm['invocation_id'], pm['automation_run_id'], pm_result, pm_event, self.path)
        self.store.publication_receipt(pm['invocation_id'], pm_receipt, self.path)
        self.child_qa = qa
        self.child_pm = pm
        mapping = {
            'acceptance_id': 'REQ-SVC.acceptance',
            'requirement_id': 'REQ-SVC',
            'operation': 'restart_service',
            'resource_id': 'owned-fixture',
        }
        policy = {'schema_version': 1, 'project_id': P, 'srs_sha256': '1' * 64, 'mappings': [mapping]}
        parent = self.packet(SECOND, 'developer')
        parent.update(requirement_ids=['REQ-ONE', 'REQ-SVC'], dependency_acceptance_policy=policy)
        parent['acceptance_tests'] = [
            {'id': req + '.acceptance', 'requirement_ids': [req], 'method': 'readback', 'expected': 'Actual ' + req}
            for req in parent['requirement_ids']
        ]
        parent['content_sha256'] = self.bridge_module.packet_digest(parent)
        previous = self.packet
        self.packet = lambda *args, **kwargs: deepcopy(parent)
        self.parent = self.running('parent-developer-original', SECOND, 'developer')
        self.packet = previous
        self.parent_packet = parent
        self.native = {
            'schema_version': 1,
            'project_id': P,
            'parent_task_id': SECOND,
            'parent_role': 'developer',
            'parent_invocation_id': self.parent['invocation_id'],
            'parent_run_id': self.parent['automation_run_id'],
            'parent_agent_id': self.parent['agent_id'],
            'parent_task_revision': parent['task_revision'],
            'srs_sha256': '1' * 64,
            'policy_sha256': self.bridge_module.digest(policy),
            'receipts': [],
        }
        proof = {
            **mapping,
            **{
                key: self.proof[key]
                for key in (
                    'implementation_invocation_id',
                    'implementation_run_id',
                    'implementation_agent_id',
                    'action_id',
                    'plan_digest',
                    'action_fingerprint',
                    'consumed_approval_receipt_id',
                    'host_receipt_sha256',
                    'action_file_id',
                    'action_file_sha256',
                    'ready_event_id',
                    'ready_result_sha256',
                    'ready_receipt_sha256',
                )
            },
            'child_task_id': T,
            'child_task_revision': 'b' * 64,
            'qa_invocation_id': qa['invocation_id'],
            'qa_run_id': qa['automation_run_id'],
            'qa_result_sha256': self.bridge_module.digest(qa_result),
            'qa_receipt_sha256': self.bridge_module.digest(qa_receipt),
            'pm_invocation_id': pm['invocation_id'],
            'pm_run_id': pm['automation_run_id'],
            'pm_result_sha256': self.bridge_module.digest(pm_result),
            'pm_receipt_sha256': self.bridge_module.digest(pm_receipt),
            'ancestry': [
                {
                    'child_task_id': T,
                    'parent_task_id': SECOND,
                    'task_revision': 'b' * 64,
                    'source_run_id': 'creator-native-run',
                    'request_sha256': '9' * 64,
                }
            ],
        }
        self.native['receipts'] = [proof]
        self.bridge.packet = AsyncMock(side_effect=lambda *args: deepcopy(self.parent_packet))

        async def native_request(method, path, **kwargs):
            self.native_calls += 1
            assert method == 'GET' and path == self.dependency.RPC
            return {'message': deepcopy(self.native)}

        self.bridge.erp.request = native_request
        self.config['agentic_repository_gateway'] = {'projects': {P: {'dependency_acceptance_policy': policy}}}
        await self.server.close()
        resources = self.resources_module.RegisteredBrokerResources.__new__(
            self.resources_module.RegisteredBrokerResources
        )
        resources.executor = self.executor
        resources.profile = {'project_id': P, 'dependency_acceptance_policy': policy}
        self.resources = resources
        self.server = await self.broker.BrokerServer(
            self.root / 'broker.sock',
            self.root / 'key',
            client_uid=os.geteuid(),
            key_owner=os.geteuid(),
            prepare=lambda _: None,
            launch_original=lambda *_: (_ for _ in ()).throw(AssertionError('No host execution allowed')),
            observe_original=lambda _: None,
            verify_dependency=resources.verify_dependency,
        ).start()
        self.authority = AsyncMock()

    async def verify_parent(self, call='parent-read-original'):
        return await self.dependency.verify_dependencies(
            {},
            row=self.parent,
            packet=self.parent_packet,
            config=self.config,
            bridge=self.bridge,
            db_path=self.path,
            gateway_call_id=call,
            check_authority=self.authority,
        )

    async def test_actual_sqlite_unix_and_owned_bytes_form_parent_proof_without_extra_host_execution(self):
        result = await self.verify_parent()
        self.assertEqual(1, len(result['dependency_verifications']))
        self.assertEqual('REQ-SVC.acceptance', result['test_results'][0]['test_id'])
        self.assertFalse('exit_code' in result['dependency_verifications'][0])
        with self.executor._db(False) as db:
            self.assertEqual(1, db.execute('SELECT count(*) FROM original_operation').fetchone()[0])
        with self.g.connect(self.path) as db:
            self.assertEqual(1, db.execute('SELECT count(*) FROM governance_dependency_verification').fetchone()[0])
        self.assertGreaterEqual(self.native_calls, 3)
        self.assertGreater(self.authority.await_count, 2)

    async def test_duplicate_original_call_returns_same_bytes_and_receipt_not_another_observation(self):
        first = await self.verify_parent()
        second = await self.verify_parent()
        self.assertEqual(first, second)
        with self.g.connect(self.path) as db:
            self.assertEqual(1, db.execute('SELECT count(*) FROM governance_dependency_verification').fetchone()[0])

    async def test_saved_original_proof_is_append_only_and_status_recovers_only_exact_bytes(self):
        original = await self.verify_parent()
        artifact = original['evidence'][0]
        file = self.root / 'qa-artifacts' / self.parent['invocation_id'] / artifact['uri'].removeprefix('artifact://')
        raw = file.read_bytes()
        file.unlink()
        recovered = await self.dependency.status(
            {},
            row=self.parent,
            packet=self.parent_packet,
            config=self.config,
            bridge=self.bridge,
            db_path=self.path,
            check_authority=self.authority,
        )
        self.assertEqual(original, recovered)
        self.assertEqual(raw, file.read_bytes())
        with self.g.connect(self.path) as db:
            with self.assertRaises(__import__('sqlite3').IntegrityError):
                db.execute("UPDATE governance_dependency_verification SET result_json='{}'")

    async def test_crash_after_durable_proof_intent_recovers_exact_original_artifact(self):
        with patch.object(self.dependency, '_install_artifacts', side_effect=OSError('fixture crash before bytes')):
            with self.assertRaises(OSError):
                await self.verify_parent()
        with self.g.connect(self.path) as db:
            original = json.loads(
                db.execute('SELECT result_json FROM governance_dependency_verification').fetchone()[0]
            )
        recovered = await self.verify_parent()
        self.assertEqual(original, recovered)

    async def test_wrong_parent_project_child_revision_or_current_srs_denied_before_host_probe(self):
        for key, value in [('project_id', 'PROJ-other'), ('parent_task_id', 'TASK-other'), ('srs_sha256', '0' * 64)]:
            old = self.native[key]
            self.native[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                await self.verify_parent()
            self.native[key] = old
        self.native['receipts'][0]['child_task_revision'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'current inputs'):
            await self.verify_parent()

    async def test_qa_or_pm_not_current_or_completed_denies(self):
        with self.g.connect(self.path) as db:
            db.execute("UPDATE governance_pm_review SET phase='held'")
        with self.assertRaisesRegex(ValueError, 'completed PM'):
            await self.verify_parent()
        with self.g.connect(self.path) as db:
            db.execute("UPDATE governance_pm_review SET phase='completed'")
        self.native['receipts'][0]['qa_invocation_id'] = 'older-or-invented-qa'
        with self.assertRaises(ValueError):
            await self.verify_parent()

    async def test_wrong_resource_borrowed_consumption_or_action_file_cannot_pass(self):
        proof = self.native['receipts'][0]
        for key, value in [
            ('resource_id', 'unregistered'),
            ('consumed_approval_receipt_id', 'borrowed'),
            ('action_file_sha256', '0' * 64),
        ]:
            old = proof[key]
            proof[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                await self.verify_parent()
            proof[key] = old

    async def test_real_host_changed_after_original_proof_blocks_immutable_recovery(self):
        original = await self.verify_parent()
        self.output.write_bytes(b'later actual host change\n')
        with self.assertRaises(ValueError):
            await self.verify_parent()
        with self.g.connect(self.path) as db:
            saved = json.loads(db.execute('SELECT result_json FROM governance_dependency_verification').fetchone()[0])
        self.assertEqual(original, saved)

    async def test_parent_stop_hold_and_lease_loss_deny_before_any_new_artifact(self):
        for kind in ('stop', 'hold', 'lease'):
            if kind == 'stop':
                self.g.project_control(P, 'stop', actor='human-fixture', db_path=self.path)
            elif kind == 'hold':
                self.bridge.erp.request = AsyncMock(side_effect=ValueError('Native Project Held'))
            else:
                with self.g.connect(self.path) as db:
                    db.execute(
                        'UPDATE governance_invocation SET lease_expires_at=0 WHERE invocation_id=?',
                        (self.parent['invocation_id'],),
                    )
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                await self.verify_parent()
            if kind == 'stop':
                self.g.project_control(
                    P, 'enable', actor='human-fixture', policy_revision='project-revision-1', db_path=self.path
                )

    async def test_parent_qa_must_collect_its_own_new_artifact_and_host_read(self):
        developer = await self.verify_parent()
        source = self.parent
        packet = deepcopy(self.parent_packet)
        self.g.finish_execution(
            source['invocation_id'], source['automation_run_id'], source['lease_token'], db_path=self.path
        )
        packet['binding'] = {
            'project_id': P,
            'role': 'qa',
            'agent_id': self.g.agent_id(P, 'qa'),
            'erp_user': self.users['qa'],
        }
        packet['implementation_run_id'] = source['automation_run_id']
        packet['content_sha256'] = self.bridge_module.packet_digest(packet)
        self.g.handoff(
            source_invocation_id=source['invocation_id'],
            source_run_id=source['automation_run_id'],
            source_lease_token=source['lease_token'],
            invocation_id='parent-independent-qa',
            project=P,
            task=SECOND,
            from_role='developer',
            target_role='qa',
            task_revision=packet['task_revision'],
            context='Own parent physical refresh',
            work_packet=packet,
            gate=self.gate_value,
            actor='server-fixture',
            db_path=self.path,
        )
        row = self.g.claim_invocation('parent-independent-qa', 'parent-qa-run', gate=self.gate_value, db_path=self.path)
        self.g.mark_submitted(
            row['invocation_id'], row['automation_run_id'], row['lease_token'], gate=self.gate_value, db_path=self.path
        )
        self.parent = self.g.find(row['invocation_id'], self.path, include_private=True)
        self.parent_packet = packet
        self.native.update(
            parent_role='qa',
            parent_invocation_id=row['invocation_id'],
            parent_run_id=row['automation_run_id'],
            parent_agent_id=row['agent_id'],
        )
        qa = await self.verify_parent('qa-current-read')
        self.assertNotEqual(
            developer['dependency_verifications'][0]['log_evidence_id'],
            qa['dependency_verifications'][0]['log_evidence_id'],
        )
        self.assertNotEqual(
            developer['dependency_verifications'][0]['observation_sha256'],
            qa['dependency_verifications'][0]['observation_sha256'],
        )
        await self.dependency.assert_dependency_evidence(
            self.parent, packet, qa, self.config, self.path, bridge=self.bridge
        )

    async def test_native_hold_arriving_after_host_read_prevents_output_and_artifact(self):
        original = self.bridge.erp.request
        calls = 0

        async def changed(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls >= 3:
                raise ValueError('Native Project Held during read')
            return await original(*args, **kwargs)

        self.bridge.erp.request = changed
        with self.assertRaisesRegex(ValueError, 'Held during'):
            await self.verify_parent()
        self.assertFalse((self.root / 'qa-artifacts' / self.parent['invocation_id']).exists())

    async def test_root_registry_independently_denies_changed_srs_or_resource_mapping(self):
        self.resources.profile = deepcopy(self.resources.profile)
        for key, value in [('srs_sha256', '0' * 64), ('project_id', 'PROJ-unrelated')]:
            old = self.resources.profile['dependency_acceptance_policy'][key]
            self.resources.profile['dependency_acceptance_policy'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                await self.verify_parent()
            self.resources.profile['dependency_acceptance_policy'][key] = old
        self.resources.profile['dependency_acceptance_policy']['mappings'][0]['resource_id'] = 'other-owned-resource'
        with self.assertRaises(ValueError):
            await self.verify_parent()

    async def test_root_grant_never_accepts_parent_role_spoof_or_extra_execute_arguments(self):
        result = await self.verify_parent()
        original = json.loads(
            (
                self.root
                / 'qa-artifacts'
                / self.parent['invocation_id']
                / result['evidence'][0]['uri'].removeprefix('artifact://')
            ).read_bytes()
        )
        observed = original['host_observation']
        payload = {key: observed[key] for key in self.readback.PAYLOAD_FIELDS}
        grant = {
            'authorized': True,
            'mode': 'verify_dependency',
            'role': 'developer',
            **payload,
            'request_payload_sha256': self.readback.digest(payload),
        }
        self.readback.check_grant(payload, grant)
        for change in ('role', 'parent', 'extra'):
            bad = deepcopy(payload)
            wrong = deepcopy(grant)
            if change == 'role':
                wrong['role'] = 'it_manager'
            elif change == 'parent':
                bad['parent_task_id'] = 'TASK-another'
            else:
                bad['command'] = 'forbidden'
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.readback.check_grant(bad, wrong)

    async def test_parent_qa_cannot_borrow_developer_readback_or_child_qa_evidence(self):
        result = await self.verify_parent()
        borrowed = deepcopy(self.parent)
        borrowed['invocation_id'] = 'another-parent-qa'
        borrowed['target_role'] = 'qa'
        with self.assertRaisesRegex(ValueError, 'same parent invocation'):
            await self.dependency.assert_dependency_evidence(
                borrowed, self.parent_packet, result, self.config, self.path, bridge=self.bridge
            )


if __name__ == '__main__':
    __import__('unittest').main()
