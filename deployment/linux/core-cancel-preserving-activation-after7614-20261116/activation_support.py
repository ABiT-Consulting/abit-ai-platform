"""Pinned preservation helpers for the new cancellation-preserving Core cutover.

These helpers are copied as source from the reviewed historical preservation
procedure. This module never imports or invokes either historical controller.
No signing-key/JWT creator or historical process/gate binding is included.
"""
from pathlib import Path
from http.client import HTTPConnection
import hashlib
import json
import os
import sqlite3
import stat
import subprocess
import sys
import time

ROOT = Path("/srv/abit-dev-linux/abit-ai")
DATA = ROOT / "data"
RUNTIME = ROOT / "releases/20261031-live-0.11.4/runtime/open_webui"
MAIN = "abit-ai.service"
QA = "abit-ai-erp-review-qa.service"
OUT = Path("/var/lib/abit-ai-erp-rollouts/core-original-hold-cancel-preserving-20261116-v1")
DATABASE_NAMES = (
    "abit-slack.sqlite", "abit-telegram.sqlite", "abitdot-primary.sqlite",
    "agentic-governance.sqlite", "aibit-chatgpt-relay.sqlite", "codex-workspace-runs.sqlite",
    "erp-dot-issues.sqlite", "erp-pending-review-qa.sqlite", "erp-task-resolution.sqlite",
    "maintenance-admission/gate.sqlite", "webui.db",
)

def need(value, code):
    if not value: raise RuntimeError(code)

def sha(raw): return hashlib.sha256(raw).hexdigest()

def canonical(value): return json.dumps(value,sort_keys=True,separators=(',',':'),default=lambda v: {'bytes_hex':v.hex()} if isinstance(v,bytes) else str(v)).encode()

def read(path):
    need(not path.is_symlink() and path.is_file(), 'REGULAR_FILE_REQUIRED')
    return path.read_bytes()

def save(name, value):
    raw=value if isinstance(value,bytes) else canonical(value)
    fd=os.open(OUT/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f: f.write(raw);f.flush();os.fsync(f.fileno())

def metadata(path):
    st=path.lstat();need(stat.S_ISREG(st.st_mode) and st.st_nlink==1,'OWNED_REGULAR_SINGLE_LINK_FILE_REQUIRED')
    return {'uid':st.st_uid,'gid':st.st_gid,'mode':stat.S_IMODE(st.st_mode),
        'xattrs':{k:sha(os.getxattr(path,k)) for k in os.listxattr(path)}}

def query(name, sql, args=()):
    with sqlite3.connect((DATA/name).as_uri()+'?mode=ro',uri=True,timeout=10) as db:
        db.execute('PRAGMA query_only=ON')
        return db.execute(sql,args).fetchall()

def identity(pid):
    p=Path('/proc')/str(pid);s=p.joinpath('stat').read_text().split(') ',1)[1].split()
    need(s[0]!='Z','LIVE_PROCESS_REQUIRED')
    return {'pid':pid,'uid':p.stat().st_uid,'start':s[19],
        'argv_sha256':sha(p.joinpath('cmdline').read_bytes()),
        'cgroup_sha256':sha(p.joinpath('cgroup').read_bytes())}

def service(unit):
    raw=subprocess.check_output(['systemctl','show',unit,
        '--property=MainPID,ActiveState,SubState,NRestarts,Restart,KillMode,SendSIGKILL,TimeoutStopUSec'],text=True,timeout=10)
    return dict(line.split('=',1) for line in raw.splitlines())

def http(path, headers=None, port=8888):
    c=HTTPConnection('127.0.0.1',port,timeout=8)
    try:
        c.request('GET',path,headers=headers or {});r=c.getresponse();raw=r.read(100001)
        need(r.status==200 and len(raw)<=100000,'LOCAL_STATUS_UNAVAILABLE')
        return json.loads(raw)
    finally:c.close()

def configuration():
    paths=[ROOT/'config/erp-assignment/resolver.json',ROOT/'config/runtime-environment.json']
    for unit in (MAIN,QA):
        paths.append(Path('/etc/systemd/system')/unit)
        drops=subprocess.check_output(['systemctl','show',unit,'--property=DropInPaths','--value'],text=True,timeout=10).split()
        paths.extend(Path(v) for v in drops)
    return {str(p):sha(read(p)) for p in paths}

def originals():
    result={}
    tables={
        'maintenance-admission/gate.sqlite':['lease'],
        'abitdot-primary.sqlite':[r[0] for r in query('abitdot-primary.sqlite',"SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")],
        'agentic-governance.sqlite':[r[0] for r in query('agentic-governance.sqlite',"SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")],
        'webui.db':['automation','automation_run'],
        'erp-pending-review-qa.sqlite':['review'],
    }
    for database,names in tables.items():
        for table in names:
            need(table.replace('_','').isalnum(),'TABLE_IDENTIFIER_REQUIRED')
            columns=query(database,'PRAGMA table_info("'+table+'")')
            ordering=','.join(str(i+1) for i in range(len(columns)))
            rows=query(database,'SELECT * FROM "'+table+'" ORDER BY '+ordering)
            result[database+':'+table]={'rows':len(rows),'sha256':sha(canonical(rows))}
    return result

def backup(phase):
    need(phase in {"prestop", "stopped"}, "EXACT_BACKUP_PHASE_REQUIRED")
    actual = sorted([p.name for p in DATA.glob("*.sqlite")] +
                    ["webui.db", "maintenance-admission/gate.sqlite"])
    need(actual == sorted(DATABASE_NAMES), "REVIEWED_DATABASE_SET_CHANGED")
    directory = OUT / ("databases-" + phase)
    directory.mkdir(mode=0o700)
    rows = []
    deadline = time.monotonic() + 120
    def progress(status, remaining, total):
        need(time.monotonic() < deadline, "BACKUP_DEADLINE_REACHED")
    for name in DATABASE_NAMES:
        source = DATA / name
        before = source.lstat()
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, "REGULAR_DATA_FILE_REQUIRED")
        dest = directory / source.name
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=10) as a:
            a.execute("PRAGMA query_only=ON")
            with sqlite3.connect(dest, timeout=10) as b:
                a.backup(b, pages=256, progress=progress, sleep=0.05)
                need(b.execute("PRAGMA quick_check").fetchone()[0] == "ok", "BACKUP_NOT_CONSISTENT")
        after = source.lstat()
        need((before.st_dev, before.st_ino, before.st_uid, before.st_gid) ==
             (after.st_dev, after.st_ino, after.st_uid, after.st_gid), "BACKUP_SOURCE_IDENTITY_CHANGED")
        with dest.open("rb") as retained:
            os.fsync(retained.fileno())
        rows.append({"source": str(source), "phase": phase, "sha256": sha(read(dest)),
                     "bytes": dest.stat().st_size, "quick_check": "ok"})
    save("database-backups-" + phase + ".json", rows)

def install(path, raw, backup_name):
    st=path.lstat();save(backup_name,read(path))
    temp=path.with_name('.'+path.name+'.task-controls-new')
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f:
        f.write(raw);f.flush();os.fchown(f.fileno(),st.st_uid,st.st_gid)
        os.fchmod(f.fileno(),stat.S_IMODE(st.st_mode));os.fsync(f.fileno())
    for key in os.listxattr(path):os.setxattr(temp,key,os.getxattr(path,key))
    os.replace(temp,path)

def lifecycle(unit, action):
    save(unit+'.'+action+'.intent.json',{'unit':unit,'action':action,'attempts':1})
    r=subprocess.run(['systemctl',action,unit],capture_output=True,timeout=105)
    save(unit+'.'+action+'.ack.json',{'returncode':r.returncode,'stdout_sha256':sha(r.stdout),'stderr_sha256':sha(r.stderr)})
    need(r.returncode==0,'ORIGINAL_SERVICE_OUTCOME_UNCONFIRMED_NO_REPEAT')

def database_identities():
    result = {}
    for name in ("webui.db", "agentic-governance.sqlite", "abitdot-primary.sqlite",
                 "maintenance-admission/gate.sqlite"):
        path = DATA / name
        st = path.lstat()
        need(stat.S_ISREG(st.st_mode) and st.st_nlink == 1, "REGULAR_DATA_FILE_REQUIRED")
        result[name] = {"device": st.st_dev, "inode": st.st_ino, "uid": st.st_uid,
                        "gid": st.st_gid, "mode": stat.S_IMODE(st.st_mode)}
    return result

def snapshot():
    need(os.getuid() == os.geteuid() == 999 and os.getgid() == os.getegid() == 988,
         "EXACT_QUERY_ONLY_SERVICE_IDENTITY_REQUIRED")
    counts = {
        "running_automations": query("webui.db", "SELECT count(*) FROM automation_run WHERE status='running'")[0][0],
        "live_governed_runs": query("agentic-governance.sqlite", "SELECT count(*) FROM governance_invocation WHERE state='running'")[0][0],
        "dispatching_provider_rows": query("abitdot-primary.sqlite", "SELECT count(*) FROM dot_primary_runs WHERE status='dispatching'")[0][0],
        "legacy_QA_work": query("erp-pending-review-qa.sqlite", "SELECT count(*) FROM review WHERE phase!='done'")[0][0],
    }
    gate = query("maintenance-admission/gate.sqlite", "SELECT state,epoch FROM gate WHERE id=1")
    need(len(gate) == 1 and type(gate[0][1]) is int, "EXACT_GATE_ROW_REQUIRED")
    running = query("webui.db", "SELECT id,automation_id FROM automation_run WHERE status='running' ORDER BY id")
    need(running == [("5955703a-4a31-5a33-8265-63a5299bdace",
                      "1ce26fc3-a045-5f94-afa0-c9d869963e19")], "UNREVIEWED_RUNNING_AUTOMATION")
    return {"gate": {"state": gate[0][0], "epoch": gate[0][1]}, "counts": counts,
            "original_journals": originals(), "database_identities": database_identities(),
            "cross_database_atomic": False, "query_only": True}

if __name__ == "__main__":
    try:
        need(sys.argv[1:] == ["--state-snapshot"], "EXACT_READ_ONLY_MODE_REQUIRED")
        print(json.dumps(snapshot(), sort_keys=True), flush=True)
    except Exception as error:
        print(json.dumps({"state": "SNAPSHOT_HELD", "error_code":
              str(error) if isinstance(error, RuntimeError) else type(error).__name__}), flush=True)
        raise SystemExit(65)
