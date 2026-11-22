"""Apply this change to the instructed checkout without staging unrelated edits."""
from pathlib import Path
import difflib, hashlib, json, subprocess

ROOT=Path(__file__).resolve().parents[2]
PKG=Path(__file__).resolve().parent
fragment=(PKG/'admission-route.fragment').read_bytes()
route=ROOT/'backend/open_webui/routers/automations.py'
html=ROOT/'src/app.html'
backup=PKG/'authoritative-before'
backup.mkdir(exist_ok=True)
changes=[]
index_patches=[]

def endpoint(raw):
    if b'async def get_automation_admission(' in raw:
        return raw
    sep=b'\r\n' if b'\r\n' in raw else b'\n'
    return raw + fragment.replace(b'\n',sep)

def script(raw):
    if b'/agent-activity.js' in raw:
        return raw
    old=b'%sveltekit.head%'
    if raw.count(old)!=1:raise RuntimeError('Exact head marker missing')
    sep=b'\r\n' if b'\r\n' in raw else b'\n'
    return raw.replace(old,old+sep+b'\t\t<script defer src="%sveltekit.assets%/agent-activity.js"></script>',1)

for target,transform in [(route,endpoint),(html,script)]:
    rel=target.relative_to(ROOT).as_posix()
    raw=target.read_bytes()
    saved=backup/(rel.replace('/','__'))
    if saved.exists():
        original=saved.read_bytes()
        expected=transform(original)
        if raw!=expected:raise RuntimeError('Authoritative source drift: '+rel)
    else:
        saved.write_bytes(raw)
        original=raw
        target.write_bytes(transform(raw))
    head=subprocess.run(['git','show','HEAD:'+rel],cwd=ROOT,check=True,capture_output=True).stdout
    after=transform(head)
    index_patches.extend(difflib.unified_diff(head.decode().splitlines(keepends=True),after.decode().splitlines(keepends=True),fromfile='a/'+rel,tofile='b/'+rel))
    changes.append({'path':rel,'before_sha256':hashlib.sha256(original).hexdigest(),'after_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'unrelated_bytes_preserved':True})
(PKG/'authoritative-integration.patch').write_text(''.join(index_patches),encoding='utf-8',newline='\n')
(PKG/'authoritative-preservation.json').write_text(json.dumps(changes,indent=2)+'\n',encoding='utf-8')
print(json.dumps(changes,indent=2))
