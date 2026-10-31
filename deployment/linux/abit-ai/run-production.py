"""Production launcher. A staging snapshot cannot activate the production service."""
from pathlib import Path
import json,os,sys
ROOT=Path("/srv/abit-dev-linux/abit-ai")
marker=ROOT/"config/final-snapshot.json"
if not marker.is_file() or json.loads(marker.read_text()).get("snapshotType")!="quiesced-final-cutover":
    raise SystemExit("A verified final snapshot with the Windows writers stopped is required.")
if not (ROOT/"data/webui.db").is_file():
    raise SystemExit("The existing production database has not been installed.")
if not Path("/etc/pam.d/abit-ai").is_file():
    raise SystemExit("The dedicated Linux Active Directory PAM service is not installed.")
config=ROOT/"config"
for name,value in json.loads((config/"runtime-environment.json").read_text()).items():
    os.environ[name]=str(value)
os.environ.update({
    "DATA_DIR":str(ROOT/"data"),"DATABASE_URL":"sqlite:///"+str(ROOT/"data/webui.db"),
    "WEBUI_SECRET_KEY":(config/"webui-secret-key").read_text().strip(),
    "FROM_INIT_PY":"true","HOST":"127.0.0.1","PORT":"8888",
    "ABIT_CHATGPT_RELAY_URL":"http://127.0.0.1:8810",
    "ABIT_CHATGPT_RELAY_TOKEN_FILE":"/srv/abit-dev-linux/relay-runtime/secrets/relay.token",
    "HF_HOME":str(ROOT/"data/cache/huggingface"),"TORCH_HOME":str(ROOT/"data/cache/torch"),
})
sys.path.insert(0,str(ROOT/"current/runtime"))
import uvicorn
uvicorn.run("open_webui.main:app",host="127.0.0.1",port=8888,log_level="info")
