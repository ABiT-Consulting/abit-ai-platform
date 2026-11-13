#!/usr/bin/env python3
"""Offline schema + semantic checks for a project Solution Architect plan.

This does NOT authorize an ERP task write or prove an ABIT.AI run. The integration
must also resolve ERP SRS versions, signed agent bindings and remote artifact bytes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

try:
    from jsonschema import Draft202012Validator, FormatChecker
except ImportError as exc:
    raise SystemExit("Install test dependency: pip install -r requirements-agentic-governance.txt") from exc

ROOT = Path(__file__).resolve().parents[1]
LOCAL_SCHEMA = Path(__file__).resolve().parent / "schemas/solution_architect_plan.schema.json"
SCHEMA = LOCAL_SCHEMA if LOCAL_SCHEMA.is_file() else ROOT / "schemas/solution_architect_plan.schema.json"


def _verify_repo_artifact(uri: str, expected_sha: str, root: Path) -> str | None:
    if not uri.startswith("repo:"):
        return None  # Remote ERP/content storage must be verified by authenticated API.
    path = uri.removeprefix("repo:")
    parsed = PurePosixPath(path)
    if not path or parsed.is_absolute() or ".." in parsed.parts or "." in parsed.parts:
        return "unsafe repo artifact path"
    destination = (root / path).resolve()
    try:
        destination.relative_to(root.resolve())
    except ValueError:
        return "repo artifact escapes the approved project root"
    if not destination.is_file():
        return "repo artifact not found"
    actual = hashlib.sha256(destination.read_bytes()).hexdigest()
    if actual != expected_sha:
        return "repo artifact content hash mismatch"
    return None


def validate_plan(plan: dict, *, root: Path = ROOT) -> list[str]:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = [
        f"schema at {'.'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
        for e in validator.iter_errors(plan)
    ]
    if errors:
        return sorted(errors)

    requirements = {r["id"]: r for r in plan["requirements"]}
    designs = {d["id"]: d for d in plan["designs"]}
    mockups = {m["id"]: m for m in plan["mockups"]}
    tasks = {t["task_key"]: t for t in plan["tasks"]}
    tests = {t["id"]: t for t in plan["tests"]}
    decisions = {d["id"]: d for d in plan["decisions"]}
    meta = plan["metadata"]

    def unique(values: list[str], label: str) -> None:
        for item, count in Counter(values).items():
            if count > 1:
                errors.append(f"duplicate {label}: {item}")

    for collection, key, label in [
        (plan["requirements"], "id", "requirement ID"),
        (plan["designs"], "id", "design ID"),
        (plan["mockups"], "id", "mockup ID"),
        (plan["tasks"], "task_key", "task key"),
        (plan["tests"], "id", "QA test ID"),
        (plan["decisions"], "id", "decision ID"),
    ]:
        unique([c[key] for c in collection], label)
    unique([t["idempotency_key"] for t in plan["tasks"]], "task idempotency key")

    if meta["agent_binding"]["project_id"] != meta["project_id"]:
        errors.append("architect agent binding project mismatches SRS project")
    url = urlsplit(meta["source_url"])
    if (url.scheme != "https" or url.netloc != "erp.abit.ai" or url.query or url.fragment
            or url.path != f'/desk/project/{meta["project_id"]}'):
        errors.append("source SRS URL does not match ERP project")

    def existing_refs(refs: list[str], known: dict, subject: str) -> None:
        for ref in refs:
            if ref not in known:
                errors.append(f"{subject}: unknown reference {ref}")

    artifacts = {**designs, **mockups}
    for group, rows in [("design", plan["designs"]), ("mockup", plan["mockups"])]:
        for row in rows:
            existing_refs(row["requirement_ids"], requirements, f"{group} {row['id']}")
            issue = _verify_repo_artifact(row["artifact"]["uri"], row["artifact"]["sha256"], root)
            if issue:
                errors.append(f"{group} {row['id']}: {issue}")

    children = Counter()
    for task in plan["tasks"]:
        key = task["task_key"]
        existing_refs(task["requirement_ids"], requirements, f"task {key}")
        existing_refs(task["depends_on"], tasks, f"task {key}")
        existing_refs(task["qa_test_ids"], tests, f"task {key}")
        existing_refs(task["artifact_ids"], artifacts, f"task {key}")
        if key in task["depends_on"]:
            errors.append(f"task {key}: cannot depend on itself")
        parent = task["parent_task_key"]
        if parent is not None:
            existing_refs([parent], tasks, f"task {key} parent")
            if parent == key:
                errors.append(f"task {key}: cannot be its own parent")
            children[parent] += 1
        for rid in task["requirement_ids"]:
            if rid in requirements and requirements[rid]["status"] == "needs_decision":
                errors.append(f"task {key}: unresolved SRS requirement {rid} cannot be scheduled")
        for tid in task["qa_test_ids"]:
            if tid in tests and key not in tests[tid]["task_keys"]:
                errors.append(f"task {key}: QA test {tid} lacks reverse task reference")
        for rid in task["requirement_ids"]:
            if not any(tid in tests and rid in tests[tid]["requirement_ids"] for tid in task["qa_test_ids"]):
                errors.append(f"task {key}: SRS requirement {rid} lacks a matching QA test")
            if rid in requirements and requirements[rid]["kind"] == "ui":
                expected_mockups = {
                    mock_id for mock_id, row in mockups.items() if rid in row["requirement_ids"]
                }
                if not set(task["artifact_ids"]).intersection(expected_mockups):
                    errors.append(f"task {key}: UI requirement {rid} has no linked mockup")
    for parent, count in children.items():
        if count > 5:
            errors.append(f"task {parent}: {count} direct children exceeds max 5")

    for test in plan["tests"]:
        existing_refs(test["requirement_ids"], requirements, f"QA test {test['id']}")
        existing_refs(test["task_keys"], tasks, f"QA test {test['id']}")
        for key in test["task_keys"]:
            if key in tasks and test["id"] not in tasks[key]["qa_test_ids"]:
                errors.append(f"QA test {test['id']}: task {key} lacks reverse QA reference")
            if key in tasks and not set(test["requirement_ids"]).intersection(tasks[key]["requirement_ids"]):
                errors.append(f"QA test {test['id']}: task {key} has unrelated SRS requirements")
    for decision in plan["decisions"]:
        existing_refs(decision["requirement_ids"], requirements, f"decision {decision['id']}")

    def visit(key: str, trail: tuple[str, ...], memo: set[str]) -> None:
        if key in trail:
            errors.append("dependency/parent cycle: " + " -> ".join((*trail, key)))
            return
        if key in memo or key not in tasks:
            return
        row = tasks[key]
        neighbors = row["depends_on"] + ([row["parent_task_key"]] if row["parent_task_key"] else [])
        for target in neighbors:
            visit(target, (*trail, key), memo)
        memo.add(key)

    visited: set[str] = set()
    for key in tasks:
        visit(key, (), visited)

    for key, task in tasks.items():
        depth = 0
        chain = {key}
        parent = task["parent_task_key"]
        while parent in tasks and parent not in chain:
            depth += 1
            chain.add(parent)
            parent = tasks[parent]["parent_task_key"]
        if depth > 2:
            errors.append(f"task {key}: parent chain depth {depth} exceeds 2")

    for rid, requirement in requirements.items():
        has_task = any(rid in t["requirement_ids"] for t in plan["tasks"])
        has_design = any(rid in d["requirement_ids"] for d in plan["designs"])
        has_test = any(rid in t["requirement_ids"] for t in plan["tests"])
        has_mockup = any(rid in m["requirement_ids"] for m in plan["mockups"])
        has_decision = any(rid in d["requirement_ids"] for d in plan["decisions"])
        if requirement["status"] == "ready":
            for label, check in [
                ("implementation task", has_task),
                ("design artifact", has_design),
                ("independent QA test", has_test),
                ("UI mockup", has_mockup or requirement["kind"] != "ui"),
            ]:
                if not check:
                    errors.append(f"ready requirement {rid} missing {label}")
        elif not has_decision:
            errors.append(f"unresolved requirement {rid} missing decision request")
    return sorted(set(errors))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path, help="Architect output JSON")
    args = parser.parse_args()
    plan = json.loads(args.path.read_text(encoding="utf-8"))
    errors = validate_plan(plan)
    if errors:
        for error in errors:
            print("INVALID: " + error, file=sys.stderr)
        return 1
    print(f"VALID: {len(plan['requirements'])} requirements, {len(plan['tasks'])} tasks, {len(plan['tests'])} QA tests; local artifacts checked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
