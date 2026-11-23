"""Prepare only the exact reviewed source inventory; never install or register."""
from pathlib import Path
import hashlib,json,subprocess
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
mapping=json.loads((HERE/'broker-source-map.json').read_text())
manifest={}
inventory={}
for target,source in sorted(mapping.items()):
    raw=(ROOT/source).read_bytes() if source else b''
    digest=hashlib.sha256(raw).hexdigest()
    manifest[target]=digest
    inventory[target]={'sourcePath':source,'sha256':digest,'bytes':len(raw)}
(HERE/'broker-code-manifest.json').write_text(json.dumps(manifest,sort_keys=True,separators=(',',':'))+'\n')
(HERE/'broker-source-inventory.json').write_text(json.dumps({
    'schema':'intermarket.fixed-pr241-broker-source-inventory.v1',
    'status':'SOURCE_PREPARED_NOT_INSTALLED',
    'sourceBase':'5697c52d690b827d368a3057d130aff006577248',
    'sourceHeadAtInventory':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
    'entries':inventory,
    'installationConstraints':[
        'Use the existing protected broker/profile/worker journal and execution_state_root.',
        'Read and preserve every unresolved original unit reservation.',
        'Preserve unrelated installed customizations; owner reviews exact composition and preimages.',
        'Register no new privilege, group, network access or broad writable path.',
        'This inventory is code provenance, never execution authority or deployment approval.'
    ]},indent=2,sort_keys=True)+'\n')
print(json.dumps({'entries':len(manifest),'manifestSha256':hashlib.sha256((HERE/'broker-code-manifest.json').read_bytes()).hexdigest()}))
