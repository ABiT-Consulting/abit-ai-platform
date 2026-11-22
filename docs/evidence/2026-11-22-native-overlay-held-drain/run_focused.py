"""Run only finite holder interruption tests; real control/network launch is denied."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,importlib.util,io,json,os,socket,subprocess,sys,time,unittest
from unittest.mock import patch
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
PACKAGE=ROOT/"deployment/native-overlay-held-drain-20261122"
paths=[p for p in PACKAGE.rglob("*") if p.is_file()]
paths += [HERE/"test_holder_recovery.py",Path(__file__),HERE/"reference/native-holder-baseline-draft-20261122T1704.json"]
def hashes():return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
def deny(*args,**kwargs):raise AssertionError("REAL_NETWORK_OR_PROCESS_ACTION_DENIED")
before=hashes();stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
mode="normal" if __debug__ else "optimized";log=HERE/(mode+"-"+stamp+".log")
start=time.monotonic();stream=io.StringIO()
with patch.object(socket,"create_connection",deny),patch.object(socket,"socket",deny), \
     patch.object(subprocess,"Popen",deny),patch.object(subprocess,"run",deny), \
     patch.object(os,"system",deny),patch.object(os,"kill",deny),patch.object(os,"killpg",deny):
    spec=importlib.util.spec_from_file_location("holder_recovery_cases",HERE/"test_holder_recovery.py")
    tests=importlib.util.module_from_spec(spec);sys.modules[spec.name]=tests;spec.loader.exec_module(tests)
    result=unittest.TextTestRunner(stream=stream,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(tests))
log.write_text(stream.getvalue());after=hashes()
record={"schema_version":1,"mode":mode,"tests":result.testsRun,"failures":len(result.failures),
        "errors":len(result.errors),"skipped":len(result.skipped),"seconds":round(time.monotonic()-start,3),
        "source_hashes":before,"source_drift":before!=after,"log":log.name,
        "log_sha256":hashlib.sha256(log.read_bytes()).hexdigest(),
        "finished_utc":datetime.now(timezone.utc).isoformat(),
        "test_boundary":"Real ordinary temporary journals, original command_once/control_receipt and nft helper; fake external service/kernel/keeper boundaries. No host/root/provider execution.",
        "real_network_or_process_actions_denied":True}
path=log.with_suffix(".json");path.write_text(json.dumps(record,sort_keys=True,indent=2)+"\n")
print(json.dumps({"receipt":str(path),"receipt_sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
                 **{k:record[k] for k in ("tests","failures","errors","skipped","source_drift","seconds")}}))
if not result.wasSuccessful():print(stream.getvalue())
raise SystemExit(0 if result.wasSuccessful() and not record["source_drift"] else 1)
