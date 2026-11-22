"""Bounded original-admission observation for the existing authorized service operator.

This is a read-only diagnostic, not an admission, recovery, release or privilege
grant. Execute only through the existing operator identity/route that owns the
actual gate; never use a second DATA_DIR or change filesystem permissions.
"""
from pathlib import Path
import hashlib, importlib.util, json, os, sqlite3

ROOT=Path('/srv/abit-dev-linux/abit-ai/current/runtime/open_webui')
DATA=Path('/srv/abit-dev-linux/abit-ai/data')
AGENT='d4a6e561-cb2a-41d9-93b5-2888624cf8d6'

def main():
    source=ROOT/'utils/agentic_maintenance.py'
    spec=importlib.util.spec_from_file_location('observed_installed_maintenance',source)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    database=module.path(DATA)
    # The existing protected read validates real path, owner and modes.
    with module._db(database,write=False) as db:
        status=module._snapshot(db)
        leases=[dict(r) for r in db.execute(
            "SELECT id,kind,reference,epoch,pid,process_start,created_ns,state,parent_id,receipt_sha256 "
            "FROM lease WHERE kind='automation' AND reference=? AND state IN ('active','unresolved') LIMIT 2",
            (AGENT,))]
        audits=[]
        for lease in leases:
            for row in db.execute(
                "SELECT seq,kind,payload,created_ns FROM audit WHERE "
                "CASE WHEN json_valid(payload) THEN json_extract(payload,'$.lease') END=? "
                "ORDER BY seq LIMIT 12",(lease['id'],)):
                body=json.loads(row['payload'])
                audits.append({'seq':row['seq'],'kind':row['kind'],'created_ns':row['created_ns'],
                    'fields':{k:body.get(k) for k in ['lease','kind','reference','epoch','parent','run_id','receipt_sha256']}})
    result={'agent_id':AGENT,'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
            'service_uid':os.geteuid(),'gate':status,'original_leases':leases,
            'original_audit':audits,'read_only':True,'new_execution':False}
    print(json.dumps(result,indent=2))
if __name__=='__main__':
    main()
