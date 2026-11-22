"""Build an isolated source-only API gate input; no production data or services."""
from pathlib import Path
import hashlib,json,shutil,subprocess
ROOT=Path(__file__).resolve().parents[3]
E=Path(__file__).resolve().parent
OLD=Path("/srv/abit-dev-linux/tmp/native-overlay-held-drain-20261122/composed-api")
TARGET=Path("/srv/abit-dev-linux/tmp/native-overlay-original-finalize-20261122/composed-api")
source_map=ROOT/"docs/evidence/2026-11-22-native-overlay-held-drain/composed-source-20261122T171924159368Z.json"
expected=json.loads(source_map.read_text())
if TARGET.exists():raise RuntimeError("CREATE_ONLY_API_HARNESS")
TARGET.mkdir(parents=True)
for rel,pin in expected.items():
    if not rel.startswith("backend/open_webui/") or ".." in Path(rel).parts:raise RuntimeError("SOURCE_SCOPE")
    raw=(OLD/rel).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=pin:raise RuntimeError("RETAINED_API_SOURCE_DRIFT")
    path=TARGET/rel;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
for rel in ("scripts","src","backend/tests","docs",".svelte-kit"):
    shutil.copytree(OLD/rel,TARGET/rel,symlinks=True,ignore=shutil.ignore_patterns("__pycache__","*.pyc"))
for rel in ("package.json","package-lock.json","postcss.config.js","svelte.config.js","tailwind.config.js",
            "tsconfig.developers.json","tsconfig.json","vite.config.ts","vite.developers.config.ts",
            "backend/requirements.txt","AGENTS.md"):
    path=TARGET/rel;path.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(OLD/rel,path)
(TARGET/"node_modules").symlink_to((OLD/"node_modules").resolve(),target_is_directory=True)
holder=ROOT/"deployment/native-overlay-held-drain-20261122"
shutil.copytree(holder,TARGET/"deployment/native-overlay-held-drain-20261122")
shutil.copytree(ROOT/"deployment/native-overlay-original-finalize-20261122",TARGET/"deployment/native-overlay-original-finalize-20261122")
subprocess.run(["git","init","--quiet",str(TARGET)],check=True)
record={"schema_version":1,"source":str(TARGET),"retained_core_entries":len(expected),
        "source_map_sha256":hashlib.sha256(source_map.read_bytes()).hexdigest(),
        "controller_sources":{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in sorted(holder.rglob("*")) if p.is_file()},
        "production_data_copied":False,"external_services_disabled_by_runner":True,
        "source_only_harness":True}
(E/"api-harness-preparation.json").write_text(json.dumps(record,sort_keys=True,indent=2)+"\n")
print(json.dumps(record))
