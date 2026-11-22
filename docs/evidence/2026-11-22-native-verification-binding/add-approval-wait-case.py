from pathlib import Path
p=Path(__file__).resolve().parents[3]
f=p/"backend/tests/test_agentic_native_verification.py";s=f.read_text()
s=s.replace('        self.started = False','        self.started = False\n        self.modified = "modified-started"\n        self.drafts = {}\n        self.receipts = {}',1)
s=s.replace('packet["source_modified"] = "modified-started"','packet["source_modified"] = self.modified',1)
anchor='''        if operation == "get_action_approval":'''
s=s.replace(anchor,'''        if operation == "request_action_approval":
            self.drafts[args["approval_id"]] = deepcopy(args)
            return {"message": {"approval_id": args["approval_id"]}}
        if operation == "get_result_receipt":
            event = kwargs["params"]["event_id"]
            return {"message": {"exists": event in self.receipts, "receipt": self.receipts.get(event)}}
'''+anchor,1)
anchor='''    async def close(self):
        pass'''
s=s.replace(anchor,'''    async def publish(self, event):
        self.modified = "modified-checkpoint"
        receipt = {key:event[key] for key in ("event_id","run_id","task_id")}
        receipt["modified"] = self.modified
        self.receipts[event["event_id"]] = receipt
        return receipt

'''+anchor,1)
s += '''

    async def test_existing_human_approval_wait_resumes_exact_native_run_once(self):
        row, packet = self.claimed(approval=False)
        with self.assertRaises(self.gateway.ApprovalRequired) as caught:
            await self.submit(row,packet)
        await self.it.suspend(row["invocation_id"],packet,self.adapter,self.bridge,
                              {"modified":"modified-started"},caught.exception,self.path)
        with self.g.connect(self.path) as db:
            pending=dict(db.execute("SELECT * FROM governance_action_wait WHERE invocation_id=?",
                                    (row["invocation_id"],)).fetchone())
        self.assertEqual(pending["checkpoint_phase"],"published")
        action=self.it.make_action(row,packet);now=int(time.time())
        self.bridge.approval={"schema_version":1,"approval_id":pending["approval_id"],"project_id":P,"task_id":T,
            "action_fingerprint":self.validator.action_fingerprint(action),"approved_by":"human@example.invalid",
            "approved_at":now-1,"expires_at":now+600,"status":"approved","used_by_action_id":None}
        completed=[]
        async def complete(current, original_packet, record, *args):
            completed.append((current,original_packet,record))
            return {"completed_original":current["automation_run_id"]}
        executor=types.ModuleType("open_webui.utils.agentic_executor")
        executor.complete_original=complete
        automations=types.ModuleType("open_webui.models.automations")
        automations.Automations=types.SimpleNamespace(get_by_id=AsyncMock(return_value=types.SimpleNamespace(id=row["agent_id"])))
        import sys
        with patch.dict(sys.modules,{"open_webui.utils.agentic_executor":executor,"open_webui.models.automations":automations}), \
             patch.object(self.adapter_module,"configured_runtime",return_value=self.runtime):
            value=await self.adapter_module.resume(row["invocation_id"],config=self.config,project_gate=self.gate,
                                                   bridge=self.bridge,db_path=self.path)
        self.assertEqual(value,{"completed_original":row["automation_run_id"]})
        self.assertEqual(self.operations.count("execute"),1)
        self.assertEqual(self.bridge.calls.count("consume_action_approval"),1)
        self.assertEqual(len(completed),1)
        self.assertEqual(completed[0][0]["invocation_id"],row["invocation_id"])
'''
f.write_text(s)
