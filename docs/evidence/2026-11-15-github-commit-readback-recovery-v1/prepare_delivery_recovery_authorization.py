"""Prepare the exact retained delivery authorization; never stage or send it.

All inputs are immutable retained local files. The only output is a new proposal
beside this source. Root separately reviews/stages it; live code still requires
normal human Open readiness and an acknowledged signed Retry greater than 10.
"""
from __future__ import annotations

import ast
import base64
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import stat

HERE = Path(__file__).resolve().parent
EVIDENCE = HERE / "retained-recovery-evidence.private.json"
GET_PROOF = HERE.parent / "original-delivery-commit-GET-proof-20261115.json"
SOURCE = HERE / "agentic_gateway_delivery.py"
BASELINE = HERE / "agentic_gateway_delivery.py.before"
OUTPUT = HERE / "delivery-recovery-authorization.proposed.json"
EVIDENCE_SHA = "48b70fb4e089fd8da37479f23575a270ebfb1e47dfb4243d21a31cbbc698e5cf"
GET_PROOF_SHA = "6d521de1c2eda5d4b6dd2936821f8ddf0e720b912dd0770f293c989291eff3b2"
SOURCE_SHA = "18646bd51b98add2af5612132b2c9e2da9f77251606d1f22096edaf02dce27ff"
BASELINE_SHA = "36d0354155a4a037c5e1174c0fb18cd2f623a4a59240a411e312839c8fbbae68"
BLOCKED = "agov-parent-bc60a4e72de7c6771af1cf25f317e8c19f517d36b6696ff1"
RUN = "74685093-2067-561e-80b0-b51852f4e7ba"
ORIGINAL = "agov-child-972b2a1794c982c7c3aefab1314314e4b5c660f6cba9c32e"
PROJECT = "PROJ-0198"
TASK = "TASK-2026-06200"
AGENT = "e6da1587-ac1d-5123-a665-9966caa6edf8"
RESULT_SHA = "b185e94680c390b9c8422bfee9cb2fafbf6ab1ab6dbf946258c648e67cba8aa6"
RECEIPT_SHA = "fa1815b13d42779f3826cd03371f508361e990b3925b4bb8b61e373b3094a60d"
INPUT_SHA = "b16ea85d9530f36481d0fcc1ece3f5f1d6b803e482d2704f4459e2aaa8f063cd"
REQUEST_SHA = "c95b7b25b33006c44a6cc95ac38340fc793aff5a537d7f9b902d00ec7bd8103f"
COMMIT = "cc4afa2ba6a4bda5f951d62f6c8afd80276f53e1"
STEPS = [
    "blob:881ef58791f8693f3e6b83d91e6f70c512bb7af6",
    "blob:bd24b447cec30f881b3ef61a4cf3097f5e5b9b35",
    "commit",
    "tree",
]


def need(value, code):
    if not value:
        raise ValueError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    # Exactly the production agentic_bridge canonical/digest contract.
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode()


def digest(value):
    return sha(canonical(value))


def unique(rows):
    value = {}
    for key, item in rows:
        need(key not in value, "DUPLICATE_RETAINED_JSON_KEY")
        value[key] = item
    return value


def read_pinned(path, expected, *, private=False):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
             "EXACT_SINGLE_REGULAR_INPUT_REQUIRED")
        if private:
            need(info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o600,
                 "ROOT_PRIVATE_RETAINED_INPUT_REQUIRED")
        raw = stream.read(262145)
    need(len(raw) <= 262144 and sha(raw) == expected, "RETAINED_INPUT_SHA_CHANGED")
    return raw


def git_hash(kind, raw):
    return hashlib.sha1(kind.encode() + b" " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


def commit_hash(body):
    def person(value):
        stamp = int(datetime.fromisoformat(value["date"].replace("Z", "+00:00")).timestamp())
        return value["name"] + " <" + value["email"] + "> " + str(stamp) + " +0000"
    raw = "tree " + body["tree"] + "\n" + "".join("parent " + p + "\n" for p in body["parents"])
    raw += "author " + person(body["author"]) + "\ncommitter " + person(body["committer"]) + "\n\n" + body["message"]
    return git_hash("commit", raw.encode())


def prepare(evidence_raw, get_raw, source_raw, baseline_raw):
    need(sha(evidence_raw) == EVIDENCE_SHA and sha(get_raw) == GET_PROOF_SHA
         and sha(source_raw) == SOURCE_SHA and sha(baseline_raw) == BASELINE_SHA,
         "EXACT_REVIEWED_EVIDENCE_AND_SOURCE_REQUIRED")
    evidence = json.loads(evidence_raw, object_pairs_hook=unique)
    proof = json.loads(get_raw, object_pairs_hook=unique)
    need(evidence["schema_version"] == 1
         and evidence["purpose"] == "retained-single-LF-delivery-recovery-evidence",
         "EXACT_RETAINED_EVIDENCE_PURPOSE_REQUIRED")
    new_tree, old_tree = ast.parse(source_raw), ast.parse(baseline_raw)
    select = lambda tree: next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_commit_hash")
    need(ast.dump(select(new_tree)) == ast.dump(select(old_tree)),
         "ORIGINAL_COMMIT_HASH_CONTRACT_CHANGED")

    blocked = evidence["blocked"]
    row, saved = blocked["invocation"], blocked["published"]
    result, event, receipt = saved["result"], saved["event"], saved["receipt"]
    need(row["invocation_id"] == BLOCKED and row["automation_run_id"] == RUN
         and row["erp_project"] == PROJECT and row["erp_task"] == TASK
         and row["agent_id"] == AGENT and row["target_role"] == "developer"
         and row["state"] == "execution_finished" and row["submitted_at"] == 1794732638
         and row["dispatch_attempts"] == 1 and saved["invocation_id"] == BLOCKED
         and saved["run_id"] == RUN and saved["phase"] == "published",
         "EXACT_PUBLISHED_BLOCKED_INVOCATION_REQUIRED")
    need(digest(result) == saved["result_sha256"] == RESULT_SHA
         and result["outcome"] == "blocked" and not result.get("defects")
         and not result.get("action_requests") and result["invocation_id"] == BLOCKED
         and result["project_id"] == PROJECT and result["task_id"] == TASK
         and result["actor"]["agent_id"] == AGENT and result["actor"]["role"] == "developer"
         and result["task_revision"] == row["task_revision"],
         "EXACT_IMMUTABLE_BLOCKED_RESULT_REQUIRED")
    need(event["type"] == "run.blocked" and event["invocation_id"] == BLOCKED
         and event["run_id"] == RUN and event["project_id"] == PROJECT
         and event["task_id"] == TASK and event["agent_id"] == AGENT
         and event["role"] == "developer" and event["acceptance_passed"] is False
         and receipt["event_id"] == event["event_id"] and receipt["run_id"] == RUN
         and receipt["task_id"] == TASK and receipt["status"] == "Blocked"
         and receipt["durable"] is True
         and digest(receipt) == blocked["receipt_sha256"] == RECEIPT_SHA,
         "EXACT_DURABLE_NATIVE_BLOCKED_RECEIPT_REQUIRED")
    audits = evidence["readiness_result_audit"]
    need(len(audits) == 1, "EXACT_ORIGINAL_READINESS_RESULT_AUDIT_REQUIRED")
    audit = audits[0]
    restored = json.loads(canonical(event))
    restored["work_packet_digest"] = audit["original_native_digest"]
    restored["result_contract"]["work_packet_digest"] = audit["original_native_digest"]
    need(audit["policy"] == "denied-start-readiness.v1"
         and audit["invocation_id"] == BLOCKED and audit["run_id"] == RUN
         and audit["original_packet_hash"] == row["packet_hash"]
         and audit["result_sha256"] == RESULT_SHA
         and audit["native_event_sha256"] == digest(event)
         and audit["original_event_sha256"] == digest(restored)
         and audit["pointers"] == ["/work_packet_digest", "/result_contract/work_packet_digest"]
         and event["work_packet_digest"] == event["result_contract"]["work_packet_digest"] == audit["effective_native_digest"]
         and restored["result_contract"] == result
         and result["work_packet_digest"] == audit["original_native_digest"],
         "ORIGINAL_TWO_POINTER_READINESS_RESULT_LINEAGE_CHANGED")

    original = evidence["original"]
    source, delivery = original["invocation"], original["delivery"]
    need(source["invocation_id"] == ORIGINAL and source["target_role"] == "developer"
         and source["erp_project"] == PROJECT and source["erp_task"] == TASK
         and source["agent_id"] == AGENT and source["task_revision"] == row["task_revision"]
         and delivery["invocation_id"] == ORIGINAL and delivery["project_id"] == PROJECT
         and delivery["task_id"] == TASK and delivery["state"] == "prepared"
         and digest(delivery["input"]) == original["input_sha256"] == INPUT_SHA,
         "EXACT_ORIGINAL_FROZEN_DELIVERY_REQUIRED")
    steps = original["steps"]
    need(len(steps) == 4 and sorted(s["step_key"] for s in steps) == STEPS
         and all(s["state"] == "completed" and s["invocation_id"] == ORIGINAL for s in steps),
         "EXACT_FOUR_COMPLETED_STEPS_REQUIRED")
    for step in steps:
        if step["step_key"].startswith("blob:"):
            blob = step["step_key"].split(":", 1)[1]
            request = step["request"]
            need(request["path"] == "/git/blobs" and request["body"]["encoding"] == "base64"
                 and git_hash("blob", base64.b64decode(request["body"]["content"], validate=True)) == blob
                 and step["result"]["sha"] == blob, "ORIGINAL_COMPLETED_BLOB_CHANGED")
    tree = next(s for s in steps if s["step_key"] == "tree")
    commit = next(s for s in steps if s["step_key"] == "commit")
    request, actual = commit["request"], commit["result"]
    body = request["body"]
    need(request["path"] == "/git/commits" and digest(request) == REQUEST_SHA
         and tree["request"]["path"] == "/git/trees"
         and body["tree"] == tree["result"]["sha"] == actual["tree"]["sha"]
         and [v["sha"] for v in actual["parents"]] == body["parents"]
         and commit_hash(body) == actual["sha"] == COMMIT
         and body["message"].endswith("\n") and not body["message"].endswith("\n\n")
         and actual["message"] == body["message"][:-1],
         "ORIGINAL_COMMIT_IDENTITY_OR_SINGLE_LF_PRESENTATION_CHANGED")
    need(proof["schema_version"] == 1 and proof["purpose"] == "original-commit-exact-readonly-GET-presentation-proof"
         and proof["GET_status"] == 200 and proof["remote_POSTs"] == 0
         and proof["source_sha256"] == BASELINE_SHA
         and proof["repository"] == "https://github.com/ABiT-Consulting/abit-ai-agents"
         and proof["commit_sha"] == proof["predicted_original_sha"] == COMMIT
         and proof["sha_equal"] is True and proof["tree_equal"] is True and proof["parents_equal"] is True
         and proof["exact_single_trailing_LF_transport_projection"] is True and proof["message_exact"] is False
         and proof["GET_record_sha256"] == digest(actual)
         and proof["requested_message_bytes"] == len(body["message"].encode()) == 193
         and proof["GET_message_bytes"] == len(actual["message"].encode()) == 192
         and proof["requested_message_sha256"] == sha(body["message"].encode())
         and proof["GET_message_sha256"] == sha(actual["message"].encode()),
         "EXACT_RETAINED_READONLY_COMMIT_GET_PROOF_REQUIRED")
    return {
        "schema_version": 1,
        "purpose": "single-lf-delivery-recovery.v1",
        "blocked_invocation_id": BLOCKED,
        "blocked_run_id": RUN,
        "blocked_result_sha256": RESULT_SHA,
        "native_block_receipt_sha256": RECEIPT_SHA,
        "original_invocation_id": ORIGINAL,
        "original_input_sha256": INPUT_SHA,
        "delivery_source_sha256": SOURCE_SHA,
        "completed_steps": STEPS,
        "commit_request_sha256": REQUEST_SHA,
        "commit_sha": COMMIT,
        "commit_GET_proof_sha256": GET_PROOF_SHA,
        "minimum_control_sequence": 10,
        "control_task": "",
    }


def main():
    need(os.geteuid() == 0, "ROOT_PRIVATE_RETAINED_EVIDENCE_READER_REQUIRED")
    value = prepare(read_pinned(EVIDENCE, EVIDENCE_SHA, private=True),
                    read_pinned(GET_PROOF, GET_PROOF_SHA, private=True),
                    read_pinned(SOURCE, SOURCE_SHA), read_pinned(BASELINE, BASELINE_SHA))
    raw = canonical(value) + b"\n"
    fd = os.open(OUTPUT, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    print(json.dumps({"ok": True, "proposal_path": str(OUTPUT), "sha256": sha(raw),
                      "bytes": len(raw), "protected_staging_performed": False,
                      "network_or_DB_calls": 0, "original_result_rewritten": False,
                      "runtime_readiness_still_required": "fresh Task Open and signed Retry sequence >10"},
                     sort_keys=True))


if __name__ == "__main__":
    main()
