"""Controlled external boundaries; real original journal/once-intent/file logic."""
from pathlib import Path
import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec);sys.modules[name]=value;spec.loader.exec_module(value)
    return value
old=module("original_finalize_fixture",ROOT/"docs/evidence/2026-11-22-native-overlay-held-drain/test_holder_recovery.py")
x=module("original_finalize_under_test",ROOT/"deployment/native-overlay-original-finalize-20261122/finalize_original.py")
h,api,nft,receipt=old.h,old.api,old.boot,old.receipt

def baseline():
    return [
      {"table":{"family":"ip","name":"filter","handle":10}},
      {"chain":{"family":"ip","table":"filter","name":"DOCKER","handle":11}},
      {"rule":{"family":"ip","table":"filter","chain":"DOCKER","expr":[{"accept":None}],"handle":12}},
      {"table":{"family":"ip","name":"nat","handle":20}},
      {"chain":{"family":"ip","table":"nat","name":"POSTROUTING","handle":21}},
      {"table":{"family":"ip","name":"raw","handle":30}},
      {"chain":{"family":"ip","table":"raw","name":"PREROUTING","handle":31}},
    ]
def addition(number=1):
    return {"rule":{"family":"ip","table":"filter","chain":"DOCKER","handle":300+number,
      "expr":[{"match":{"op":"!=","left":{"meta":{"key":"iifname"}},"right":"br-000000000001"}},
              {"drop":None}],"comment":"controlled-docker-"+str(number)}}

class FinalizerTests(unittest.TestCase):
    def setUp(self):
        self.f=old.Fixture();self.extra=[]
        self.f.full();self.f.sign()
        self.base=baseline();self.f.m["nft_before_sha256"]=nft.digest(self.base)
        self.f.rules=copy.deepcopy(self.base)+[addition()]+self.f.rules
        self.f.m["preserved_processes"]={"core":old.identity(111)}
        self.f.m["source_files"]={str(h.CORE/"utils/agentic_maintenance.py"):"a"*64}
        self.f.c["quiet_proof"]={"quiet_seconds":47}
        self.f.c["quiet_seconds_required"]=47
        api.update(self.f.r)
        self.initial=api.filehash(self.f.stage/"journal.json")
        self.binding={"schema_version":1,"source_sha256":"f"*64,"initial_journal_sha256":self.initial}
        native=b'{"status":"ORIGINAL_REJECTED_BEFORE_NATIVE_MUTATION_VERIFIED"}\n'
        restored=b'{"revision":47,"restored":true}\n'
        api.save(self.f.stage/"native-result.json",native)
        api.save(self.f.stage/"definition-restored.json",restored)
        value={"schema_version":1,"purpose":"ROOT_REVIEWED_ORIGINAL_NATIVE_OUTCOME",
          "operation_id":self.f.m["native"]["operation_id"],
          "native_manifest_sha256":self.f.m["native"]["manifest_sha256"],
          "held_sha256":api.filehash(self.f.stage/"HELD.json"),"outcome":"REJECTED_BEFORE_MUTATION",
          "native_result_sha256":h.digest(native),"definition_restore_sha256":h.digest(restored),
          "all_owned_mutations_terminal":True,"source_and_configuration_verified":True,
          "backend_health_verified":True,"worker_health_verified":True,"definition_950_restored":True,
          "unknown_native_action_remaining":False,"retry_performed":False,"reopen_original_intake":True}
        api.savejson(self.f.stage/"native-reconciliation.json",value)
        self.original_command=self.f.command
        self.on_delete=None
        self.before_snapshot=None
        self.extra=[
          patch.object(x,"INITIAL_JOURNAL_SHA",self.initial),
          patch.object(x,"NATIVE_RESULT_SHA",h.digest(native)),
          patch.object(x,"RESTORE_SHA",h.digest(restored)),
          patch.object(x,"core_health",return_value={"version":"0.11.4"}),
          patch.object(h,"work_counts",return_value={"current_core_active_or_unresolved":0,"governance_running":0,"codex_running":0}),
          patch.object(h,"runtime_status",return_value={"active_http_handlers":0,"active_relay_turns":0,"erp_active_turns":0,"erp_inspections":0}),
          patch.object(nft.Bootstrap,"zero_connections",return_value={"usable_ingress":0,"node_network_work":0}),
          patch.object(api.subprocess,"run",self.command),
        ]
        for p in self.extra:p.start()
        self.original_factory=lambda *args:(self.f.b,self.f.g)
        self.original_signature=(self.f.stage/"native-admission.signed.json").read_bytes()
        self.original_held=(self.f.stage/"HELD.json").read_bytes()
    def tearDown(self):
        for p in reversed(self.extra):p.stop()
        self.f.close()
    def command(self,argv,**kwargs):
        if argv[0]!=nft.NFT:
            return self.original_command(argv,**kwargs)
        document=json.loads(Path(argv[-1]).read_text())
        self.assertEqual(document,{"nftables":[{"delete":{"table":{"family":"ip","handle":100}}}]})
        others,owned=nft.split_ruleset({"nftables":self.f.rules})
        self.assertEqual(nft.verify_owned(owned,old.SHA),self.f.c["intents"]["nft_seal"]["proof"])
        self.f.rules=copy.deepcopy(others)
        self.f.calls.append("unseal")
        if self.on_delete:self.on_delete()
        if "unseal" in self.f.fail_after:
            self.f.fail_after.remove("unseal")
            raise subprocess.TimeoutExpired(argv,30)
        return subprocess.CompletedProcess(argv,0,b"{}",b"")
    def run_finalizer(self,finalize=True):
        api.barriers=self.original_factory
        return x.run(h,api,receipt,self.f.m,self.base,self.binding,finalize)
    def assert_originals(self):
        self.assertEqual(self.original_signature,(self.f.stage/"native-admission.signed.json").read_bytes())
        self.assertEqual(self.original_held,(self.f.stage/"HELD.json").read_bytes())
        self.assertEqual(self.f.m["nft_before_sha256"],nft.digest(self.base))
    def test_exact_original_finish_preserves_foreign_and_reopens_once(self):
        foreign,_=nft.split_ruleset({"nftables":self.f.rules})
        self.assertEqual(self.run_finalizer(),"native_operation_reconciled_original_intake_reopened")
        self.assertEqual(self.f.rules,foreign)
        self.assertEqual(self.f.calls,["close","seal","keeper_release","unseal","open"])
        result=json.loads((self.f.stage/"FOREIGN_RULES_UNSEAL_VERIFIED.json").read_text())
        self.assertEqual(result["foreign_before_sha256"],result["foreign_after_sha256"])
        self.assertTrue(result["foreign_before_after_identical"])
        self.assert_originals()
        self.run_finalizer()
        self.assertEqual(self.f.calls.count("unseal"),1)
        self.assertEqual(self.f.calls.count("open"),1)
    def test_preflight_does_not_write_or_release(self):
        before={p.relative_to(self.f.stage):p.read_bytes() for p in self.f.stage.rglob("*") if p.is_file()}
        self.assertEqual(self.run_finalizer(False)["state"],"ORIGINAL_FINALIZATION_PREFLIGHT_ONLY")
        after={p.relative_to(self.f.stage):p.read_bytes() for p in self.f.stage.rglob("*") if p.is_file()}
        self.assertEqual(before,after);self.assertEqual(self.f.calls,["close","seal"])
    def test_dynamic_addition_count_is_captured_not_rewritten_baseline(self):
        self.f.rules[8:8]=[addition(i) for i in range(2,17)]
        self.run_finalizer()
        row=json.loads((self.f.stage/"inputs/foreign-nft-before-01.json").read_text())
        self.assertEqual(row["delta"]["added_docker_rules"],16)
        self.assert_originals()
    def test_removed_or_mutated_original_rule_blocks_before_release(self):
        self.f.rules[2]["rule"]["expr"]=[{"drop":None}]
        with self.assertRaisesRegex(RuntimeError,"OUTCOME_UNRESOLVED"):self.run_finalizer()
        self.assertEqual(self.f.calls,["close","seal"])
    def test_reordered_original_rules_are_rejected(self):
        self.f.rules[0],self.f.rules[1]=self.f.rules[1],self.f.rules[0]
        with self.assertRaisesRegex(RuntimeError,"OUTCOME_UNRESOLVED"):self.run_finalizer()
        self.assertEqual(self.f.calls,["close","seal"])
    def test_foreign_non_docker_scope_is_rejected(self):
        self.f.rules.insert(8,{"rule":{"family":"ip","table":"filter","chain":"INPUT","handle":90,"expr":[{"accept":None}]}})
        with self.assertRaisesRegex(RuntimeError,"OUTCOME_UNRESOLVED"):self.run_finalizer()
        self.assertEqual(self.f.calls,["close","seal"])
    def test_seal_handle_or_content_change_is_rejected(self):
        self.f.rules[-1]["rule"]["handle"]+=1
        with self.assertRaisesRegex(RuntimeError,"OUTCOME_UNRESOLVED"):self.run_finalizer()
        self.assertEqual(self.f.calls,["close","seal"])
    def test_missing_or_wrong_native_reconciliation_denies_release(self):
        path=self.f.stage/"native-reconciliation.json"
        value=json.loads(path.read_text());value["definition_950_restored"]=False
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(RuntimeError,"RECONCILIATION_INCOMPLETE"):self.run_finalizer()
        self.assertEqual(self.f.calls,["close","seal"])
    def test_different_valid_native_result_is_not_this_original(self):
        with patch.object(x,"NATIVE_RESULT_SHA","e"*64):
            with self.assertRaisesRegex(RuntimeError,"EXACT_ORIGINAL_7906"):self.run_finalizer()
        self.assertEqual(self.f.calls,["close","seal"])
    def test_live_original_holder_denies_finalization(self):
        self.f.processes[333]=old.identity(333)
        with self.assertRaisesRegex(RuntimeError,"STILL_RUNNING"):self.run_finalizer()
        self.assertEqual(self.f.calls,["close","seal"])
    def test_foreign_change_after_capture_before_command_is_held_then_fresh_capture(self):
        original=api.save
        changed=[False]
        def racing(path,data):
            original(path,data)
            if path.name=="foreign-nft-before-01.json" and not changed[0]:
                changed[0]=True;self.f.rules.insert(8,addition(2))
        with patch.object(api,"save",racing):
            with self.assertRaisesRegex(RuntimeError,"CHANGED_BEFORE_UNSEAL"):self.run_finalizer()
        row=json.loads((self.f.stage/"journal.json").read_text())
        self.assertEqual(row["cycles"]["apply"]["intents"]["nft_unseal"]["command_phase"],"precondition_failed")
        self.assertEqual(self.f.calls,["close","seal","keeper_release"])
        self.run_finalizer()
        self.assertTrue((self.f.stage/"inputs/foreign-nft-before-01.json").exists())
        self.assertTrue((self.f.stage/"inputs/foreign-nft-before-02.json").exists())
        self.assertEqual(self.f.calls.count("unseal"),1)
    def test_foreign_change_during_delete_denies_open_and_never_redeletes(self):
        self.on_delete=lambda:self.f.rules.append(addition(2))
        with self.assertRaisesRegex(RuntimeError,"CHANGED_DURING_UNSEAL"):self.run_finalizer()
        self.assertEqual(self.f.gate["state"],"draining")
        with self.assertRaisesRegex(RuntimeError,"OUTCOME_UNRESOLVED"):self.run_finalizer()
        self.assertEqual(self.f.calls.count("unseal"),1)
        self.assertEqual(self.f.calls.count("open"),0)
        self.assertFalse((self.f.stage/"FOREIGN_RULES_UNSEAL_VERIFIED.json").exists())
    def test_lost_delete_ack_observes_original_absence_without_resend(self):
        self.f.fail_after.add("unseal")
        with self.assertRaises(subprocess.TimeoutExpired):self.run_finalizer()
        self.assertEqual(self.f.gate["state"],"draining")
        self.run_finalizer()
        self.assertEqual(self.f.calls.count("unseal"),1)
        self.assertEqual(self.f.calls.count("open"),1)
    def test_unknown_delete_with_table_still_present_is_never_repeated(self):
        original=self.command
        def unknown(argv,**kw):
            if argv[0]==nft.NFT:
                self.f.calls.append("unseal")
                raise subprocess.TimeoutExpired(argv,30)
            return original(argv,**kw)
        with patch.object(api.subprocess,"run",unknown):
            with self.assertRaises(subprocess.TimeoutExpired):self.run_finalizer()
            with self.assertRaisesRegex(RuntimeError,"OUTCOME_UNRESOLVED"):self.run_finalizer()
        self.assertEqual(self.f.calls.count("unseal"),1);self.assertEqual(self.f.calls.count("open"),0)
    def test_lost_keeper_ack_and_lost_open_ack_do_not_repeat_actions(self):
        self.f.fail_after.add("keeper_release")
        with self.assertRaises(TimeoutError):self.run_finalizer()
        self.f.fail_after.add("open")
        with self.assertRaises(subprocess.TimeoutExpired):self.run_finalizer()
        self.run_finalizer()
        self.assertEqual(self.f.calls,["close","seal","keeper_release","unseal","open"])
    def test_completed_attestation_allows_later_docker_evolution_without_new_commands(self):
        self.run_finalizer();self.f.rules.append(addition(9))
        self.run_finalizer()
        self.assertEqual(self.f.calls.count("unseal"),1)
        self.assertEqual(self.f.calls.count("open"),1)
        self.assertEqual(self.f.rules[-1],addition(9))
    def test_corrupt_capture_or_completion_never_opens(self):
        self.f.fail_after.add("unseal")
        with self.assertRaises(subprocess.TimeoutExpired):self.run_finalizer()
        path=self.f.stage/"inputs/foreign-nft-before-01.json"
        path.write_bytes(path.read_bytes()+b" ")
        with self.assertRaisesRegex(RuntimeError,"OUTCOME_UNRESOLVED"):self.run_finalizer()
        self.assertEqual(self.f.calls.count("open"),0)

class DeltaTests(unittest.TestCase):
    def test_more_than_64_additions_rejected(self):
        with self.assertRaisesRegex(RuntimeError,"BOUNDED_FOREIGN"):
            x.additive_delta(nft,baseline(),baseline()+[addition(i) for i in range(65)])
    def test_comment_only_docker_label_cannot_authorize_unrelated_rule(self):
        row=addition();row["rule"]["expr"]=[{"accept":None}]
        row["rule"]["comment"]="br-000000000001"
        with self.assertRaisesRegex(RuntimeError,"OUTSIDE_DOCKER_SCOPE"):
            x.additive_delta(nft,baseline(),baseline()+[row])
    def test_handle_renumbering_does_not_rewrite_semantics(self):
        current=copy.deepcopy(baseline())
        for item in current:next(iter(item.values()))["handle"]+=1000
        self.assertEqual(x.additive_delta(nft,baseline(),current)["added_docker_rules"],0)

if __name__=="__main__":
    unittest.main(verbosity=2)
