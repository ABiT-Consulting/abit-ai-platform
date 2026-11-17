"""Prepare current Core source and offline validators; no application import or live mutation."""
from pathlib import Path
import ast
import datetime
import difflib
import hashlib
import importlib.util
import json
import re

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
OLD = REPO / "deployment/linux/core-cancel-preserving-activation-after7614-20261116"
V2 = REPO / "deployment/linux/original-contract-rejection-hold-20261116-v2"
RUNTIME = Path("/srv/abit-dev-linux/abit-ai/releases/20261031-live-0.11.4/runtime/open_webui")
QA = Path("/srv/abit-dev-linux/abit-ai/qa-service/modules")
EXPECTED_QUEUE = "a53887200f7c6ad9cc3d4c26b5095dab0af7ff089ae3d345fa25214e36556541"

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def read(path, expected=None):
    path = Path(path)
    before = path.lstat()
    assert path.is_file() and not path.is_symlink() and 0 <= before.st_size <= 1048576
    raw = path.read_bytes()
    after = path.lstat()
    assert (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)==(
        after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns)
    if expected:
        assert sha(raw)==expected, str(path)
    return raw

def save(relative, raw):
    p = HERE / relative
    p.parent.mkdir(parents=True,exist_ok=True)
    with p.open("xb") as f:f.write(raw)

def save_json(relative, value):
    save(relative,(json.dumps(value,indent=2,sort_keys=True)+"\n").encode())

def function(raw, name):
    s=raw.decode()
    rows=[n for n in ast.parse(s).body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name]
    assert len(rows)==1
    return ast.get_source_segment(s,rows[0]).encode()

def changes(raw):
    lines=raw.decode().splitlines(keepends=True)
    chunks=[];i=0
    while i<len(lines):
        if not lines[i].startswith("@@ "):
            assert lines[i].startswith(("--- ","+++ "))
            i+=1
            continue
        i+=1;before=[];after=[]
        while i<len(lines) and not lines[i].startswith("@@ "):
            marker,text=lines[i][0],lines[i][1:]
            assert marker in " +-"
            if marker in " -":before.append(text)
            if marker in " +":after.append(text)
            i+=1
        chunks.append(("".join(before).encode(),"".join(after).encode()))
    return chunks

def apply_exact(raw, chunks, reverse=False):
    pairs=[(b,a) if reverse else (a,b) for a,b in chunks]
    if reverse:pairs.reverse()
    for before,after in pairs:
        assert raw.count(before)==1, "Exact unique reviewed source context required"
        raw=raw.replace(before,after,1)
    return raw

original=json.loads(read(OLD/"current-runtime-cohort.json",
                   "96eeb3ea0e4c8dc29bd6a44248442f724abff1097f199ecdd2dba5f06272b3a7"))
expected={row["path"]:row["sha256"] for row in original["files"]}
drift=json.loads(read(OLD/"evidence/runtime-drift-before-freeze-1.json"))
assert len(expected)==79 and len(drift["changed"])==8
for row in drift["changed"]:
    assert expected[row["path"]]==row["old_sha256"]
    expected[row["path"]]=row["sha256"]
expected[str(RUNTIME/"utils/agentic_project_queue.py")]=EXPECTED_QUEUE
assert len(expected)==80

retained={};primary=[]
for path,digest in sorted(expected.items()):
    p=Path(path);raw=read(p,digest);st=p.lstat()
    if p.is_relative_to(RUNTIME):
        relative="snapshot/open_webui/"+str(p.relative_to(RUNTIME))
    else:
        assert p.parent==QA and p.name in {"erp_assigned_qa.py","erp_pending_review_qa.py"}
        relative="snapshot/qa-service/modules/"+p.name
    retained[relative]=raw
    primary.append({"path":path,"snapshot":relative,"sha256":digest,"bytes":len(raw),
                    "uid":st.st_uid,"gid":st.st_gid,"mode":st.st_mode&0o777,"mtime_ns":st.st_mtime_ns})

# Capture source-only utility import closure for isolated tests; no imports execute.
source_pins=dict(expected)
main=RUNTIME/"main.py"
raw=read(main,"353719427f5881b46d448143d99139b3c340fb7e2f07a326702196b6baa248e7")
retained["snapshot/open_webui/main.py"]=raw
source_pins[str(main)]=sha(raw)
pending=[Path(path) for path in expected if Path(path).is_relative_to(RUNTIME) and path.endswith(".py")]+[main]
visited=set();extra=[]
def locate(full):
    if not full.startswith("open_webui.utils"):
        return None
    relative=Path(*full.split(".")[1:])
    for p in (RUNTIME/(str(relative)+".py"),RUNTIME/relative/"__init__.py"):
        if p.is_file():
            assert p.resolve()==p and p.is_relative_to(RUNTIME)
            return p
    return None
while pending:
    path=pending.pop()
    if path in visited:continue
    visited.add(path)
    assert len(visited)<=256
    raw=read(path,source_pins.get(str(path)))
    if str(path) not in source_pins:
        source_pins[str(path)]=sha(raw)
        relative="snapshot/open_webui/"+str(path.relative_to(RUNTIME))
        retained[relative]=raw
        extra.append({"path":str(path),"snapshot":relative,"sha256":sha(raw),"bytes":len(raw)})
    relative=path.relative_to(RUNTIME)
    parts=list(relative.with_suffix("").parts)
    if parts[-1]=="__init__":parts=parts[:-1];package="open_webui."+".".join(parts)
    else:package="open_webui."+".".join(parts[:-1])
    for node in ast.walk(ast.parse(raw)):
        names=[]
        if isinstance(node,ast.Import):
            names=[a.name for a in node.names]
        elif isinstance(node,ast.ImportFrom):
            base=node.module or ""
            if node.level:
                try:base=importlib.util.resolve_name("."*node.level+base,package)
                except (ImportError,ValueError):continue
            names=[base]+[base+"."+a.name for a in node.names if a.name!="*"]
        for full in names:
            target=locate(full)
            if target is not None and target not in visited:pending.append(target)
assert sum(len(raw) for raw in retained.values())<=6291456

# The four utility deltas are the exact reviewed v2 patches with unique context and inverse.
candidates={};proof=[]
for name in ("agentic_executor","agentic_result_store","agentic_gateway_contracts","agentic_blocker_review"):
    relative="utils/"+name+".py"
    before=retained["snapshot/open_webui/"+relative]
    patch=read(V2/(name+".py.diff"))
    chunks=changes(patch)
    after=apply_exact(before,chunks)
    assert apply_exact(after,chunks,reverse=True)==before
    if name!="agentic_executor":
        assert after==read(V2/"candidate/open_webui"/relative)
    compile(after,relative,"exec")
    candidates["candidate/open_webui/"+relative]=after
    proof.append({"path":relative,"before_sha256":sha(before),"after_sha256":sha(after),
                  "before_bytes":len(before),"after_bytes":len(after),
                  "exact_inverse":True,"reviewed_patch_sha256":sha(patch)})

before=retained["snapshot/open_webui/routers/agentic_governance.py"]
old=read(OLD/"source-before/runtime-agentic_governance.py",
         "ee5c1bca3f2294577f1fb1599b2a3cd46c1f388592603ade47a161307269e2cd")
reviewed=read(OLD/"candidate/open_webui/routers/agentic_governance.py",
              "1d93c966720a3cc1b64c321e026dfa45f0e4ab046d6f20f3c98128fd1479b6ef")
old_function=function(old,"reconciliation_loop")
new_function=function(reviewed,"reconciliation_loop")
assert function(before,"reconciliation_loop")==old_function and before.count(old_function)==1
after=before.replace(old_function,new_function,1)
assert after.count(new_function)==1 and after.replace(new_function,old_function,1)==before
assert len(after)-len(before)==367
canonical=read(REPO/"backend/open_webui/routers/agentic_governance.py",
               "4f9e5822500bf06c3fd7f82d5c972bfa022f2d30ac0973f374a62ccd5f616789")
assert function(canonical,"reconciliation_loop")==function(after,"reconciliation_loop")
candidates["candidate/open_webui/routers/agentic_governance.py"]=after
proof.append({"path":"routers/agentic_governance.py","before_sha256":sha(before),"after_sha256":sha(after),
              "before_bytes":len(before),"after_bytes":len(after),"exact_inverse":True,
              "changed_function":"reconciliation_loop","preserved_external_event_documentation":True})
for relative,raw in candidates.items():
    compile(raw,relative,"exec")
    source=retained[relative.replace("candidate/","snapshot/",1)]
    retained["diff/"+relative.split("open_webui/",1)[1]+".diff"]="".join(difflib.unified_diff(
        source.decode().splitlines(keepends=True),raw.decode().splitlines(keepends=True),
        fromfile="current/"+relative.split("open_webui/",1)[1],
        tofile="candidate/"+relative.split("open_webui/",1)[1])).encode()
retained.update(candidates)

# Controllers change only unique namespace/command ID and exact cohort size.
controller=read(OLD/"activate_once.py",
                "74c53c8e6e49db70a760d46a59a8074220c0f434e6cdf73c0958ba506e53b254").decode()
controller=controller.replace("core-original-hold-cancel-preserving-20261116-v1",
                              "core-original-hold-cancel-preserving-after2321-20261116-v2")
controller=controller.replace('"core-original-hold-cancel-" + action + "-20261116-v1"',
                              '"core-original-hold-cancel-" + action + "-after2321-20261116-v2"')
assert controller.count('== 79')==1
controller=controller.replace("== 79","== 80").replace('"source_files": 79','"source_files": 80')
controller=controller.replace('"count": 79','"count": 80').replace("runtime79-after.json","runtime80-after.json")
support=read(OLD/"activation_support.py",
             "6d36b9b712da3e495ebed0e83d3280511caa60da6f0772f66e760caef157f5e4").decode()
support=support.replace("core-original-hold-cancel-preserving-20261116-v1",
                        "core-original-hold-cancel-preserving-after2321-20261116-v2")
retained["activate_once.py"]=controller.encode();retained["activation_support.py"]=support.encode()
retained["binding.schema.json"]=read(OLD/"binding.schema.json")
offline=read(OLD/"validate_activation.py",
             "9a4c7760e28edb7ff014a8b53d47fa4152b8aa1d13ee361576cb99cd0135d825").decode()
assert offline.count("range(74)")==1
offline=offline.replace("test_entire79_file","test_entire80_file").replace("range(74)","range(75)")
retained["validate_activation.py"]=offline.encode()

candidate_test=read(V2/"validate_candidate.py").decode()
needle="LIVE = Path('/srv/abit-dev-linux/abit-ai/releases/20261031-live-0.11.4/runtime/open_webui/utils')"
assert candidate_test.count(needle)==1
candidate_test=candidate_test.replace(needle,"LIVE = Path(__file__).resolve().parent / 'snapshot/open_webui/utils'")
retained["validate_candidate.py"]=candidate_test.encode()

startup=read(REPO/"backend/tests/test_agentic_startup_maintenance.py",
             "32ef9ad381045df33a3671ababdb5d8ceec18ee5e4ce37dc41a777f96756b14c").decode()
startup=startup.replace('ROOT = Path(__file__).resolve().parents[2]',
                       'ROOT = Path("/srv/abit-dev-linux/abit-ai-platform")')
startup=startup.replace('CANDIDATE = ROOT / "deployment/linux/core-cancel-preserving-activation-after7614-20261116/candidate/open_webui/routers/agentic_governance.py"',
                       'CANDIDATE = Path(__file__).resolve().parent / "candidate/open_webui/routers/agentic_governance.py"')
retained["validate_startup.py"]=startup.encode()

for path,digest in source_pins.items():read(path,digest)
for relative,raw in retained.items():save(relative,raw)
save_json("current-source-cohort.json",{
    "schema_version":1,"observed_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "files":primary,"count":len(primary),"new_dependency":"utils/agentic_project_queue.py",
    "additional_validation_dependencies":extra,"main_source":{"path":str(main),"sha256":source_pins[str(main)]},
    "all_source_pins":source_pins,"per_file_stable":True,"whole_collection_rechecked":True,
    "cross_file_atomic":False,"runtime_actions":0,"activation_binding_included":False})
save_json("composition-proof.json",{
    "schema_version":1,"state":"CURRENT_SOURCE_COMPOSED_NOT_ACTIVATED",
    "replacements":proof,"current_cohort":80,"additional_dependency_count":len(extra),
    "current_executor_PM_enrollment_and_serialization_preserved":True,
    "current_governance_maintenance_IT_lock_fixes_unchanged":True,
    "current_project_queue_and_orchestrator_retained_unchanged":True,
    "historical_orchestrator_full_diff_unavailable":True,"runtime_actions":0,
    "copied_source_files":len(retained)})
print(json.dumps({"state":"CURRENT_SOURCE_COMPOSED_NOT_ACTIVATED","replacements":proof,
                  "current_cohort":80,"additional_dependency_count":len(extra),
                  "copied_source_files":len(retained),"runtime_actions":0},sort_keys=True))
