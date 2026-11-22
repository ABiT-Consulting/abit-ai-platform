from pathlib import Path
R=Path(__file__).resolve().parents[3]
p=R/'backend/tests/test_agentic_native_verification.py'
s=p.read_text()
assert 'test_exact_maintenance_admission_missing_before_binding_denies' not in s
s+='''

    def maintenance_db(self):
        return self.path.parent / "maintenance-admission" / "gate.sqlite"

    def test_exact_maintenance_admission_missing_before_binding_denies(self):
        row, packet = self.claimed()
        self.consumed(row, packet)
        with sqlite3.connect(self.maintenance_db()) as db:
            db.execute("UPDATE lease SET state='unresolved' WHERE reference=?", (row["invocation_id"],))
        with self.assertRaisesRegex(Exception, "original admission is not active"):
            self.n.bind_original(row, packet, self.native_gate, self.path)
        self.assertIsNone(self.n.original(row["invocation_id"], self.path))

    async def test_sealed_maintenance_at_final_challenge_denies_before_grant(self):
        row, packet = self.claimed()
        def seal_fixture():
            # Fault injection only: normal maintenance seal rejects an active original.
            with sqlite3.connect(self.maintenance_db()) as db:
                db.execute("UPDATE gate SET state='sealed' WHERE id=1")
        self.before_grant = seal_fixture
        with self.assertRaisesRegex(Exception, "original admission is not active"):
            await self.submit(row, packet)
        saved = self.n.original(row["invocation_id"], self.path)
        self.assertIsNone(saved["grant_issued_at"])
        self.assertEqual(saved["phase"], "launch_unknown")

    def test_replaced_maintenance_lease_or_epoch_denies_original_launch(self):
        row, packet, saved = self.bind()
        self.n.begin_launch(row, packet, self.path)
        with sqlite3.connect(self.maintenance_db()) as db:
            db.execute("UPDATE lease SET id=?,epoch=epoch+1 WHERE reference=?", ("f"*48,row["invocation_id"]))
        payload={"binding":saved["binding"],"gate_sha256":saved["binding"]["gate_sha256"]}
        with self.assertRaisesRegex(ValueError, "MAINTENANCE_ADMISSION_REPLACED"):
            self.n.issue_launch_grant(row, packet, payload, self.path)
        self.assertIsNone(self.n.original(row["invocation_id"],self.path)["grant_issued_at"])

    def test_changed_maintenance_process_identity_denies_before_launch_intent(self):
        row, packet, saved = self.bind()
        with sqlite3.connect(self.maintenance_db()) as db:
            db.execute("UPDATE lease SET process_start='0' WHERE reference=?",(row["invocation_id"],))
        with self.assertRaisesRegex(Exception, "process identity changed"):
            self.n.begin_launch(row,packet,self.path)
        self.assertEqual(self.n.original(row["invocation_id"],self.path)["phase"],"bound")

    async def test_draining_original_finishes_but_receipt_alone_does_not_settle_admission(self):
        row,packet=self.claimed()
        from open_webui.utils import agentic_maintenance as maintenance
        before=maintenance.require_active("governed",row["invocation_id"],db_path=self.maintenance_db())
        epoch=maintenance.status(self.maintenance_db())["epoch"]
        closed=maintenance.control("close",expected_epoch=epoch,command_id="fixture-close-native",actor="fixture-operator",db_path=self.maintenance_db())
        result=await self.submit(row,packet)
        self.assertIsNotNone(result)
        self.assertEqual(self.operations.count("execute"),1)
        saved=self.n.original(row["invocation_id"],self.path)
        self.assertEqual(saved["binding"]["maintenance_admission"],before)
        self.assertEqual(maintenance.require_active("governed",row["invocation_id"],db_path=self.maintenance_db()),before)
        with self.assertRaisesRegex(Exception,"zero unresolved leases"):
            maintenance.control("seal",expected_epoch=closed["epoch"],command_id="fixture-seal-native",actor="fixture-operator",db_path=self.maintenance_db())

    async def test_root_rejects_grant_without_exact_maintenance_proof_before_any_launch(self):
        row,packet,saved=self.bind()
        service=object.__new__(self.root.RootNativeService)
        payload={"binding":saved["binding"],"gate_sha256":saved["binding"]["gate_sha256"]}
        grant={"binding_sha256":saved["binding_sha256"],"issued_at":int(time.time()),"expires_at":int(time.time())+10}
        with patch.object(service,"original",return_value=(saved["binding"],self.native_gate,Path("/unreached"))), \\
             patch.object(self.root,"verify_inputs") as verify, \\
             patch.object(self.root,"launch_controller_once",AsyncMock()) as launch:
            with self.assertRaisesRegex(ValueError,"FRESH_CORE_GRANT_REQUIRED"):
                await service.launch(payload,grant)
            verify.assert_not_called();launch.assert_not_awaited()
'''
p.write_text(s)
