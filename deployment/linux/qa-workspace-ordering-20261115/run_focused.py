#!/usr/bin/env python3
"""Isolated QA-ordering tests; creates only private fixture/evidence files."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]
EVIDENCE=REPO/".git/qa-workspace-ordering-validation"
p=argparse.ArgumentParser()
p.add_argument("--mode", choices=("baseline","candidate"), required=True)
p.add_argument("--flavor", choices=("canonical","live"), required=True)
p.add_argument("--label", required=True)
p.add_argument("--only", action="append")
a=p.parse_args()
if not a.label.replace("-","").isalnum(): raise SystemExit("Invalid evidence label")
EVIDENCE.mkdir(exist_ok=True)
fixture=EVIDENCE/("fixture-"+a.label)
fixture.mkdir(mode=0o700)
tempfile.tempdir=str(fixture)
sys.dont_write_bytecode=True
manifest=json.loads((HERE/"source-manifest.json").read_text())
for rel,fact in manifest["files"].items():
    body=(HERE/rel).read_bytes()
    if hashlib.sha256(body).hexdigest()!=fact["sha256"] or len(body)!=fact["bytes"]:
        raise SystemExit("Frozen source changed: "+rel)
source_pins={}
for root in (REPO/"backend/open_webui/utils",REPO/"backend/tests",HERE):
    for path in root.rglob("*"):
        if path.is_file() and path.suffix in (".py",".json") and "__pycache__" not in path.parts:
            source_pins[str(path)]={"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"bytes":path.stat().st_size}
forbidden=tuple(map(Path,(
 "/srv/abit-dev-linux/abit-ai/data", "/srv/abit-dev-linux/abit-ai/current",
 "/srv/abit-dev-linux/abit-ai/releases", "/srv/abit-dev-linux/abit-ai/config",
 "/var/lib/abit-ai-agentic-gateway", "/var/lib/abit-ai-erp-rollouts",
)))
executed=set()
network_denials=[]
def inside(path,root):
    return path==root or root in path.parents
def fdpath(value):
    return Path(os.readlink("/proc/self/fd/"+str(value)))
def audit(event,args):
    if event in {"socket.connect","socket.bind","socket.getaddrinfo","socket.gethostbyname","socket.gethostbyaddr",
                 "subprocess.Popen","os.system","os.exec","os.posix_spawn"}:
        network_denials.append(event)
        raise AssertionError("External network/process action denied: "+event)
    if event=="exec":
        filename=getattr(args[0],"co_filename","")
        if filename.startswith(str(REPO)):
            if filename not in source_pins:
                raise AssertionError("Unpinned repository source execution")
            executed.add(filename)
    if event=="open" and isinstance(args[0],(str,bytes,os.PathLike)):
        name=os.fsdecode(args[0])
        path=Path(name).absolute()
        if any(inside(path,root) for root in forbidden):
            raise AssertionError("Live state file access denied")
        mode,flags=args[1],args[2]
        writing=(isinstance(mode,str) and any(x in mode for x in "wax+")) or (
            isinstance(flags,int) and bool(flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)))
        # Source handlers use relative O_EXCL writes through a previously opened
        # exact temporary directory descriptor. Those are independently bounded
        # by their real nofollow helpers; absolute writes must remain in evidence.
        if writing and Path(name).is_absolute() and not inside(path,EVIDENCE):
            raise AssertionError("Write outside isolated fixture/evidence")
    if event=="sqlite3.connect":
        name=str(args[0])
        if name.startswith("file:"):
            from urllib.parse import urlsplit, unquote
            parsed=urlsplit(name)
            if parsed.netloc or parsed.query != "mode=ro" or parsed.fragment:
                raise AssertionError("Only the exact temporary read-only SQLite URI is supported")
            name=unquote(parsed.path)
        target=Path(name)
        if not target.is_absolute() or not inside(target.resolve(),fixture):
            raise AssertionError("SQLite must use the temporary fixture")
sys.addaudithook(audit)
sys.path.insert(0,str(HERE))
import test_qa_workspace_ordering as tests
tests.MODE=a.mode;tests.FLAVOR=a.flavor
names=a.only or unittest.defaultTestLoader.getTestCaseNames(tests.QAWorkspaceOrdering)
suite=unittest.TestSuite(tests.QAWorkspaceOrdering(name) for name in names)
started=time.time()
logpath=EVIDENCE/(a.label+".log")
with logpath.open("x") as log:
    result=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
ended=time.time()
changed=[]
for filename in sorted(executed):
    if hashlib.sha256(Path(filename).read_bytes()).hexdigest()!=source_pins[filename]["sha256"]:
        changed.append(filename)
receipt={
 "mode":a.mode,"flavor":a.flavor,"source_commit":manifest["source_commit"],
 "started_at_unix":started,"finished_at_unix":ended,"duration_seconds":ended-started,
 "tests_run":result.testsRun,"failures":[{"id":test.id(),"traceback":trace} for test,trace in result.failures],
 "errors":[{"id":test.id(),"traceback":trace} for test,trace in result.errors],
 "skipped":[{"id":test.id(),"reason":reason} for test,reason in result.skipped],
 "test_ids":[tests.QAWorkspaceOrdering(name).id() for name in names],
 "success":result.wasSuccessful() and not changed,"source_changed":changed,
 "external_actions_denied":True,"denial_events":network_denials,
 "fixture_boundary":"Temporary SQLite/files; fixed offline GitHub GET responses; controlled sandbox process/budget. No live application, provider, network, native, GitHub or subprocess execution.",
 "test_source":{"sha256":hashlib.sha256((HERE/"test_qa_workspace_ordering.py").read_bytes()).hexdigest()},
 "executed_repository_sources":{name:source_pins[name] for name in sorted(executed)},
 "frozen_candidate_inputs":manifest["files"],
 "log":{"path":str(logpath),"sha256":hashlib.sha256(logpath.read_bytes()).hexdigest(),"bytes":logpath.stat().st_size},
}
receiptpath=EVIDENCE/(a.label+".json")
with receiptpath.open("x") as output:json.dump(receipt,output,indent=2,sort_keys=True);output.write("\n")
print(json.dumps({"receipt":str(receiptpath),"sha256":hashlib.sha256(receiptpath.read_bytes()).hexdigest(),
 "tests_run":result.testsRun,"failures":len(result.failures),"errors":len(result.errors),"skipped":len(result.skipped),
 "success":receipt["success"],"duration_seconds":receipt["duration_seconds"],"log":receipt["log"]},sort_keys=True))
if not receipt["success"]:
    print(logpath.read_text())
raise SystemExit(0 if receipt["success"] else 1)
