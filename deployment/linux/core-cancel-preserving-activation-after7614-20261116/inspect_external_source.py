"""Retain exact externally changed Python source, never importing or executing it."""
from pathlib import Path
import ast
import datetime
import difflib
import hashlib
import json
import re

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
RUNTIME = Path("/srv/abit-dev-linux/abit-ai/releases/20261031-live-0.11.4/runtime/open_webui")
V2 = REPO / "deployment/linux/original-contract-rejection-hold-20261116-v2"
OUT = HERE / "external-change-review-1"

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def read_exact(path, expected):
    st = path.stat()
    raw = path.read_bytes()
    after = path.stat()
    assert (st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns)==(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns)
    assert sha(raw) == expected, str(path)
    return raw

def reverse_unified(new, patch):
    source = new.splitlines(keepends=True)
    lines = patch.splitlines(keepends=True)
    cursor, result, i = 0, [], 0
    assert not any(line.startswith("\\ No newline") for line in lines)
    while i < len(lines):
        if not lines[i].startswith("@@ "):
            assert lines[i].startswith(("--- ","+++ ")), repr(lines[i])
            i += 1
            continue
        match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", lines[i])
        assert match
        start = int(match[3]) - 1
        assert cursor <= start
        result.extend(source[cursor:start])
        cursor = start
        old_count = new_count = 0
        i += 1
        while i < len(lines) and not lines[i].startswith("@@ "):
            line = lines[i]
            marker, text = line[0], line[1:]
            assert marker in " +-"
            if marker in " +":
                assert source[cursor] == text
                cursor += 1
                new_count += 1
            if marker in " -":
                result.append(text)
                old_count += 1
            i += 1
        assert old_count == int(match[2] or 1) and new_count == int(match[4] or 1)
    result.extend(source[cursor:])
    return "".join(result).encode()

def definitions(raw):
    s = raw.decode()
    return {node.name: ast.get_source_segment(s,node) for node in ast.parse(s).body
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))}

drift = json.loads((HERE / "evidence/runtime-drift-before-freeze-1.json").read_text())
assert len(drift["changed"]) == 8 and not OUT.exists()
rows, retained = [], {}
for item in drift["changed"]:
    live = Path(item["path"])
    rel = live.relative_to(RUNTIME)
    raw = read_exact(live,item["sha256"])
    retained["current/"+str(rel)] = raw
    before = None
    if str(rel) == "routers/agentic_governance.py":
        before = read_exact(HERE / "source-before/runtime-agentic_governance.py",item["old_sha256"])
    elif rel.name == "agentic_executor.py":
        candidate = read_exact(V2 / "candidate/open_webui/utils/agentic_executor.py",
                               "d14747f2eb00da233099e105b4a7000a053efd5027d027c6dec4af41671e6cd8")
        before = reverse_unified(candidate.decode(),(V2/"agentic_executor.py.diff").read_text())
        assert sha(before) == item["old_sha256"]
    elif rel.name != "agentic_orchestrator.py":
        before = read_exact(REPO / "backend/open_webui" / rel,item["old_sha256"])
    row = {"path":str(rel),"before_sha256":item["old_sha256"],"current_sha256":item["sha256"],
           "current_bytes":len(raw),"baseline_bytes_available":before is not None}
    current_definitions = definitions(raw)
    if before is not None:
        retained["before/"+str(rel)] = before
        patch = "".join(difflib.unified_diff(before.decode().splitlines(keepends=True),
                    raw.decode().splitlines(keepends=True),fromfile="before/"+str(rel),tofile="current/"+str(rel)))
        retained["diff/"+str(rel)+".diff"] = patch.encode()
        old_definitions = definitions(before)
        row.update({
            "changed_definitions": sorted(k for k in current_definitions.keys() & old_definitions.keys()
                                         if current_definitions[k] != old_definitions[k]),
            "added_definitions":sorted(current_definitions.keys()-old_definitions.keys()),
            "removed_definitions":sorted(old_definitions.keys()-current_definitions.keys()),
            "diff_sha256":sha(patch.encode()),
            "diff_bytes":len(patch.encode()),
        })
    else:
        row["current_definitions"] = sorted(current_definitions)
    rows.append(row)

OUT.mkdir()
for rel,raw in retained.items():
    path = OUT / rel
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("xb") as f:f.write(raw)
summary={"schema_version":1,"observed_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),
         "runtime_actions":0,"source_only":True,"current_files":8,"exact_baselines_available":7,
         "baseline_adopted":False,"rows":rows,
         "retained_files":{rel:{"bytes":len(raw),"sha256":sha(raw)} for rel,raw in sorted(retained.items())}}
with (OUT/"source-diff-summary.json").open("x") as f:f.write(json.dumps(summary,indent=2,sort_keys=True)+"\n")
print(json.dumps({"source_only":True,"runtime_actions":0,"rows":rows,"retained_file_count":len(retained)},sort_keys=True))
