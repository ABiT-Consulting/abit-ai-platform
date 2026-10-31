"""Stage permanent paths without installing a data directory or activating production."""
from pathlib import Path
import hashlib,json,os,shutil,socket
STAGE=Path("/srv/abit-dev-linux/abit-ai-migration-20261031")
ROOT=Path("/srv/abit-dev-linux/abit-ai")
assert json.loads((STAGE/"staging/verification.json").read_text())["snapshotVerified"]
with socket.socket() as sock:
    if sock.connect_ex(("127.0.0.1",18888))==0:raise RuntimeError("Stop the bounded smoke app before relocating its environment.")
release=ROOT/"releases/20261031-live-0.11.4"
if release.exists() or (ROOT/"venv").exists() or (ROOT/"current").exists():raise RuntimeError("Permanent layout already exists; refusing replacement.")
os.umask(0o077)
for name in ["config","deployment","releases"]:(ROOT/name).mkdir(mode=0o700,parents=True,exist_ok=True)
def copy_contents(source,destination):
    destination.mkdir(mode=0o700,parents=True,exist_ok=True)
    for base,dirs,files in os.walk(source):
        out=destination/Path(base).relative_to(source)
        out.mkdir(mode=0o700,parents=True,exist_ok=True)
        for name in files:
            p=Path(base)/name
            if p.is_symlink():raise RuntimeError("Unexpected runtime symlink")
            shutil.copyfile(p,out/name)
copy_contents(STAGE/"staging/runtime",release/"runtime")
for name in ["runtime-environment.json","webui-secret-key","cloudflare-tunnel.token"]:
    target=ROOT/"config"/name
    if target.exists():raise RuntimeError("Existing private configuration")
    shutil.copyfile(STAGE/"staging/payload/config"/name,target)
    target.chmod(0o600)
copy_contents(STAGE/"deployment",ROOT/"deployment")
os.rename(STAGE/"venv",ROOT/"venv")
(STAGE/"venv").symlink_to(ROOT/"venv",target_is_directory=True)
(ROOT/"current").symlink_to("releases/20261031-live-0.11.4",target_is_directory=True)
provenance={"runtimeVersion":"0.11.4","sourceBaseline":"b2e892d1bf1f76a6efa645d970b38f1ec26fc7b2","liveRuntimePreserved":True,"portabilityChanges":["Linux PAM validation dispatch","configurable relay token file"],"productionActivated":False,"finalSnapshotInstalled":False}
(release/"provenance.json").write_text(json.dumps(provenance,indent=2))
assert not (ROOT/"config/final-snapshot.json").exists()
assert not (ROOT/"data").exists()
print(json.dumps({"permanentRoot":str(ROOT),"release":str(release),"finalDataInstalled":False,"productionStarted":False}))
