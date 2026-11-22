"""Bounded recovery holder/source-contract fixtures; no live service or provider."""
import base64,copy,fcntl,hashlib,importlib.util,json,os,socket,stat,sys,tempfile,time,types,unittest
from pathlib import Path
from unittest.mock import patch,Mock
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
def module(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m
h=module("held_admission_receipt",ROOT/"deployment/native-overlay-held-drain-20261122/held_admission_receipt.py")
r=module("recovery_receipt",HERE/"recovery_receipt.py")
s=module("recovery_signer",HERE/"hold_native_recovery.py")
NOW=1795371900.0
KEY=b"fixture-only-never-a-production-key"*2
H="a"*64
OP="1"*64
NEW="4"*64

def original_binding():
 core={k:H for k in h.CORE_KEYS};core.update(pid=100,start_ticks=1000,epoch=99)
 relay={k:H for k in h.RELAY_KEYS};relay.update(pid=101,start_ticks=1001,
  table_name=h.TABLE,table_handle=10,quiet_seconds_required=3)
 return {"operation_id":OP,"controller_sha256":"2"*64,"manifest_sha256":"3"*64,
  "holder_sha256":s.OLD_HOLDER_SHA,"native_preflight_sha256":H,"restore_receipt_sha256":H,
  "core":core,"relay":relay,"holder_lease_expires_at":h.utc(NOW-60)}

def carrier_input(status="succeeded",release=7905,revision=39):
 job={"job":{"status":("failed"if status=="rejected"else status)},"tasks":[{"status":"succeeded"},{"status":("failed"if status=="rejected"else status)}]}
 phase={"status":job["job"]["status"],"deploymentJobs":[job]}
 step={"id":33564,"attempt":1,"status":phase["status"],"releaseDeployPhases":[phase]}
 value={"id":release,"status":"active","releaseDefinition":{"id":950},
  "releaseDefinitionRevision":revision+1,"environments":[{"id":14070,"releaseId":release,
  "definitionEnvironmentId":967,"status":status,"deploySteps":[step]}]}
 before={"id":950,"revision":revision,"name":"Synthetic definition","script":"retained fixture",
  "modifiedOn":"before","modifiedBy":{"synthetic":True},"lastRelease":{"id":release-1},
  "environments":[{"id":967,"currentRelease":{"id":release-1},"unchanged":{"all_fields":True}}],
  "unknown_future_business_field":{"must_be_preserved":["x",1]}}
 restored=copy.deepcopy(before);restored.update(revision=revision+2,modifiedOn="after",modifiedBy={"synthetic":False},lastRelease={"id":release})
 restored["environments"][0]["currentRelease"]={"id":release}
 return {"original-carrier-terminal.json":(value,s.canonical(value)+b"\n"),
  "definition950-before.json":(before,s.canonical(before)+b"\n"),
  "definition950-restored.json":(restored,s.canonical(restored)+b"\n")}

def binding():
 old=original_binding();carrier=s.carrier_proof(carrier_input(),7905)
 v={k:"5"*64 for k in r.DIGESTS}
 v.update(operation_id=NEW,original_binding=old,signer_sha256=H,
  original_holder={"pid":501,"start_ticks":5001,"uid":0,"cmdline_sha256":H,
   "source_sha256":s.OLD_HOLDER_SHA,"positively_ceased":True,"cessation_proof_sha256":H},
  recovery_holder={"pid":601,"start_ticks":6001,"uid":0,"cmdline_sha256":H,
   "source_sha256":H,"same_release_flock_held":True,"flock_proof_sha256":H},
  original_carrier=carrier,holder_lease_expires_at=h.utc(NOW+1200))
 return v

def observation(b,at=NOW):
 old=b["original_binding"]
 held={"captured_at":h.utc(at),"holder_sha256":old["holder_sha256"],
  "core":old["core"],"relay":old["relay"],"counts":dict(h.COUNTS),"core_state":"draining",
  "relay_state":{"ingress_sealed":True,"keeper_held":True,"draining":False,"server_closed":False,"quiet_seconds":3},
  "native_preflight_sha256":old["native_preflight_sha256"]}
 v={k:copy.deepcopy(b[k])for k in r.OBSERVATION_KEYS if k in b}
 v.update(held_observation=held,original_barriers_continuously_retained=True,original_apply_resumed=False,barriers_recreated=False)
 return v

def native_obs():
 old=original_binding();records={"context-index.json":{"sha256":H,"bytes":120}}
 v={"schema_version":1,"state":"ORIGINAL_ONLY_OBSERVATION","operation_id":OP,"release_id":7905,
  "manifest_sha256":old["manifest_sha256"],"controller_sha256":old["controller_sha256"],
  "context_sha256":H,"context_integrity_verified":True,
  "keeper":{"state":"ORIGINAL_FDS_HELD","current_verified":True,"ready_sha256":H,
   "source_sha256":H,"operation_id":OP,"context_sha256":H,
   "unit":"abit-native-fd-keeper-20261122-"+OP+".service","invocation_id":"a"*32,
   "pid":900,"start_ticks":9000,"apply_authority_granted":False},
  "source_or_config_actions":0,"service_actions":0,"journal_writes":0,
  "admission_renewed":False,"replay_allowed":False,"stopped_root_rollback_authority":False,
  "roles":{role:{"running":role=="backend","container_id":s.ROLE_IDS[role],"image_id":s.ROLE_IMAGE,
   "leaves":{leaf:{"state":"owned_postimage","source_actions":0}for leaf in s.LEAVES}}for role in s.ROLES},
  "site_pause":{"state":"paused_candidate_bytes","source_actions":0},
  "services":{event:{"intent_present":True,"may_have_started":True,"wire_ack_present":False,
   "positive_receipt_present":False,"current_original_outcome_proved":False,
   "error_code":"ORIGINAL_OUTCOME_NOT_CURRENTLY_PROVED","new_calls":0}for event in ("worker-stop","backend-hup","worker-start")},
  "original_journal":{"records":records,"sha256":s.sha(s.canonical(records)),"values_exported":False},
  "recovery_eligibility":{"native_mutations_by_observer":0,"apply_resume_authority":False,
   "source_attribution_complete":True,"current_original_keeper_verified":True,
   "original_carrier_terminal_and_950_restored":"requires_external_verified_receipts",
   "service_changes_require_new_recovery_attestation":True}}
 return v

def terminal(b):
 return {"schema_version":1,"state":"ORIGINAL_TWO_ROLE_SOURCE_RESTORED_AND_VERIFIED",
  "operation_id":b["original_binding"]["operation_id"],"manifest_sha256":b["original_binding"]["manifest_sha256"],
  "context_sha256":b["original_context_sha256"],"keeper_ready_sha256":b["keeper_ready_sha256"],
  "recovery_operation_id":b["operation_id"],"recovery_manifest_sha256":b["manifest_sha256"],
  "all_original_leaves_and_metadata_restored":True,"scheduler_original_bytes_restored":True,
  "container_ids_and_images_preserved":True,"unrelated_app_bytes_and_metadata_preserved":True,
  "root_continuous_closure_still_required":True,"original_apply_resumed":False,
  "source_readback_sha256":H,"runtime_readback_sha256":H}

class FakeAPI:
 def __init__(self):self.events=[];self.failure=None
 def save(self,path,raw):
  self.events.append(("save",Path(path).name))
  if Path(path).name==self.failure:raise OSError("synthetic interrupted write")
  with open(path,"xb")as f:f.write(raw)
 def savejson(self,path,value):
  self.events.append(("json",Path(path).name,value.get("phase")))
  self.save(path,s.canonical(value)+b"\n")
 def save_or_verify(self,path,raw):
  if Path(path).exists():
   if Path(path).read_bytes()!=raw:raise s.Held("CHANGED")
  else:self.save(path,raw)
 def filehash(self,path):return s.sha(Path(path).read_bytes())
 def fsyncdir(self,path):pass
 def raw(self,path,maximum):return KEY
 def now(self):return h.utc(NOW)

class CarrierTests(unittest.TestCase):
 def test_succeeded_and_rejected_actual_shape_is_terminal(self):
  for status in ("succeeded","rejected"):
   with self.subTest(status=status):
    value=s.carrier_proof(carrier_input(status),7905)
    self.assertEqual(value["terminal_state"],status)
    self.assertTrue(value["business_fields_exact"])
    self.assertEqual(value["attempt_id"],33564)
 def test_release_active_is_not_mistaken_for_environment_running(self):
  fixture=carrier_input();self.assertEqual(fixture["original-carrier-terminal.json"][0]["status"],"active")
  self.assertEqual(s.carrier_proof(fixture,7905)["terminal_state"],"succeeded")
 def test_wrong_release_definition_or_environment_denied(self):
  for mut in (lambda x:x.update(id=7904),lambda x:x["releaseDefinition"].update(id=951),
   lambda x:x["environments"][0].update(definitionEnvironmentId=968)):
   q=carrier_input();mut(q["original-carrier-terminal.json"][0])
   with self.assertRaises(s.Held):s.carrier_proof(q,7905)
 def test_no_attempt_or_repeated_attempt_is_not_terminal_proof(self):
  for mode in ("missing","repeated","second"):
   q=carrier_input();steps=q["original-carrier-terminal.json"][0]["environments"][0]["deploySteps"]
   if mode=="missing":steps.clear()
   elif mode=="repeated":steps.append(copy.deepcopy(steps[0]))
   else:steps[0]["attempt"]=2
   with self.assertRaises(s.Held):s.carrier_proof(q,7905)
 def test_running_phase_job_or_task_denies_carrier_terminal(self):
  for level in ("environment","phase","job","task"):
   q=carrier_input();env=q["original-carrier-terminal.json"][0]["environments"][0];phase=env["deploySteps"][0]["releaseDeployPhases"][0]
   target={"environment":env,"phase":phase,"job":phase["deploymentJobs"][0]["job"],
    "task":phase["deploymentJobs"][0]["tasks"][0]}[level];target["status"]="inProgress"
   with self.assertRaises(s.Held):s.carrier_proof(q,7905)
 def test_full_business_projection_preserves_unknown_and_script_fields(self):
  for mutate in (lambda x:x.update(script="changed"),lambda x:x["unknown_future_business_field"].update(extra=True),
   lambda x:x["environments"][0]["unchanged"].update(all_fields=False)):
   q=carrier_input();mutate(q["definition950-restored.json"][0])
   with self.assertRaisesRegex(s.Held,"BUSINESS_FIELDS"):s.carrier_proof(q,7905)
 def test_only_retained_volatile_fields_are_ignored(self):
  q=carrier_input();rest=q["definition950-restored.json"][0]
  rest["createdOn"]="unrelated changed creation date"
  with self.assertRaises(s.Held):s.carrier_proof(q,7905)
 def test_revision_and_latest_release_pointers_are_exact(self):
  for mut in (lambda x:x.update(revision=42),lambda x:x["lastRelease"].update(id=7906),
   lambda x:x["environments"][0]["currentRelease"].update(id=7906)):
   q=carrier_input();mut(q["definition950-restored.json"][0])
   with self.assertRaises(s.Held):s.carrier_proof(q,7905)

class NativeContractTests(unittest.TestCase):
 def test_partial_owned_states_are_restore_context_not_completed(self):
  value=s.native_observation(native_obs(),original_binding(),{"native_fd_keeper_sha256":H})
  self.assertEqual(value["original_context_sha256"],H)
  self.assertEqual(value["original_observation_sha256"],s.sha(s.canonical(native_obs())))
 def test_foreign_unknown_or_missing_existing_source_denied(self):
  for state in ("foreign_or_unsettled","arbitrary-new-state","missing","archive_unexpected_absence"):
   v=native_obs();v["roles"]["backend"]["leaves"]["agentic_control.py"]["state"]=state
   with self.assertRaises(s.Held):s.native_observation(v,original_binding(),{"native_fd_keeper_sha256":H})
 def test_original_helper_absence_is_allowed_without_apply_authority(self):
  v=native_obs();v["roles"]["backend"]["leaves"]["agentic_native_verification.py"]["state"]="missing"
  s.native_observation(v,original_binding(),{"native_fd_keeper_sha256":H})
 def test_foreign_scheduler_or_wrong_keeper_denies_signing_context(self):
  for mutate in (lambda x:x["site_pause"].update(state="foreign_or_unsettled"),
   lambda x:x["keeper"].update(current_verified=False),lambda x:x["keeper"].update(context_sha256="9"*64),
   lambda x:x["keeper"].update(source_sha256="9"*64),lambda x:x["keeper"].update(pid=True)):
   v=native_obs();mutate(v)
   with self.assertRaises(s.Held):s.native_observation(v,original_binding(),{"native_fd_keeper_sha256":H})
 def test_no_observer_mutation_or_boolean_count_substitution(self):
  for key in ("source_or_config_actions","service_actions","journal_writes"):
   v=native_obs();v[key]=False
   with self.assertRaises(s.Held):s.native_observation(v,original_binding(),{"native_fd_keeper_sha256":H})
 def test_native_journal_hash_and_bound_sizes_required(self):
  v=native_obs();v["original_journal"]["records"]["context-index.json"]["bytes"]=121
  with self.assertRaises(s.Held):s.native_observation(v,original_binding(),{"native_fd_keeper_sha256":H})
 def test_terminal_original_and_new_recovery_bindings_exact(self):
  b=binding();s.check_native_terminal(terminal(b),b)
  for key in ("operation_id","manifest_sha256","context_sha256","keeper_ready_sha256",
              "recovery_operation_id","recovery_manifest_sha256"):
   v=terminal(b);v[key]="8"*64
   with self.assertRaises(s.Held):s.check_native_terminal(v,b)
 def test_terminal_every_preservation_flag_required(self):
  b=binding()
  for key in ("all_original_leaves_and_metadata_restored","scheduler_original_bytes_restored",
   "container_ids_and_images_preserved","unrelated_app_bytes_and_metadata_preserved","root_continuous_closure_still_required"):
   v=terminal(b);v[key]=False
   with self.assertRaises(s.Held):s.check_native_terminal(v,b)

class FlockTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/"existing.lock"
  self.path.write_bytes(b"");self.path.chmod(0o600)
  self.p=patch.object(s,"GLOBAL_LOCK",self.path);self.p.start()
  self.meta=patch.object(s,"lock_metadata",lambda info:s.identity(info));self.meta.start()
 def tearDown(self):self.meta.stop();self.p.stop();self.tmp.cleanup()
 def test_actual_kernel_flock_write_witness(self):
  fd=os.open(self.path,os.O_RDWR)
  try:
   fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
   proof=s.fd_lock_proof(os.getpid(),fd,s.identity(self.path.stat()))
   self.assertEqual(proof["owner_pid"],os.getpid());self.assertEqual(proof["flock"],"FLOCK_ADVISORY_WRITE")
  finally:os.close(fd)
 def test_unlocked_descriptor_is_not_ownership_proof(self):
  fd=os.open(self.path,os.O_RDWR)
  try:
   with self.assertRaises(s.Held):s.fd_lock_proof(os.getpid(),fd,s.identity(self.path.stat()))
  finally:os.close(fd)
 def test_same_existing_inode_acquired_without_creation(self):
  before=s.identity(self.path.stat());fd,proof=s.acquire_same_lock(None,{"lock":{"file":before}})
  try:self.assertEqual(proof["file"],before)
  finally:os.close(fd)
 def test_missing_or_replaced_lock_is_not_recreated(self):
  before=s.identity(self.path.stat());self.path.unlink()
  with self.assertRaises(FileNotFoundError):s.acquire_same_lock(None,{"lock":{"file":before}})
  self.assertFalse(self.path.exists())
  self.path.write_bytes(b"replacement");self.path.chmod(0o600)
  with self.assertRaises(s.Held):s.acquire_same_lock(None,{"lock":{"file":before}})
 def test_contended_existing_flock_is_preserved(self):
  fd=os.open(self.path,os.O_RDWR);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);before=s.identity(self.path.stat())
  try:
   with self.assertRaises(BlockingIOError):s.acquire_same_lock(None,{"lock":{"file":before}})
   self.assertEqual(s.fd_lock_proof(os.getpid(),fd,before)["owner_pid"],os.getpid())
  finally:os.close(fd)
 def test_named_replacement_after_fd_read_denied(self):
  fd=os.open(self.path,os.O_RDWR);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
  before=s.identity(self.path.stat());real=s.proc_bytes
  def changed(path,limit=8192):
   value=real(path,limit);self.path.rename(self.path.with_name("old"));self.path.write_bytes(b"");self.path.chmod(0o600);return value
  try:
   with patch.object(s,"proc_bytes",side_effect=changed):
    with self.assertRaisesRegex(s.Held,"NAMED_ENTRY"):s.fd_lock_proof(os.getpid(),fd,before)
  finally:os.close(fd)

class PublicationTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.stage=Path(self.tmp.name);self.p=patch.object(s,"STAGE",self.stage);self.p.start()
  self.a=FakeAPI();self.old=types.SimpleNamespace(KEY=Path("/not-read-live"))
  self.b=binding();self.context={"binding":self.b,"original_journal_raw":b"fixed fixture"}
  self.j={"phase":"prepared","manifest_sha256":H};self.m={"native":{"operation_id":NEW}}
  self.clock=patch.object(s.time,"time",return_value=NOW);self.clock.start()
  self.obs=patch.object(s,"continuity",return_value=observation(self.b));self.obs.start()
 def tearDown(self):self.obs.stop();self.clock.stop();self.p.stop();self.tmp.cleanup()
 def publish(self):
  return s.publish_once(self.old,self.a,h,r,{},self.m,{}, {},self.j,self.context,9,{}, {})
 def test_real_receiver_accepts_new_restore_only_signature_after_publish_last(self):
  self.publish()
  encoded=(self.stage/"recovery-admission.txt").read_text().strip()
  accepted=r.verify(encoded,KEY,self.b,now=NOW)
  self.assertEqual(accepted["action"],"restore_original_or_reconcile")
  self.assertFalse(accepted["original_apply_authority"]);self.assertFalse(accepted["gate_open_authority"])
  writes=[x[1]for x in self.a.events if x[0]=="save"]
  self.assertLess(writes.index("recovery-admission.signed.json"),writes.index("RECOVERY_HELD.json"))
  self.assertLess(writes.index("RECOVERY_HELD.json"),writes.index("PUBLICATION_INTENT.json"))
  self.assertLess(writes.index("PUBLICATION_INTENT.json"),writes.index("recovery-admission.txt"))
  self.assertEqual(self.j["phase"],"published")
 def test_both_continuity_observations_required(self):
  with patch.object(s,"continuity",side_effect=[observation(self.b),s.Held("SECOND_CHANGED")]) as observe:
   with self.assertRaises(s.Held):self.publish()
   self.assertEqual(observe.call_count,2)
  self.assertFalse((self.stage/"recovery-admission.txt").exists())
  self.assertEqual(self.j["phase"],"prepared")
 def test_lost_publication_ack_never_resigns(self):
  with patch.object(r,"make_receipt",wraps=r.make_receipt) as sign:
   self.a.failure="recovery-admission.txt"
   with self.assertRaises(OSError):self.publish()
   self.assertEqual(self.j["phase"],"publication_may_have_started")
   self.a.failure=None
   with self.assertRaisesRegex(s.Held,"SIGNATURE_ONLY"):self.publish()
   self.assertEqual(sign.call_count,1)
  self.assertFalse((self.stage/"recovery-admission.txt").exists())
 def test_signing_entry_uncertainty_cannot_retry(self):
  with patch.object(r,"make_receipt",side_effect=s.Held("SIGN_OUTCOME_UNKNOWN")) as sign:
   with self.assertRaises(s.Held):self.publish()
   with self.assertRaisesRegex(s.Held,"SIGNATURE_ONLY"):self.publish()
   self.assertEqual(sign.call_count,1)
  self.assertEqual(self.j["phase"],"signing_may_have_started")
 def test_interruption_after_signed_before_held_stays_unpublished(self):
  self.a.failure="RECOVERY_HELD.json"
  with self.assertRaises(OSError):self.publish()
  self.assertTrue((self.stage/"recovery-admission.signed.json").exists())
  self.assertFalse((self.stage/"recovery-admission.txt").exists())
  with self.assertRaises(s.Held):self.publish()
 def test_old_apply_receipt_cannot_be_used_as_recovery(self):
  self.publish();encoded=(self.stage/"recovery-admission.txt").read_text().strip()
  with self.assertRaises(h.Held):h.verify(encoded,KEY,self.b["original_binding"],now=NOW)
 def test_changed_scope_or_new_work_prevents_signature(self):
  bad=observation(self.b);bad["held_observation"]["counts"]["governance_running"]=1
  with patch.object(s,"continuity",return_value=bad):
   with self.assertRaises(h.Held):self.publish()
  self.assertFalse(self.a.events)
 def test_current_scope_or_window_cannot_be_renewed(self):
  self.publish();encoded=(self.stage/"recovery-admission.txt").read_text().strip()
  with self.assertRaises(r.Held):r.verify(encoded,KEY,self.b,now=NOW+120)
  with self.assertRaises(s.Held):self.publish()
 def test_readonly_partial_observe_needs_no_held_or_flock(self):
  old=types.SimpleNamespace(root_read=lambda p,*args:Path(p).read_bytes())
  result=s.observe(old,self.a,h,r,{},b"manifest",{})
  self.assertEqual(result["state"],"RECOVERY_STAGE_WITHOUT_JOURNAL")
  self.assertFalse(result["writes_by_observer"]);self.assertEqual(self.a.events,[])
 def test_completion_writes_only_new_stage_and_proposes_original_finalize(self):
  b=self.b;proof={"request_sha256":H,"native_terminal_sha256":"7"*64,
   "recovery_carrier":s.carrier_proof(carrier_input(release=7906,revision=41),7906),
   "native_result":terminal(b),"original_barriers_must_remain_held":True}
  with patch.object(s,"completion_proof",return_value=proof),patch.object(s,"final_observation",return_value=observation(b)["held_observation"]):
   result=s.record_completion(self.old,self.a,h,{}, {}, {},{"original_held_sha256":H},self.j,b)
  proposal=json.loads((self.stage/"original-holder-reconciliation.proposed.json").read_bytes())
  self.assertEqual(proposal["outcome"],"ROLLED_BACK_VERIFIED")
  self.assertTrue(result["own_flock_may_release"]);self.assertFalse(result["original_barriers_released"])
  self.assertTrue(json.loads((self.stage/"COMPLETED.json").read_bytes())["original_holder_finalize_still_required"])

class SourceInvariantTests(unittest.TestCase):
 def test_receiver_copy_exact(self):
  self.assertEqual(hashlib.sha256((HERE/"recovery_receipt.py").read_bytes()).hexdigest(),s.RECEIVER_SHA)
 def test_no_barrier_mutation_or_provider_or_original_signature_call(self):
  import ast
  tree=ast.parse((HERE/"hold_native_recovery.py").read_text())
  forbidden={"gate_action","command_once","seal","unseal","acquire","release","restart","kill","system","Popen"}
  calls={x.func.attr for x in ast.walk(tree) if isinstance(x,ast.Call) and isinstance(x.func,ast.Attribute)}
  self.assertFalse(calls&forbidden)
  self.assertNotIn("sudo",(HERE/"hold_native_recovery.py").read_text())
 def test_original_source_and_receiver_pins_are_not_templates(self):
  self.assertEqual(s.OLD_HOLDER_SHA,hashlib.sha256((ROOT/"deployment/native-overlay-held-drain-20261122/hold_native_overlay.py").read_bytes()).hexdigest())
  self.assertTrue(s.SHA.fullmatch(s.RECEIVER_SHA))
 def test_capture_requires_live_exact_original_holder(self):
  expected={"pid":501,"start_ticks":5001,"uid":0,"cmdline_sha256":H}
  a=types.SimpleNamespace(process=lambda pid:{**expected,"start_ticks":999})
  old=types.SimpleNamespace(operator=lambda *args:None)
  with patch.object(s,"frozen_original",return_value=({"original_holder":expected},{},b"j",{},b"h")):
   with self.assertRaisesRegex(s.Held,"NOT_LIVE"):s.capture_original_lock(old,a,h,{})
 def test_initial_recovery_denies_live_original_before_any_flock(self):
  a=types.SimpleNamespace(original_absent=lambda value:False)
  with patch.object(s,"frozen_original",return_value=({"original_holder":{}},{},b"j",{},b"h")),patch.object(s,"witness") as witness:
   with self.assertRaisesRegex(s.Held,"NOT_POSITIVELY_CEASED"):s.initial_context(None,a,h,r,{}, {}, {})
   witness.assert_not_called()

if __name__=="__main__":unittest.main(verbosity=2)
