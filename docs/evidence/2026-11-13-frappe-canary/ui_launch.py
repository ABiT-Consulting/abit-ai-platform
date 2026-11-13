"""Launch only this canary's private, outbound-blocked headless Chrome."""
from pathlib import Path
import json,os,socket,subprocess
import httpx
D=Path(__file__).resolve().parent
os.umask(0o077)
with socket.socket() as probe:
    if probe.connect_ex(('127.0.0.1',19239))==0:
        raise RuntimeError('Reserved own-browser port already in use; inspect original process before proceeding')
settings=json.loads((D/'core-private.json').read_text())
if settings['base_url']!='http://172.21.0.5:8080':raise ValueError('Only the named owned canary origin is allowed')
secret=json.loads((D/'secrets.json').read_text())
with httpx.Client(base_url=settings['base_url'],trust_env=False,follow_redirects=False,timeout=30) as client:
    response=client.post('/api/method/login',data={'usr':'Administrator','pwd':secret['admin_password']})
    response.raise_for_status();sid=client.cookies.get('sid')
    if not sid or sid=='Guest':raise ValueError('Private synthetic administrator session was not created')
private=D/'ui-private.json';private.write_text(json.dumps({'sid':sid,'base_url':settings['base_url'],'project':settings['project']}));private.chmod(0o600)
args=['/opt/google/chrome/chrome','--headless=new','--no-sandbox','--disable-dev-shm-usage',
    '--remote-debugging-address=127.0.0.1','--remote-debugging-port=19239','--user-data-dir='+str(D/'chrome-profile'),
    '--disable-background-networking','--disable-component-update','--disable-sync','--disable-default-apps',
    '--disable-extensions','--no-first-run','--proxy-server=http://127.0.0.1:1',
    '--proxy-bypass-list=172.21.0.5;127.0.0.1','about:blank']
with (D/'chrome.log').open('wb') as log:
    process=subprocess.Popen(args,stdout=log,stderr=log,start_new_session=True)
(D/'chrome.pid').write_text(str(process.pid));print('OWN_HEADLESS_CHROME_STARTED',process.pid)
