from pathlib import Path
p=Path(__file__).resolve().parents[3]
f=p/"backend/open_webui/utils/agentic_native_execution.py"
s=f.read_text().replace('        self.runtime, self.gate = runtime, None','        self.runtime, self.gate, self.action = runtime, None, None')
old='''        require(self.gate is not None, "NATIVE_REVIEWED_GATE_NOT_PREPARED")
        receipt = await self.runtime_for(row).submit(row, packet, self.gate, bridge, before_submit)
        return make_record(row, packet, receipt, self.config)'''
new='''        from open_webui.utils.agentic_it_execution import make_action
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
            if saved is None or saved["phase"] == "bound":
                return None
            packet = json.loads(row["work_packet"])
            receipt = await self.runtime_for(row).observe(row, packet, bridge)
            return {"native_receipt": receipt, "executed": False, "automatic_replay_permitted": False}

        adapter = TrustedAdapter(preflight, execute, capability="verify_native_source",
                                 roles=("it_manager",), read_only=False, recover=recover)
        from open_webui.utils.agentic_native_contract import OPERATION
        return {OPERATION: adapter}'''
assert s.count(old)==1
s=s.replace(old,new)
s += '''

async def resume(invocation, *, config, project_gate, bridge, app=None, db_path=None):
    """Use the existing human approval wait/consume path with only this adapter."""
    from open_webui.utils.agentic_it_execution import resume as resume_it
    chosen = NativeVerificationAdapter(app, project_gate, config, db_path)
    return await resume_it(invocation, config=config, project_gate=project_gate, bridge=bridge,
                           app=app, db_path=db_path, adapters=chosen.registry(bridge))
'''
f.write_text(s)
f=p/"backend/open_webui/routers/agentic_governance.py";s=f.read_text()
old='''        adapter = ITAdapter(app, _gate, cfg)'''
new='''        from open_webui.utils.agentic_native_verification import selected_packet
        packet = row.get("work_packet")
        if isinstance(packet, str):
            packet = json.loads(packet)
        if selected_packet(packet or {}):
            from open_webui.utils.agentic_native_execution import NativeVerificationAdapter
            adapter = NativeVerificationAdapter(app, _gate, cfg)
        else:
            adapter = ITAdapter(app, _gate, cfg)'''
assert s.count(old)==1
s=s.replace(old,new)
old='''                elif row['state'] == 'awaiting_approval':
                    receipt = await resume(ident, config=cfg, project_gate=_gate, bridge=bridge, app=app)'''
new='''                elif row['state'] == 'awaiting_approval':
                    from open_webui.utils.agentic_native_verification import selected_packet
                    approval_resume = resume
                    if row['target_role'] == 'it_manager' and selected_packet(json.loads(row['work_packet'])):
                        from open_webui.utils.agentic_native_execution import resume as approval_resume
                    receipt = await approval_resume(ident, config=cfg, project_gate=_gate, bridge=bridge, app=app)'''
assert s.count(old)==1
s=s.replace(old,new)
old='''                    recovery = recover_completed if row['target_role'] == 'it_manager' else recover_original'''
new='''                    recovery = recover_completed if row['target_role'] == 'it_manager' else recover_original
                    if row['target_role'] == 'it_manager':
                        from open_webui.utils.agentic_native_verification import selected_packet
                        if selected_packet(json.loads(row['work_packet'])):
                            from open_webui.utils.agentic_native_execution import recover_completed as recovery'''
assert s.count(old)==1
s=s.replace(old,new)
s += '''

class NativeVerificationReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    state: Literal["uncertain", "running", "completed", "failed_retained"]
    binding_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    gate_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    nonce: str = Field(pattern=r"^[a-f0-9]{32}$")
    authority_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    execution_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    worker_head: str = Field(pattern=r"^[a-f0-9]{40}$")
    outcome_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    worker_result_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    reservation_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    cleanup_confirmed: bool
    operations_count: Literal[0, 10]
    automatic_replay_permitted: Literal[False]
    authority_issued: Literal[False]


class NativeVerificationStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    invocation_id: str
    available: bool
    phase: Literal["bound", "launch_unknown", "uncertain", "running", "completed", "failed_retained"] | None
    binding_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    gate_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    receipt: NativeVerificationReceipt | None = None
    automatic_replay_permitted: Literal[False]
    independent_qa_approved: Literal[False]


@router.get("/projects/{project_id}/native-verification/{invocation_id}",
            response_model=NativeVerificationStatus, tags=["Agentic native verification"])
async def native_verification_status(project_id: str, invocation_id: str,
        control_secret: str = Header(default="", alias="X-ABIT-ERP-Control")):
    """Read bounded original native verification metadata under ERP control auth.

    Execution is only the explicit verify_native_source IT WorkPacket operation,
    in a registered native-verification environment, with current human ERP
    approval consumed once and an independently issued root gate/v4 authority.
    This GET never dispatches, reconciles, issues authority or changes a Task.
    A completed fixed plan is evidence for independent QA, not business approval.
    Original launch uncertainty never permits a repeat.
    """
    _auth(control_secret)
    if not PROJECT_ID.fullmatch(project_id) or not invocation_id or len(invocation_id) > 160:
        raise HTTPException(400, "Invalid native verification identity")
    row = find(invocation_id)
    if not row or row["erp_project"] != project_id:
        raise HTTPException(404, "Original governed invocation not found")
    from open_webui.utils.agentic_native_verification import public_status
    try:
        return public_status(invocation_id)
    except (ValueError, TypeError, KeyError):
        raise HTTPException(409, "Original native evidence integrity requires review") from None
'''
f.write_text(s)
