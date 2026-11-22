"""Finite filesystem/lost-ACK tests. No host control, network, provider or root work."""
from pathlib import Path
import base64
import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch, Mock
import sys

HERE=Path(__file__).resolve().parent
PACKAGE=HERE.parents[2]/"deployment/native-overlay-held-drain-20261122"

def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec);sys.modules[name]=value;spec.loader.exec_module(value)
    return value

h=module("held_test_operator",PACKAGE/"hold_native_overlay.py")
receipt=module("held_test_receipt",PACKAGE/"held_admission_receipt.py")
api=module("held_test_reviewed_api",PACKAGE/"reviewed/release_relay.py")
boot=module("held_test_bootstrap",PACKAGE/"relay_bootstrap.py")
ORIGINAL_SOURCE_CHECK=h.source_check
SHA="a"*64
def identity(pid):return {"pid":pid,"start_ticks":12345+pid,"uid":0,"cmdline_sha256":"b"*64}

class Guard:
    """Controlled external keeper boundary; real files retain original receipts."""
    def __init__(self,f):self.f=f;self.child=None
    def keeper_path(self,suffix):return self.f.stage/("lock-apply-"+suffix+".json")
    def acquire(self,*args,**kwargs):raise AssertionError("A recovery must never acquire a keeper")
    def keeper_check(self):
        f=self.f
        if not f.keeper_live or not self.keeper_path("held").exists():raise RuntimeError("KEEPER_UNKNOWN")
        return {"held_sha256":api.filehash(self.keeper_path("held")),"keeper":identity(888),
                "lock_sha256":"c"*64}
    def release(self,issue=True):
        f=self.f
        if not self.keeper_path("release").exists():
            if not issue:raise RuntimeError("RELEASE_NOT_ISSUED")
            proof=self.keeper_check()
            api.savejson(self.keeper_path("release"),{"manifest_sha256":SHA,"cycle":"apply",
                "held_sha256":proof["held_sha256"]})
            f.calls.append("keeper_release")
            f.keeper_live=False
            api.savejson(self.keeper_path("released"),{"manifest_sha256":SHA,"cycle":"apply",
                "held_sha256":proof["held_sha256"]})
            if "keeper_release" in f.fail_after:
                f.fail_after.remove("keeper_release")
                raise TimeoutError("controlled lost release ACK")
        request=json.loads(self.keeper_path("release").read_text())
        released=json.loads(self.keeper_path("released").read_text())
        if request!=released:raise RuntimeError("RELEASE_BINDING_CHANGED")
        return {"released_sha256":api.filehash(self.keeper_path("released")),"owner_only_unlock":True}

class Fixture:
    def __init__(self):
        self.temp=tempfile.TemporaryDirectory(prefix="native-holder-offline-")
        self.stage=Path(self.temp.name)/"stage";self.stage.mkdir(mode=0o700)
        (self.stage/"inputs").mkdir(mode=0o700)
        self.calls=[];self.fail_after=set();self.rules=[];self.audit={};self.keeper_live=False
        self.processes={os.getpid():identity(os.getpid()),222:identity(222)}
        self.m={"gate_before":{"state":"open","epoch":94,"command_id":"prior","actor":"prior"},
          "relay_before":identity(222),"operator":{"owner":h.OWNER,"actor":h.OWNER+":native-leaf-hold"},
          "native":{"operation_id":"1"*64,"controller_sha256":"2"*64,"manifest_sha256":"3"*64,
                    "native_preflight_sha256":"4"*64,"restore_receipt_sha256":"5"*64},
          "holder_sha256":"6"*64,"nft_before_sha256":boot.digest([])}
        self.gate=copy.deepcopy(self.m["gate_before"])
        self.c={"mode":"apply","gate_before":copy.deepcopy(self.gate),"node_before":identity(222),
                "no_node_transition":True,"intents":{}}
        self.r={"schema_version":1,"manifest_sha256":SHA,"state":"prepared","cycles":{"apply":self.c},
                "created_at":"2026-11-22T17:00:00Z","original_holder":identity(333)}
        self.old_raw=api.raw
        self.patches=[
          patch.object(h,"STAGE",self.stage),patch.object(api,"STAGE",self.stage),
          patch.object(api,"MANIFEST_SHA",SHA),patch.object(h,"source_check",lambda *args:None),
          patch.object(h,"consistent",lambda *args:{"history":"frozen"}),
          patch.object(h,"operator",lambda *args: h.time.time()+2000),
          patch.object(api,"owner",lambda *args:None),patch.object(api,"preserve",lambda *args:None),
          patch.object(api,"units",lambda *args:None),patch.object(api,"gate",lambda *args:copy.deepcopy(self.gate)),
          patch.object(api,"db_rows",self.db_rows),patch.object(api,"process",self.process),
          patch.object(api,"original_absent",lambda expected:expected["pid"] not in self.processes),
          patch.object(api,"raw",self.raw),patch.object(api,"protected",self.protected),
          patch.object(api.subprocess,"run",self.command),
          patch.object(boot,"read_ruleset",lambda:{"nftables":copy.deepcopy(self.rules)}),
          patch.object(api,"verify_replacement",self.verify_replacement),
          patch.object(h,"root_read",self.root_read),
        ]
        for p in self.patches:p.start()
        self.b=boot.Bootstrap(api,self.m,self.r,self.c);self.g=Guard(self)
        self.patches.append(patch.object(api,"barriers",lambda *args:(self.b,self.g)));self.patches[-1].start()
        api.update(self.r)
    def close(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def raw(self,path,limit=2*1024*1024):
        if path==h.KEY:return b"fixture-control-key-never-production-0123456789"
        return self.old_raw(path,limit)
    def protected(self,path,directory=False):
        path=Path(path)
        if self.stage not in path.parents:raise AssertionError("NONFIXTURE_METADATA")
        st=path.lstat();wanted=0o700 if directory else 0o600
        if path.is_symlink() or st.st_uid!=os.geteuid() or st.st_gid!=os.getegid() or st.st_mode&0o777!=wanted:
            raise RuntimeError("FIXTURE_OWNER_METADATA")
        if (directory and not path.is_dir()) or (not directory and (not path.is_file() or st.st_nlink!=1)):
            raise RuntimeError("FIXTURE_TYPE_METADATA")
    def root_read(self,path,mode=0o400,limit=8*1024*1024):
        # Root ancestry itself is unchanged, separately reviewed. This fixture
        # allows only its own ordinary temporary regular files with exact mode.
        path=Path(path)
        if self.stage not in path.parents:raise AssertionError("NONFIXTURE_READ")
        st=path.lstat()
        if path.is_symlink() or not path.is_file() or st.st_nlink!=1 or st.st_mode&0o777!=mode:
            raise RuntimeError("FIXTURE_METADATA")
        raw=path.read_bytes()
        if len(raw)>limit:raise RuntimeError("FIXTURE_BOUND")
        return raw
    def process(self,pid):
        if pid not in self.processes:raise FileNotFoundError()
        return copy.deepcopy(self.processes[pid])
    def verify_replacement(self,m,c):
        if self.process(222)!=m["relay_before"] or c.get("no_node_transition") is not True:
            raise RuntimeError("ORIGINAL_NODE_CHANGED")
        return {"node_after":identity(222)}
    def db_rows(self,m,path,queries):
        result=[]
        for sql,args in queries:
            if not sql.startswith("SELECT kind,payload FROM audit WHERE event_id="):raise AssertionError(sql)
            row=self.audit.get(args[0]);result.append([copy.deepcopy(row)] if row else [])
        return result
    def command(self,argv,**kwargs):
        if argv[0]==boot.NFT:
            document=json.loads(Path(argv[-1]).read_text())
            action="seal" if "create" in document["nftables"][0] else "unseal"
            if action=="seal":
                if self.rules:raise RuntimeError("DUPLICATE_SEAL")
                self.rules=copy.deepcopy(boot.expected_readback(SHA))
                for index,row in enumerate(self.rules):next(iter(row.values()))["handle"]=100+index
            else:
                if not self.rules:raise RuntimeError("DUPLICATE_UNSEAL")
                self.rules=[]
        else:
            if argv[:4]!=["/usr/bin/sudo","-n","-u","mcpdev"]:raise AssertionError("UNEXPECTED_COMMAND")
            action="close" if "close" in argv else "open"
            epoch=int(argv[argv.index("--expected-epoch")+1]);command_id=argv[argv.index("--command-id")+1]
            actor=argv[argv.index("--actor")+1]
            if self.gate["epoch"]!=epoch or command_id in self.audit:raise RuntimeError("DUPLICATE_GATE_COMMAND")
            self.gate={"state":"draining" if action=="close" else "open","epoch":epoch+1,
                       "command_id":command_id,"actor":actor}
            self.audit[command_id]={"kind":"operator_control","payload":json.dumps(
                {"action":action,"expected_epoch":epoch,"actor":actor})}
        self.calls.append(action)
        if action in self.fail_after:
            self.fail_after.remove(action)
            raise subprocess.TimeoutExpired(argv,30)
        return subprocess.CompletedProcess(argv,0,b"{}",b"")
    def write_request(self):
        raw=(self.stage/"journal.json").read_bytes()
        value={"schema_version":1,"purpose":h.ABORT_PURPOSE,"manifest_sha256":SHA,
          "operation_id":self.m["native"]["operation_id"],"journal_sha256":h.digest(raw),
          "original_holder_sha256":h.digest(h.canonical(self.r["original_holder"])),
          "admission_never_published":True,"native_action_started":False,"carrier_dispatched":False,
          "all_invoked_operations_resolved":True,"retry_performed":False,"restore_original_intake":True}
        api.savejson(self.stage/"unpublished-abort-reconciliation.json",value)
        return value
    def fresh_record(self):
        r,c,raw=h.read_journal(api,receipt,self.m)
        self.r,self.c=r,c;self.b.r=r;self.b.c=c
        return r,c,raw
    def observe(self):
        r,c,raw=self.fresh_record()
        return h.partial_observation(api,receipt,self.m,r,c,raw,self.b,self.g)
    def abort(self):
        r,c,raw=self.fresh_record()
        return h.abort_unpublished(api,receipt,self.m,r,c,raw,self.b,self.g)
    def close_gate(self):return h.gate_action(api,self.m,self.r,self.c,"close")
    def seal(self):return self.b.seal(True)
    def keeper(self):
        self.keeper_live=True
        self.c["intents"]["browser_lock"]={"command_phase":"invocation_may_have_started","spawn_pid":888}
        api.savejson(self.g.keeper_path("held"),{"manifest_sha256":SHA,"cycle":"apply","keeper":identity(888)})
        api.update(self.r)
    def full(self):self.close_gate();self.seal();self.keeper()
    def observation(self):
        core={k:"7"*64 for k in receipt.CORE_KEYS}
        core.update(pid=111,start_ticks=222,epoch=95)
        relay={k:"8"*64 for k in receipt.RELAY_KEYS}
        relay.update(pid=222,start_ticks=333,table_handle=100,quiet_seconds_required=47,table_name=receipt.TABLE)
        return {"captured_at":receipt.utc(h.time.time()),"holder_sha256":self.m["holder_sha256"],
                "core":core,"relay":relay,"counts":copy.deepcopy(receipt.COUNTS),"core_state":"draining",
                "relay_state":{"ingress_sealed":True,"keeper_held":True,"draining":False,
                    "server_closed":False,"quiet_seconds":47},
                "native_preflight_sha256":self.m["native"]["native_preflight_sha256"]}
    def sign(self):
        with patch.object(h,"observations",lambda *args:self.observation()):
            return h.sign_once(api,receipt,self.m,self.r,self.c,self.b,self.g)

class RecoveryTests(unittest.TestCase):
    def setUp(self):self.f=Fixture()
    def tearDown(self):self.f.close()
    def test_journal_rejects_unknown_phase(self):
        self.f.c["intents"]["close"]={"command_phase":"maybe_done"};api.update(self.f.r)
        with self.assertRaisesRegex(RuntimeError,"UNKNOWN_ORIGINAL"):self.f.fresh_record()
    def test_journal_rejects_foreign_keeper_cycle(self):
        self.f.c["keeper_cycle"]="rollback";api.update(self.f.r)
        with self.assertRaisesRegex(RuntimeError,"ORIGINAL_HOLDER_CYCLE"):self.f.fresh_record()
    def test_journal_rejects_seal_without_invoked_close(self):
        self.f.c["intents"]["nft_seal"]={"command_phase":"intent_saved"};api.update(self.f.r)
        with self.assertRaisesRegex(RuntimeError,"SEAL_WITHOUT"):self.f.fresh_record()
    def test_observe_without_stage_or_journal_never_writes(self):
        (self.f.stage/"journal.json").unlink()
        before=sorted(self.f.stage.rglob("*"))
        out=h.inspect_or_finish(api,receipt,self.f.m,False)
        self.assertEqual(out["state"],"HOLDER_STAGE_WITHOUT_JOURNAL")
        self.assertEqual(before,sorted(self.f.stage.rglob("*")))
        with self.assertRaisesRegex(RuntimeError,"JOURNAL_UNCONFIRMED"):
            h.inspect_or_finish(api,receipt,self.f.m,False,True)
    def test_observe_never_invoked_close_phases_are_original_open(self):
        for phase in ("intent_saved","precondition_failed"):
            self.f.c["intents"]["close"]={"command_phase":phase};api.update(self.f.r)
            out=self.f.observe()
            self.assertEqual(out["gate"]["status"],"original_open")
            self.assertEqual(out["blocking_stages"],[])
        self.assertEqual(self.f.calls,[])
    def test_observe_unknown_close_does_not_retry(self):
        self.f.c["intents"]["close"]={"command_phase":"invocation_may_have_started","action":"close",
            "expected_epoch":94,"command_id":"missing"};api.update(self.f.r)
        before=(self.f.stage/"journal.json").read_bytes();out=self.f.observe()
        self.assertIn("gate",out["blocking_stages"]);self.assertEqual(self.f.calls,[])
        self.assertEqual(before,(self.f.stage/"journal.json").read_bytes())
    def test_observe_lost_close_ack_reads_exact_audit(self):
        self.f.fail_after.add("close")
        with self.assertRaises(subprocess.TimeoutExpired):self.f.close_gate()
        out=self.f.observe();self.assertEqual(out["gate"]["status"],"closed_owned")
        self.assertEqual(self.f.calls,["close"])
    def test_observe_held_keeper_before_held_receipt(self):
        self.f.full();out=self.f.observe()
        self.assertEqual(out["keeper"]["status"],"held_owned")
        self.assertEqual(out["admission"]["status"],"unpublished")
        self.assertFalse((self.f.stage/"HELD.json").exists())
    def test_observe_after_release_and_unseal_is_phase_aware(self):
        self.f.full();self.f.g.release(True);self.f.b.unseal(True)
        before=list(self.f.calls);out=self.f.observe()
        self.assertEqual(out["keeper"]["status"],"released_owned")
        self.assertEqual(out["firewall"]["status"],"unsealed_owned")
        self.assertEqual(out["blocking_stages"],[]);self.assertEqual(before,self.f.calls)
    def test_observe_flags_journal_change_without_writing(self):
        raw=(self.f.stage/"journal.json").read_bytes()
        real=self.f.root_read
        def racing(path,*args):
            data=real(path,*args)
            return data+b" " if path==self.f.stage/"journal.json" else data
        with patch.object(h,"root_read",racing):
            out=h.partial_observation(api,receipt,self.f.m,self.f.r,self.f.c,raw,self.f.b,self.f.g)
        self.assertFalse(out["stable_journal"]);self.assertEqual(raw,(self.f.stage/"journal.json").read_bytes())
    def test_observe_main_failure_has_no_lock_or_failure_write(self):
        with patch.object(h,"__file__",str(h.CODE/"hold_native_overlay.py")), \
             patch.object(h.sys,"argv",["holder","--observe","--manifest-sha256",SHA]), \
             patch.object(h.sys,"flags",types.SimpleNamespace(isolated=1,no_site=1)), \
             patch.object(h.sys,"dont_write_bytecode",True), \
             patch.object(h,"load_inputs",return_value=(api,receipt,self.f.m,b"{}")), \
             patch.object(h,"inspect_or_finish",side_effect=RuntimeError("READ_FAILED")), \
             patch.object(api,"acquire_release_lock",side_effect=AssertionError("OBSERVE_LOCK_WRITE")):
            with self.assertRaisesRegex(RuntimeError,"READ_FAILED"):h.main()
        self.assertFalse((self.f.stage/"HOLDER_FAILURE.json").exists())
    def test_abort_no_actions_requires_no_gate_command(self):
        self.f.write_request();self.assertEqual(self.f.abort(),h.ABORTED)
        self.assertEqual(self.f.calls,[]);self.assertEqual(self.f.gate,self.f.m["gate_before"])
        self.assertEqual(self.f.abort(),h.ABORTED);self.assertEqual(self.f.calls,[])
    def test_abort_closed_without_seal_opens_exact_epoch(self):
        self.f.close_gate();self.f.write_request();self.assertEqual(self.f.abort(),h.ABORTED)
        self.assertEqual(self.f.calls,["close","open"]);self.assertEqual(self.f.gate["epoch"],96)
    def test_abort_sealed_without_keeper_only_removes_owned_table(self):
        self.f.close_gate();self.f.seal();self.f.write_request();self.f.abort()
        self.assertEqual(self.f.calls,["close","seal","unseal","open"]);self.assertEqual(self.f.rules,[])
    def test_abort_held_keeper_orders_original_release_unseal_open(self):
        self.f.full();self.f.write_request();self.f.abort()
        self.assertEqual(self.f.calls,["close","seal","keeper_release","unseal","open"])
        self.assertEqual(self.f.abort(),h.ABORTED)
        self.assertEqual(self.f.calls,["close","seal","keeper_release","unseal","open"])
    def test_abort_wrong_journal_pin_has_no_cleanup(self):
        self.f.full();v=self.f.write_request()
        v["journal_sha256"]="f"*64
        (self.f.stage/"unpublished-abort-reconciliation.json").write_text(json.dumps(v))
        with self.assertRaisesRegex(RuntimeError,"JOURNAL_CHANGED"):self.f.abort()
        self.assertEqual(self.f.calls,["close","seal"])
    def test_abort_wrong_root_native_disposition_has_no_cleanup(self):
        self.f.full();v=self.f.write_request();v["carrier_dispatched"]=True
        (self.f.stage/"unpublished-abort-reconciliation.json").write_text(json.dumps(v))
        with self.assertRaisesRegex(RuntimeError,"DISPOSITION_CONFLICT"):self.f.abort()
        self.assertEqual(self.f.calls,["close","seal"])
    def test_abort_original_holder_live_is_denied(self):
        self.f.write_request();self.f.processes[333]=identity(333)
        with self.assertRaisesRegex(RuntimeError,"STILL_OWNS"):self.f.abort()
        self.assertEqual(self.f.calls,[])
    def test_abort_unknown_close_outcome_is_held(self):
        self.f.c["intents"]["close"]={"command_phase":"invocation_may_have_started","action":"close",
            "expected_epoch":94,"command_id":"missing"};api.update(self.f.r);self.f.write_request()
        with self.assertRaisesRegex(RuntimeError,"OUTCOME_UNRESOLVED"):self.f.abort()
        self.assertEqual(self.f.calls,[])
    def test_abort_unknown_seal_outcome_is_held_without_recreation(self):
        self.f.close_gate();self.f.c["intents"]["nft_seal"]={"command_phase":"invocation_may_have_started"}
        api.update(self.f.r);self.f.write_request()
        with self.assertRaisesRegex(RuntimeError,"OUTCOME_UNRESOLVED"):self.f.abort()
        self.assertEqual(self.f.calls,["close"])
    def test_abort_unknown_keeper_is_held_without_spawn(self):
        self.f.close_gate();self.f.seal()
        self.f.c["intents"]["browser_lock"]={"command_phase":"invocation_may_have_started"}
        api.update(self.f.r);self.f.write_request()
        with self.assertRaisesRegex(RuntimeError,"OUTCOME_UNRESOLVED"):self.f.abort()
        self.assertEqual(self.f.calls,["close","seal"])
    def test_abort_publication_marker_without_text_is_unknown(self):
        self.f.close_gate();api.savejson(self.f.stage/"ADMISSION_PUBLICATION_INTENT.json",{"partial":True})
        self.f.write_request()
        with self.assertRaisesRegex(RuntimeError,"PUBLICATION_UNKNOWN"):self.f.abort()
        self.assertEqual(self.f.calls,["close"])
    def test_abort_publication_phase_even_without_marker_is_unknown(self):
        self.f.c["admission"]={"phase":"publication_may_have_started"};api.update(self.f.r);self.f.write_request()
        with self.assertRaisesRegex(RuntimeError,"PUBLICATION_UNKNOWN"):self.f.abort()
    def test_abort_signed_only_prepublication_failure_is_safe(self):
        self.f.close_gate();self.f.c["admission"]={"phase":"signing_may_have_started"}
        api.save(self.f.stage/"native-admission.signed.json",b"preserved-partial-signature")
        api.update(self.f.r);self.f.write_request();self.f.abort()
        self.assertEqual(self.f.calls,["close","open"])
        self.assertEqual((self.f.stage/"native-admission.signed.json").read_bytes(),b"preserved-partial-signature")
    def test_abort_lost_release_ack_reconciles_without_second_release(self):
        self.f.full();self.f.write_request();self.f.fail_after.add("keeper_release")
        with self.assertRaises(TimeoutError):self.f.abort()
        self.assertEqual(self.f.abort(),h.ABORTED)
        self.assertEqual(self.f.calls,["close","seal","keeper_release","unseal","open"])
    def test_abort_lost_unseal_ack_reconciles_without_second_delete(self):
        self.f.full();self.f.write_request();self.f.fail_after.add("unseal")
        with self.assertRaises(subprocess.TimeoutExpired):self.f.abort()
        self.assertEqual(self.f.abort(),h.ABORTED)
        self.assertEqual(self.f.calls,["close","seal","keeper_release","unseal","open"])
    def test_abort_lost_open_ack_reconciles_without_second_open(self):
        self.f.full();self.f.write_request();self.f.fail_after.add("open")
        with self.assertRaises(subprocess.TimeoutExpired):self.f.abort()
        self.assertEqual(self.f.gate["state"],"open")
        self.assertEqual(self.f.abort(),h.ABORTED)
        self.assertEqual(self.f.calls,["close","seal","keeper_release","unseal","open"])
    def test_abort_precondition_failed_open_can_issue_once_after_proof(self):
        self.f.full();self.f.write_request()
        original=api.command_once
        def fail_open(r,c,key,fields,argv,before):
            if key=="open":
                api.begin_intent(r,c,key,dict(fields,command_phase="precondition_failed"))
                raise RuntimeError("controlled before invocation")
            return original(r,c,key,fields,argv,before)
        with patch.object(api,"command_once",fail_open):
            with self.assertRaisesRegex(RuntimeError,"controlled"):self.f.abort()
        self.assertEqual(self.f.abort(),h.ABORTED)
        self.assertEqual(self.f.calls.count("open"),1)
    def test_sign_publishes_text_last_after_bound_journal(self):
        self.f.full();order=[];original=api.save
        def tracked(path,data):
            order.append(path.name)
            if path.name=="native-admission.txt":
                r=json.loads((self.f.stage/"journal.json").read_text())
                self.assertEqual(r["held_sha256"],api.filehash(self.f.stage/"HELD.json"))
                self.assertTrue((self.f.stage/"native-admission.signed.json").exists())
                self.assertTrue((self.f.stage/"ADMISSION_PUBLICATION_INTENT.json").exists())
                self.assertEqual(r["cycles"]["apply"]["admission"]["phase"],"publication_may_have_started")
            return original(path,data)
        with patch.object(api,"save",tracked):self.f.sign()
        self.assertLess(order.index("native-admission.signed.json"),order.index("HELD.json"))
        self.assertLess(order.index("HELD.json"),order.index("native-admission.txt"))
        self.assertEqual(self.f.observe()["admission"]["status"],"published")
    def test_signing_interruption_never_regenerates_or_renews(self):
        self.f.full()
        with patch.object(receipt,"make_receipt",side_effect=RuntimeError("controlled signing failure")) as make:
            with self.assertRaisesRegex(RuntimeError,"controlled"):self.f.sign()
            with self.assertRaisesRegex(RuntimeError,"ORIGINAL_HOLD_RECEIPT_ONLY"):self.f.sign()
            self.assertEqual(make.call_count,1)
    def test_publication_interruption_never_republishes_or_allows_abort(self):
        self.f.full();original=api.save
        def fail_text(path,data):
            if path.name=="native-admission.txt":raise RuntimeError("controlled prewrite interruption")
            return original(path,data)
        with patch.object(api,"save",fail_text):
            with self.assertRaisesRegex(RuntimeError,"controlled"):self.f.sign()
        self.assertFalse((self.f.stage/"native-admission.txt").exists())
        self.assertEqual(self.f.observe()["admission"]["status"],"publication_unknown")
        with self.assertRaisesRegex(RuntimeError,"ORIGINAL_HOLD_RECEIPT_ONLY"):self.f.sign()
        self.f.write_request()
        with self.assertRaisesRegex(RuntimeError,"PUBLICATION_UNKNOWN"):self.f.abort()
    def test_lost_publication_ack_is_read_back_without_new_signature(self):
        self.f.full();original=api.update
        def fail_after_text(r):
            if self.f.c.get("admission",{}).get("phase")=="published":raise RuntimeError("lost published ACK")
            return original(r)
        with patch.object(api,"update",fail_after_text):
            with self.assertRaisesRegex(RuntimeError,"lost published"):self.f.sign()
        self.assertEqual(self.f.observe()["admission"]["status"],"published")
        with self.assertRaisesRegex(RuntimeError,"ORIGINAL_HOLD_RECEIPT_ONLY"):self.f.sign()
    def test_removed_published_text_remains_unknown_not_unpublished(self):
        self.f.full();self.f.sign();(self.f.stage/"native-admission.txt").unlink()
        self.f.write_request()
        with self.assertRaisesRegex(RuntimeError,"PUBLISHED_ADMISSION_FILE_MISSING"):self.f.abort()
    def test_corrupt_published_bytes_are_never_accepted(self):
        self.f.full();self.f.sign()
        (self.f.stage/"native-admission.txt").write_bytes(b"wrong\n")
        self.assertIn("admission",self.f.observe()["blocking_stages"])
    def test_finalize_before_held_is_denied_without_cleanup(self):
        self.f.full()
        with self.assertRaises(FileNotFoundError):h.inspect_or_finish(api,receipt,self.f.m,True)
        self.assertEqual(self.f.calls,["close","seal"])
    def test_finalize_uses_original_published_outcome_and_no_resign(self):
        self.f.full();held=self.f.sign()
        proof={"request_sha256":"9"*64,"outcome":"REJECTED_BEFORE_MUTATION",
               "native_result_sha256":"a"*64,"definition_restore_sha256":"b"*64}
        with patch.object(h,"reconciliation",return_value=proof), \
             patch.object(h,"observations",side_effect=AssertionError("Already released")):
            self.f.c["native_reconciled"]=True;self.f.c["native_reconciliation"]=proof;api.update(self.f.r)
            self.f.g.release(True);self.f.b.unseal(True)
            result=h.inspect_or_finish(api,receipt,self.f.m,True)
        self.assertEqual(result,"native_operation_reconciled_original_intake_reopened")
        self.assertEqual(self.f.calls,["close","seal","keeper_release","unseal","open"])
        self.assertEqual(held["receipt_sha256"],h.digest((self.f.stage/"native-admission.signed.json").read_bytes()))
    def test_exact_watchdog_dropin_is_allowed_but_neighbor_is_not(self):
        baseline=json.loads((HERE/"reference/native-holder-baseline-draft-20261122T1704.json").read_text())
        allowed="/etc/systemd/system/aibit-linux-relay-watchdog.service.d/20-abit-xvfb-private-tmp.conf"
        self.assertEqual(baseline["source_files"][allowed],"1b2c9df8d44ef56ff6370200caa3f114d89c34e3c3930db068a66c6aaed873f2")
        fake=types.SimpleNamespace(BASE=api.BASE,filehash=lambda p:baseline["source_files"][str(p)])
        def resolved(path):
            return h.CORE if path==api.BASE/"current/runtime/open_webui" else path
        with patch.object(Path,"resolve",resolved):
            ORIGINAL_SOURCE_CHECK(fake,baseline)
            baseline["source_files"][allowed.replace("20-abit-xvfb-private-tmp.conf","99-unreviewed.conf")]="d"*64
            with self.assertRaisesRegex(RuntimeError,"SOURCE_PATH_SCOPE"):ORIGINAL_SOURCE_CHECK(fake,baseline)

if __name__=="__main__":
    unittest.main(verbosity=2)
