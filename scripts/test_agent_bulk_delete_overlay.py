"""Synthetic browser acceptance for the additive legacy Agent map overlay.

The generated bundle runs against observed legacy DOM hooks and a synthetic
localhost API. This never loads a live account, database or production page.
"""
from functools import partial
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import hashlib
import json
from pathlib import Path
import re
import threading
import traceback
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "deployment" / "bulk-agent-delete-20261122"
BUNDLE = EVIDENCE / "additive" / "agent-bulk-delete-20261122.js"
EVIDENCE.mkdir(parents=True, exist_ok=True)
if not BUNDLE.is_file():
    raise SystemExit("Build the additive source first; generated bundle is missing.")
bundle_bytes = BUNDLE.read_bytes()
harness_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
manifest_path = BUNDLE.parent / "source-manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
bundle_sha = hashlib.sha256(bundle_bytes).hexdigest()
if manifest["asset"]["sha256"].lower() != bundle_sha:
    raise SystemExit("Generated bundle does not match its build manifest.")
source_sha = {}
for entry in manifest["source_files"]:
    target = (ROOT / entry["path"]).resolve()
    if not target.is_relative_to(ROOT.resolve()):
        raise SystemExit("Manifest source path escapes the repository.")
    actual = hashlib.sha256(target.read_bytes()).hexdigest()
    if actual != entry["sha256"].lower():
        raise SystemExit("Compiled source drift: " + entry["path"])
    source_sha[entry["path"]] = actual
build_manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()

HTML = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Legacy Agent map additive acceptance fixture</title>
<style>
*{box-sizing:border-box}html{font:14px Arial,sans-serif;color:#111827;background:#fff}body{margin:0}
html.dark{color-scheme:dark;color:#e5e7eb;background:#171717}
button,input,select{font:inherit}button{cursor:pointer;min-height:36px}button:disabled{cursor:default}
.fixture-shell{padding:20px;max-width:1400px;margin:auto}.fixture-nav{display:flex;gap:8px;margin-bottom:18px}
.map-toolbar{display:flex;gap:10px;flex-wrap:wrap;margin:16px 0}.map-toolbar input{flex:1;min-width:130px}
.map-toolbar input,.map-toolbar select,.map-toolbar button{border:1px solid #9ca3af;border-radius:8px;padding:9px}
.legacy-card-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,290px),1fr));gap:16px}
.agent-card{position:relative;border:1px solid #cbd5e1;padding:16px;border-radius:14px;min-height:210px;background:#f8fafc}
.dark .agent-card{background:#181d27;border-color:#374151}.agent-name{display:block;margin:12px 0;color:inherit;font-weight:700}
.agent-card p{font-size:12px;color:#64748b}.agent-card button{margin-top:12px}.status{font-size:12px}
@media(max-width:480px){.fixture-shell{padding:10px}.fixture-nav{flex-wrap:wrap}.map-toolbar{gap:6px}}
</style></head><body>
<div class="fixture-shell">
 <div class="fixture-nav" aria-label="Agentic Governance view">
  <button type="button" id="map-view" aria-pressed="true">Agent map</button>
  <button type="button" id="list-view" aria-pressed="false">Manage agents</button>
  <a id="navigate-away" href="/chat">Open another page</a>
 </div>
 <div id="native-surface"></div>
</div>
<script>
(() => {
const ids={
 a:'00000000-0000-4000-8000-000000000001', b:'00000000-0000-4000-8000-000000000002',
 c:'00000000-0000-4000-8000-000000000003', d:'00000000-0000-4000-8000-000000000004',
 e:'00000000-0000-4000-8000-000000000005', n:'00000000-0000-4000-8000-000000000006'
};
function makeAgent(id,name,status='idle',context='alpha') {
 return {id,name,status,context,meta:{},last_run:null,data:{model_id:'fixture-model',rrule:'FREQ=DAILY'},is_active:status!=='disabled'};
}
const f=window.legacyFixture={
 ids,makeAgent,agents:[
 makeAgent(ids.a,'Alpha Planner'),makeAgent(ids.b,'Alpha Developer','paused'),
 makeAgent(ids.c,'Beta Cleanup','disabled','beta'),makeAgent(ids.d,'Running Worker','running','beta'),
 makeAgent(ids.e,'Unknown Worker','unknown','beta')],
 requests:[],deletes:[],failures:{},preflightFailures:{},serverRuns:{},mismatchIds:{},
 pending:{},activeDeletes:0,maxActiveDeletes:0,refreshes:0,refreshResponses:0,
 delayNextRefresh:false,pendingRefresh:null,duplicateId:null,nativePlays:[],view:'map',
 release(id){const fn=this.pending[id];delete this.pending[id];if(fn)fn();},
 releaseRefresh(){const fn=this.pendingRefresh;this.pendingRefresh=null;if(fn)fn();}
};
localStorage.token='isolated-overlay-fixture-token';
let renderedAgents=[];
function addNativeToolbar(){
 const map=document.querySelector('[data-testid="context-map"]');if(!map)return;
 const node=document.createElement('div');node.dataset.testid='bulk-agent-actions';node.id='fixture-native-bulk';
 node.textContent='Existing native bulk toolbar';map.append(node);
}
f.addNativeToolbar=addNativeToolbar;
function renderCards(snapshot) {
 if(f.view!=='map')return;
 renderedAgents=snapshot;
 const grid=document.querySelector('.legacy-card-grid');if(!grid)return;
 const query=(document.querySelector('[aria-label="Search agents"]').value||'').toLowerCase();
 const context=document.querySelector('[aria-label="Select context"]').value;
 const shown=snapshot.filter(a=>(!query||a.name.toLowerCase().includes(query))&&(context==='all'||a.context===context));
 grid.replaceChildren();
 for(const a of shown) {
  const card=document.createElement('article');card.className='agent-card';card.dataset.agentStatus=a.status;
  card.dataset.agentId='automation:'+a.id;
  const status=document.createElement('span');status.className='status';status.textContent=a.status;
  const link=document.createElement('a');link.className='agent-name';link.href='/automations/'+a.id;link.textContent=a.name;
  const model=document.createElement('p');model.textContent='fixture-model';
  const action=document.createElement('button');action.type='button';action.textContent='Play '+a.name;
  action.addEventListener('click',()=>f.nativePlays.push(a.id));
  const top=document.createElement('div');top.className='agent-top';top.append(status);
  card.append(top,link,model,action);grid.append(card);
  if(f.duplicateId===a.id)grid.append(card.cloneNode(true));
 }
}
function nativeRefresh() {
 f.refreshes++;
 const snapshot=structuredClone(f.agents);
 const apply=()=>{f.refreshResponses++;renderCards(snapshot);};
 if(f.delayNextRefresh){f.delayNextRefresh=false;f.pendingRefresh=apply;return;}
 apply();
}
f.refresh=nativeRefresh;
function renderMap() {
 const surface=document.getElementById('native-surface');
 const map=document.createElement('section');map.dataset.testid='context-map';
 map.innerHTML='<h1>Agentic Governance</h1><p>Scheduled agents and their activity.</p><div class="map-toolbar"><input aria-label="Search agents" placeholder="Search agents"><select aria-label="Select context"><option value="all">All contexts</option><option value="alpha">Alpha Project</option><option value="beta">Beta Project</option></select><select aria-label="Stale threshold"><option value="24">24 hours</option><option value="168">7 days</option></select><button type="button">Refresh</button></div><div class="legacy-card-grid"></div>';
 surface.replaceChildren(map);
 map.querySelector('input').addEventListener('input',()=>renderCards(renderedAgents));
 map.querySelector('[aria-label="Select context"]').addEventListener('change',()=>renderCards(renderedAgents));
 map.querySelector('[aria-label="Stale threshold"]').addEventListener('change',nativeRefresh);
 map.querySelector('.map-toolbar button').addEventListener('click',nativeRefresh);
 if(new URLSearchParams(location.search).get('native')==='1')addNativeToolbar();
 nativeRefresh();
}
function changeView(view) {
 f.view=view;
 document.getElementById('map-view').setAttribute('aria-pressed',String(view==='map'));
 document.getElementById('list-view').setAttribute('aria-pressed',String(view==='list'));
 if(view==='map')renderMap();
 else document.getElementById('native-surface').innerHTML='<section id="legacy-list"><h2>Manage agents</h2><p>Legacy management list remains unchanged.</p></section>';
}
document.getElementById('map-view').addEventListener('click',()=>changeView('map'));
document.getElementById('list-view').addEventListener('click',()=>changeView('list'));
document.getElementById('navigate-away').addEventListener('click',event=>{
 event.preventDefault();
 history.pushState({},'', '/chat');
 document.getElementById('native-surface').innerHTML='<h2>Other page</h2>';
});
f.changeView=changeView;
f.goAway=()=>{history.pushState({},'', '/chat');document.getElementById('native-surface').innerHTML='<h2>Other page</h2>';};
f.goBack=()=>{history.pushState({},'', '/automations');changeView('map');};
window.fetch=async(input,options={})=>{
 const url=new URL(String(input),location.origin);
 if(url.origin!==location.origin||!url.pathname.startsWith('/api/v1/automations/'))throw new Error('Fixture blocked unexpected network: '+url.href);
 const method=options.method||'GET';f.requests.push({path:url.pathname,search:url.search,method});
 const headers=new Headers(options.headers);
 if(headers.get('authorization')!=='Bearer isolated-overlay-fixture-token')throw new Error('Fixture authorization header missing');
 const pieces=url.pathname.split('/');const id=decodeURIComponent(pieces[4]||'');
 if(method==='GET'&&pieces[5]==='runs')return Response.json(f.serverRuns[id]||[]);
 if(method==='GET'&&pieces.length===5){
  if(f.preflightFailures[id]==='network')throw new TypeError('Synthetic preflight interruption');
  if(f.preflightFailures[id]==='403')return Response.json({detail:'Fixture preflight denied'},{status:403});
  const a=f.agents.find(a=>a.id===id);
  if(!a)return Response.json({detail:'Fixture agent missing'},{status:404});
  return Response.json({...a,id:f.mismatchIds[id]||id,user_id:'fixture-owner'});
 }
 if(method==='DELETE'&&pieces[5]==='delete'){
  f.deletes.push(id);f.activeDeletes++;f.maxActiveDeletes=Math.max(f.maxActiveDeletes,f.activeDeletes);
  try{
   const failure=f.failures[id];
   if(failure==='delay')await new Promise(resolve=>f.pending[id]=resolve);
   if(failure==='network')throw new TypeError('Synthetic delete interruption');
   if(failure==='403')return Response.json({detail:'Permission denied for fixture agent'},{status:403});
   if(failure==='409')return Response.json({detail:'Agent has active or unconfirmed delegated work'},{status:409});
   if(failure==='false')return Response.json(false);
   if(failure==='null')return Response.json(null);
   if(!f.agents.some(a=>a.id===id))return Response.json({detail:'Fixture agent missing'},{status:404});
   f.agents=f.agents.filter(a=>a.id!==id);
   return Response.json(true);
  }finally{f.activeDeletes--;}
 }
 throw new Error('Unimplemented synthetic route: '+method+' '+url.pathname);
};
renderMap();
})();
</script><script src="/overlay.js"></script></body></html>
'''

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        pathname=urlparse(self.path).path
        if pathname=="/overlay.js":
            body=bundle_bytes;mime="text/javascript"
        elif pathname in ("/automations","/automations/","/chat"):
            body=HTML.encode("utf-8");mime="text/html; charset=utf-8"
        elif pathname=="/favicon.ico":
            self.send_response(204);self.end_headers();return
        else:
            self.send_response(404);self.end_headers();return
        self.send_response(200);self.send_header("Content-Type",mime)
        self.send_header("Content-Length",str(len(body)));self.end_headers();self.wfile.write(body)
    def log_message(self,*_):
        pass

server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
origin=f"http://127.0.0.1:{server.server_port}"
results=[]
try:
 with sync_playwright() as p:
  browser=p.chromium.launch(headless=True,channel="chrome")
  page=browser.new_page(viewport={"width":1280,"height":900})
  page.set_default_timeout(10000)
  errors=[];blocked=[]
  page.on("pageerror",lambda error:errors.append(str(error)))
  def network(route):
   if urlparse(route.request.url).netloc==urlparse(origin).netloc:route.continue_()
   else:blocked.append(route.request.url);route.abort()
  page.route("**/*",network)
  def reset(query=""):
   errors.clear()
   page.set_viewport_size({"width":1280,"height":900})
   page.goto(origin+"/automations"+query)
   page.wait_for_function("Boolean(window.__abitAgentBulkDelete20261122 && document.getElementById('abit-bulk-delete-style-20261122'))")
   if "native=1" not in query:expect(page.locator("[data-abit-bulk-root]")).to_be_visible()
  def check(name):return page.get_by_role("checkbox",name="Select agent "+name,exact=True)
  def toolbar():return page.locator("[data-abit-bulk-root]")
  def count():return page.get_by_test_id("bulk-agent-selected-count")
  def master():return page.get_by_test_id("bulk-agent-select-all")
  def delete_button():return page.get_by_test_id("bulk-agent-delete")
  def pending():return page.locator("dialog#abit-bulk-delete-confirm")
  def open_dialog():
   delete_button().click();expect(pending()).to_be_visible();return pending()
  def confirm():page.get_by_test_id("bulk-agent-confirm").click()
  def cancel():page.get_by_test_id("bulk-agent-cancel").click()
  def deletes():return page.evaluate("window.legacyFixture.deletes")
  def ids():return page.evaluate("window.legacyFixture.ids")
  def settled():expect(delete_button()).not_to_have_text("Deleting…")
  def no_errors():
   assert not errors,errors
   assert not blocked,blocked
  def run(name,test):
   try:
    test();no_errors();results.append({"name":name,"status":"passed"});print("PASS:",name,flush=True)
   except Exception as exc:
    results.append({"name":name,"status":"failed","error":str(exc),"traceback":traceback.format_exc()})
    print("FAIL:",name,"-",str(exc),flush=True)
    page.screenshot(path=str(EVIDENCE/("additive-browser-failure-"+re.sub(r"[^a-z0-9]+","-",name.lower())[:55]+".png")))
    page.evaluate("if(window.legacyFixture){for(const id of Object.keys(legacyFixture.pending))legacyFixture.release(id);legacyFixture.releaseRefresh();}")

  def selection_and_exclusions():
   reset();expect(delete_button()).to_be_disabled()
   expect(check("Running Worker")).to_be_disabled();expect(check("Unknown Worker")).to_be_disabled()
   master().check();expect(count()).to_contain_text("3")
   expect(check("Alpha Planner")).to_be_checked();expect(check("Beta Cleanup")).to_be_checked()
   assert not check("Running Worker").is_checked() and not check("Unknown Worker").is_checked()
   page.get_by_test_id("bulk-agent-clear").click();expect(delete_button()).to_be_disabled()
   assert deletes()==[]
  run("Selection, clear and running/unknown legacy status exclusion",selection_and_exclusions)

  def filtered_selection():
   reset();page.get_by_label("Search agents",exact=True).fill("Alpha")
   master().check();expect(count()).to_contain_text("2")
   page.get_by_label("Search agents",exact=True).fill("Beta")
   expect(delete_button()).to_be_disabled();expect(check("Beta Cleanup")).not_to_be_checked()
   page.get_by_label("Search agents",exact=True).fill("")
   page.get_by_label("Select context",exact=True).select_option("alpha")
   master().check();expect(count()).to_contain_text("2")
   page.get_by_label("Select context",exact=True).select_option("beta");expect(delete_button()).to_be_disabled()
   page.get_by_label("Search agents",exact=True).fill("no such fixture");expect(master()).to_be_disabled()
   assert deletes()==[]
  run("Search/context filtering bounds selection and clears invisible targets",filtered_selection)

  def cancel_and_focus():
   reset();check("Alpha Planner").check();open_dialog()
   expect(page.get_by_test_id("bulk-agent-cancel")).to_be_focused()
   expect(pending()).to_contain_text(re.compile("run history",re.I))
   expect(pending()).to_contain_text(re.compile("cannot be undone|permanent",re.I))
   cancel();expect(pending()).not_to_be_visible();assert deletes()==[]
   open_dialog();page.keyboard.press("Escape");expect(pending()).not_to_be_visible();assert deletes()==[]
   expect(check("Alpha Planner")).to_be_checked()
  run("Cancel focus, cancellation and Escape never delete",cancel_and_focus)

  def exact_once():
   reset();ident=ids();check("Alpha Planner").check();check("Beta Cleanup").check()
   open_dialog();confirm()
   page.wait_for_function("legacyFixture.deletes.length===2 && legacyFixture.activeDeletes===0")
   expect(page.get_by_test_id("bulk-agent-result")).to_contain_text("2")
   assert sorted(deletes())==sorted([ident["a"],ident["c"]])
   expect(check("Alpha Developer")).to_be_visible()
   assert page.evaluate("legacyFixture.agents.some(a=>a.id===legacyFixture.ids.b)")
  run("Exact selected IDs deleted once; unselected agents preserved",exact_once)

  def new_arrivals():
   reset();master().check()
   page.evaluate("legacyFixture.agents.push(legacyFixture.makeAgent(legacyFixture.ids.n,'New Arriving Agent'));legacyFixture.refresh()")
   expect(check("New Arriving Agent")).to_be_visible();expect(check("New Arriving Agent")).not_to_be_checked()
   expect(count()).to_contain_text("3");open_dialog()
   expect(pending()).not_to_contain_text("New Arriving Agent");confirm()
   page.wait_for_function("legacyFixture.deletes.length===3 && legacyFixture.activeDeletes===0")
   expect(check("New Arriving Agent")).to_be_visible()
  run("Live arrivals do not expand existing selection",new_arrivals)

  def immutable_snapshot():
   reset();check("Alpha Planner").check();open_dialog()
   page.evaluate("legacyFixture.agents.find(a=>a.id===legacyFixture.ids.a).name='Changed Name';legacyFixture.agents.push(legacyFixture.makeAgent(legacyFixture.ids.n,'New Arriving Agent'));legacyFixture.refresh()")
   expect(pending()).to_contain_text("Alpha Planner");expect(pending()).not_to_contain_text("New Arriving Agent")
   confirm();page.wait_for_function("legacyFixture.deletes.length===1 && legacyFixture.activeDeletes===0")
   assert deletes()==[ids()["a"]]
  run("Dialog snapshot remains exact through concurrent map updates",immutable_snapshot)

  def duplicate_pending():
   reset();page.evaluate("legacyFixture.failures[legacyFixture.ids.a]='delay'")
   check("Alpha Planner").check();open_dialog();confirm()
   page.wait_for_function("Boolean(legacyFixture.pending[legacyFixture.ids.a])")
   expect(delete_button()).to_be_disabled();delete_button().dispatch_event("click")
   assert deletes()==[ids()["a"]]
   page.evaluate("legacyFixture.release(legacyFixture.ids.a)")
   page.wait_for_function("legacyFixture.activeDeletes===0")
   expect(page.get_by_test_id("bulk-agent-result")).to_contain_text("1")
   assert deletes()==[ids()["a"]]
  run("Pending batch prevents duplicate confirmation/submission",duplicate_pending)

  def partial_retry():
   reset();page.evaluate("legacyFixture.failures[legacyFixture.ids.b]='403';legacyFixture.failures[legacyFixture.ids.c]='false'")
   master().check();open_dialog();confirm()
   page.wait_for_function("legacyFixture.deletes.length===3 && legacyFixture.activeDeletes===0")
   expect(page.get_by_test_id("bulk-agent-failures")).to_be_visible()
   expect(check("Alpha Developer")).to_be_checked();expect(check("Beta Cleanup")).to_be_checked()
   expect(count()).to_contain_text("2")
   page.evaluate("legacyFixture.failures={}")
   open_dialog();confirm();page.wait_for_function("legacyFixture.deletes.length===5 && legacyFixture.activeDeletes===0")
   ident=ids();actual=deletes()
   assert actual.count(ident["a"])==1 and actual.count(ident["b"])==2 and actual.count(ident["c"])==2
  run("Partial failures remain selected and retry excludes successful targets",partial_retry)

  def ambiguity():
   reset();page.evaluate("legacyFixture.failures[legacyFixture.ids.a]='network';legacyFixture.failures[legacyFixture.ids.b]='null'")
   check("Alpha Planner").check();check("Alpha Developer").check();open_dialog();confirm()
   page.wait_for_function("legacyFixture.deletes.length===2 && legacyFixture.activeDeletes===0")
   expect(check("Alpha Planner")).to_be_checked();expect(check("Alpha Developer")).to_be_checked()
   expect(page.get_by_test_id("bulk-agent-result")).to_contain_text("0")
   assert page.evaluate("legacyFixture.agents.length")==5
  run("Network interruptions and null replies never count as success",ambiguity)

  def active_preflight():
   for status in ["running","pending","queued","handoff"]:
    reset();page.evaluate("(status)=>legacyFixture.serverRuns[legacyFixture.ids.a]=[{id:'fixture-run',status}]",status)
    check("Alpha Planner").check();open_dialog();confirm()
    expect(page.get_by_test_id("bulk-agent-failures")).to_be_visible()
    assert deletes()==[],status
  run("Fresh run preflight denies running/pending/queued/handoff work",active_preflight)

  def protected_metadata():
   reset();page.evaluate("legacyFixture.agents.find(a=>a.id===legacyFixture.ids.a).meta={governance_managed:true}")
   check("Alpha Planner").check();open_dialog();confirm()
   expect(page.get_by_test_id("bulk-agent-failures")).to_be_visible();assert deletes()==[]
  run("Fresh protected-agent metadata blocks deletion",protected_metadata)

  def conflict409():
   reset();page.evaluate("legacyFixture.failures[legacyFixture.ids.a]='409'")
   check("Alpha Planner").check();open_dialog();confirm()
   page.wait_for_function("legacyFixture.deletes.length===1 && legacyFixture.activeDeletes===0")
   expect(page.get_by_test_id("bulk-agent-failures")).to_contain_text("Alpha Planner")
   expect(check("Alpha Planner")).to_be_checked()
   assert page.evaluate("legacyFixture.agents.length")==5
  run("Backend 409 is retained and reported without automatic retry",conflict409)

  def duplicate_cards():
   reset();page.evaluate("legacyFixture.duplicateId=legacyFixture.ids.a;legacyFixture.refresh()")
   expect(page.locator('article.agent-card a.agent-name').filter(has_text="Alpha Planner")).to_have_count(2)
   master().check();expect(count()).to_contain_text("3");open_dialog();confirm()
   page.wait_for_function("legacyFixture.deletes.length===3 && legacyFixture.activeDeletes===0")
   assert len(set(deletes()))==3 and deletes().count(ids()["a"])==1
  run("Duplicate card representations produce one target and one delete",duplicate_cards)

  def invalid_identity():
   reset()
   page.evaluate("""() => {
    const grid=document.querySelector('.legacy-card-grid');
    const invalid=[
     ['Foreign Agent','https://other.invalid/automations/'+legacyFixture.ids.a],
     ['Nested Agent','/automations/'+legacyFixture.ids.a+'/runs'],
     ['Invalid ID Agent','/automations/not-an-agent-id'],
     ['Query Agent','/automations/'+legacyFixture.ids.a+'?unexpected=1']
    ];
    for(const [name,href] of invalid){
     const card=document.createElement('article');card.className='agent-card';card.dataset.agentStatus='idle';
     const link=document.createElement('a');link.className='agent-name';link.href=href;link.textContent=name;
     card.append(link);grid.append(card);
    }
   }""")
   master().check();expect(count()).to_contain_text("3");open_dialog()
   for name in ["Foreign Agent","Nested Agent","Invalid ID Agent","Query Agent"]:
    expect(check(name)).to_have_count(0);expect(pending()).not_to_contain_text(name)
   cancel();assert deletes()==[]
  run("Foreign, nested, malformed and query-bearing links never become deletion targets",invalid_identity)

  def native_deferral():
   reset("?native=1");expect(page.locator("#fixture-native-bulk")).to_be_visible()
   expect(toolbar()).to_have_count(0);expect(page.locator("[data-abit-bulk-select]")).to_have_count(0)
   assert deletes()==[]
  run("Existing native bulk toolbar causes a complete no-op",native_deferral)

  def native_arrival_cleanup():
   reset();check("Alpha Planner").check();page.evaluate("legacyFixture.addNativeToolbar()")
   expect(toolbar()).to_have_count(0);expect(page.locator("[data-abit-bulk-select]")).to_have_count(0)
   expect(page.locator("#fixture-native-bulk")).to_be_visible()
   assert deletes()==[]
  run("Arriving native bulk UI cleans up additive controls",native_arrival_cleanup)

  def view_switch():
   reset();check("Alpha Planner").check()
   page.get_by_role("button",name="Manage agents",exact=True).click()
   expect(toolbar()).to_have_count(0);expect(page.locator("#legacy-list")).to_be_visible()
   page.get_by_role("button",name="Agent map",exact=True).click()
   expect(toolbar()).to_be_visible();expect(check("Alpha Planner")).not_to_be_checked()
   expect(delete_button()).to_be_disabled();assert deletes()==[]
  run("Map/list transitions clean up and reset additive selection",view_switch)

  def stale_refresh():
   reset();page.evaluate("legacyFixture.failures[legacyFixture.ids.a]='delay'")
   check("Alpha Planner").check();open_dialog();confirm()
   page.wait_for_function("Boolean(legacyFixture.pending[legacyFixture.ids.a])")
   page.evaluate("legacyFixture.delayNextRefresh=true;legacyFixture.refresh()")
   page.wait_for_function("Boolean(legacyFixture.pendingRefresh)")
   page.evaluate("legacyFixture.release(legacyFixture.ids.a)")
   page.wait_for_function("legacyFixture.activeDeletes===0")
   expect(page.get_by_test_id("bulk-agent-result")).to_contain_text("1")
   page.evaluate("legacyFixture.releaseRefresh()")
   # A stale native card may reappear, but must remain ineligible/tombstoned.
   if check("Alpha Planner").count():expect(check("Alpha Planner")).to_be_disabled()
   master().check();open_dialog();expect(pending()).not_to_contain_text("Alpha Planner");cancel()
   assert deletes()==[ids()["a"]]
  run("Stale predelete refresh cannot reselect or replay a confirmed deletion",stale_refresh)

  def repeated_bundle():
   reset();page.add_script_tag(url=origin+"/overlay.js?second=1")
   expect(toolbar()).to_have_count(1);check("Alpha Planner").check();open_dialog();confirm()
   page.wait_for_function("legacyFixture.deletes.length===1 && legacyFixture.activeDeletes===0")
   assert deletes()==[ids()["a"]]
  run("Repeated bundle loading does not duplicate controls or handlers",repeated_bundle)

  def navigation_cleanup():
   reset();check("Alpha Planner").check();open_dialog()
   page.evaluate("legacyFixture.goAway()")
   expect(toolbar()).to_have_count(0);expect(pending()).to_have_count(0)
   expect(page.locator("[data-abit-bulk-select]")).to_have_count(0)
   page.evaluate("legacyFixture.goBack()")
   expect(toolbar()).to_have_count(1);expect(check("Alpha Planner")).not_to_be_checked()
   expect(delete_button()).to_be_disabled();assert deletes()==[]
  run("Navigation removes pending confirmation and preserves no stale selection",navigation_cleanup)

  def preflight_identity_and_errors():
   for mode in ["403","network","mismatch"]:
    reset()
    if mode=="mismatch":page.evaluate("legacyFixture.mismatchIds[legacyFixture.ids.a]=legacyFixture.ids.b")
    else:page.evaluate("(mode)=>legacyFixture.preflightFailures[legacyFixture.ids.a]=mode",mode)
    check("Alpha Planner").check();open_dialog();confirm()
    expect(page.get_by_test_id("bulk-agent-failures")).to_be_visible()
    assert deletes()==[],mode
  run("Preflight permission/network/identity failures fail closed",preflight_identity_and_errors)

  def native_controls_unchanged():
   reset();page.get_by_role("button",name="Play Alpha Planner",exact=True).click()
   assert page.evaluate("legacyFixture.nativePlays")==[ids()["a"]]
   check("Alpha Planner").focus();page.keyboard.press("Space")
   expect(check("Alpha Planner")).to_be_checked()
   assert page.url.endswith("/automations") and deletes()==[]
  run("Original card actions and native checkbox keyboard operation remain usable",native_controls_unchanged)

  def mobile_dark():
   reset();check("Alpha Planner").check();check("Beta Cleanup").check()
   toolbar().scroll_into_view_if_needed()
   page.screenshot(path=str(EVIDENCE/"additive-browser-desktop-light.png"))
   page.evaluate("document.documentElement.classList.add('dark')")
   page.screenshot(path=str(EVIDENCE/"additive-browser-desktop-dark.png"))
   for width in [320,375,430]:
    page.set_viewport_size({"width":width,"height":900})
    delete_button().scroll_into_view_if_needed();box=delete_button().bounding_box()
    assert box and box["x"]>=-1 and box["x"]+box["width"]<=width+1,(width,box)
   page.set_viewport_size({"width":375,"height":900});open_dialog()
   box=pending().bounding_box()
   assert box and box["x"]>=-1 and box["x"]+box["width"]<=376,box
   page.screenshot(path=str(EVIDENCE/"additive-browser-mobile-confirm-dark.png"))
   cancel();assert deletes()==[]
  run("Desktop light/dark and 320/375/430px controls and confirmation fit",mobile_dark)

  browser.close()
finally:
 server.shutdown();server.server_close()

report={
 "scope":"Generated additive overlay against synthetic observed-legacy DOM and localhost API; no live deployment or deletion",
 "bundle":str(BUNDLE.relative_to(ROOT)).replace("\\","/"),"bundle_sha256":bundle_sha,
 "harness_sha256":harness_sha,"source_sha256":source_sha,"build_manifest_sha256":build_manifest_sha,
 "cases":len(results),"passed":sum(r["status"]=="passed" for r in results),
 "failed":sum(r["status"]=="failed" for r in results),"results":results
}
(EVIDENCE/"additive-browser-results.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
print(json.dumps({k:report[k] for k in ["scope","bundle_sha256","cases","passed","failed"]}),flush=True)
raise SystemExit(1 if report["failed"] else 0)
