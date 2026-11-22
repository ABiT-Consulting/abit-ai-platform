"""Original-only finalization after bounded foreign Docker rule additions.

No import action. No execute, acquire, reseal, replay, baseline rewrite, service
restart or native operation exists here. The exact reviewed original holder
retains published-outcome, source, process, browser and once-command controls.
"""
from __future__ import annotations
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import stat
import sys
import types

ENTRY = Path("/var/lib/abit-ai-native-overlay-original-finalize-source-20261122/finalize_original.py")
ORIGINAL = Path("/var/lib/abit-ai-native-overlay-held-drain-source-20261122/hold_native_overlay.py")
ORIGINAL_SHA = "214f79fa62136a29019178ee3241d1c6f2ea19855a2f25785d55b3595df0899c"
MANIFEST_SHA = "bb2d235e33497ea0d1641646537b8c671e1a191d95b14e70e98acc4f46375d31"
INITIAL_JOURNAL_SHA = "92a67a372803cbc13c5e0ad5e0e7a5b12fdcb1059db5640b7493230f569a2e89"
BEFORE = Path("/var/lib/abit-ai-restored-target-activation-20261122/nft-before.json")
BEFORE_SHA = "d571b0f4b3f1c4c07eab4c266cdc7c03e9434b3854a7d2d3df0f5568314a7f57"
NATIVE_RESULT_SHA = "c95164a4361052bd9c4881a9a64724f4a5daecab0e348c2f4fe3cba7efb09f4d"
RESTORE_SHA = "16512abe69c55ee0fa4abf4a48162e68d51a65f9da77ae3cceb67f87bc378322"
MAX_ADDED = 64
MAX_CAPTURES = 8
DOCKER_CHAINS = {
    "filter": {"DOCKER", "DOCKER-BRIDGE", "DOCKER-CT", "DOCKER-FORWARD", "DOCKER-INTERNAL"},
    "nat": {"DOCKER", "POSTROUTING"},
    "raw": {"PREROUTING"},
}

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

def docker_expression(expr):
    """Only observed Docker bridge matches/jumps identify an additive rule."""
    if isinstance(expr, list):
        return any(docker_expression(x) for x in expr)
    if not isinstance(expr, dict):
        return False
    jump = expr.get("jump")
    if isinstance(jump, dict) and isinstance(jump.get("target"), str) and \
            re.fullmatch(r"DOCKER(?:-[A-Z]+)?", jump["target"]):
        return True
    match = expr.get("match")
    if isinstance(match, dict) and match.get("left") in (
            {"meta":{"key":"iifname"}}, {"meta":{"key":"oifname"}}):
        right = match.get("right")
        if isinstance(right, str) and re.fullmatch(r"br-[a-f0-9]{12}", right):
            return True
    return any(docker_expression(x) for x in expr.values())

def additive_delta(nft, baseline, current):
    need(type(baseline) is list and 1 <= len(baseline) <= 512 and
         type(current) is list and len(current) <= len(baseline)+MAX_ADDED and
         len(canonical(current)) <= 2*1024*1024, "BOUNDED_FOREIGN_RULESET")
    original = nft.without_handles(baseline)
    observed = nft.without_handles(current)
    index, added = 0, []
    for row in observed:
        if index < len(original) and row == original[index]:
            index += 1
        else:
            added.append(row)
    need(index == len(original), "ORIGINAL_FIREWALL_SEMANTICS_OR_ORDER_CHANGED")
    need(len(added) <= MAX_ADDED, "FOREIGN_ADDITION_BOUND")
    for item in added:
        need(type(item) is dict and set(item) == {"rule"}, "FOREIGN_ADDITION_NOT_DOCKER_RULE")
        row = item["rule"]
        need(type(row) is dict and {"family","table","chain","expr"} <= set(row) <=
             {"family","table","chain","expr","comment"} and row["family"] == "ip" and
             row["chain"] in DOCKER_CHAINS.get(row["table"],set()) and
             type(row["expr"]) is list and 1 <= len(row["expr"]) <= 32 and
             len(canonical(row)) <= 8192 and docker_expression(row["expr"]),
             "FOREIGN_ADDITION_OUTSIDE_DOCKER_SCOPE")
    return {"original_semantic_objects":len(original), "added_docker_rules":len(added),
            "added_semantics_sha256":digest(canonical(added)),
            "original_semantics_and_order_preserved":True}

def exact_native_reconciliation(h, a, m, held):
    proof = h.reconciliation(a,m,held)
    need(proof["outcome"] == "REJECTED_BEFORE_MUTATION" and
         proof["native_result_sha256"] == NATIVE_RESULT_SHA and
         proof["definition_restore_sha256"] == RESTORE_SHA,
         "EXACT_ORIGINAL_7906_REJECTION_AND_7908_RESTORE_REQUIRED")
    return proof

def core_health():
    results = {}
    for path in ("/health","/api/config"):
        connection = http.client.HTTPConnection("127.0.0.1",8888,timeout=5)
        try:
            connection.request("GET",path,headers={"Connection":"close"})
            response = connection.getresponse()
            raw = response.read(262145)
            need(response.status == 200 and len(raw) <= 262144, "CURRENT_CORE_HEALTH_REQUIRED")
            value = json.loads(raw)
            need(type(value) is dict, "CURRENT_CORE_RESPONSE_SHAPE")
            if path == "/health":
                need(value.get("status") is True, "CURRENT_CORE_HEALTH_FALSE")
            else:
                need(value.get("version") == "0.11.4", "CURRENT_CORE_VERSION_CHANGED")
            results[path] = {"status":200}
        finally:
            connection.close()
    return {"responses":results,"version":"0.11.4","no_provider_or_browser_action":True}

class OriginalFirewall:
    def __init__(self, h, a, m, r, c, original, baseline, binding, receipt):
        self.h,self.a,self.m,self.r,self.c = h,a,m,r,c
        self.original,self.baseline,self.binding,self.receipt = original,baseline,binding,receipt
        self.nft = sys.modules[type(original).__module__]

    def zero_connections(self,*args,**kwargs):
        return self.original.zero_connections(*args,**kwargs)

    def current(self, required):
        other,owned = self.nft.split_ruleset(self.nft.read_ruleset())
        delta = additive_delta(self.nft,self.baseline,other)
        if required:
            proof = self.nft.verify_owned(owned,self.a.MANIFEST_SHA)
            need(proof == self.c["intents"]["nft_seal"]["proof"], "ORIGINAL_OWNED_SEAL_CHANGED")
        else:
            need(not owned, "ORIGINAL_OWNED_SEAL_STILL_PRESENT")
            proof = {"owned_table_absent":True,"preexisting_rules_sha256":self.nft.digest(other)}
        return other,proof,delta

    def capture_record(self):
        intent = self.c["intents"].get("nft_unseal",{})
        name,pin = intent.get("foreign_snapshot_name"),intent.get("foreign_snapshot_sha256")
        need(isinstance(name,str) and re.fullmatch(r"foreign-nft-before-0[1-8]\.json",name) and
             isinstance(pin,str) and re.fullmatch("[a-f0-9]{64}",pin), "ORIGINAL_FOREIGN_CAPTURE_REQUIRED")
        raw = self.h.root_read(self.a.STAGE/"inputs"/name,0o600,2*1024*1024+65536)
        need(digest(raw) == pin, "ORIGINAL_FOREIGN_CAPTURE_CHANGED")
        value = json.loads(raw)
        need(set(value) == {"schema_version","purpose","binding","original_baseline_sha256",
             "owned_proof","foreign_objects","foreign_sha256","delta","captured_at"} and
             value["schema_version"] == 1 and value["purpose"] == "ORIGINAL_ONLY_FOREIGN_UNSEAL_PREIMAGE" and
             value["binding"] == self.binding and
             value["original_baseline_sha256"] == self.m["nft_before_sha256"] and
             value["owned_proof"] == intent["owned_table"] == self.c["intents"]["nft_seal"]["proof"] and
             self.nft.digest(value["foreign_objects"]) == value["foreign_sha256"] and
             additive_delta(self.nft,self.baseline,value["foreign_objects"]) == value["delta"],
             "ORIGINAL_FOREIGN_CAPTURE_BINDING")
        return value,pin

    def completion(self):
        path = self.a.STAGE/"FOREIGN_RULES_UNSEAL_VERIFIED.json"
        if not os.path.lexists(path):
            return None
        value = json.loads(self.h.root_read(path,0o600,65536))
        before,pin = self.capture_record()
        expected = {"schema_version":1,"purpose":"ORIGINAL_ONLY_FOREIGN_RULES_PRESERVED",
                    "binding":self.binding,"capture_sha256":pin,
                    "foreign_before_sha256":before["foreign_sha256"],
                    "foreign_after_sha256":before["foreign_sha256"],
                    "foreign_before_after_identical":True,"only_original_owned_table_removed":True}
        need(value == expected, "ORIGINAL_FOREIGN_COMPLETION_CHANGED")
        return value

    def snapshot(self,required):
        other,proof,_ = self.current(required)
        if not required:
            need(self.h.invoked(self.c,"nft_unseal"), "ABSENCE_WITHOUT_ORIGINAL_UNSEAL_INTENT")
            if self.completion() is None:
                before,_ = self.capture_record()
                need(other == before["foreign_objects"], "FOREIGN_RULES_CHANGED_DURING_UNSEAL")
        return proof

    def unseal(self,issue=True):
        intent = self.c["intents"].get("nft_unseal")
        if intent and intent.get("command_phase") not in ("intent_saved","precondition_failed"):
            return self.finish_unseal()
        need(issue, "ORIGINAL_UNSEAL_NOT_ISSUED")
        _,owned,_ = self.current(True)
        if intent is not None:
            need(intent.get("owned_table") == owned, "ORIGINAL_UNSEAL_INTENT_CHANGED")
        document = canonical({"nftables":[{"delete":{"table":{"family":"ip","handle":owned["table_handle"]}}}]})+b"\n"
        path = self.a.STAGE/"inputs"/"nft-apply-unseal.json"
        self.a.save_or_verify(path,document)
        def before():
            self.h.consistent(self.a,self.m)
            self.a.control_receipt(self.m,self.c["intents"]["close"])
            held = self.h.held_record(self.a,self.receipt,self.r)
            exact_native_reconciliation(self.h,self.a,self.m,held)
            self.a.verify_replacement(self.m,self.c)
            core_health()
            foreign,proof,delta = self.current(True)
            need(proof == owned and self.a.filehash(path) == digest(document),
                 "ORIGINAL_UNSEAL_INPUT_CHANGED")
            # Preserve every prior capture. Only a proven-unissued command can
            # capture again; an unknown command never enters this callback.
            names = sorted((self.a.STAGE/"inputs").glob("foreign-nft-before-*.json"))
            need(len(names) < MAX_CAPTURES and all(re.fullmatch(
                 r"foreign-nft-before-0[1-8]\.json",x.name) for x in names), "FOREIGN_CAPTURE_LIMIT")
            number = next(i for i in range(1,MAX_CAPTURES+1)
                          if not os.path.lexists(self.a.STAGE/"inputs"/f"foreign-nft-before-{i:02d}.json"))
            name = f"foreign-nft-before-{number:02d}.json"
            value = {"schema_version":1,"purpose":"ORIGINAL_ONLY_FOREIGN_UNSEAL_PREIMAGE",
                     "binding":self.binding,"original_baseline_sha256":self.m["nft_before_sha256"],
                     "owned_proof":proof,"foreign_objects":foreign,
                     "foreign_sha256":self.nft.digest(foreign),"delta":delta,"captured_at":self.a.now()}
            raw = canonical(value)+b"\n"
            self.a.save(self.a.STAGE/"inputs"/name,raw)
            current_intent = self.c["intents"]["nft_unseal"]
            current_intent["foreign_snapshot_name"] = name
            current_intent["foreign_snapshot_sha256"] = digest(raw)
            self.a.update(self.r)
            self.capture_record()
            latest,latest_proof,_ = self.current(True)
            need(latest == foreign and latest_proof == proof, "FOREIGN_RULES_CHANGED_BEFORE_UNSEAL")
        self.a.command_once(self.r,self.c,"nft_unseal",{"owned_table":owned},
                            [self.nft.NFT,"-j","-f",str(path)],before)
        return self.finish_unseal()

    def finish_unseal(self):
        need(self.h.invoked(self.c,"nft_unseal"), "ORIGINAL_UNSEAL_INTENT_REQUIRED")
        other,proof,_ = self.current(False)
        before,pin = self.capture_record()
        prior = self.completion()
        if prior is None:
            need(other == before["foreign_objects"], "FOREIGN_RULES_CHANGED_DURING_UNSEAL")
            value = {"schema_version":1,"purpose":"ORIGINAL_ONLY_FOREIGN_RULES_PRESERVED",
                     "binding":self.binding,"capture_sha256":pin,
                     "foreign_before_sha256":before["foreign_sha256"],
                     "foreign_after_sha256":self.nft.digest(other),
                     "foreign_before_after_identical":True,"only_original_owned_table_removed":True}
            self.a.save_or_verify(self.a.STAGE/"FOREIGN_RULES_UNSEAL_VERIFIED.json",canonical(value)+b"\n")
        if not self.c["intents"]["nft_unseal"].get("fulfilled_utc"):
            self.a.finished(self.r,self.c,"nft_unseal",proof)
        return proof

def install_adapter(h,a,m,receipt,baseline,binding):
    original_factory = a.barriers
    def factory(manifest,record,cycle):
        original,guard = original_factory(manifest,record,cycle)
        wrapped = OriginalFirewall(h,a,manifest,record,cycle,original,baseline,binding,receipt)
        guard.b = wrapped
        return wrapped,guard
    a.barriers = factory

def load_original():
    raw = root_read(ORIGINAL)
    need(digest(raw) == ORIGINAL_SHA, "EXACT_ORIGINAL_HOLDER_SOURCE_REQUIRED")
    h = types.ModuleType("original_finalize_holder")
    h.__file__ = str(ORIGINAL)
    sys.modules[h.__name__] = h
    exec(compile(raw,str(ORIGINAL),"exec"),h.__dict__)
    a,receipt,m,_ = h.load_inputs(MANIFEST_SHA)
    before_raw = root_read(BEFORE,0o600)
    need(digest(before_raw) == BEFORE_SHA, "EXACT_ORIGINAL_FIREWALL_PREIMAGE_REQUIRED")
    baseline = json.loads(before_raw)
    need(digest(canonical(baseline)) == m["nft_before_sha256"], "ORIGINAL_BASELINE_HASH_MISMATCH")
    source_sha = digest(root_read(ENTRY))
    binding = {"schema_version":1,"purpose":"ORIGINAL_7906_REJECTION_FINALIZE_WITH_FOREIGN_RULE_PRESERVATION",
               "source_sha256":source_sha,"original_holder_sha256":ORIGINAL_SHA,
               "manifest_sha256":MANIFEST_SHA,"initial_journal_sha256":INITIAL_JOURNAL_SHA,
               "original_before_raw_sha256":BEFORE_SHA,"native_result_sha256":NATIVE_RESULT_SHA,
               "definition_restore_sha256":RESTORE_SHA}
    return h,a,receipt,m,baseline,binding

def run(h,a,receipt,m,baseline,binding,finalize):
    h.operator(a,m)
    r,c,raw = h.read_journal(a,receipt,m)
    need(a.original_absent(r["original_holder"]), "ORIGINAL_HOLDER_STILL_RUNNING")
    need(c.get("unpublished_abort") is None, "ORIGINAL_PUBLISHED_OPERATION_ONLY")
    held = h.held_record(a,receipt,r)
    state = h.admission_state(a,m,r,c)
    need(state["status"] == "published", "EXACT_ORIGINAL_PUBLISHED_ADMISSION_REQUIRED")
    exact_native_reconciliation(h,a,m,held)
    old_binding = c.get("foreign_finalize")
    if old_binding is None:
        need(digest(raw) == INITIAL_JOURNAL_SHA and h.phase(c,"nft_unseal") ==
             h.phase(c,"open") == "absent", "ORIGINAL_FIRST_FINALIZATION_STATE_CHANGED")
    else:
        need(old_binding == binding, "ORIGINAL_FINALIZER_BINDING_CHANGED")
    install_adapter(h,a,m,receipt,baseline,binding)
    b,g = a.barriers(m,r,c)
    status = h.partial_observation(a,receipt,m,r,c,raw,b,g)
    need(not status["blocking_stages"] or status["blocking_stages"] == ["keeper_release_pending"],
         "ORIGINAL_FINALIZATION_OUTCOME_UNRESOLVED")
    if not h.invoked(c,"open"):
        h.consistent(a,m)
        core_health()
    if not finalize:
        return {"state":"ORIGINAL_FINALIZATION_PREFLIGHT_ONLY","firewall":status["firewall"],
                "native_reconciliation_verified":True,"writes_by_observer":False}
    if old_binding is None:
        c["foreign_finalize"] = binding
        a.update(r)
    # This is the original reviewed published-outcome path. It owns release,
    # nft_unseal and open intents; none is regenerated by this addon.
    return h.inspect_or_finish(a,receipt,m,True)

def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--preflight",action="store_true")
    group.add_argument("--finalize-original",action="store_true")
    args = parser.parse_args()
    need(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode and
         Path(__file__) == ENTRY, "FIXED_ISOLATED_ROOT_ENTRYPOINT_REQUIRED")
    h,a,receipt,m,baseline,binding = load_original()
    lock = a.acquire_release_lock(a.GLOBAL_LOCK) if args.finalize_original else None
    try:
        result = run(h,a,receipt,m,baseline,binding,args.finalize_original)
        print(json.dumps({"result":result,"native_actions":0,"service_restarts":0,
                          "original_baseline_rewritten":False,"foreign_rules_deleted":False}),flush=True)
    finally:
        if lock is not None:
            os.close(lock)

if __name__ == "__main__":
    try:
        main()
    except BaseException as error:
        if isinstance(error,SystemExit):
            raise
        print(json.dumps({"state":"ORIGINAL_FINALIZATION_HELD","error_type":type(error).__name__}),flush=True)
        raise SystemExit(78)
