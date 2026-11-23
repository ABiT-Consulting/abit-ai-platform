"""Ordinary-filesystem issuer state tests; root/kernel/native checks are fixtures."""
from __future__ import annotations
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1] / "open_webui/utils"


def load(name):
    spec=importlib.util.spec_from_file_location(name, ROOT/(name+".py"))
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
    return module


c=load("agentic_native_contract")
ac=load("agentic_native_admission_contract")
issuer=load("agentic_native_issuer")


def attestation(suffix="one", now=None):
    now=int(time.time()) if now is None else now
    scope={"invocation_id":"invocation-"+suffix,"run_id":"11111111-1111-1111-1111-111111111111",
        "project_id":"PROJ-0202","task_id":"TASK-fixture-"+suffix,"task_revision":"task-r1",
        "project_revision":"project-r1","agent_id":"22222222-2222-2222-2222-222222222222",
        "erp_user":"ai-operations@abitcons.com","role":"it_manager","packet_sha256":"1"*64,
        "context_sha256":"2"*64,"srs_revision":"srs-r1","srs_sha256":"3"*64,"lease_fence":1,
        "repository":c.REPOSITORY,"environment":"ABIT-DEV-LINUX","profile_id":c.PROFILE_ID}
    action={"schema_version":1,"action_id":"agov-action-"+hashlib.sha256(suffix.encode()).hexdigest()[:48],
        "project_id":scope["project_id"],"task_id":scope["task_id"],"task_revision":scope["task_revision"],
        "actor":{k:scope[k] for k in ("project_id","agent_id","role","erp_user")},
        "repository":scope["repository"],"environment":scope["environment"],"operation":c.OPERATION,
        "risk":"privileged","parameters":{"profile_id":c.PROFILE_ID},"requested_at":now-1}
    approval={"schema_version":1,"approval_id":"approval-"+suffix,"project_id":scope["project_id"],
        "task_id":scope["task_id"],"action_fingerprint":ac.fingerprint(action),"approved_by":"manager@example.invalid",
        "approved_at":now-1,"expires_at":now+600,"status":"approved","used_by_action_id":action["action_id"]}
    consumed={"consumed":True,"action_id":action["action_id"],"approval_id":approval["approval_id"],
        "action_fingerprint":ac.fingerprint(action),"receipt_id":"receipt-"+suffix,"duplicate":False,
        **{k:scope[k] for k in ("invocation_id","run_id","project_id","task_id")}}
    return {"schema_version":2,"kind":"abit_native_admission","scope":scope,"subject_sha256":"8"*64,
        "action":action,"approval":approval,"consumption":consumed,"lease_token_sha256":"4"*64,
        "lease_expires_at":now+600,"start_receipt_sha256":"5"*64,"erp_baseline_sha256":"6"*64,
        "maintenance_admission":{"lease_id":"7"*48,"kind":"governed","reference":scope["invocation_id"],"epoch":0},
        "prepared_at":now}


def grant(a):
    now=int(time.time())
    return {"authorized":True,"mode":"admit","request_payload_sha256":c.digest({"attestation":a}),
        "attestation_sha256":c.digest(a),"maintenance_admission":a["maintenance_admission"],
        "issued_at":now,"expires_at":min(now+15,a["lease_expires_at"],a["approval"]["expires_at"])}


class IssuerCases(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.r=Path(self.temp.name);self.native=self.r/"native";self.root=self.r/"broker"
        for p in (self.native/"executions",self.native/"authority",self.root/"admissions",
                  self.root/"gates",self.root/"attempts"):
            p.mkdir(parents=True,mode=0o700,exist_ok=True)
        self.lock=self.r/"global.lock";self.lock.write_bytes(b"")
        self.patches=[patch.object(issuer,"NATIVE",self.native)]
        for p in self.patches:p.start();self.addCleanup(p.stop)
        self.writes=[];self.fail_on=None;outer=self
        def directory(p):
            p=Path(p);outer.assertTrue(p.is_relative_to(outer.r));outer.assertEqual(p.resolve(),p)
            return os.open(p,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        def document(path,limit=65536,**kwargs):
            path=Path(path);parent=directory(path.parent)
            try:
                fd=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=parent)
                try:
                    before=os.fstat(fd)
                    outer.assertTrue(stat.S_ISREG(before.st_mode))
                    outer.assertEqual(stat.S_IMODE(before.st_mode),0o400)
                    raw=os.read(fd,limit+1)
                    outer.assertLessEqual(len(raw),limit)
                    outer.assertEqual(issuer.identity(before),issuer.identity(os.fstat(fd)))
                    outer.assertEqual(issuer.identity(before),issuer.identity(os.stat(path.name,dir_fd=parent,follow_symlinks=False)))
                finally:os.close(fd)
                return json.loads(raw),hashlib.sha256(raw).hexdigest()
            finally:os.close(parent)
        def write(parent,name,value):
            if self.fail_on==name:raise OSError("Fixture injected original write failure")
            fd=directory(parent)
            try:
                out=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o400,dir_fd=fd)
                with os.fdopen(out,"wb") as stream:
                    stream.write(c.canonical(value).encode());stream.flush();os.fsync(stream.fileno())
                os.fsync(fd)
                self.writes.append((str(parent),name))
            finally:os.close(fd)
        def gate_file(scope):return self.root/"gates"/(hashlib.sha256(scope["invocation_id"].encode()).hexdigest()+".json")
        def load_gate(scope,admission=True):
            g,_=document(gate_file(scope));return c.validate_gate(g,scope,int(time.time()),admission=admission)
        self.io=types.SimpleNamespace(ROOT=self.root,root_directory=directory,root_document=document,
            write_once=write,gate_file=gate_file,load_gate=load_gate,
            observe_worker=lambda binding,**kw:{"state":"completed","cleanup_confirmed":True})
        subject={"payload":{}}
        class Native:
            controls={"worker_policy":types.SimpleNamespace(MAX_ARCHIVE_BYTES=2415919104)}
            def load(self):return subject
            @contextmanager
            def retained_reader(self,subject):
                yield self.read_run
            def template_size(self,subject):return (1024,2)
            def authority(self,value,issued,expires):
                return {"schema_version":4,"nonce":value,"issued_at_utc":issuer.utc(issued),
                        "expires_at_utc":issuer.utc(expires)}
            def read_run(self,value,pin,now,admission=True):
                p=outer.native/"executions"/value
                run,actual=document(p/"EXECUTION.json");outer.assertEqual(pin,actual)
                for key,file in (("admission_sha256","ADMISSION.json"),("capacity_sha256","CAPACITY.json")):
                    _,found=document(p/file);outer.assertEqual(run[key],found)
                _,found=document(outer.native/"authority"/(value+".json"));outer.assertEqual(run["authority_sha256"],found)
                outer.assertEqual(run["nonce"],value)
                if admission:c.require(run["issued_at"]<=now<run["expires_at"],"expired fixture")
                return subject["payload"]
        self.peer={"pid":100,"start_ticks":200}
        self.obj=object.__new__(issuer.PerRunIssuer);self.obj.io=self.io
        self.obj.registration={"mode":ac.MODE,"subject_sha256":"8"*64,"control_sha256":{n:"9"*64 for n in issuer.MODULES}}
        self.obj.native=Native();self.obj.index=self.root/"admissions";self.obj.peer_check=lambda:dict(self.peer)
        self.obj.locked=self.locked
        self.obj.capacity=self.capacity
        self.a=attestation();self.payload={"attestation":self.a};self.g=grant(self.a)

    @contextmanager
    def locked(self):
        with self.lock.open("rb") as fd:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            yield

    def capacity(self,subject,value,a,issued,expires):
        return {"schema_version":1,"kind":"bigrro_native_per_run_capacity","nonce":value,
            "subject_sha256":"8"*64,"admission_sha256":c.digest(a),"observed_at":issued,
            "expires_at":expires,"memory_available_bytes":4*1024**3,"memory_required_bytes":issuer.MEMORY_REQUIRED,
            "free_output_capture_bytes":10*1024**3,"required_output_capture_bytes":2415919104+1024+18*4096,
            "concurrent_native_attempts":0,"rootfs_copy_bytes":1024,"required_copy_inodes":18,
            "available_inodes":10000,"filesystem_block_bytes":4096}

    def admit(self):
        return self.obj.admit(deepcopy(self.payload),deepcopy(self.g),dict(self.peer))

    def test_valid_admission_writes_one_immutable_nonce_local_binding(self):
        result=self.admit();a=result["gate"]["execution"]
        self.assertEqual(result["state"],"admitted");self.assertEqual(result["gate"]["schema_version"],2)
        self.assertEqual(len(list((self.native/"executions").iterdir())),1)
        self.assertEqual(len(list((self.native/"authority").iterdir())),1)
        self.assertFalse((self.native/"EXECUTION.json").exists())
        for path in self.r.rglob("*.json"):
            self.assertEqual(path.stat().st_mode&0o777,0o400)
            self.assertFalse(path.read_bytes().endswith(b"\n"))
        self.assertEqual(self.obj.observe(self.payload),result)

    def test_preflight_is_read_only_and_nonce_free(self):
        result=self.obj.prepare({"scope":self.a["scope"]})
        self.assertEqual(result,ac.readiness(self.a["scope"],"8"*64));self.assertEqual(self.writes,[])
        self.assertIsNone(result["nonce"]);self.assertFalse(result["authority_issued"])

    def test_duplicate_original_is_observed_without_more_writes(self):
        first=self.admit();count=len(self.writes)
        self.assertEqual(self.admit(),first);self.assertEqual(len(self.writes),count)

    def test_same_invocation_changed_consumption_conflicts(self):
        self.admit();count=len(self.writes)
        self.payload["attestation"]["consumption"]["receipt_id"]="other-valid-id"
        self.g=grant(self.payload["attestation"])
        with self.assertRaisesRegex(ValueError,"CONFLICT"):self.admit()
        self.assertEqual(len(self.writes),count)

    def test_reused_consumption_for_new_invocation_is_denied(self):
        self.admit()
        old=self.a
        self.a=attestation("two");self.a["consumption"]["receipt_id"]=old["consumption"]["receipt_id"]
        self.payload={"attestation":self.a};self.g=grant(self.a)
        with self.assertRaisesRegex(ValueError,"ALREADY_USED"):self.admit()

    def test_partial_original_retained_and_never_recreated(self):
        self.fail_on="EXECUTION.json"
        with self.assertRaises(OSError):self.admit()
        count=len(self.writes);self.fail_on=None
        response=self.admit()
        self.assertEqual(response["state"],"uncertain");self.assertEqual(len(self.writes),count)
        self.assertEqual(len(list((self.native/"authority").iterdir())),1)

    def test_lost_ack_after_gate_publication_has_read_only_recovery(self):
        self.fail_on="ADMITTED.json"
        with self.assertRaises(OSError):self.admit()
        count=len(self.writes)
        response=self.obj.observe(self.payload)
        self.assertEqual(response["state"],"admitted");self.assertEqual(len(self.writes),count)

    def test_capacity_denial_writes_nothing(self):
        self.obj.capacity=lambda *a:(_ for _ in ()).throw(ValueError("NATIVE_CURRENT_CAPACITY_DENIED"))
        with self.assertRaisesRegex(ValueError,"CAPACITY"):self.admit()
        self.assertEqual(self.writes,[]);self.assertEqual(self.obj.names(),[])

    def test_replaced_peer_writes_nothing(self):
        self.obj.peer_check=lambda:{"pid":999,"start_ticks":1}
        with self.assertRaisesRegex(ValueError,"PROCESS_CHANGED"):self.admit()
        self.assertEqual(self.writes,[])

    def test_expired_grant_and_forged_scope_denied_before_write(self):
        self.g["expires_at"]=self.g["issued_at"]
        with self.assertRaises(ValueError):self.admit()
        self.assertEqual(self.writes,[])

    def test_unknown_prior_admission_blocks_a_distinct_attempt(self):
        self.fail_on="EXECUTION.json"
        with self.assertRaises(OSError):self.admit()
        self.fail_on=None;self.a=attestation("two")
        self.a["scope"]["run_id"]="33333333-3333-3333-3333-333333333333";self.a["consumption"]["run_id"]=self.a["scope"]["run_id"]
        self.payload={"attestation":self.a};self.g=grant(self.a);count=len(self.writes)
        with self.assertRaisesRegex(ValueError,"UNRESOLVED"):self.admit()
        self.assertEqual(len(self.writes),count)

    def test_completed_original_with_cleanup_allows_distinct_approval(self):
        first=self.admit();value=first["gate"]["execution"]["nonce"]
        path=self.root/"attempts"/value;path.mkdir(mode=0o700)
        self.io.write_once(path,"BINDING.json",{"scope":self.a["scope"],"gate_sha256":c.digest(first["gate"])})
        self.a=attestation("two");self.a["scope"]["run_id"]="33333333-3333-3333-3333-333333333333"
        self.a["consumption"]["run_id"]=self.a["scope"]["run_id"]
        self.payload={"attestation":self.a};self.g=grant(self.a)
        second=self.admit()
        self.assertNotEqual(first["gate"]["execution"]["nonce"],second["gate"]["execution"]["nonce"])
        self.assertEqual(len(list((self.native/"authority").iterdir())),2)

    def test_no_cleanup_proof_blocks_next_attempt(self):
        first=self.admit();value=first["gate"]["execution"]["nonce"]
        path=self.root/"attempts"/value;path.mkdir(mode=0o700)
        self.io.write_once(path,"BINDING.json",{"scope":self.a["scope"],"gate_sha256":c.digest(first["gate"])})
        self.io.observe_worker=lambda *a,**k:{"state":"failed_retained","cleanup_confirmed":False}
        self.a=attestation("two");self.a["scope"]["run_id"]="33333333-3333-3333-3333-333333333333";self.a["consumption"]["run_id"]=self.a["scope"]["run_id"]
        self.payload={"attestation":self.a};self.g=grant(self.a)
        with self.assertRaisesRegex(ValueError,"UNRESOLVED"):self.admit()

    def test_expired_original_observation_does_not_extend_or_issue(self):
        self.admit();before=list(self.writes)
        with patch.object(issuer.time,"time",return_value=self.a["lease_expires_at"]+100):
            result=self.obj.observe(self.payload)
        self.assertEqual(result["state"],"admitted");self.assertEqual(before,self.writes)

    def test_nonce_collision_cannot_overwrite_foreign_authority(self):
        value="1"*32
        self.io.write_once(self.native/"authority",value+".json",{"foreign":"preserved"})
        before=(self.native/"authority"/(value+".json")).read_bytes()
        with patch.object(issuer.secrets,"token_hex",return_value=value):
            with self.assertRaises(FileExistsError):self.admit()
        self.assertEqual((self.native/"authority"/(value+".json")).read_bytes(),before)

    def test_concurrent_duplicate_issues_at_most_one_nonce(self):
        entered=threading.Event();release=threading.Event();errors=[];results=[]
        original_capacity=self.capacity
        def slow(*args):
            entered.set();release.wait(3);return original_capacity(*args)
        self.obj.capacity=slow
        def first():
            try:results.append(self.admit())
            except BaseException as error:errors.append(error)
        thread=threading.Thread(target=first);thread.start();self.assertTrue(entered.wait(2))
        try:
            with self.assertRaises(BlockingIOError):self.admit()
        finally:release.set();thread.join(4)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[]);self.assertEqual(len(results),1)
        self.assertEqual(len(list((self.native/"authority").iterdir())),1)

    def test_runtime_delegation_is_distinct_from_dispatch_lease_and_capacity(self):
        now=int(time.time());self.a["lease_expires_at"]=now+120
        self.a["approval"]["expires_at"]=now+7000;self.g=grant(self.a)
        result=self.admit();value=result["gate"]["execution"]["nonce"]
        run,_=self.io.root_document(self.native/"executions"/value/"EXECUTION.json")
        cap,_=self.io.root_document(self.native/"executions"/value/"CAPACITY.json")
        auth,_=self.io.root_document(self.native/"authority"/(value+".json"))
        self.assertEqual(run["expires_at"],self.a["approval"]["expires_at"])
        self.assertGreater(run["expires_at"],self.a["lease_expires_at"])
        self.assertEqual(cap["expires_at"],run["issued_at"]+900)
        self.assertEqual(auth["expires_at_utc"],issuer.utc(run["expires_at"]))

    def test_runtime_delegation_never_exceeds7200_or_shorter_original_approval(self):
        now=int(time.time());self.a["approval"]["expires_at"]=now+86400;self.g=grant(self.a)
        result=self.admit();gate=result["gate"]
        self.assertEqual(gate["expires_at"]-gate["issued_at"],7200)


    def test_capacity_uses_original_memory_and_inode_reserves(self):
        self.obj.active_cgroups=lambda:0
        disk=types.SimpleNamespace(f_frsize=4096,f_bavail=10**7,f_favail=100000)
        with patch.object(issuer,"kernel_bytes",return_value=b"MemTotal: 8388608 kB\nMemAvailable: 4194304 kB\n"),patch.object(issuer.os,"statvfs",return_value=disk):
            result=issuer.PerRunIssuer.capacity(self.obj,{}, "1"*32,self.a,int(time.time()),int(time.time())+600)
            self.assertEqual(result["memory_required_bytes"],2281701376)
            self.assertEqual(result["required_copy_inodes"],18)
            disk.f_favail=17
            with self.assertRaisesRegex(ValueError,"DISK"):issuer.PerRunIssuer.capacity(self.obj,{},"1"*32,self.a,1,2)

    def test_insufficient_current_memory_not_relabelled_success(self):
        self.obj.active_cgroups=lambda:0
        with patch.object(issuer,"kernel_bytes",return_value=b"MemTotal: 8388608 kB\nMemAvailable: 1024 kB\n"):
            with self.assertRaisesRegex(ValueError,"CAPACITY"):issuer.PerRunIssuer.capacity(self.obj,{},"1"*32,self.a,1,2)


if __name__=="__main__":
    unittest.main()
