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


def developer_result_guidance(role):
    """Describe existing Developer outcomes without changing result validation."""
    if role != "developer":
        return ""
    return (
        "DEVELOPER RESULT CONTRACT: Allowed result outcomes are continue, blocked, or ready_for_review. "
        "Only independent QA may return a verification verdict. For an observed implementation "
        "or test failure, return outcome blocked with the actual failure, evidence and next action; "
        "retain genuine defects and failed packet test_results entries with status fail and their real "
        "evidence_ids. A test_results status is an observation, separate from the result outcome. "
        "Report only packet acceptance-test IDs as test_results; retain other registered command "
        "failures in evidence and the blocker summary without inventing acceptance-test mappings. "
        "Use continue for unfinished work with a concrete checkpoint. Use ready_for_review only with "
        "the required actual commit, PR and passing test/delivery evidence. Never relabel a failure "
        "as passing, claim QA approval or change ERP status directly. A separate server-only path may "
        "preserve a previously captured Developer fail as an implementation blocker when exact original "
        "failed-test receipts prove it; do not select fail to invoke that path. It grants no QA approval."
    )


def model_contracts(role):
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
        "test results, approvals, artifact hashes or completion. "
        "Use exact acceptance_tests[].id values for test_results[].test_id. "
        "Repository command IDs are operational checks, not acceptance IDs: "
        "describe their receipts or denials in summary, defects and evidence. "
        "Never infer acceptance from a command exit code."
    )
    developer = developer_result_guidance(role)
    if developer:
        response["result_contract_guidance"] += "\n" + developer
    return response
