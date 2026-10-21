$ErrorActionPreference = 'Stop'
$env:ABIT_ERP_CHATGPT_RELAY_HOST = '127.0.0.1'
$env:ABIT_ERP_CHATGPT_RELAY_PORT = '8808'
$env:ABIT_ERP_CHATGPT_CDP_URL = 'http://127.0.0.1:9227'
$env:ABIT_ERP_CHATGPT_RELAY_TOKEN = (Get-Content -LiteralPath 'C:\ProgramData\ABiT\erp-chatgpt-relay.token' -Raw).Trim()
$env:ABIT_ERP_CHATGPT_WINDOW_NAME = 'AIBIT_CHATGPT_RELAY'
$env:ABIT_ERP_CHATGPT_TARGET_FILE = 'C:\ProgramData\ABiT\OpenWebUI\aibit-relay-target-id.txt'
$env:ABIT_ERP_CHATGPT_PROJECT_CACHE = 'C:\ProgramData\ABiT\OpenWebUI\aibit-relay-projects.json'
$env:ABIT_ERP_CHATGPT_RATE_LIMIT_STATE_FILE = 'C:\ProgramData\ABiT\OpenWebUI\aibit-relay-rate-limit.json'
$env:ABIT_SHARED_CHATGPT_LOCK_FILE = 'C:\ProgramData\ABiT\SharedChatGPT\browser.lock'
$env:BIGRRO_SECONDARY_LISTEN_PORT = '0'
Remove-Item Env:CHATGPT_EXISTING_CHROME_USER_DATA_DIR -ErrorAction SilentlyContinue
& 'C:\Program Files\nodejs\node.exe' (Join-Path $PSScriptRoot 'aibit_relay.cjs')
exit $LASTEXITCODE
