"""Close only the saved-original/result-to-execution transition after interruption.

No provider submission, response reconstruction, artifact upload, replacement
result or native status write occurs here. Publication/QA/PM keep their existing
idempotent transitions and authorization checks.
"""
from __future__ import annotations

import hashlib
import json

from open_webui.utils import agentic_governance as ledger
from open_webui.utils.agentic_bridge import (
    canonical, digest, packet_digest, same_business_context, stable_run_id,
    validate_packet_identity,
)
from open_webui.utils.agentic_result_store import get_result

_UNFINISHED = {"running", "uncertain"}
_IDENTITY = (
    "invocation_id", "automation_run_id", "agent_id", "erp_project", "erp_task",
    "target_role", "task_revision", "packet_hash", "work_packet", "lease_token",
    "lease_fence", "submitted_at",
)
_MANAGED = {
    "source_modified", "packet_id", "content_sha256", "implementation_user",
    "implementation_run_id", "attachments",
}


def _proof(row, saved, db_path):
    """Verify the server's canonical result and exact original publication intent."""
    from open_webui.utils.agentic_executor import result_event
    from open_webui.utils.agentic_start_recovery import project_native_result

    packet = json.loads(row["work_packet"])
    validate_packet_identity(
        packet, row["erp_project"], row["erp_task"], row["target_role"],
        row["agent_id"], row["task_revision"],
    )
    result, event = saved["result"], saved["event"]
    if (
        row["submitted_at"] is None
        or row["automation_run_id"] != stable_run_id(row["invocation_id"])
        or row["packet_hash"] != digest(packet)
        or saved["run_id"] != row["automation_run_id"]
        or saved["result_sha256"] != digest(result)
        or result.get("invocation_id") != row["invocation_id"]
        or result.get("actor") != packet["binding"]
        or result.get("work_packet_digest") != packet_digest(packet)
        or any(result.get(key) != packet[key] for key in ("project_id", "task_id", "task_revision"))
        or not isinstance(event.get("expected_modified"), str)
        or not event["expected_modified"]
        or saved["phase"] not in {"pending", "uncertain", "published"}
    ):
        raise ValueError("Saved result differs from the original submitted invocation")

    phase = None
    if row["target_role"] == "project_manager" and result.get("outcome") == "reviewed" and not packet.get("qa_review"):
        from open_webui.utils.agentic_architecture_review import verify_saved_phase
        phase = verify_saved_phase(row, packet, result, db_path=db_path)
    expected = result_event(
        row, packet, result, row["automation_run_id"], event.get("artifacts"),
        event["expected_modified"], architecture_review=phase,
    )
    with ledger.connect(db_path) as db:
        local = db.execute(
            "SELECT run_id,result_json FROM governance_local_result WHERE invocation_id=?",
            (row["invocation_id"],),
        ).fetchone()
        if not local or local["run_id"] != row["automation_run_id"] or local["result_json"] != canonical(result):
            raise ValueError("Saved publication has no matching original canonical local result")
        expected, projection = project_native_result(row, packet, result, expected, db=db)
        if projection:
            audits = db.execute(
                "SELECT payload FROM governance_event WHERE invocation_id=? AND kind='readiness_native_result'",
                (row["invocation_id"],),
            ).fetchall()
            if len(audits) != 1 or json.loads(audits[0][0]) != projection:
                raise ValueError("Saved readiness projection audit changed")
    if event != expected:
        raise ValueError("Saved event is not the exact original canonical publication intent")
    if saved["phase"] == "published":
        receipt = saved["receipt"]
        if (
            not isinstance(receipt, dict) or receipt.get("durable") is not True
            or any(receipt.get(key) != event[key] for key in ("event_id", "task_id", "run_id"))
            or not receipt.get("modified") or not receipt.get("status")
        ):
            raise ValueError("Saved publication receipt does not confirm the original event")
    return packet, result, phase


async def finish_saved_original(invocation, *, config, project_gate, bridge, db_path=None, automation=None):
    """Restore an unfinished submitted result through the existing fenced finish API."""
    row = ledger.find(invocation, db_path, include_private=True)
    saved = get_result(invocation, db_path)
    if not row or not saved or row["state"] not in _UNFINISHED:
        return False
    # IT and never-submitted readiness have separate trusted action journals.
    if row["target_role"] == "it_manager" or row["submitted_at"] is None:
        return False
    packet, result, phase = _proof(row, saved, db_path)

    from open_webui.models.automations import Automations
    automation = automation or await Automations.get_by_id(row["agent_id"])
    roster = ledger.roster(row["erp_project"], db_path)
    registered = [value for value in roster if value["agent_id"] == row["agent_id"]]
    meta = (automation.meta or {}) if automation else {}
    if (
        len(registered) != 1 or not automation
        or automation.id != row["agent_id"]
        or automation.user_id != registered[0]["owner_id"]
        or registered[0]["role"] != row["target_role"]
        or packet["binding"]["erp_user"] != registered[0]["erp_user"]
        or meta.get("agentic_project") != row["erp_project"]
        or meta.get("agentic_role") != row["target_role"]
        or meta.get("erp_role_user") != registered[0]["erp_user"]
        or meta.get("governance_managed") is not True
    ):
        raise ValueError("Saved original specialist binding or owner changed")

    async def current_authority():
        _, gate = await project_gate(row["erp_project"], config)
        with ledger.connect(db_path) as db:
            ledger._gate(db, row["erp_project"], gate)
            ledger._task_gate(db, row["erp_project"], row["erp_task"])
        task = await bridge.erp.get("Task", row["erp_task"])
        if (
            task.get("project") != row["erp_project"]
            or task.get("status") in {"Completed", "Cancelled"}
            or not packet.get("qa_review") and task.get("custom_agov_active_run") != row["automation_run_id"]
        ):
            raise ValueError("Saved original no longer owns its active native Task")
        fresh = await bridge.packet(
            row["erp_project"], row["erp_task"], row["target_role"], row["agent_id"],
        )
        validate_packet_identity(
            fresh, row["erp_project"], row["erp_task"], row["target_role"],
            row["agent_id"], row["task_revision"],
        )
        if not same_business_context(packet, fresh, _MANAGED):
            from open_webui.utils.agentic_recovery_telemetry import RecoveryContextChanged
            raise RecoveryContextChanged("Saved original business context changed; preserve its evidence")
        return gate

    await current_authority()
    bindings = {value["role"]: {"agent_id": value["agent_id"], "erp_user": value["erp_user"]} for value in roster}
    from open_webui.utils.agentic_project_profiles import resolve_repository_profile, ProfileUnavailable
    registered_tests = frozenset()
    try:
        profile, _ = resolve_repository_profile(config, row["erp_project"], packet["repository"], packet["environment"])
        registered_tests = frozenset(profile["tests"])
    except ProfileUnavailable:
        pass
    from open_webui.utils.agentic_blocker_review import failure_proof, require_triage_outcome
    failure = failure_proof(row, result, db_path) if config.get("agentic_pm_blocker_triage") is True else None
    from open_webui.utils.agentic_contracts.validator import validate_result
    errors = validate_result(
        result, packet, registered_bindings=bindings, architecture_review=phase,
        registered_blocked_tests=registered_tests, execution_failure=bool(failure),
    )
    if errors:
        raise ValueError("; ".join(errors)[:2000])
    require_triage_outcome(row, result, db_path, event=saved["event"])

    # Only read/validate original code, test, dependency and evidence receipts.
    # This neither runs tests nor invokes developer delivery or approval actions.
    from open_webui.utils.agentic_artifacts import artifact_directory, local_bytes, verify_developer_manifest
    root = artifact_directory(row, config)
    artifacts = {item["id"]: item for item in saved["event"].get("artifacts") or []}
    if len(artifacts) != len(result["evidence"]):
        raise ValueError("Saved publication evidence does not match its canonical result")
    for item in result["evidence"]:
        actual = artifacts.get(item["id"])
        if not actual or any(actual.get(key) != item[key] for key in ("kind", "sha256", "requirement_ids")):
            raise ValueError("Saved evidence identity or content hash changed")
        _, raw = local_bytes(root, item["uri"])
        if hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise ValueError("Original evidence bytes changed after result capture")
    verify_developer_manifest(row, config, result)
    from open_webui.utils.agentic_gateway_delivery import assert_gateway_result
    await assert_gateway_result(row, packet, result, config, db_path=db_path, bridge=bridge)
    if row["target_role"] == "qa":
        from open_webui.utils.agentic_executor import implementation_source
        source = implementation_source(row, packet, db_path)
        if (result.get("implementation_invocation_id") != source["invocation_id"]
                or result.get("implementation_agent_id") != source["agent_id"]):
            raise ValueError("Saved QA verdict differs from the exact implementation predecessor")
    from open_webui.utils.agentic_orchestrator import _task_done
    for dependency in packet["dependencies"]:
        if not await _task_done(bridge, row["erp_project"], dependency, db_path):
            raise ValueError("Original dependency acceptance is no longer current")

    gate = await current_authority()
    current = ledger.find(invocation, db_path, include_private=True)
    if not current or any(current.get(key) != row.get(key) for key in _IDENTITY):
        raise ValueError("Saved original identity changed during completion recovery")
    if current["state"] not in _UNFINISHED:
        return False  # A concurrent finish or human revocation already won.
    if get_result(invocation, db_path) != saved:
        raise ValueError("Saved original publication changed during completion recovery")
    qa = result["outcome"] if row["target_role"] == "qa" and result["outcome"] in {"pass", "fail", "blocked"} else None
    return ledger.finish_execution(
        invocation, row["automation_run_id"], row["lease_token"],
        "execution_finished", qa_verdict=qa, gate=gate, db_path=db_path,
    )
