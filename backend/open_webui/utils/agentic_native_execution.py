"""Explicit verification-only IT adapter; ordinary IT and Dot are unchanged."""
from __future__ import annotations
import hashlib
import json
import os

from open_webui.utils import agentic_governance as ledger
from open_webui.utils.agentic_bridge import canonical, packet_digest, same_business_context
from open_webui.utils.agentic_native_contract import require
from open_webui.utils.agentic_native_verification import (
    NativeRuntime, configured_runtime, selected_packet, scope_for, MANAGED,
)
from open_webui.utils.agentic_tool_gateway import CapabilityUnavailable


class NativeVerificationAdapter:
    requires_governed_gateway = False

    def __init__(self, app, project_gate, config, db_path=None, runtime=None):
        require(runtime is None or isinstance(runtime, NativeRuntime), "NATIVE_TYPED_RUNTIME_REQUIRED")
        self.app, self.project_gate, self.config, self.db_path = app, project_gate, config, db_path
        self.runtime, self.gate, self.action = runtime, None, None

    def runtime_for(self, row):
        if self.runtime is None:
            self.runtime = configured_runtime(self.config, project=row["erp_project"],
                                              project_gate=self.project_gate, db_path=self.db_path)
        if self.runtime is None:
            raise CapabilityUnavailable("Native verification has no registered root broker; no execution is permitted")
        return self.runtime

    async def preflight(self, row, packet):
        scope_for(row, packet)
        from open_webui.utils.agentic_tool_gateway import _profile
        from open_webui.utils.agentic_native_contract import OPERATION
        profile = _profile(self.config, row["erp_project"])
        require(OPERATION in profile.get("operations", []) and
                profile.get("environment_class") in {"development", "test"},
                "NATIVE_DEVELOPMENT_VERIFICATION_PROFILE_REQUIRED")
        self.gate = await self.runtime_for(row).prepare_gate(row, packet)
        # No relay proof is returned or admitted for this separate native lane.
        return None

    async def prepare(self, row, packet, run_id):
        from open_webui.utils.agentic_executor import DotAdapter
        return await DotAdapter(self.app).prepare(row, packet, run_id)

    async def recover_prepare(self, row, packet, run_id):
        from open_webui.utils.agentic_executor import DotAdapter
        return await DotAdapter(self.app).recover_prepare(row, packet, run_id)

    async def submit(self, row, packet, run_id, automation, bridge, *, before_submit):
        from open_webui.utils.agentic_it_execution import make_action
        from open_webui.utils.agentic_tool_gateway import execute_action
        await before_submit()
        self.action = make_action(row, packet)
        result = await execute_action(row["invocation_id"], self.action, project_gate=self.project_gate,
            config=self.config, bridge=bridge, adapters=self.registry(bridge), db_path=self.db_path)
        if result and result["state"] == "completed" and isinstance(result.get("result"), dict):
            return make_record(row, packet, result["result"]["native_receipt"], self.config)
        return None

    def registry(self, bridge):
        from open_webui.utils.agentic_tool_gateway import TrustedAdapter, _fresh
        from open_webui.utils.agentic_native_verification import original
        async def preflight(action, scope, profile):
            row = ledger.find(scope["invocation_id"], self.db_path, include_private=True)
            packet = json.loads(row["work_packet"])
            scope_for(row, packet)
            return await self.runtime_for(row).prepare_gate(row, packet, bridge=bridge, claimed=True)

        async def execute(action, scope, gate, profile):
            row = ledger.find(scope["invocation_id"], self.db_path, include_private=True)
            packet = json.loads(row["work_packet"])
            async def current_authority():
                return await _fresh(row["invocation_id"], action, self.project_gate, self.config,
                                    bridge, profile, adapter, self.db_path)
            receipt = await self.runtime_for(row).submit(row, packet, gate, bridge, current_authority)
            if receipt["state"] != "completed":
                raise RuntimeError("Native original is unresolved or failed; observe its retained receipt only")
            return {"native_receipt": receipt, "executed": True, "automatic_replay_permitted": False}

        async def recover(action, scope, gate, profile):
            row = ledger.find(scope["invocation_id"], self.db_path, include_private=True)
            saved = original(row["invocation_id"], self.db_path)
            if saved is None:
                runtime = self.runtime_for(row)
                if runtime.per_run:
                    await runtime.observe_admission(row, json.loads(row["work_packet"]), bridge)
                return None
            if saved["phase"] == "bound":
                return None
            packet = json.loads(row["work_packet"])
            receipt = await self.runtime_for(row).observe(row, packet, bridge)
            return {"native_receipt": receipt, "executed": False, "automatic_replay_permitted": False}

        adapter = TrustedAdapter(preflight, execute, capability="verify_native_source",
                                 roles=("it_manager",), read_only=False, recover=recover)
        from open_webui.utils.agentic_native_contract import OPERATION
        return {OPERATION: adapter}

    async def record(self, automation, run_id, record):
        from open_webui.utils.agentic_it_execution import ITAdapter
        return await ITAdapter.record(self, automation, run_id, record)


def make_record(row, packet, receipt, config):
    if receipt["state"] not in {"completed", "failed_retained"}:
        return None
    from open_webui.utils.agentic_artifacts import artifact_directory, ensure_directory, local_bytes
    from open_webui.utils.agentic_tool_gateway import _open_dir
    root = artifact_directory(row, config)
    ensure_directory(root)
    name = "native-verification-receipt.json"
    content = canonical(receipt).encode()
    require(len(content) <= 8192, "NATIVE_PUBLIC_RECEIPT_TOO_LARGE")
    directory = _open_dir(root)
    try:
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640, dir_fd=directory)
        except FileExistsError:
            _, previous = local_bytes(root, "artifact://" + name)
            require(previous == content, "NATIVE_EVIDENCE_IMMUTABLE")
        else:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
                os.fchmod(handle.fileno(), 0o640)
            os.fsync(directory)
    finally:
        os.close(directory)
    completed = receipt["state"] == "completed"
    result = {
        "schema_version": 1, "invocation_id": row["invocation_id"], "actor": packet["binding"],
        "project_id": row["erp_project"], "task_id": row["erp_task"],
        "task_revision": packet["task_revision"], "work_packet_digest": packet_digest(packet),
        "outcome": "ready_for_review" if completed else "blocked",
        "summary": (
            "The exact fixed native verification plan and owned cleanup have root receipts. Independent QA must verify this Task; no source development or publication is claimed."
            if completed else "The original fixed native attempt failed and is retained. No automatic retry, new nonce or business completion is authorized."),
        "test_results": [{"test_id": item["id"], "status": "skipped",
                          "observed": "Original native receipt is attached. Independent Task acceptance testing remains required.",
                          "evidence_ids": ["native-receipt"]} for item in packet["acceptance_tests"]],
        "evidence": [{"id": "native-receipt", "kind": "test_log", "uri": "artifact://" + name,
                      "sha256": hashlib.sha256(content).hexdigest(), "requirement_ids": packet["requirement_ids"]}],
        "implementation_invocation_id": None, "implementation_agent_id": None,
        "defects": [] if completed else [{
            "id": "native-original-failed", "requirement_ids": packet["requirement_ids"],
            "evidence_ids": ["native-receipt"],
            "reproduction": "Review the original root-owned native attempt referenced by the bound receipt; do not replay it.",
            "observed": "The original attempt has a retained failure receipt.",
            "expected": "Resolve the retained failure and obtain a separately reviewed fresh task and authority before another native attempt.",
        }],
    }
    return {"response_text": "AGOV RESULT: " + canonical(result),
            "transport": "trusted-native-verification", "model_requests": 0}


async def recover_completed(invocation, *, config, project_gate, bridge, app=None, db_path=None,
                            adapter=None, automation=None):
    """Read the original root receipt; never call prepare or execute again."""
    from open_webui.utils.agentic_executor import complete_original
    from open_webui.utils.agentic_controls import task_control
    from open_webui.utils.agentic_native_verification import admission_receipt
    row = ledger.find(invocation, db_path, include_private=True)
    if not row or row["target_role"] != "it_manager" or row["state"] not in {"uncertain", "cancel_requested"}:
        return None
    packet = json.loads(row["work_packet"])
    if not selected_packet(packet):
        return None
    chosen = adapter or NativeVerificationAdapter(app, project_gate, config, db_path)
    require(isinstance(chosen, NativeVerificationAdapter), "NATIVE_RECOVERY_ADAPTER_REQUIRED")
    receipt = await chosen.runtime_for(row).observe(row, packet, bridge)
    # The receipt can be retained after Stop, but only ordinary current project,
    # Task, ERP context and publication gates can turn it into a handoff.
    if row["state"] == "cancel_requested" or ledger.project_control(row["erp_project"], db_path=db_path)["state"] != "enabled":
        return None
    if task_control(row["erp_project"], row["erp_task"], db_path=db_path)["state"] != "enabled":
        return None
    await project_gate(row["erp_project"], config)
    fresh = await bridge.packet(row["erp_project"], row["erp_task"], "it_manager", row["agent_id"])
    require(same_business_context(packet, fresh, MANAGED), "NATIVE_RECOVERY_PACKET_CHANGED")
    start = admission_receipt(row, packet, db_path)
    require(fresh["source_modified"] == start["modified"], "NATIVE_RECOVERY_ERP_START_CHANGED")
    record = make_record(row, packet, receipt, config)
    if record is None:
        return None
    if automation is None:
        from open_webui.models.automations import Automations
        automation = await Automations.get_by_id(row["agent_id"])
    return await complete_original(row, packet, record, chosen, automation, bridge,
                                   {"modified": fresh["source_modified"]}, project_gate, config, db_path)


async def resume(invocation, *, config, project_gate, bridge, app=None, db_path=None):
    """Use the existing human approval wait/consume path with only this adapter."""
    from open_webui.utils.agentic_it_execution import resume as resume_it
    chosen = NativeVerificationAdapter(app, project_gate, config, db_path)
    return await resume_it(invocation, config=config, project_gate=project_gate, bridge=bridge,
                           app=app, db_path=db_path, adapters=chosen.registry(bridge))
