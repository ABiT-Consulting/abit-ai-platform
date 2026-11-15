"""Fixed local contracts returned as data to an admitted governance model.

These schemas document the existing validators. They do not grant an operation,
select a file, contact a remote schema server, or change validation policy.
"""

from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path

SCHEMAS = Path(__file__).resolve().parent / "agentic_contracts" / "schemas"
ROLES = frozenset({"solution_architect", "project_manager", "developer", "qa", "operations", "it_manager"})


def _references(value):
    if isinstance(value, dict):
        reference = value.get("$ref")
        if reference is not None:
            if not isinstance(reference, str) or not reference.startswith("#/$defs/"):
                raise ValueError("Only fixed local governance schema references are supported")
            yield reference[len("#/$defs/") :]
        for child in value.values():
            yield from _references(child)
    elif isinstance(value, list):
        for child in value:
            yield from _references(child)


def _read(name):
    # Names are literal call sites below, never a model argument.
    raw = (SCHEMAS / name).read_bytes()
    if len(raw) > 100_000:
        raise ValueError("Fixed governance schema exceeds its bound")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Fixed governance schema must be an object")
    return value


@lru_cache(maxsize=1)
def _contracts():
    source = _read("governance_v1.schema.json")
    definitions = source["$defs"]
    pending, selected = ["result"], {}
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        selected[name] = definitions[name]
        pending.extend(_references(definitions[name]))
    result = {"$schema": source["$schema"], "$ref": "#/$defs/result", "$defs": selected}
    architect = _read("solution_architect_plan.schema.json")
    for name in _references(architect):
        if name not in architect["$defs"]:
            raise ValueError("Architect contract contains an unresolved local definition")
    return result, architect


def planning_pm_result_guidance(role, packet):
    """Describe the existing planning verdict; never grant or infer authority."""
    if role != "project_manager" or not isinstance(packet, dict) or "qa_review" in packet:
        return ""
    return (
        "PLANNING PM RESULT CONTRACT: This is a planning PM turn without a bound qa_review. "
        "Review the exact handoff source against the SRS and Project backlog. Successful planning "
        "review returns outcome ready_for_review with concrete planning review evidence; unresolved "
        "work uses continue or blocked with a concrete checkpoint. The outcome reviewed is also supported only when the server explicitly supplies "
        "architecture-review.v1 phase guidance for the exact published Architect source; otherwise "
        "reviewed and qa_review_ack remain reserved for a server-bound QA coordination turn. Return this PM's "
        "invocation_id, not the Architect predecessor or outer AutomationRun ID; copy the exact "
        "packet actor, Project, Task, revision and digest. Set implementation_invocation_id and "
        "implementation_agent_id to null for this planning verdict. Do not return task_plan or "
        "architect_plan. When an Architect predecessor is supplied, the server uses its original "
        "published plan after the validated PM result. test_results may be empty when no packet "
        "acceptance test was observed; report only actual packet tests with real evidence, never "
        "invented implementation or QA passes. This verdict grants no direct ERP Task creation "
        "or completion authority."
    )


def model_contracts(role, *, packet=None):
    if role not in ROLES:
        raise ValueError("Unsupported governance role")
    result, architect = _contracts()
    response = {"result_contract_schema": deepcopy(result)}
    if role in {"solution_architect", "project_manager"}:
        response["architect_plan_schema"] = deepcopy(architect)
    response["result_contract_guidance"] = (
        "Return exactly one AGOV RESULT: line containing JSON conforming to "
        "result_contract_schema. Copy actor and all Project/Task/revision/packet "
        "identities from this exact work packet. Use null implementation identities "
        "when there is no predecessor. Schema validity is only structural: actual "
        "evidence bytes, server test/delivery receipts, current authority and the "
        "role-specific acceptance validators remain mandatory. Do not fabricate "
        "test results, approvals, artifact hashes or completion."
    )
    planning = planning_pm_result_guidance(role, packet)
    if planning:
        response["result_contract_guidance"] += "\n" + planning
    return response
