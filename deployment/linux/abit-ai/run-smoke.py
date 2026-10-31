"""Start only the separate migration smoke copy on loopback."""
from pathlib import Path
import json,os,sys
ROOT=Path("/srv/abit-dev-linux/abit-ai-migration-20261031")
verification=json.loads((ROOT/"staging/verification.json").read_text())
assert verification["snapshotVerified"] and verification["backgroundWorkDisabledInSmokeCopy"]
source=ROOT/"staging/payload/config"
for name,value in json.loads((source/"runtime-environment.json").read_text()).items():
    os.environ[name]=str(value)
os.environ.update({
    "DATA_DIR":str(ROOT/"staging/smoke-data"),
    "DATABASE_URL":"sqlite:///"+str(ROOT/"staging/smoke-data/webui.db"),
    "WEBUI_SECRET_KEY":(source/"webui-secret-key").read_text().strip(),
    "FROM_INIT_PY":"true","HOST":"127.0.0.1","PORT":"18888",
    "ABIT_CHATGPT_RELAY_URL":"http://127.0.0.1:8810",
    "ABIT_CHATGPT_RELAY_TOKEN_FILE":"/srv/abit-dev-linux/relay-runtime/secrets/relay.token",
    "GLOBAL_LOG_LEVEL":"WARNING",
    "HF_HOME":str(ROOT/"staging/smoke-data/cache/huggingface"),
    "TORCH_HOME":str(ROOT/"staging/smoke-data/cache/torch"),
})
sys.path.insert(0,str(ROOT/"staging/runtime"))
import uvicorn
uvicorn.run("open_webui.main:app",host="127.0.0.1",port=18888,log_level="warning",access_log=False)
