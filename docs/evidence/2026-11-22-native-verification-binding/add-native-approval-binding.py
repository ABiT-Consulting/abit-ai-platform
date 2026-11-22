from pathlib import Path
p=Path(__file__).resolve().parents[3]/"backend/open_webui/utils/agentic_native_verification.py"
s=p.read_text()
anchor="def bind_original(row, packet, gate, db_path=None):"
helper='''def consumed_approval(db, row, packet):
    from open_webui.utils.agentic_it_execution import make_action
    from open_webui.utils.agentic_contracts.validator import action_fingerprint
    action = make_action(row, packet)
    require(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='governance_action'").fetchone(),
            "NATIVE_ERP_APPROVAL_INTENT_MISSING")
    saved = db.execute("SELECT * FROM governance_action WHERE action_id=? AND invocation_id=?",
                       (action["action_id"], row["invocation_id"])).fetchone()
    require(saved is not None and saved["state"] == "uncertain" and saved["approval_json"],
            "NATIVE_CONSUMED_ERP_APPROVAL_REQUIRED")
    original_action = json.loads(saved["action_json"])
    require(action_fingerprint(original_action) == action_fingerprint(action),
            "NATIVE_APPROVED_ACTION_CHANGED")
    consumed = json.loads(saved["approval_json"])
    require(consumed.get("consumed") is True and consumed.get("action_id") == action["action_id"] and
            consumed.get("action_fingerprint") == action_fingerprint(action) and
            isinstance(consumed.get("approval_id"), str) and consumed["approval_id"] and
            isinstance(consumed.get("receipt_id"), str) and consumed["receipt_id"],
            "NATIVE_CONSUMED_ERP_RECEIPT_INVALID")
    return action, consumed


'''
assert s.count(anchor)==1
s=s.replace(anchor,helper+anchor)
s=s.replace('    binding = {"schema_version": 1, "scope": scope, "execution": gate["execution"],',
'''    with ledger.connect(db_path) as db:
        action, consumed = consumed_approval(db, row, packet)
    binding = {"schema_version": 1, "scope": scope, "execution": gate["execution"],''')
s=s.replace('"start_receipt_sha256": digest(start), "created_at": now}',
'''"start_receipt_sha256": digest(start), "action_id": action["action_id"],
               "native_approval_sha256": digest(consumed), "created_at": now}''')
s=s.replace('        _owned(db, row, packet, now)\n        prior =',
'''        _owned(db, row, packet, now)
        current_action, current_consumed = consumed_approval(db, row, packet)
        require(current_action["action_id"] == binding["action_id"] and
                digest(current_consumed) == binding["native_approval_sha256"],
                "NATIVE_ERP_CONSUMPTION_CHANGED")
        prior =''',1)
s=s.replace('        entry = db.execute("SELECT receipt_json,lease_token_sha256 FROM governance_start WHERE invocation_id=?",',
'''        action, consumed = consumed_approval(db, current, packet)
        require(action["action_id"] == binding["action_id"] and
                digest(consumed) == binding["native_approval_sha256"], "NATIVE_CONSUMED_APPROVAL_CHANGED")
        entry = db.execute("SELECT receipt_json,lease_token_sha256 FROM governance_start WHERE invocation_id=?",''')
s=s.replace('        self.row = self.packet = self.bridge = self.before_submit = None',
'''        self.row = self.packet = self.bridge = self.before_submit = None
        self.prepare_claimed = False''')
s=s.replace('            await self._fresh(claimed=False)','            await self._fresh(claimed=self.prepare_claimed)')
s=s.replace('            current = await self._fresh(claimed=True)\n            return issue_launch_grant',
'''            current = await self._fresh(claimed=True)
            from open_webui.utils.agentic_tool_gateway import _approval
            with ledger.connect(self.db_path) as db:
                action, consumed = consumed_approval(db, current, self.packet)
            approval, _ = await _approval(self.bridge, current, action)
            now = int(time.time())
            require(isinstance(approval, dict) and approval.get("status") == "approved" and
                    approval.get("approval_id") == consumed["approval_id"] and
                    approval.get("action_fingerprint") == consumed["action_fingerprint"] and
                    approval.get("used_by_action_id") == action["action_id"] and
                    type(approval.get("approved_at")) is int and type(approval.get("expires_at")) is int and
                    approval["approved_at"] <= now < approval["expires_at"],
                    "NATIVE_CURRENT_CONSUMED_APPROVAL_REQUIRED")
            return issue_launch_grant''')
s=s.replace('    async def prepare_gate(self, row, packet):\n        self.row, self.packet = row, packet',
'''    async def prepare_gate(self, row, packet, *, bridge=None, claimed=False):
        self.row, self.packet, self.bridge, self.prepare_claimed = row, packet, bridge, claimed''')
p.write_text(s)
