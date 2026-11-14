"""Offline integration: actual ledger/contracts/bridge/artifact/outbox/executor.

Only ERP transport and the remote Dot execution are controlled doubles. All
files and SQLite state live in TemporaryDirectory; no app startup/network/model.
The packet follows abit-ai-agents/tests/test_governance_contract.py's canonical
fixture (that repository has no governance_v1_valid.json fixture file).
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import importlib
from io import BytesIO
import json
import os
import sqlite3
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch, AsyncMock

from PIL import Image

UTILS = Path(__file__).resolve().parents[1] / "open_webui/utils"
PROJECT = "PROJ-0197"
TASK = "TASK-2026-60000"
SECRET = "offline-fixture-secret-never-sent-to-network-00001"
ROLE_USERS = {
    "project_manager": "ai-projects@abitcons.com",
    "developer": "ai-developer@abitcons.com",
    "qa": "ai-qa@abitcons.com",
}


class FakeERP:
    """Authoritative in-memory ERP with signed, CAS, idempotent result receipts."""

    def __init__(self, modules):
        self.modules = modules
        self.project = {"name": PROJECT, "status": "Open", "modified": "project-version-1", "enabled": True}
        self.task = {
            "name": TASK,
            "project": PROJECT,
            "status": "Open",
            "modified": "task-modified-1",
            "revision": "task-inputs-1",
        }
        self.revision = 1
        self.files = {}
        self.uploads = []
        self.receipts = {}
        self.signed_events = []
        self.publish_calls = []
        self.receipt_reads = []
        self.lose_ack_for = set()
        self.lose_before_commit = set()
        self.implementation = None
        self.mutate_packet = None
        self.packet_calls = 0
        self.activity = []
        self.service_user = "ai-developer@abitcons.com"
        self.upload_actor = self.service_user

    def binding(self, role):
        ledger = self.modules["agentic_governance"]
        return {
            "project_id": PROJECT,
            "role": role,
            "agent_id": ledger.agent_id(PROJECT, role),
            "erp_user": ROLE_USERS[role],
        }

    def packet(self, role):
        packet = {
            "schema_version": 1,
            "project_id": PROJECT,
            "task_id": TASK,
            "task_revision": self.task["revision"],
            "source_modified": self.task["modified"],
            "project_revision": self.project["modified"],
            "srs": {"revision": "fixture-srs-v1", "sha256": "1" * 64},
            "binding": self.binding(role),
            "objective": "Verify an isolated project task dispatch.",
            "requirement_ids": ["REQ-API-02"],
            "acceptance_tests": [
                {
                    "id": "TEST-API-01",
                    "requirement_ids": ["REQ-API-02"],
                    "method": "integration",
                    "expected": "One original run; independent acceptance verdict.",
                }
            ],
            "dependencies": [],
            "repository": "https://github.com/ABiT-Consulting/abit-ai-platform",
            "environment": "development",
            "tool_scopes": ["repository.read", "repository.write", "test.run"],
            "activity": [
                {key: a[key] for key in ("id", "text", "modified") if key in a}
                for a in self.activity
                if not (a.get("owner") == self.service_user and a["text"].startswith("ABiT governed "))
            ],
            "attachments": [],
        }
        if role == "qa" and self.implementation:
            packet.update(
                implementation_run_id=self.implementation["run"],
                implementation_user=ROLE_USERS["developer"],
                implementation_delivery=deepcopy(self.implementation["delivery"]),
            )
        if self.mutate_packet:
            packet = self.mutate_packet(deepcopy(packet))
        packet["packet_id"] = "erp-packet-" + self.modules["agentic_bridge"].digest(packet)[:48]
        packet["content_sha256"] = self.modules["agentic_bridge"].packet_digest(packet)
        return packet

    def advance(self, status):
        self.revision += 1
        self.task.update(status=status, modified="task-modified-" + str(self.revision))

    async def get(self, doctype, ident):
        if doctype == "Task":
            if ident != TASK:
                raise ValueError("Unknown test Task")
            return deepcopy(self.task)
        if doctype == "Project":
            if ident != PROJECT:
                raise ValueError("Unknown test Project")
            return deepcopy(self.project)
        if doctype == "File":
            return deepcopy(self.files[ident])
        raise AssertionError((doctype, ident))

    async def rows(self, doctype, fields, filters):
        if doctype != "File":
            raise AssertionError(doctype)
        for doc in self.files.values():
            if all(op == "=" and doc.get(field) == value for field, op, value in filters):
                yield deepcopy({field: doc[field] for field in fields})

    async def request(self, method, path, **kwargs):
        bridge = self.modules["agentic_bridge"]
        if path.endswith("get_work_packet"):
            self.packet_calls += 1
            args = kwargs["params"]
            if (args["project_id"], args["task_id"]) != (PROJECT, TASK):
                raise ValueError("Cross-project work packet refused")
            return {"message": self.packet(args["role"])}
        if path.endswith("get_logged_user"):
            return {"message": self.upload_actor}
        if path.endswith("upload_file"):
            name, content, mime = kwargs["files"]["file"]
            ident = "fixture-file-" + str(len(self.files) + 1)
            self.files[ident] = {
                "name": ident,
                "owner": self.upload_actor,
                "file_name": name,
                "file_url": "/private/files/" + name,
                "file_size": len(content),
                "content_hash": hashlib.md5(content, usedforsecurity=False).hexdigest(),
                "is_private": 1,
                "attached_to_doctype": "Task",
                "attached_to_name": kwargs["data"]["docname"],
            }
            self.uploads.append({"name": name, "bytes": content, "mime": mime})
            return {"message": {"name": ident}}
        if path.endswith("get_result_receipt"):
            event_id = kwargs["params"]["event_id"]
            self.receipt_reads.append(event_id)
            return {"message": {"exists": event_id in self.receipts, "receipt": deepcopy(self.receipts.get(event_id))}}
        if path.endswith("receive_result"):
            event = kwargs["json"]["event"]
            bridge.verify_event(event, kwargs["headers"], SECRET)
            self.signed_events.append(deepcopy(event))
            self.publish_calls.append(event["type"])
            if event["event_id"] in self.receipts:
                return {"message": deepcopy(self.receipts[event["event_id"]])}
            if event["type"] in self.lose_before_commit:
                raise TimeoutError("ERP outcome unknown before acknowledgment")
            if (event["project_id"], event["task_id"]) != (PROJECT, TASK):
                raise ValueError("Callback crossed project/task")
            if event["expected_modified"] != self.task["modified"]:
                raise ValueError("ERP Task was edited before result commit")
            if event["expected_project_revision"] != self.project["modified"]:
                raise ValueError("ERP Project changed")
            for artifact in event.get("artifacts", []):
                from urllib.parse import parse_qs, urlsplit

                file_id = parse_qs(urlsplit(artifact["url"]).query)["fid"][0]
                file_doc = self.files[file_id]
                allowed = {ROLE_USERS[event["role"]], self.service_user}
                if (
                    file_doc["owner"] not in allowed
                    or file_doc["attached_to_name"] != TASK
                    or file_doc["is_private"] != 1
                ):
                    raise ValueError("Callback evidence identity/scope was not verified")
            kind = event["type"]
            if kind == "qa.passed" and event.get("acceptance_passed") is not True:
                raise ValueError("QA callback lacks validated passing acceptance results")
            if kind == "run.started":
                self.advance("QA Reviewing" if event["role"] == "qa" else "Working")
            elif kind == "run.ready":
                self.advance("Pending Review")
                self.implementation = {
                    "invocation": event["invocation_id"],
                    "agent": event["agent_id"],
                    "run": event["run_id"],
                    "delivery": deepcopy(event["result_contract"]["delivery"]),
                }
            elif kind in {"qa.passed", "qa.rejected"}:
                result = event["result_contract"]
                if not self.implementation or (
                    result["implementation_invocation_id"],
                    result["implementation_agent_id"],
                ) != (self.implementation["invocation"], self.implementation["agent"]):
                    raise ValueError("QA does not refer to the actual implementer")
                self.advance("QA APPROVED" if kind == "qa.passed" else "QA REJECTED")
            elif kind == "run.checkpoint":
                self.advance("Working")
            else:
                self.advance("Blocked")
            receipt = {
                "event_id": event["event_id"],
                "run_id": event["run_id"],
                "durable": True,
                "task_id": TASK,
                "status": self.task["status"],
                "modified": self.task["modified"],
            }
            self.activity.append(
                {"id": "technical-" + event["event_id"], "text": "ABiT governed " + kind, "owner": self.service_user}
            )
            self.receipts[event["event_id"]] = receipt
            if kind in self.lose_ack_for:
                self.lose_ack_for.remove(kind)
                raise TimeoutError("ERP committed the original request but its acknowledgment was lost")
            return {"message": deepcopy(receipt)}
        raise AssertionError((method, path))


class ControlledDot:
    """Records external submission boundaries and returns a schema-valid reply."""

    def __init__(self, flow):
        self.flow = flow
        self.prepared = []
        self.calls = []
        self.recorded = []
        self.outcomes = {"developer": "ready_for_review", "qa": "pass"}
        self.unknown = False
        self.entered = asyncio.Event()
        self.release = None
        self.result_mutator = None
        self.after_prepare = None
        self.before_actual_submission = None

    async def prepare(self, row, packet, run_id):
        self.prepared.append((row["invocation_id"], run_id))
        if self.after_prepare:
            self.after_prepare()
        return types.SimpleNamespace(id=row["agent_id"], user_id="verified-owner")

    async def submit(self, row, packet, run_id, automation, bridge, *, before_submit):
        if self.before_actual_submission:
            self.before_actual_submission()
        await before_submit()
        self.calls.append((row["invocation_id"], run_id, row["target_role"]))
        self.entered.set()
        if self.release is not None:
            await self.release.wait()
        if self.unknown:
            return None
        outcome = self.outcomes[row["target_role"]]
        root = self.flow.artifact_root(row)
        root.mkdir(parents=True, exist_ok=True)
        filename = root / "verification.png"
        filename.write_bytes(self.flow.png)
        filename.chmod(0o640)
        evidence = {
            "id": "EVIDENCE-001",
            "kind": "screenshot",
            "uri": "artifact://verification.png",
            "sha256": hashlib.sha256(self.flow.png).hexdigest(),
            "requirement_ids": ["REQ-API-02"],
        }
        implementation = self.flow.erp.implementation
        result = {
            "schema_version": 1,
            "invocation_id": row["invocation_id"],
            "actor": deepcopy(packet["binding"]),
            "project_id": packet["project_id"],
            "task_id": packet["task_id"],
            "task_revision": packet["task_revision"],
            "work_packet_digest": self.flow.bridge_module.packet_digest(packet),
            "outcome": outcome,
            "summary": "Independent observed fixture checks, backed by exact PNG bytes.",
            "test_results": [
                {
                    "test_id": "TEST-API-01",
                    "status": "fail" if outcome == "fail" else "pass",
                    "observed": (
                        "Two dispatches observed." if outcome == "fail" else "One original invocation observed."
                    ),
                    "evidence_ids": ["EVIDENCE-001"],
                }
            ],
            "evidence": [evidence],
            "implementation_invocation_id": (
                implementation["invocation"] if row["target_role"] == "qa" and implementation else None
            ),
            "implementation_agent_id": (
                implementation["agent"] if row["target_role"] == "qa" and implementation else None
            ),
            "defects": (
                [
                    {
                        "id": "BUG-001",
                        "requirement_ids": ["REQ-API-02"],
                        "reproduction": "Submit the same fixture twice.",
                        "expected": "One execution",
                        "observed": "Two executions",
                        "evidence_ids": ["EVIDENCE-001"],
                    }
                ]
                if outcome == "fail"
                else []
            ),
        }
        if row['target_role'] == 'developer' and outcome == 'ready_for_review':
            log = b'Controlled test fixture: one original invocation observed; exit code 0.\n'
            log_file = root / 'tests.txt'
            log_file.write_bytes(log)
            log_file.chmod(0o640)
            result['evidence'][0] = {
                **evidence,
                'kind': 'test_log',
                'uri': 'artifact://tests.txt',
                'sha256': hashlib.sha256(log).hexdigest(),
            }
            delivery = {
                'repository': packet['repository'],
                'base_commit': 'a' * 40,
                'head_commit': 'b' * 40,
                'pull_request_url': packet['repository'] + '/pull/4',
                'changed_paths': ['src/fixture.py'],
                'commit_evidence_id': 'COMMIT-001',
                'test_runs': [
                    {
                        'test_ids': ['TEST-API-01'],
                        'command': 'python -m unittest controlled_fixture',
                        'exit_code': 0,
                        'log_evidence_id': 'EVIDENCE-001',
                    }
                ],
            }
            from open_webui.utils.agentic_contracts.developer_delivery import delivery_manifest

            commit = json.dumps(delivery_manifest(delivery)).encode()
            commit_file = root / 'commit.json'
            commit_file.write_bytes(commit)
            commit_file.chmod(0o640)
            result['evidence'].append(
                {
                    'id': 'COMMIT-001',
                    'kind': 'commit',
                    'uri': 'artifact://commit.json',
                    'sha256': hashlib.sha256(commit).hexdigest(),
                    'requirement_ids': packet['requirement_ids'],
                }
            )
            result['delivery'] = delivery
        if row['target_role'] == 'qa' and packet.get('implementation_delivery'):
            from open_webui.utils.agentic_contracts.developer_delivery import delivery_digest

            result['reviewed_delivery_sha256'] = delivery_digest(packet['implementation_delivery'])
        if self.result_mutator:
            result = self.result_mutator(result)
        self.flow.returned_results[row["invocation_id"]] = deepcopy(result)
        return {"response_text": "AGOV RESULT: " + json.dumps(result)}

    async def record(self, automation, run_id, record):
        self.recorded.append(run_id)


class ExecutorFlow(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_it_action_packet_is_rejected_before_adapter_preflight_or_run(self):
        with patch.dict(ROLE_USERS, {'it_manager': 'ai-operations@abitcons.com'}):
            self.ledger.enroll(PROJECT, 'verified-owner', {'it_manager': ROLE_USERS['it_manager']}, self.db)
            row, _, _ = await self.enqueue(ident='it-malformed-fixture-00001', role='it_manager')
        self.erp.mutate_packet = lambda packet: {
            **packet,
            'tool_action': {
                'request_key': 'invalid-it-fixture',
                'operation': 'inspect',
                'risk': 'routine',
                'parameters': {'command': 'unapproved-shell', 'token': 'fixture-placeholder'},
            },
        }
        adapter = types.SimpleNamespace(preflight=AsyncMock(), prepare=AsyncMock(), submit=AsyncMock())
        with patch.dict(ROLE_USERS, {'it_manager': 'ai-operations@abitcons.com'}):
            receipt = await self.executor.execute(
                row['invocation_id'],
                project_gate=self.project_gate,
                config=self.config,
                adapter=adapter,
                db_path=self.db,
                bridge=self.bridge,
            )
        self.assertIsNone(receipt)
        adapter.preflight.assert_not_awaited()
        adapter.prepare.assert_not_awaited()
        adapter.submit.assert_not_awaited()
        self.assertEqual([], self.erp.publish_calls)
        self.assertIsNone(self.ledger.find(row['invocation_id'], self.db)['automation_run_id'])

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "governance.sqlite"
        package = types.ModuleType("open_webui")
        package.__path__ = [str(UTILS.parent)]
        utils = types.ModuleType("open_webui.utils")
        utils.__path__ = [str(UTILS)]
        env = types.ModuleType("open_webui.env")
        env.DATA_DIR = self.root
        self.import_patch = patch.dict(
            sys.modules, {"open_webui": package, "open_webui.utils": utils, "open_webui.env": env}
        )
        self.import_patch.start()
        self.addCleanup(self.import_patch.stop)
        # Fresh real helper modules; no config/internal.db/main import occurs.
        for name in list(sys.modules):
            if name.startswith("open_webui.utils.agentic_") or name in {
                "open_webui.utils.erp_dot_workflow",
                "open_webui.utils.erp_task_resolution",
                "open_webui.utils.erp_status_write",
                "open_webui.utils.erp_workspace",
            }:
                sys.modules.pop(name)
        self.modules = {
            name: importlib.import_module("open_webui.utils." + name)
            for name in (
                "agentic_governance",
                "agentic_bridge",
                "agentic_executor",
                "agentic_artifacts",
                "agentic_result_store",
                "agentic_ownership",
            )
        }
        self.ledger = self.modules["agentic_governance"]
        self.bridge_module = self.modules["agentic_bridge"]
        self.executor = self.modules["agentic_executor"]
        locks = importlib.import_module("open_webui.utils.erp_status_write")
        self.addCleanup(patch.stopall)
        patch.object(locks, "LOCK_ROOT", self.root / "locks").start()
        patch.object(self.modules["agentic_artifacts"], "artifact_directory", self.artifact_root).start()
        self.config = {"agentic_artifact_roots": {PROJECT: str(self.root / "artifacts")}}
        self.erp = FakeERP(self.modules)
        self.bridge = object.__new__(self.bridge_module.ERPBridge)
        self.bridge.erp = self.erp
        self.bridge.secret = SECRET
        self.bridge.config = self.config
        self.ledger.enroll(PROJECT, "verified-owner", ROLE_USERS, self.db)
        self.ledger.project_control(
            PROJECT,
            "enable",
            actor="authorized-manager@example.test",
            policy_revision=self.erp.project["modified"],
            db_path=self.db,
        )
        image = BytesIO()
        Image.new("RGB", (8, 8), (16, 150, 220)).save(image, format="PNG")
        self.png = image.getvalue()
        self.returned_results = {}
        self.adapter = ControlledDot(self)
        self.gate_calls = 0
        self.validator = importlib.import_module("open_webui.utils.agentic_contracts.validator")

    def artifact_root(self, row, *args):
        return self.root / "artifacts" / row["erp_task"] / row["invocation_id"]

    async def project_gate(self, project, config):
        self.gate_calls += 1
        if project != PROJECT or not self.erp.project["enabled"] or self.erp.project["status"] != "Open":
            raise ValueError("Actual ERP project gate rejected")
        return deepcopy(self.erp.project), {
            "enabled": True,
            'pm_task_automation_enabled': True,
            "project_status": "Open",
            "assigned_user": "ai-projects@abitcons.com",
            "revision": self.erp.project["modified"],
        }

    async def enqueue(self, ident="developer-fixture-invocation-001", role="developer"):
        packet = await self.bridge.packet(
            PROJECT, TASK, role, self.ledger.agent_id(PROJECT, role), self.erp.task["revision"]
        )
        self.assertEqual(self.validator.validate_work_packet(packet), [])
        _, gate = await self.project_gate(PROJECT, self.config)
        row, created = self.ledger.enqueue(
            invocation_id=ident,
            project=PROJECT,
            task=TASK,
            from_role="project_manager",
            target_role=role,
            task_revision=packet["task_revision"],
            context="ERP-scoped acceptance work",
            work_packet=packet,
            gate=gate,
            actor="authenticated-pm",
            db_path=self.db,
        )
        return row, packet, created

    async def execute(self, ident):
        return await self.executor.execute(
            ident,
            project_gate=self.project_gate,
            config=self.config,
            adapter=self.adapter,
            db_path=self.db,
            bridge=self.bridge,
        )

    async def test_real_gateway_adapter_cannot_publish_model_only_developer_delivery(self):
        row, _, _ = await self.enqueue()
        self.adapter.requires_governed_gateway = True
        self.assertIsNone(await self.execute(row['invocation_id']))
        self.assertEqual(self.ledger.find(row['invocation_id'], self.db)['state'], 'uncertain')
        self.assertEqual(self.erp.uploads, [])
        self.assertNotIn(self.erp.task['status'], {'Completed', 'QA APPROVED', 'Pending Review'})

    async def test_real_gateway_qa_cannot_publish_pass_without_independent_server_receipt(self):
        row, _, _ = await self.enqueue()
        self.assertIsNotNone(await self.execute(row['invocation_id']))
        qa, _, _ = await self.handoff(row['invocation_id'], 'qa', 'qa-gateway-proof-fixture-001')
        self.adapter.requires_governed_gateway = True
        prior_uploads = len(self.erp.uploads)
        self.assertIsNone(await self.execute(qa['invocation_id']))
        self.assertEqual(self.ledger.find(qa['invocation_id'], self.db)['state'], 'uncertain')
        self.assertEqual(len(self.erp.uploads), prior_uploads)
        self.assertNotIn(self.erp.task['status'], {'Completed', 'QA APPROVED'})

    async def handoff(self, source, target, ident):
        prior = self.ledger.find(source, self.db, include_private=True)
        packet = await self.bridge.packet(
            PROJECT, TASK, target, self.ledger.agent_id(PROJECT, target), self.erp.task["revision"]
        )
        _, gate = await self.project_gate(PROJECT, self.config)
        row, created = self.ledger.handoff(
            source_invocation_id=source,
            source_run_id=prior["automation_run_id"],
            source_lease_token=prior["lease_token"],
            invocation_id=ident,
            project=PROJECT,
            task=TASK,
            from_role=prior["target_role"],
            target_role=target,
            task_revision=packet["task_revision"],
            context="Independent acceptance work" if target == "qa" else "Rework the observed defect",
            work_packet=packet,
            gate=gate,
            actor="authenticated-specialist",
            db_path=self.db,
        )
        return row, packet, created

    async def test_developer_then_separate_qa_pass_with_real_artifact_bytes_and_receipt(self):
        developer, packet, _ = await self.enqueue()
        receipt = await self.execute(developer["invocation_id"])
        self.assertIsNotNone(receipt)
        self.assertEqual(self.erp.task["status"], "Pending Review")
        self.assertEqual(self.ledger.find(developer["invocation_id"], self.db)["state"], "execution_finished")
        qa, qa_packet, _ = await self.handoff(developer["invocation_id"], "qa", "qa-fixture-invocation-001")
        approved = await self.execute(qa["invocation_id"])
        self.assertEqual(approved["status"], "QA APPROVED")
        self.assertEqual([r[2] for r in self.adapter.calls], ["developer", "qa"])
        self.assertEqual(len({run for _, run, _ in self.adapter.calls}), 2)
        self.assertEqual(len(self.erp.uploads), 3)
        self.assertEqual(sum(upload["bytes"] == self.png for upload in self.erp.uploads), 1)
        self.assertTrue(any(b'"base_commit"' in upload["bytes"] for upload in self.erp.uploads))
        stored = self.modules["agentic_result_store"].get_result(qa["invocation_id"], self.db)
        self.assertEqual(stored["phase"], "published")
        self.assertEqual(stored["event"]["implementation_run_id"], self.erp.implementation["run"])
        self.assertEqual(stored["event"]["result_contract"]["implementation_invocation_id"], developer["invocation_id"])
        self.assertTrue(stored["event"]["artifacts"][0]["url"].startswith("https://erp.abit.ai/private/files/"))
        self.assertTrue(
            self.ledger.verify_completion(
                developer["invocation_id"],
                qa["invocation_id"],
                self.bridge_module.digest(stored["event"]["artifacts"]),
                self.bridge_module.digest(approved),
                "erp-verified-publisher",
                db_path=self.db,
            )
        )

    async def test_real_dot_adapter_requires_enforced_scope_before_any_run_or_erp_start(self):
        scope = importlib.import_module('open_webui.utils.agentic_relay_scope')
        gateway = importlib.import_module('open_webui.utils.agentic_tool_gateway')
        row, _, _ = await self.enqueue()
        actual = self.executor.DotAdapter(None)
        actual.prepare = AsyncMock(side_effect=AssertionError('No run may be prepared without enforced tools'))
        with (
            patch.object(self.ledger, 'path', return_value=self.db),
            patch.object(
                scope,
                'require_relay_scope',
                new=AsyncMock(side_effect=gateway.CapabilityUnavailable('Scoped relay proof unavailable')),
            ),
        ):
            self.assertIsNone(
                await self.executor.execute(
                    row['invocation_id'],
                    project_gate=self.project_gate,
                    config=self.config,
                    adapter=actual,
                    db_path=self.db,
                    bridge=self.bridge,
                )
            )
        actual.prepare.assert_not_awaited()
        self.assertEqual(self.erp.publish_calls, [])
        self.assertEqual(self.ledger.find(row['invocation_id'], self.db)['state'], 'blocked')

    async def test_scoped_transport_is_rechecked_at_actual_submission_boundary(self):
        gateway = importlib.import_module('open_webui.utils.agentic_tool_gateway')
        row, _, _ = await self.enqueue()
        self.adapter.scope_check = AsyncMock(
            side_effect=gateway.CapabilityUnavailable('Scope revoked while waiting for relay slot')
        )
        self.assertIsNone(await self.execute(row['invocation_id']))
        self.adapter.scope_check.assert_awaited_once()
        self.assertEqual(self.adapter.calls, [])
        current = self.ledger.find(row['invocation_id'], self.db)
        self.assertIsNone(current['submitted_at'])
        self.assertEqual(current['state'], 'uncertain')

    async def test_exact_initial_and_final_proofs_are_returned_to_transport(self):
        row, _, _ = await self.enqueue()
        initial = {'fixture_initial_verified_proof': 'original-preflight'}
        final = {'fixture_final_verified_proof': 'actual-dispatch-slot'}
        self.adapter.preflight = AsyncMock(return_value=deepcopy(initial))
        self.adapter.scope_check = AsyncMock(return_value=deepcopy(final))
        original_submit = self.adapter.submit
        observed = {}

        async def submit(claimed, packet, run_id, automation, bridge, *, before_submit):
            observed['preflight'] = deepcopy(claimed['_relay_scope_preflight'])

            async def guard():
                proof = await before_submit()
                observed['submission'] = deepcopy(proof)
                return proof

            return await original_submit(claimed, packet, run_id, automation, bridge, before_submit=guard)

        self.adapter.submit = submit
        self.assertIsNotNone(await self.execute(row['invocation_id']))
        self.assertEqual(observed, {'preflight': initial, 'submission': final})
        self.adapter.scope_check.assert_awaited_once()

    async def test_qa_failure_rework_and_second_independent_pass_is_ordered(self):
        developer, _, _ = await self.enqueue()
        await self.execute(developer["invocation_id"])
        qa, _, _ = await self.handoff(developer["invocation_id"], "qa", "qa-reject-invocation-001")
        self.adapter.outcomes["qa"] = "fail"
        rejected = await self.execute(qa["invocation_id"])
        self.assertEqual(rejected["status"], "QA REJECTED")
        self.assertEqual(len(self.returned_results[qa["invocation_id"]]["defects"]), 1)
        # This is the ERP-approved rework transition, not a model status write.
        self.erp.advance("Open")
        self.erp.task["revision"] = "task-inputs-rework-2"
        rework, _, _ = await self.handoff(qa["invocation_id"], "developer", "developer-rework-invocation-001")
        await self.execute(rework["invocation_id"])
        reviewer, _, _ = await self.handoff(rework["invocation_id"], "qa", "qa-rework-pass-invocation-001")
        self.adapter.outcomes["qa"] = "pass"
        receipt = await self.execute(reviewer["invocation_id"])
        self.assertEqual(receipt["status"], "QA APPROVED")
        self.assertEqual([r[2] for r in self.adapter.calls], ["developer", "qa", "developer", "qa"])
        self.assertEqual(len({r[0] for r in self.adapter.calls}), 4)
        self.assertEqual(
            self.returned_results[reviewer["invocation_id"]]["implementation_invocation_id"], rework["invocation_id"]
        )

    async def test_parallel_duplicate_execute_and_reconciliation_never_repeat_model(self):
        row, _, _ = await self.enqueue()
        self.adapter.release = asyncio.Event()
        first = asyncio.create_task(self.execute(row["invocation_id"]))
        await asyncio.wait_for(self.adapter.entered.wait(), timeout=5)
        self.assertIsNone(await self.execute(row["invocation_id"]))
        self.adapter.release.set()
        receipt = await first
        self.assertIsNotNone(receipt)
        self.assertIsNone(await self.execute(row["invocation_id"]))
        for _ in range(3):
            self.assertEqual(
                await self.executor.reconcile_publication(row["invocation_id"], self.bridge, self.db), receipt
            )
        self.assertEqual(len(self.adapter.calls), 1)
        self.assertEqual(self.erp.publish_calls, ["run.started", "run.ready"])
        self.assertEqual(len(self.erp.uploads), 2)

    async def test_unknown_erp_result_ack_reads_receipt_without_model_or_callback_replay(self):
        row, _, _ = await self.enqueue()
        self.erp.lose_ack_for.add("run.ready")
        self.assertIsNone(await self.execute(row["invocation_id"]))
        self.assertEqual(self.erp.task["status"], "Pending Review")
        saved = self.modules["agentic_result_store"].get_result(row["invocation_id"], self.db)
        self.assertEqual(saved["phase"], "uncertain")
        self.assertIsNone(await self.execute(row["invocation_id"]))
        receipt = await self.executor.reconcile_publication(row["invocation_id"], self.bridge, self.db)
        self.assertEqual(receipt["status"], "Pending Review")
        self.assertEqual(len(self.adapter.calls), 1)
        self.assertEqual(self.erp.publish_calls, ["run.started", "run.ready"])
        self.assertEqual(len(self.erp.receipt_reads), 1)

    async def test_unknown_erp_start_ack_prevents_any_model_submission(self):
        row, _, _ = await self.enqueue()
        self.erp.lose_ack_for.add("run.started")
        self.assertIsNone(await self.execute(row["invocation_id"]))
        self.assertEqual(self.adapter.calls, [])
        self.assertEqual(self.ledger.find(row["invocation_id"], self.db)["state"], "uncertain")
        self.assertIsNone(await self.execute(row["invocation_id"]))
        self.assertEqual(self.adapter.calls, [])
        self.assertEqual(self.erp.publish_calls, ["run.started"])

    async def test_uncertain_negative_receipt_never_authorizes_repost_or_model_replay(self):
        row, _, _ = await self.enqueue()
        self.erp.lose_before_commit.add("run.ready")
        self.assertIsNone(await self.execute(row["invocation_id"]))
        for _ in range(2):
            self.assertIsNone(await self.executor.reconcile_publication(row["invocation_id"], self.bridge, self.db))
        self.assertEqual(self.erp.publish_calls, ["run.started", "run.ready"])
        self.assertEqual(len(self.adapter.calls), 1)

    async def test_unknown_dot_submission_stays_fenced_and_is_never_replayed(self):
        row, _, _ = await self.enqueue()
        self.adapter.unknown = True
        self.assertIsNone(await self.execute(row["invocation_id"]))
        self.assertEqual(self.ledger.find(row["invocation_id"], self.db)["state"], "uncertain")
        self.assertIsNone(await self.execute(row["invocation_id"]))
        self.assertEqual(self.ledger.recoverable(self.db)["queued_for_reconciliation"], [])
        self.assertEqual(len(self.adapter.calls), 1)

    async def test_lost_start_ack_recovers_same_run_before_first_model_submission(self):
        recovery = importlib.import_module('open_webui.utils.agentic_recovery')
        row, _, _ = await self.enqueue()
        self.erp.lose_ack_for.add('run.started')
        self.assertIsNone(await self.execute(row['invocation_id']))
        original = self.ledger.find(row['invocation_id'], self.db, include_private=True)
        self.assertIsNone(original['submitted_at'])
        self.assertEqual(self.adapter.calls, [])
        automation = types.SimpleNamespace(
            id=row['agent_id'],
            user_id='verified-owner',
            meta={
                'governance_managed': True,
                'agentic_project': PROJECT,
                'agentic_role': 'developer',
                'erp_role_user': ROLE_USERS['developer'],
            },
        )

        async def no_original_model(*args):
            return None

        receipt = await recovery.recover_original(
            row['invocation_id'],
            config=self.config,
            project_gate=self.project_gate,
            bridge=self.bridge,
            db_path=self.db,
            reader=no_original_model,
            adapter=self.adapter,
            automation=automation,
        )
        self.assertEqual(receipt['status'], 'Pending Review')
        self.assertEqual(len(self.adapter.prepared), 1)
        self.assertEqual(self.adapter.calls, [(row['invocation_id'], original['automation_run_id'], 'developer')])
        self.assertEqual(self.erp.publish_calls, ['run.started', 'run.ready'])
        self.assertEqual(
            self.ledger.find(row['invocation_id'], self.db)['automation_run_id'], original['automation_run_id']
        )

    async def _lost_completed_original(self):
        row, _, _ = await self.enqueue()
        with patch.object(
            self.executor, 'complete_original', side_effect=TimeoutError('Process ended after the original reply')
        ):
            self.assertIsNone(await self.execute(row['invocation_id']))
        original = self.ledger.find(row['invocation_id'], self.db, include_private=True)
        self.assertEqual(original['state'], 'uncertain')
        automation = types.SimpleNamespace(
            id=row['agent_id'],
            user_id='verified-owner',
            meta={
                'governance_managed': True,
                'agentic_project': PROJECT,
                'agentic_role': 'developer',
                'erp_role_user': ROLE_USERS['developer'],
            },
        )

        async def observe(stored, specialist):
            self.assertEqual(stored['automation_run_id'], original['automation_run_id'])
            return {'response_text': 'AGOV RESULT: ' + json.dumps(self.returned_results[row['invocation_id']])}

        return original, automation, observe

    async def test_restart_recovers_original_reply_and_same_run_without_model_replay(self):
        recovery = importlib.import_module('open_webui.utils.agentic_recovery')
        original, automation, observe = await self._lost_completed_original()
        args = dict(
            config=self.config,
            project_gate=self.project_gate,
            bridge=self.bridge,
            db_path=self.db,
            reader=observe,
            adapter=self.adapter,
            automation=automation,
        )
        receipt = await recovery.recover_original(original['invocation_id'], **args)
        self.assertEqual(receipt['status'], 'Pending Review')
        self.assertEqual(len(self.adapter.calls), 1)
        self.assertEqual(self.adapter.recorded, [original['automation_run_id']])
        self.assertEqual(
            self.ledger.find(original['invocation_id'], self.db)['automation_run_id'], original['automation_run_id']
        )
        self.assertEqual(await recovery.recover_original(original['invocation_id'], **args), receipt)
        self.assertEqual(self.erp.publish_calls, ['run.started', 'run.ready'])

    async def test_original_reply_recovery_rejects_changed_business_context(self):
        recovery = importlib.import_module('open_webui.utils.agentic_recovery')
        original, automation, observe = await self._lost_completed_original()
        self.erp.activity.append(
            {
                'id': 'new-human-note',
                'text': 'Change the acceptance scope',
                'modified': 'later',
                'owner': 'human@example.test',
            }
        )
        with self.assertRaisesRegex(ValueError, 'ERP inputs changed'):
            await recovery.recover_original(
                original['invocation_id'],
                config=self.config,
                project_gate=self.project_gate,
                bridge=self.bridge,
                db_path=self.db,
                reader=observe,
                adapter=self.adapter,
                automation=automation,
            )
        self.assertEqual(len(self.adapter.calls), 1)
        self.assertEqual(self.erp.publish_calls, ['run.started'])
        self.assertEqual(self.ledger.find(original['invocation_id'], self.db)['state'], 'uncertain')

    async def test_stop_keeps_recovered_reply_as_evidence_without_erp_completion(self):
        recovery = importlib.import_module('open_webui.utils.agentic_recovery')
        original, automation, observe = await self._lost_completed_original()
        self.ledger.request_cancel(original['invocation_id'], 'human@example.test', self.db)
        self.assertIsNone(
            await recovery.recover_original(
                original['invocation_id'],
                config=self.config,
                project_gate=self.project_gate,
                bridge=self.bridge,
                db_path=self.db,
                reader=observe,
                adapter=self.adapter,
                automation=automation,
            )
        )
        self.assertEqual(len(self.adapter.calls), 1)
        self.assertEqual(self.erp.publish_calls, ['run.started'])
        self.assertEqual(self.ledger.find(original['invocation_id'], self.db)['state'], 'cancel_requested')
        self.assertIsNone(self.modules['agentic_result_store'].get_result(original['invocation_id'], self.db))
        self.assertEqual(self.erp.uploads, [])
        with self.ledger.connect(self.db) as db:
            saved = db.execute(
                'SELECT run_id FROM governance_local_result WHERE invocation_id=?', (original['invocation_id'],)
            ).fetchone()
        self.assertEqual(saved[0], original['automation_run_id'])

    async def test_changed_workpacket_rejects_before_model_and_run_creation(self):
        row, _, _ = await self.enqueue()
        self.erp.mutate_packet = lambda p: {**p, "activity_digest": "f" * 64}
        self.assertIsNone(await self.execute(row["invocation_id"]))
        self.assertEqual(self.adapter.prepared, [])
        self.assertEqual(self.adapter.calls, [])
        self.assertEqual(self.erp.publish_calls, [])

    async def test_paused_project_rejects_before_model(self):
        row, _, _ = await self.enqueue()
        self.ledger.project_control(PROJECT, "pause", actor="erp-manager", db_path=self.db)
        self.assertIsNone(await self.execute(row["invocation_id"]))
        self.assertEqual(self.adapter.calls, [])
        self.assertEqual(self.erp.publish_calls, [])

    async def test_gate_revoked_after_claim_before_submission_never_sends_model(self):
        row, _, _ = await self.enqueue()
        self.adapter.after_prepare = lambda: self.erp.project.update(enabled=False)
        self.assertIsNone(await self.execute(row["invocation_id"]))
        self.assertEqual(self.adapter.calls, [])

    async def test_user_activity_added_inside_relay_queue_prevents_actual_submission(self):
        row, _, _ = await self.enqueue()
        self.adapter.before_actual_submission = lambda: self.erp.activity.append(
            {"id": "human-context-change", "owner": "manager@example.test", "text": "Acceptance changed while queued"}
        )
        self.assertIsNone(await self.execute(row["invocation_id"]))
        self.assertEqual(self.adapter.calls, [])
        self.assertNotIn("run.ready", self.erp.publish_calls)

    async def test_stop_inside_relay_queue_fences_original_without_model_call(self):
        row, _, _ = await self.enqueue()
        self.adapter.before_actual_submission = lambda: self.ledger.request_cancel(
            row["invocation_id"], "erp-manager", self.db
        )
        self.assertIsNone(await self.execute(row["invocation_id"]))
        self.assertEqual(self.adapter.calls, [])
        self.assertEqual(self.ledger.find(row["invocation_id"], self.db)["state"], "cancel_requested")

    async def test_cross_project_packet_rejected_without_dispatch(self):
        row, _, _ = await self.enqueue()
        self.erp.mutate_packet = lambda p: {**p, "project_id": "PROJ-0091"}
        self.assertIsNone(await self.execute(row["invocation_id"]))
        self.assertEqual(self.adapter.calls, [])
        self.assertEqual(self.erp.publish_calls, [])

    async def test_forged_qa_actor_or_incomplete_acceptance_cannot_publish_approval(self):
        developer, _, _ = await self.enqueue()
        await self.execute(developer["invocation_id"])
        qa, _, _ = await self.handoff(developer["invocation_id"], "qa", "qa-invalid-verdict-001")
        self.adapter.result_mutator = lambda value: {**value, "test_results": []}
        self.assertIsNone(await self.execute(qa["invocation_id"]))
        self.assertNotEqual(self.erp.task["status"], "QA APPROVED")
        self.assertNotIn("qa.passed", self.erp.publish_calls)

    async def test_qa_cannot_approve_a_different_original_implementation_invocation(self):
        developer, _, _ = await self.enqueue()
        await self.execute(developer["invocation_id"])
        qa, _, _ = await self.handoff(developer["invocation_id"], "qa", "qa-foreign-implementation-001")
        self.adapter.result_mutator = lambda value: {
            **value,
            "implementation_invocation_id": "another-completed-developer-invocation",
        }
        self.assertIsNone(await self.execute(qa["invocation_id"]))
        self.assertNotIn("qa.passed", self.erp.publish_calls)
        self.assertEqual(len(self.erp.uploads), 2)

    async def test_human_comment_cannot_masquerade_as_excluded_technical_activity(self):
        row, _, _ = await self.enqueue()
        self.adapter.before_actual_submission = lambda: self.erp.activity.append(
            {
                "id": "human-context-change",
                "owner": "manager@example.test",
                "text": "ABiT governed new human requirement",
            }
        )
        self.assertIsNone(await self.execute(row["invocation_id"]))
        self.assertEqual(self.adapter.calls, [])

    async def test_forged_artifact_hash_cannot_publish_ready_or_approval(self):
        row, _, _ = await self.enqueue()

        def tamper(result):
            result["evidence"][0]["sha256"] = "f" * 64
            return result

        self.adapter.result_mutator = tamper
        self.assertIsNone(await self.execute(row["invocation_id"]))
        self.assertNotIn("run.ready", self.erp.publish_calls)
        self.assertEqual(self.erp.uploads, [])

    async def test_changed_delivery_without_matching_private_manifest_cannot_publish(self):
        row, _, _ = await self.enqueue()

        def tamper(result):
            result['delivery']['head_commit'] = 'c' * 40
            return result

        self.adapter.result_mutator = tamper
        self.assertIsNone(await self.execute(row['invocation_id']))
        self.assertNotIn('run.ready', self.erp.publish_calls)
        self.assertEqual(self.erp.uploads, [])

    async def test_qa_cannot_approve_a_different_code_delivery_digest(self):
        developer, _, _ = await self.enqueue()
        await self.execute(developer['invocation_id'])
        qa, _, _ = await self.handoff(developer['invocation_id'], 'qa', 'qa-different-delivery-001')
        self.adapter.result_mutator = lambda result: {**result, 'reviewed_delivery_sha256': 'f' * 64}
        self.assertIsNone(await self.execute(qa['invocation_id']))
        self.assertNotIn('qa.passed', self.erp.publish_calls)
        self.assertEqual(len(self.erp.uploads), 2)

    async def test_existing_legacy_task_owner_blocks_governed_model_until_its_run_finishes(self):
        row, _, _ = await self.enqueue()
        with sqlite3.connect(self.root / "webui.db") as db:
            db.executescript(
                "CREATE TABLE automation(id TEXT PRIMARY KEY, meta TEXT); CREATE TABLE automation_run(id TEXT PRIMARY KEY,automation_id TEXT,status TEXT);"
            )
            db.execute("INSERT INTO automation VALUES(?,?)", ("old-task-agent", json.dumps({"erp_task": TASK})))
            db.execute("INSERT INTO automation_run VALUES(?,?,?)", ("old-original-run", "old-task-agent", "running"))
        self.assertIsNone(await self.execute(row["invocation_id"]))
        self.assertEqual(self.adapter.calls, [])
        self.assertEqual(self.erp.publish_calls, [])
        with sqlite3.connect(self.root / "webui.db") as db:
            db.execute("UPDATE automation_run SET status='success' WHERE id='old-original-run'")
        # A technical preflight block never retries implicitly. An explicit
        # authorized retry reuses the proven-unsubmitted original invocation.
        self.assertIsNone(await self.execute(row["invocation_id"]))
        retry = importlib.import_module("open_webui.utils.agentic_unsubmitted")
        packet = await self.bridge.packet(PROJECT, TASK, "developer", self.ledger.agent_id(PROJECT, "developer"))
        _, gate = await self.project_gate(PROJECT, self.config)
        retry.rearm(row["invocation_id"], packet, gate, "authorized-manager", self.db)
        self.assertEqual((await self.execute(row["invocation_id"]))["status"], "Pending Review")
        self.assertEqual(len(self.adapter.calls), 1)

    async def test_service_interruption_after_model_submission_retains_uncertain_original_fence(self):
        row, _, _ = await self.enqueue()
        self.adapter.release = asyncio.Event()
        execution = asyncio.create_task(self.execute(row["invocation_id"]))
        await asyncio.wait_for(self.adapter.entered.wait(), timeout=5)
        execution.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await execution
        self.assertEqual(self.ledger.find(row["invocation_id"], self.db)["state"], "uncertain")
        self.assertIsNone(await self.execute(row["invocation_id"]))
        self.assertEqual(len(self.adapter.calls), 1)
        self.assertEqual(self.ledger.recoverable(self.db)["queued_for_reconciliation"], [])

    async def test_saved_original_result_is_immutable_and_duplicate_save_is_idempotent(self):
        row, _, _ = await self.enqueue()
        await self.execute(row["invocation_id"])
        store = self.modules["agentic_result_store"]
        saved = store.get_result(row["invocation_id"], self.db)
        self.assertFalse(
            store.save_result(row["invocation_id"], saved["run_id"], saved["result"], saved["event"], self.db)
        )
        changed = {**saved["result"], "summary": "Different unverified outcome"}
        with self.assertRaises(ValueError):
            store.save_result(row["invocation_id"], saved["run_id"], changed, saved["event"], self.db)


if __name__ == "__main__":
    unittest.main()
