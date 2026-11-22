"""No-action validation of the actual frozen holder's input reader and source guard."""
from pathlib import Path
import copy,hashlib,importlib.util,json,os,socket,subprocess,sys,types,unittest
from unittest.mock import patch
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
PACKAGE=ROOT/"deployment/native-overlay-held-drain-20261122"
spec=importlib.util.spec_from_file_location("fixed_holder_schema_fixture",PACKAGE/"hold_native_overlay.py")
holder=importlib.util.module_from_spec(spec);sys.modules[spec.name]=holder;spec.loader.exec_module(holder)
DRAFT=json.loads((HERE/"hold-manifest.draft.json").read_bytes())
CONTENT={str(holder.CODE/name):(PACKAGE/name).read_bytes()for name in holder.DEPENDENCIES}
CONTENT[str(holder.CODE/"hold_native_overlay.py")]=(PACKAGE/"hold_native_overlay.py").read_bytes()
for name in ("native-preflight.json","native-restore.json","native-manifest.json"):
 CONTENT[str(holder.CODE/name)]=(HERE/"inputs"/name).read_bytes()
def load(value,change=None):
 payload=dict(CONTENT);raw=(json.dumps(value,sort_keys=True,indent=2)+"\n").encode()
 payload[str(holder.CODE/"hold-manifest.json")]=raw
 if change:change(payload)
 def fixed_read(path,*args,**kwargs):
  key=str(path)
  if key not in payload:raise AssertionError("No unlisted protected read allowed")
  return payload[key]
 with patch.object(holder,"root_read",side_effect=fixed_read):
  return holder.load_inputs(hashlib.sha256(raw).hexdigest())
def authorized_fixture():
 value=copy.deepcopy(DRAFT);value["live_hold_authorized"]=True
 return value
class HolderSchema(unittest.TestCase):
 def test_draft_stays_non_executable(self):
  self.assertIs(DRAFT["live_hold_authorized"],False)
  with self.assertRaisesRegex(RuntimeError,"EXACT_HOLD_SCOPE"):load(DRAFT)
 def test_actual_frozen_reader_accepts_exact_inmemory_bound_tuple_without_actions(self):
  a,receipt,m,raw=load(authorized_fixture())
  self.assertEqual(m["native"],DRAFT["native"]);self.assertEqual(a.MANIFEST_SHA,hashlib.sha256(raw).hexdigest())
  self.assertEqual(a.PACKAGE,holder.CODE);self.assertEqual(a.STAGE,holder.STAGE)
  self.assertEqual(set(m["preserved_processes"]),holder.PRESERVED)
 def test_native_scope_crosslinks_match_actual_frozen_payloads(self):
  native=json.loads(CONTENT[str(holder.CODE/"native-manifest.json")])
  pre=json.loads(CONTENT[str(holder.CODE/"native-preflight.json")])
  restore=json.loads(CONTENT[str(holder.CODE/"native-restore.json")])
  self.assertEqual(native["operation_id"],DRAFT["native"]["operation_id"])
  self.assertEqual(native["controller_sha256"],DRAFT["native"]["controller_sha256"])
  self.assertEqual(native["expected_native_preflight_sha256"],DRAFT["native"]["native_preflight_sha256"])
  self.assertEqual(pre["actual_restore_result_sha256"],DRAFT["native"]["restore_receipt_sha256"])
  self.assertEqual(pre["native_restore_file_expected_sha256"],DRAFT["native"]["restore_receipt_sha256"])
  self.assertTrue(restore["ok"]);self.assertTrue(restore["isolated_database_stopped"])
  self.assertEqual(restore["production_database_connections"],0)
 def test_wrong_holder_bytes_deny(self):
  with self.assertRaisesRegex(RuntimeError,"HOLDER_SOURCE_CHANGED"):
   load(authorized_fixture(),lambda p:p.update({str(holder.CODE/"hold_native_overlay.py"):b"altered"}))
 def test_wrong_dependency_bytes_deny(self):
  name="relay_bootstrap.py"
  with self.assertRaisesRegex(RuntimeError,"DEPENDENCY_CHANGED"):
   load(authorized_fixture(),lambda p:p.update({str(holder.CODE/name):b"altered"}))
 def test_changed_native_receipt_bytes_deny(self):
  for name in ("native-manifest.json","native-preflight.json","native-restore.json"):
   with self.subTest(name=name),self.assertRaisesRegex(RuntimeError,"NATIVE_EVIDENCE_CHANGED"):
    load(authorized_fixture(),lambda p:p.update({str(holder.CODE/name):p[str(holder.CODE/name)]+b" "}))
 def test_missing_native_pin_or_invalid_type_denied(self):
  for value in (None,True,"x"*64):
   m=authorized_fixture();m["native"]["restore_receipt_sha256"]=value
   with self.assertRaisesRegex(RuntimeError,"EXACT_NATIVE_PIN_REQUIRED"):load(m)
 def test_exact_operator_and_gate_scope_required(self):
  for mutate in (lambda m:m["operator"].update(actor="different-actor"),
                 lambda m:m["gate_before"].update(state="draining")):
   m=authorized_fixture();mutate(m)
   with self.assertRaises(RuntimeError):load(m)
 def test_preserved_process_role_cannot_be_dropped(self):
  m=authorized_fixture();m["preserved_processes"].pop("qa")
  with self.assertRaisesRegex(RuntimeError,"INITIAL_HOLD_BINDING"):load(m)
 def test_operator_dependency_identity_cannot_be_replaced(self):
  m=authorized_fixture();m["operator_source"]["relay_bootstrap.py"]="f"*64
  with self.assertRaisesRegex(RuntimeError,"EXACT_OPERATOR_DEPENDENCIES"):load(m)
 def source_guard(self,m):
  base=Path("/ordinary-fixture-base")
  a=types.SimpleNamespace(BASE=base,filehash=lambda path:DRAFT["source_files"].get(str(path)))
  def resolve(path,*args,**kwargs):
   return holder.CORE if path==base/"current/runtime/open_webui"else path
  with patch.object(Path,"resolve",resolve):holder.source_check(a,m)
 def test_full39source_guard_accepts_exact_whitelist(self):
  self.assertEqual(len(DRAFT["source_files"]),39);self.source_guard(DRAFT)
 def test_required_runtime_source_cannot_be_removed(self):
  m=copy.deepcopy(DRAFT);m["source_files"].pop(str(holder.CORE/"utils/agentic_native_verification.py"))
  with self.assertRaisesRegex(RuntimeError,"SOURCE_CLOSURE_INCOMPLETE"):self.source_guard(m)
 def test_extra_arbitrary_path_cannot_be_added(self):
  m=copy.deepcopy(DRAFT);m["source_files"]["/etc/passwd"]="a"*64
  with self.assertRaisesRegex(RuntimeError,"SOURCE_PATH_SCOPE"):self.source_guard(m)
 def test_changed_runtime_source_pin_denied(self):
  m=copy.deepcopy(DRAFT);m["source_files"][str(holder.CORE/"utils/agentic_native_verification.py")]="f"*64
  with self.assertRaisesRegex(RuntimeError,"SOURCE_PREIMAGE_CHANGED"):self.source_guard(m)
if __name__=="__main__":
 with patch.object(subprocess,"run",side_effect=AssertionError("No commands in manifest fixture")),patch.object(subprocess,"Popen",side_effect=AssertionError("No processes in manifest fixture")),patch.object(socket,"socket",side_effect=AssertionError("No network in manifest fixture")),patch.object(os,"open",side_effect=AssertionError("No protected file access in manifest fixture")):
  unittest.main(verbosity=2)
