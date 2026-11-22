from pathlib import Path
import shutil
p=Path(__file__).resolve().parents[3]
e=Path(__file__).resolve().parent/"before-approval-checkpoint-fix";e.mkdir()
for name in ["agentic_native_contract.py","agentic_native_verification.py","agentic_native_execution.py"]:
    shutil.copy2(p/"backend/open_webui/utils"/name,e/name)
f=p/"backend/open_webui/utils/agentic_native_contract.py";s=f.read_text();s=s.replace('"start_receipt_sha256", "action_id", "native_approval_sha256", "created_at"}','"start_receipt_sha256", "erp_baseline_sha256", "action_id", "native_approval_sha256", "created_at"}');s=s.replace('    sha(value["start_receipt_sha256"])','    sha(value["start_receipt_sha256"])\n    sha(value["erp_baseline_sha256"])');f.write_text(s)
f=p/"backend/open_webui/utils/agentic_native_verification.py";s=f.read_text()
anchor="def bind_original(row, packet, gate, db_path=None):"
helper='''def _baseline(db, row, packet, start):
    """Only our exact published approval checkpoint may advance the start CAS."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='governance_action_wait'").fetchone():
        return start
    pending = db.execute("SELECT * FROM governance_action_wait WHERE invocation_id=?",
                         (row["invocation_id"],)).fetchone()
    if pending is None:
        return start
    require(pending["checkpoint_phase"] == "published" and pending["checkpoint_json"] and
            pending["checkpoint_receipt_json"], "NATIVE_APPROVAL_CHECKPOINT_UNCONFIRMED")
    event, receipt = json.loads(pending["checkpoint_json"]), json.loads(pending["checkpoint_receipt_json"])
    require(event.get("type") == "run.checkpoint" and
            event.get("invocation_id") == row["invocation_id"] and
            event.get("run_id") == row["automation_run_id"] == receipt.get("run_id") and
            event.get("project_id") == row["erp_project"] and
            event.get("task_id") == row["erp_task"] == receipt.get("task_id") and
            event.get("task_revision") == row["task_revision"] and
            event.get("work_packet_digest") == packet_digest(packet) and
            event.get("expected_modified") == start["modified"] and
            event.get("event_id") == receipt.get("event_id") and
            isinstance(receipt.get("modified"), str) and receipt["modified"],
            "NATIVE_APPROVAL_CHECKPOINT_CHANGED")
    from open_webui.utils.agentic_it_execution import make_action
    from open_webui.utils.agentic_contracts.validator import action_fingerprint
    require(action_fingerprint(json.loads(pending["action_json"])) ==
            action_fingerprint(make_action(row, packet)), "NATIVE_APPROVAL_CHECKPOINT_ACTION_CHANGED")
    return receipt


def admission_receipt(row, packet, db_path=None):
    from open_webui.utils.agentic_start_recovery import original_start_receipt
    start = original_start_receipt(row, db_path)
    with ledger.connect(db_path) as db:
        return _baseline(db, row, packet, start)


'''
assert s.count(anchor)==1;s=s.replace(anchor,helper+anchor)
s=s.replace('        action, consumed = consumed_approval(db, row, packet)\n    binding =','        action, consumed = consumed_approval(db, row, packet)\n        baseline = _baseline(db, row, packet, start)\n    binding =',1)
s=s.replace('"start_receipt_sha256": digest(start), "action_id": action["action_id"],','"start_receipt_sha256": digest(start), "erp_baseline_sha256": digest(baseline),\n               "action_id": action["action_id"],',1)
s=s.replace('                "NATIVE_ERP_CONSUMPTION_CHANGED")','                "NATIVE_ERP_CONSUMPTION_CHANGED")\n        require(digest(_baseline(db, row, packet, start)) == binding["erp_baseline_sha256"],\n                "NATIVE_ERP_BASELINE_CHANGED")',1)
s=s.replace('                "NATIVE_CONFIRMED_START_CHANGED")','                "NATIVE_CONFIRMED_START_CHANGED")\n        require(digest(_baseline(db, current, packet, json.loads(entry["receipt_json"]))) ==\n                binding["erp_baseline_sha256"], "NATIVE_ADMISSION_CHECKPOINT_CHANGED")',1)
s=s.replace('                receipt = original_start_receipt(current, self.db_path)','                receipt = admission_receipt(current, self.packet, self.db_path)',1)
f.write_text(s)
f=p/"backend/open_webui/utils/agentic_native_execution.py";s=f.read_text();s=s.replace('    from open_webui.utils.agentic_start_recovery import original_start_receipt','    from open_webui.utils.agentic_native_verification import admission_receipt',1);s=s.replace('    start = original_start_receipt(row, db_path)','    start = admission_receipt(row, packet, db_path)',1);f.write_text(s)
