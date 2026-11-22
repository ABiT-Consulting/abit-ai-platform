"""Compose an isolated source-only API gate from retained accepted inputs."""
from pathlib import Path
import hashlib,json,os,shutil,subprocess
ROOT=Path(__file__).resolve().parents[3]
E=Path(__file__).resolve().parent
OLD=Path("/srv/abit-dev-linux/tmp/native-verification-binding-20261122/composed-api")
H=Path("/srv/abit-dev-linux/tmp/native-broker-v5-20261122/composed-api")
manifest=ROOT/"docs/evidence/2026-11-22-native-verification-binding/composed-source-20261122T160555971964Z.json"
sha=lambda b:hashlib.sha256(b).hexdigest()
inputs=json.loads(manifest.read_bytes())
assert not H.exists()
H.mkdir(parents=True)
for rel,h in sorted(inputs.items()):
 path=Path(rel)
 assert not path.is_absolute() and ".."not in path.parts and path.parts[:2]==("backend","open_webui")
 raw=(OLD/path).read_bytes()
 assert sha(raw)==h,rel
 dest=H/path;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw)
for name in ("scripts","src"):
 shutil.copytree(OLD/name,H/name,ignore=shutil.ignore_patterns("__pycache__","*.pyc"))
shutil.copytree(OLD/"docs",H/"docs",ignore=shutil.ignore_patterns("evidence"))
shutil.copytree(OLD/"backend/tests",H/"backend/tests",ignore=shutil.ignore_patterns("__pycache__","*.pyc"))
for name in ("package.json","package-lock.json","svelte.config.js","tsconfig.json","tsconfig.developers.json","vite.config.ts","vite.developers.config.ts","postcss.config.js","tailwind.config.js","AGENTS.md"):
 shutil.copyfile(OLD/name,H/name)
shutil.copyfile(ROOT/"backend/requirements.txt",H/"backend/requirements.txt")
shutil.copytree(OLD/".svelte-kit",H/".svelte-kit",ignore=shutil.ignore_patterns("output"))
(H/"node_modules").symlink_to((OLD/"node_modules").resolve(),target_is_directory=True)
targets={"backend/open_webui/utils/agentic_native_broker.py":"702568f7f12ce420dbac407736cef3081cb8ac5f8869c7cf33c21d2aff348eb4",
         "backend/open_webui/utils/agentic_native_contract.py":"44c1f5c7118be8235427da695a51a3066962207d8772ff9a318c60d641f63b3b"}
for target,expected in targets.items():
 assert target in inputs and sha((H/target).read_bytes())==expected
 (H/target).write_bytes((ROOT/target).read_bytes())
after={rel:sha((H/rel).read_bytes())for rel in inputs}
assert {rel for rel in inputs if inputs[rel]!=after[rel]}==set(targets)
subprocess.run(["git","init","--quiet",str(H)],check=True)
(E/"api-source-manifest.json").write_text(json.dumps(after,sort_keys=True,indent=2)+"\n")
(E/"api-composition.json").write_text(json.dumps({"schema_version":1,"retained_source_manifest_sha256":sha(manifest.read_bytes()),"retained_source_count":len(inputs),"changed_source":list(targets),"candidate_payload_sha256":{name:after[name] for name in targets},"production_data_copied":False,"production_configuration_copied":False,"retained_harness_mutations":0},sort_keys=True,indent=2)+"\n")
print(json.dumps({"harness":str(H),"source_files":len(after),"changed_source":list(targets)}))
