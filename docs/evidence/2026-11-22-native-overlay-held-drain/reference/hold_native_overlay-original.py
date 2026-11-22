"""Continuous Core/relay hold for one exact native leaf repair.

This operator does not change application source, restart services, invoke Azure,
or issue business authority. It retains the release flock and shared browser
keeper while a separately reviewed operator carries the signed native input.
A failure leaves the original close/seal/keeper intact. Reopening requires an
explicit root-owned reconciliation record for the original native operation.
"""
from __future__ import annotations
import argparse
import base64
import datetime
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import socket
import stat
import sys
import time
import types

CODE = Path("/var/lib/abit-ai-native-overlay-held-drain-source-20261122")
STAGE = Path("/var/lib/abit-ai-native-overlay-held-drain-20261122")
LEASE = Path("/srv/abit-dev-linux/bigrro-small-medium-business-erp/governance/operator-session.json")
OWNER = "chat-authorized-root-orchestration-recovery-20261122T1007"
KEY = Path("/srv/abit-dev-linux/abit-ai/config/erp-assignment/control-token")
CORE = Path("/srv/abit-dev-linux/abit-ai/releases/20261031-live-0.11.4/runtime/open_webui")
RELAY = Path("/srv/abit-dev-linux/relay-runtime/service")
MAX_HOLD_SECONDS = 900
DEPENDENCIES = {
    "reviewed/release_relay.py": "4cbf14905acd67d0acabded257b9090443aa023ca1038d424c0baea83c764481",
    "relay_bootstrap.py": "b6292e09d4313d6fecca0e63440b5d3f557a0b976a795bbdef93fb42024bd282",
    "relay_runtime_guard.py": "cc3fd5538ed797f89770751e80bbc5b53b95c67b1fa5374db0cbb76218c2afde",
    "browser_lock_keeper.cjs": "f718f8b1a64846a876ff73bfa4e908285c12137160e7ea08a9269efaec4b7879",
    "held_admission_receipt.py": "7c4262dc3d1e9299a067023d0fb623aea33ae3c71ed18a3385b4e14176403237",
}
CORE_REQUIRED = {
    "routers/agentic_governance.py", "utils/agentic_contracts/validator.py",
    "utils/agentic_contracts/schemas/governance_v1.schema.json",
    "utils/agentic_native_contract.py", "utils/agentic_native_verification.py",
    "utils/agentic_native_execution.py", "utils/agentic_maintenance.py",
    "utils/agentic_transport_retry.py", "utils/agentic_relay_scope.py",
}
RELAY_REQUIRED = {"aibit_relay.cjs", "erp_task_relay.cjs", "original_dot_target_probe.cjs",
                  "shared_chatgpt_browser_lock.cjs"}
PRESERVED = {"core", "chrome", "watchdog", "egress", "xvfb", "tunnel", "qa"}

def need(ok, code):
    if not ok:
        raise RuntimeError(code)

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()

def trusted_directory(path):
    need(path.is_absolute() and path.resolve() == path, "ROOT_PATH_ALIAS")
    for parent in (path, *path.parents):
        value = parent.lstat()
        need(stat.S_ISDIR(value.st_mode) and value.st_uid == 0 and
             not value.st_mode & 0o022, "ROOT_ANCESTRY")

def root_read(path, mode=0o400, limit=8*1024*1024):
    trusted_directory(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_uid == before.st_gid == 0
             and before.st_nlink == 1 and stat.S_IMODE(before.st_mode) == mode
             and 0 <= before.st_size <= limit, "ROOT_INPUT_METADATA")
        data = bytearray()
        while len(data) <= limit:
            part = os.read(fd, min(65536, limit + 1 - len(data)))
            if not part:
                break
            data.extend(part)
        attrs = ("st_dev","st_ino","st_mode","st_uid","st_gid","st_nlink","st_size","st_mtime_ns","st_ctime_ns")
        after, current = os.fstat(fd), path.lstat()
        need(len(data) == before.st_size and all(getattr(before,k) == getattr(after,k) ==
             getattr(current,k) for k in attrs), "ROOT_INPUT_CHANGED")
        return bytes(data)
    finally:
        os.close(fd)

def load_module(relative, name):
    raw = root_read(CODE/relative)
    need(digest(raw) == DEPENDENCIES[relative], "REVIEWED_OPERATOR_SOURCE_CHANGED")
    value = types.ModuleType(name)
    value.__file__ = str(CODE/relative)
    sys.modules[name] = value
    exec(compile(raw, value.__file__, "exec"), value.__dict__)
    return value

def source_check(a, m, _allowed=None):
    need((a.BASE/"current/runtime/open_webui").resolve() == CORE, "CORE_RUNTIME_CHANGED")
    expected = m["source_files"]
    need(type(expected) is dict and 13 <= len(expected) <= 96, "SOURCE_SCOPE")
    need({str(CORE/x) for x in CORE_REQUIRED} <= set(expected) and
         {str(RELAY/x) for x in RELAY_REQUIRED} <= set(expected), "SOURCE_CLOSURE_INCOMPLETE")
    for text, pin in expected.items():
        path = Path(text)
        permitted = (path.parent == RELAY or path.parent == RELAY/"agentic-gateway" or
                     text in {str(CORE/x) for x in CORE_REQUIRED} or
                     text in {"/srv/abit-dev-linux/abit-ai/deployment/relay-watchdog.sh",
                              "/etc/systemd/system/aibit-linux-relay-watchdog.service",
                              "/etc/systemd/system/abit-ai.service",
                              "/etc/systemd/system/abit-ai.service.d/40-agentic-gateway.conf",
                              "/etc/systemd/system/abit-ai.service.d/erp-assignment.conf",
                              "/etc/systemd/system/abit-ai-erp-review-qa.service"})
        need(permitted and path.resolve() == path and
             type(pin) is str and re.fullmatch("[a-f0-9]{64}", pin), "SOURCE_PATH_SCOPE")
        need(a.filehash(path) == pin, "SOURCE_PREIMAGE_CHANGED")

def load_inputs(manifest_sha):
    need(re.fullmatch("[a-f0-9]{64}", manifest_sha) is not None, "MANIFEST_PIN_REQUIRED")
    raw = root_read(CODE/"hold-manifest.json")
    need(digest(raw) == manifest_sha, "HOLD_MANIFEST_CHANGED")
    receipt = load_module("held_admission_receipt.py", "native_hold_receipt")
    m = json.loads(raw, object_pairs_hook=receipt.unique)
    need(m.get("schema_version") == 1 and
         m.get("purpose") == "NATIVE_LEAF_CONTINUOUS_ROOT_HOLD_V1" and
         m.get("operator") == {"owner":OWNER,"actor":OWNER+":native-leaf-hold"} and
         m.get("site") == "erp.abit.ai" and m.get("definition_id") == 950 and
         m.get("live_hold_authorized") is True, "EXACT_HOLD_SCOPE")
    need(m["holder_sha256"] == digest(root_read(CODE/"hold_native_overlay.py")),
         "HOLDER_SOURCE_CHANGED")
    need(set(m["preserved_processes"]) == PRESERVED and
         m["gate_before"]["state"] == "open", "INITIAL_HOLD_BINDING")
    need(m["operator_source"] == {k:v for k,v in DEPENDENCIES.items()
         if k in ("relay_bootstrap.py","relay_runtime_guard.py","browser_lock_keeper.cjs")},
         "EXACT_OPERATOR_DEPENDENCIES")
    for name,pin in DEPENDENCIES.items():
        need(digest(root_read(CODE/name)) == pin, "DEPENDENCY_CHANGED")
    for key in ("operation_id","controller_sha256","manifest_sha256",
                "native_preflight_sha256","restore_receipt_sha256"):
        need(type(m["native"][key]) is str and re.fullmatch("[a-f0-9]{64}",m["native"][key]),
             "EXACT_NATIVE_PIN_REQUIRED")
    for name,pin in (("native-preflight.json",m["native"]["native_preflight_sha256"]),
                     ("native-restore.json",m["native"]["restore_receipt_sha256"]),
                     ("native-manifest.json",m["native"]["manifest_sha256"])):
        need(digest(root_read(CODE/name)) == pin, "NATIVE_EVIDENCE_CHANGED")
    a = load_module("reviewed/release_relay.py", "native_hold_reviewed_api")
    a.PACKAGE, a.STAGE, a.MANIFEST_SHA = CODE, STAGE, manifest_sha
    a.source = lambda manifest, allowed=None: source_check(a,manifest,allowed)
    return a, receipt, m, raw

def operator(a, m, minimum_seconds=0):
    need(os.getresuid() == (0,0,0) and os.getresgid() == (0,0,0) and
         os.environ.get("SUDO_USER") == "jimran@abitcons.com" and
         socket.gethostname() == "abit-dev-linux", "EXISTING_OPERATOR_REQUIRED")
    a.owner(m)
    lease = json.loads(a.raw(LEASE))
    expiry = datetime.datetime.fromisoformat(lease["lease_expires_at"].replace("Z","+00:00")).timestamp()
    need(expiry >= time.time()+minimum_seconds, "HOLDER_LEASE_RESERVE")
    return expiry

def consistent(a, m):
    operator(a,m)
    a.preserve(m)
    need(a.process(m["relay_before"]["pid"]) == m["relay_before"], "RELAY_GENERATION_CHANGED")
    a.units(m)
    source_check(a,m)
    history = a.ledger_snapshot(m)
    a.private_preservation(m)
    a.browser_snapshot(m)
    a.preserve(m)
    return history

def runtime_status(a,m):
    expected = m["relay_before"]
    need(a.process(expected["pid"]) == expected, "RELAY_CHANGED_BEFORE_STATUS")
    connection = http.client.HTTPConnection("127.0.0.1",8810,timeout=5)
    try:
        connection.request("GET","/runtime-status",headers={"Connection":"close"})
        response = connection.getresponse()
        raw = response.read(4097)
        need(response.status == 200 and len(raw)<=4096, "BOUNDED_RUNTIME_STATUS")
        value = json.loads(raw)
    finally:
        connection.close()
    need(value.get("ok") is True and value.get("protocol") == "v1_graceful_relay_drain"
         and value.get("pid") == expected["pid"] and value.get("draining") is False
         and value.get("server_closed") is False, "RELAY_STATUS_IDENTITY")
    keys = ("active_http_handlers","active_relay_turns","erp_active_turns","erp_inspections")
    need(all(type(value.get(k)) is int and value[k] == 0 for k in keys), "RELAY_ACTIVE_WORK")
    need(a.process(expected["pid"]) == expected, "RELAY_CHANGED_DURING_STATUS")
    return {k:value[k] for k in keys}

def work_counts(a,m):
    core = m["preserved_processes"]["core"]
    result = {}
    specifications = (
      ("current_core_active_or_unresolved","maintenance-admission/gate.sqlite",
       "SELECT COUNT(*) AS n FROM lease WHERE state IN ('active','unresolved') AND pid=? AND process_start=?",
       (core["pid"],str(core["start_ticks"]))),
      ("governance_running","agentic-governance.sqlite",
       "SELECT COUNT(*) AS n FROM governance_invocation WHERE state IN ('running','cancel_requested')",()),
      ("codex_running","codex-workspace-runs.sqlite",
       "SELECT COUNT(*) AS n FROM runs WHERE state IN ('submitting','running')",()),
    )
    for name,path,sql,args in specifications:
        rows = a.db_rows(m,path,[(sql,args)])[0]
        need(len(rows)==1 and type(rows[0]["n"]) is int and rows[0]["n"] == 0, "CURRENT_CORE_WORK_ACTIVE")
        result[name] = rows[0]["n"]
    return result

def gate_action(a,m,r,c,action):
    old = c["intents"].get(action)
    if old and old.get("command_phase") not in ("intent_saved","precondition_failed"):
        return a.control_receipt(m,old)
    before = c["gate_before"] if action == "close" else a.control_receipt(m,c["intents"]["close"])
    fields = {"action":action,"expected_epoch":before["epoch"],
              "command_id":OWNER+":native-leaf-hold:"+m["native"]["operation_id"][:20]+":"+action}
    def check():
        consistent(a,m)
        need(a.gate(m)==before,"MAINTENANCE_EPOCH_CHANGED")
        if action == "open":
            need(c.get("native_reconciled") is True and
                 c["intents"].get("nft_unseal",{}).get("fulfilled_utc"),"NATIVE_RECONCILIATION_REQUIRED")
    argv = ["/usr/bin/sudo","-n","-u","mcpdev","--","/usr/bin/python3","-I","-S","-B",
            str(CORE/"utils/agentic_maintenance.py"),action,"--data-dir",str(a.BASE/"data"),
            "--expected-epoch",str(before["epoch"]),"--command-id",fields["command_id"],
            "--actor",m["operator"]["actor"]]
    intent = a.command_once(r,c,action,fields,argv,check)
    proof = a.control_receipt(m,intent)
    a.finished(r,c,action,proof)
    return proof

def observations(a,receipt,m,r,c,b,g):
    history = consistent(a,m)
    close = a.control_receipt(m,c["intents"]["close"])
    firewall = b.snapshot(True)
    keeper = g.keeper_check()
    counts = work_counts(a,m)
    counts.update(runtime_status(a,m))
    network = b.zero_connections(m["relay_before"])
    counts.update(network)
    a.preserve(m)
    need(a.control_receipt(m,c["intents"]["close"])==close and
         b.snapshot(True)==firewall and g.keeper_check()==keeper, "HOLD_CHANGED_DURING_CAPTURE")
    need(c["quiet_proof"]["quiet_seconds"] >= c["quiet_seconds_required"],"ACTUAL_QUIET_INTERVAL_REQUIRED")
    core = m["preserved_processes"]["core"]
    core_fields = {"pid":core["pid"],"start_ticks":core["start_ticks"],"epoch":close["epoch"],
                   "close_command_sha256":digest(close["command_id"].encode()),
                   "actor_sha256":digest(close["actor"].encode()),
                   "control_receipt_sha256":digest(canonical(close)),
                   "maintenance_source_sha256":m["source_files"][str(CORE/"utils/agentic_maintenance.py")],
                   "history_sha256":digest(canonical(history))}
    relay_fields = {"pid":m["relay_before"]["pid"],"start_ticks":m["relay_before"]["start_ticks"],
                    "source_sha256":digest(canonical({k:v for k,v in m["source_files"].items()
                                      if k.startswith(str(RELAY)+"/")})),
                    "table_name":receipt.TABLE,"table_handle":firewall["table_handle"],
                    "objects_sha256":firewall["objects_sha256"],
                    "preserved_rules_sha256":m["nft_before_sha256"],
                    "keeper_proof_sha256":digest(canonical(keeper)),
                    "quiet_seconds_required":c["quiet_seconds_required"]}
    return {"captured_at":receipt.utc(time.time()),"holder_sha256":m["holder_sha256"],
            "core":core_fields,"relay":relay_fields,"counts":counts,"core_state":close["state"],
            "relay_state":{"ingress_sealed":True,"keeper_held":True,"draining":False,
                           "server_closed":False,"quiet_seconds":c["quiet_seconds_required"]},
            "native_preflight_sha256":m["native"]["native_preflight_sha256"]}

def sign_once(a,receipt,m,r,c,b,g):
    need(not os.path.lexists(STAGE/"HELD.json") and
         not os.path.lexists(STAGE/"native-admission.txt"),"ORIGINAL_HOLD_RECEIPT_ONLY")
    first = observations(a,receipt,m,r,c,b,g)
    final = observations(a,receipt,m,r,c,b,g)
    expiry = operator(a,m,receipt.OPERATION_SECONDS+receipt.HOLDER_RESERVE_SECONDS)
    binding = {**m["native"],"holder_sha256":m["holder_sha256"],
               "core":final["core"],"relay":final["relay"],"holder_lease_expires_at":receipt.utc(expiry)}
    need(set(binding)==receipt.BINDING_KEYS,"NATIVE_BINDING_FIELDS")
    key = a.raw(KEY,4096)
    encoded = receipt.make_receipt(binding,first,final,key,now=time.time())
    key = None
    verified = receipt.verify(encoded,a.raw(KEY,4096),binding,now=time.time())
    signed = base64.b64decode(encoded,validate=True)
    a.save(STAGE/"native-admission.txt",encoded.encode()+b"\n")
    a.save(STAGE/"native-admission.signed.json",signed)
    held = {"schema_version":1,"purpose":"ACTUAL_CONTINUOUS_NATIVE_HOLD",
            "manifest_sha256":a.MANIFEST_SHA,"holder":a.process(os.getpid()),
            "binding":binding,"first":first,"final":final,
            "receipt_sha256":digest(signed),"native_action_by_holder":False,
            "authority_or_business_approval_issued":False}
    a.savejson(STAGE/"HELD.json",held)
    r["state"]="held_for_one_native_operation"
    r["held_sha256"]=a.filehash(STAGE/"HELD.json")
    a.update(r)
    return held

def reconciliation(a,m,held):
    path = STAGE/"native-reconciliation.json"
    body = root_read(path,0o600,65536)
    value = json.loads(body)
    exact = {"schema_version","purpose","operation_id","native_manifest_sha256","held_sha256",
             "outcome","native_result_sha256","definition_restore_sha256",
             "all_owned_mutations_terminal","source_and_configuration_verified",
             "backend_health_verified","worker_health_verified","definition_950_restored",
             "unknown_native_action_remaining","retry_performed","reopen_original_intake"}
    need(type(value) is dict and set(value)==exact and type(value["schema_version"]) is int
         and value["schema_version"]==1 and value["purpose"]=="ROOT_REVIEWED_ORIGINAL_NATIVE_OUTCOME",
         "EXACT_NATIVE_RECONCILIATION_REQUIRED")
    need(value["operation_id"]==m["native"]["operation_id"] and
         value["native_manifest_sha256"]==m["native"]["manifest_sha256"] and
         value["held_sha256"]==a.filehash(STAGE/"HELD.json") and
         value["outcome"] in ("PASSED","REJECTED_BEFORE_MUTATION","ROLLED_BACK_VERIFIED"),
         "ORIGINAL_NATIVE_RECONCILIATION_BINDING")
    for key in ("all_owned_mutations_terminal","source_and_configuration_verified",
                "backend_health_verified","worker_health_verified","definition_950_restored","reopen_original_intake"):
        need(value[key] is True,"NATIVE_RECONCILIATION_INCOMPLETE")
    need(value["unknown_native_action_remaining"] is False and value["retry_performed"] is False,
         "UNKNOWN_NATIVE_ACTION_OR_RETRY")
    for name,key in (("native-result.json","native_result_sha256"),
                     ("definition-restored.json","definition_restore_sha256")):
        need(type(value[key]) is str and re.fullmatch("[a-f0-9]{64}",value[key]) and
             digest(root_read(STAGE/name,0o600))==value[key],"RECONCILIATION_EVIDENCE_CHANGED")
    return {"request_sha256":digest(body),"outcome":value["outcome"],
            "native_result_sha256":value["native_result_sha256"],
            "definition_restore_sha256":value["definition_restore_sha256"]}

def finish(a,receipt,m,r,c,b,g,held):
    proof = reconciliation(a,m,held)
    opened = c["intents"].get("open")
    if opened and opened.get("command_phase") not in ("intent_saved","precondition_failed"):
        # Intake may already have reopened after an unacknowledged command.
        # Observe that original audit receipt; ordinary new work may now evolve.
        source_check(a,m)
        a.preserve(m)
        b.snapshot(False)
        a.control_receipt(m,opened)
    else:
        release_requested = os.path.lexists(g.keeper_path("release"))
        if release_requested:
            need(c.get("native_reconciled") is True and
                 c.get("native_reconciliation")==proof,"ORIGINAL_RELEASE_BINDING_CHANGED")
            consistent(a,m)
            a.control_receipt(m,c["intents"]["close"])
            c["lock_release"]=g.release(False)
        else:
            observations(a,receipt,m,r,c,b,g)
            c["native_reconciled"]=True
            c["native_reconciliation"]=proof
            a.update(r)
            c["lock_release"]=g.release(True)
        c["sealed_preservation_verified"]=True
        a.update(r)
        # Both helpers reconcile the original one-use intent after a lost ACK.
        # Neither recreates a removed seal or repeats an issued gate command.
        b.unseal(True)
        gate_action(a,m,r,c,"open")
    source_check(a,m)
    a.preserve(m)
    r["state"]="native_operation_reconciled_original_intake_reopened"
    r.setdefault("completed_at",a.now())
    a.update(r)
    a.save_or_verify(STAGE/"COMPLETED.json",canonical({"schema_version":1,
               "manifest_sha256":a.MANIFEST_SHA,"native_reconciliation":proof,
               "gate":a.gate(m),"native_action_by_holder":False,
               "original_attempts_replayed":False,"core_or_relay_restarted":False})+b"\n")
    return r["state"]

def execute(a,receipt,m,raw):
    operator(a,m,MAX_HOLD_SECONDS+120)
    need(not os.path.lexists(STAGE),"ONE_USE_HOLDER_STAGE_EXISTS")
    consistent(a,m)
    need(a.gate(m)==m["gate_before"],"INITIAL_GATE_CHANGED")
    STAGE.mkdir(mode=0o700)
    a.fsyncdir(STAGE.parent)
    (STAGE/"inputs").mkdir(mode=0o700)
    a.fsyncdir(STAGE)
    a.save(STAGE/"hold-manifest.json",raw)
    c={"mode":"apply","gate_before":m["gate_before"],"node_before":m["relay_before"],
       "no_node_transition":True,"intents":{}}
    r={"schema_version":1,"manifest_sha256":a.MANIFEST_SHA,"state":"prepared",
       "cycles":{"apply":c},"created_at":a.now(),"original_holder":a.process(os.getpid())}
    a.update(r)
    b,g=a.barriers(m,r,c)
    b.snapshot(False)
    gate_action(a,m,r,c,"close")
    b.seal(True)
    g.acquire(True)
    c["quiet_proof"]=b.quiet(m["relay_before"],g.keeper_check,deadline_seconds=115)
    c["quiet_seconds_required"]=math.ceil(m["hermes_transport"]["timeout_ms"]/1000+2)
    remaining=max(0,c["quiet_seconds_required"]-c["quiet_proof"]["quiet_seconds"])
    start=time.monotonic()
    while time.monotonic()-start < remaining:
        consistent(a,m);b.snapshot(True);g.keeper_check()
        need(b.zero_connections(m["relay_before"],deadline_seconds=1)==
             {"usable_ingress":0,"node_network_work":0},"POST_DRAIN_CONNECTION")
        time.sleep(0.1)
    c["quiet_proof"]["quiet_seconds"]+=time.monotonic()-start
    a.update(r)
    held=sign_once(a,receipt,m,r,c,b,g)
    print(json.dumps({"state":"HELD_FOR_ONE_NATIVE_OPERATION","held_path":str(STAGE/"HELD.json"),
                      "held_sha256":a.filehash(STAGE/"HELD.json"),
                      "receipt_path":str(STAGE/"native-admission.txt"),
                      "receipt_sha256":held["receipt_sha256"],
                      "core_epoch":held["binding"]["core"]["epoch"]}),flush=True)
    deadline=time.monotonic()+MAX_HOLD_SECONDS
    while time.monotonic()<deadline:
        observations(a,receipt,m,r,c,b,g)
        if os.path.lexists(STAGE/"native-reconciliation.json"):
            return finish(a,receipt,m,r,c,b,g,held)
        time.sleep(1)
    raise RuntimeError("NATIVE_OUTCOME_NOT_RECONCILED_HOLD_RETAINED")

def inspect_or_finish(a,receipt,m,finalize):
    r=json.loads(root_read(STAGE/"journal.json",0o600))
    need(r.get("manifest_sha256")==a.MANIFEST_SHA,"ORIGINAL_HOLDER_JOURNAL")
    held=json.loads(root_read(STAGE/"HELD.json",0o600))
    need(held["manifest_sha256"]==a.MANIFEST_SHA and
         a.filehash(STAGE/"HELD.json")==r["held_sha256"],"ORIGINAL_HELD_RECEIPT")
    c=r["cycles"]["apply"]
    b,g=a.barriers(m,r,c)
    if r["state"]=="native_operation_reconciled_original_intake_reopened":
        b.snapshot(False)
        a.control_receipt(m,c["intents"]["open"])
        source_check(a,m)
        a.preserve(m)
        if finalize:
            return finish(a,receipt,m,r,c,b,g,held)
        need(os.path.lexists(STAGE/"COMPLETED.json"),"TERMINAL_RECEIPT_UNCONFIRMED")
        return r["state"]
    if not finalize:
        observations(a,receipt,m,r,c,b,g)
        return "ORIGINAL_HOLD_VERIFIED_OBSERVATION_ONLY"
    old=r["original_holder"]
    try:
        same=a.process(old["pid"])==old
    except FileNotFoundError:
        same=False
    need(not same,"ORIGINAL_HOLDER_STILL_OWNS_LIFETIME")
    return finish(a,receipt,m,r,c,b,g,held)

def main():
    p=argparse.ArgumentParser()
    group=p.add_mutually_exclusive_group(required=True)
    group.add_argument("--execute",action="store_true")
    group.add_argument("--observe",action="store_true")
    group.add_argument("--finalize",action="store_true")
    p.add_argument("--manifest-sha256",required=True)
    args=p.parse_args()
    need(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode,"ISOLATED_INTERPRETER")
    need(Path(__file__)==CODE/"hold_native_overlay.py","PROTECTED_HOLDER_ENTRYPOINT")
    a,receipt,m,raw=load_inputs(args.manifest_sha256)
    operator(a,m)
    lock=a.acquire_release_lock(a.GLOBAL_LOCK)
    try:
        result=execute(a,receipt,m,raw) if args.execute else inspect_or_finish(a,receipt,m,args.finalize)
        print(json.dumps({"state":result,"native_actions":0,"service_restarts":0}),flush=True)
    except BaseException as error:
        # No automatic gate, firewall, keeper or native cleanup after uncertainty.
        if STAGE.exists() and not (STAGE/"HOLDER_FAILURE.json").exists():
            a.savejson(STAGE/"HOLDER_FAILURE.json",{"error_type":type(error).__name__,
                       "at":a.now(),"automatic_reopen_performed":False,"operator_state_preserved":True,
                       "native_retry_authorized":False})
        raise
    finally:
        os.close(lock)

if __name__=="__main__":
    try:
        main()
    except BaseException as error:
        if isinstance(error,SystemExit):
            raise
        print(json.dumps({"state":"NATIVE_HOLDER_HELD","error_type":type(error).__name__}),flush=True)
        raise SystemExit(78)
