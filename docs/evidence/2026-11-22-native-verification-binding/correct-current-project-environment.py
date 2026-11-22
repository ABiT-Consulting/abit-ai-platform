from pathlib import Path
import shutil,hashlib,json
R=Path(__file__).resolve().parents[3];E=Path(__file__).resolve().parent;B=E/'before-actual-project-environment';B.mkdir(exist_ok=False)
p=R/'backend/open_webui/utils/agentic_native_contract.py';old=p.read_bytes();assert hashlib.sha256(old).hexdigest()=='383987e2ccb6f1410292d8bfe1d4914b4cede1fb1156bb1ce1734583686804b8';shutil.copy2(p,B/p.name);s=old.decode();a='value["environment"] == "native-verification"';assert s.count(a)==1;p.write_text(s.replace(a,'value["environment"] == "ABIT-DEV-LINUX"'))
t=R/'backend/tests/test_agentic_native_verification.py';shutil.copy2(t,B/t.name);s=t.read_text();a='environment="native-verification", tool_scopes=["verify_native_source"]';assert s.count(a)==1;s=s.replace(a,'environment="ABIT-DEV-LINUX", tool_scopes=["verify_native_source"]')
s+='''

    def test_actual_existing_project_environment_is_exact_without_global_scope_rewrite(self):
        # Root's authenticated Project read reports both current environment
        # fields as ABIT-DEV-LINUX and the exact existing BIGRRO repository.
        row=self.queued("native-existing-project-environment")
        packet=self.packet()
        self.assertEqual(packet["environment"],"ABIT-DEV-LINUX")
        self.assertEqual(self.n.scope_for(row,packet)["environment"],"ABIT-DEV-LINUX")
        for wrong in ("native-verification","abit-dev-linux","ABIT-PRODUCTION"):
            with self.subTest(environment=wrong):
                changed=deepcopy(packet);changed["environment"]=wrong
                changed_row={**row,"packet_hash":self.g.work_packet_digest(changed)}
                with self.assertRaisesRegex(ValueError,"FIXED_LANE_REQUIRED"):
                    self.n.scope_for(changed_row,changed)
        self.assertEqual(json.loads(self.g.find(row["invocation_id"],self.path,include_private=True)["work_packet"])["environment"],"ABIT-DEV-LINUX")
'''
t.write_text(s)
report={'reason':'Actual existing PROJ0202 environment is ABIT-DEV-LINUX in both Project fields. New lane must preserve that Project scope.','reported_by':'root authenticated read, job prefix9271f948, 2026-11-22 about16:04 UTC','source_before_sha256':hashlib.sha256(old).hexdigest(),'source_after_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'exact_runtime_change':'One comparison literal: native-verification -> ABIT-DEV-LINUX','unchanged':['Fixed privileged profile','Exact BIGRRO repository and IT role','Current actor/run/Task/SRS/packet/fence/lease/maintenance binding','Development/test tool gateway profile and native isolation controls','Existing Projects and original attempts'],'previous_source_commit_superseded_for_activation':'9c37afc970d54ce98938f0f48a8231347acaef0f'}
(E/'actual-project-environment-correction.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
