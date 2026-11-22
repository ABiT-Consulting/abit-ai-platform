from pathlib import Path
import shutil
R=Path(__file__).resolve().parents[3];E=Path(__file__).resolve().parent
p=R/'backend/tests/test_agentic_native_verification.py';s=p.read_text();shutil.copy2(p,E/'tests-before-queued-claim-case.py')
assert 'test_one_root_gate_survives_actual_queued_claim_start_and_approved_submit' not in s
s+='''

    async def test_one_root_gate_survives_actual_queued_claim_start_and_approved_submit(self):
        row=self.queued("native-single-gate-transition")
        packet=self.packet()
        scope_before=self.n.scope_for(row,packet)
        original_gate=self.gate_for(row,packet)
        self.native_gate=deepcopy(original_gate)
        self.runtime.bridge=self.bridge
        with patch("open_webui.utils.agentic_bridge.ERPBridge",return_value=self.bridge):
            await self.adapter.preflight(row,packet)
        self.assertEqual(self.operations,["prepare"])
        self.assertEqual(self.g.find(row["invocation_id"],self.path)["state"],"queued")
        # Reuse the fixture's start-event/ERP-response setup while executing the
        # real claim_invocation exactly once against this already queued row.
        # The helper's enqueue is suppressed; the claim itself is not mocked.
        with patch.object(self,"queued",return_value=row):
            current,claimed_packet=self.claimed(row["invocation_id"])
        self.assertEqual(current["lease_fence"],row["lease_fence"])
        self.assertEqual(current["dispatch_attempts"],1)
        self.assertEqual(current["automation_run_id"],scope_before["run_id"])
        self.assertEqual(self.n.scope_for(current,claimed_packet),scope_before)
        self.native_gate=deepcopy(original_gate)
        result=await self.submit(current,claimed_packet)
        self.assertIsNotNone(result)
        saved=self.n.original(current["invocation_id"],self.path)
        self.assertEqual(saved["gate"],original_gate)
        self.assertEqual(saved["binding"]["gate_sha256"],self.c.digest(original_gate))
        self.assertEqual(self.operations,["prepare","prepare","execute"])
        self.assertEqual(self.bridge.calls.count("consume_action_approval"),1)
        self.assertEqual(saved["phase"],"completed")
'''
p.write_text(s)
