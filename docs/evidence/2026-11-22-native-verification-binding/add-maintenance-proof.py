from pathlib import Path
import shutil,hashlib,json
R=Path(__file__).resolve().parents[3]
E=Path(__file__).resolve().parent
old=E/'before-maintenance-and-root-bundle'
old.mkdir(exist_ok=False)
for n in ['agentic_native_contract.py','agentic_native_verification.py','agentic_native_broker.py']:
 p=R/'backend/open_webui/utils'/n;shutil.copy2(p,old/n)
shutil.copy2(R/'backend/tests/test_agentic_native_verification.py',old/'test_agentic_native_verification.py')

def replace(p,a,b):
 s=p.read_text()
 if s.count(a)!=1: raise RuntimeError('Exact source anchor differs: '+str(p))
 p.write_text(s.replace(a,b))
p=R/'backend/open_webui/utils/agentic_native_contract.py'
replace(p,'def validate_binding(value, gate):','''def validate_maintenance(value, invocation):
    exact(value, {"lease_id", "kind", "reference", "epoch"}, "NATIVE_MAINTENANCE_PROOF_INVALID")
    require(type(value["lease_id"]) is str and re.fullmatch(r"[a-f0-9]{48}", value["lease_id"]) and
            value["kind"] == "governed" and value["reference"] == invocation and
            type(value["epoch"]) is int and value["epoch"] > 0,
            "NATIVE_MAINTENANCE_SCOPE_CHANGED")
    return value


def validate_binding(value, gate):''')
replace(p,'"start_receipt_sha256", "erp_baseline_sha256", "action_id", "native_approval_sha256", "created_at"}', '"start_receipt_sha256", "erp_baseline_sha256", "action_id", "native_approval_sha256", "maintenance_admission", "created_at"}')
replace(p,'    sha(value["native_approval_sha256"])','    sha(value["native_approval_sha256"])\n    validate_maintenance(value["maintenance_admission"], value["scope"]["invocation_id"])')
p=R/'backend/open_webui/utils/agentic_native_verification.py'
replace(p,'import time\n','import time\nfrom pathlib import Path\n')
replace(p,'    validate_binding, validate_receipt,','    validate_binding, validate_receipt, validate_maintenance,')
replace(p,'def bind_original(row, packet, gate, db_path=None):','''def maintenance_proof(invocation, db_path=None, expected=None):
    from open_webui.utils import agentic_maintenance as maintenance
    target = maintenance.path(Path(db_path).parent if db_path is not None else None)
    proof = maintenance.require_active("governed", invocation, db_path=target)
    validate_maintenance(proof, invocation)
    require(expected is None or proof == expected, "NATIVE_MAINTENANCE_ADMISSION_REPLACED")
    return proof


def bind_original(row, packet, gate, db_path=None):''')
replace(p,'"native_approval_sha256": digest(consumed), "created_at": now}', '"native_approval_sha256": digest(consumed),\n               "maintenance_admission": maintenance_proof(row["invocation_id"], db_path), "created_at": now}')
replace(p,'        current_action, current_consumed = consumed_approval(db, row, packet)', '        maintenance_proof(row["invocation_id"], db_path, binding["maintenance_admission"])\n        current_action, current_consumed = consumed_approval(db, row, packet)')
replace(p,'        validate_gate(gate, scope_for(row, packet), now)\n        require(binding["lease_token_sha256"]', '        validate_gate(gate, scope_for(row, packet), now)\n        maintenance_proof(row["invocation_id"], db_path, binding["maintenance_admission"])\n        require(binding["lease_token_sha256"]')
replace(p,'        validate_gate(gate, scope_for(current, packet), now)\n        action, consumed', '        validate_gate(gate, scope_for(current, packet), now)\n        maintenance_proof(current["invocation_id"], db_path, binding["maintenance_admission"])\n        action, consumed')
replace(p,'"binding_sha256": digest(binding), "issued_at": now,', '"binding_sha256": digest(binding), "maintenance_admission": binding["maintenance_admission"],\n                 "issued_at": now,')
p=R/'backend/open_webui/utils/agentic_native_broker.py'
replace(p,'"controller.py": "774f338d9e0bffb9d390bdfff271dad4f68637974c25c572158103e2fc41ffce"','"controller.py": "773bf0adea5ef2c885adbd0124050a302066c1a41513c65c063d94625c03894b"')
replace(p,'        require(grant.get("binding_sha256") == digest(binding) and\n', '        require(grant.get("binding_sha256") == digest(binding) and\n                grant.get("maintenance_admission") == binding["maintenance_admission"] and\n')
p=R/'backend/tests/test_agentic_native_verification.py'
s=p.read_text();oldline='grant={"binding_sha256":saved["binding_sha256"],"issued_at":int(time.time()),"expires_at":int(time.time())+10}'
if s.count(oldline)!=2: raise RuntimeError('root fixture grant count differs')
s=s.replace(oldline,'grant={"binding_sha256":saved["binding_sha256"],"maintenance_admission":saved["binding"]["maintenance_admission"],"issued_at":int(time.time()),"expires_at":int(time.time())+10}')
p.write_text(s)
print(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (R/'backend/open_webui/utils').glob('agentic_native_*.py')},sort_keys=True))
