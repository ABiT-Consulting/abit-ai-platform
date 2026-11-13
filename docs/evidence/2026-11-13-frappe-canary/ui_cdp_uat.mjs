import fs from 'node:fs/promises';
import path from 'node:path';
const d=path.dirname(new URL(import.meta.url).pathname);
const auth=JSON.parse(await fs.readFile(path.join(d,'ui-private.json'),'utf8'));
const tabs=await (await fetch('http://127.0.0.1:19239/json/list')).json();
const target=tabs.find(t=>t.type==='page');
const socket=new WebSocket(target.webSocketDebuggerUrl);await new Promise((r,e)=>{socket.onopen=r;socket.onerror=e;});
let seq=0;const pending=new Map();const blocked=[];const errors=[];
const call=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params}));setTimeout(()=>{if(pending.has(id)){pending.delete(id);reject(new Error('CDP timeout '+method));}},25000).unref();});
socket.onmessage=async event=>{const v=JSON.parse(event.data);if(v.id){const p=pending.get(v.id);if(p){pending.delete(v.id);v.error?p.reject(new Error(v.error.message)):p.resolve(v.result);}return;}
  if(v.method==='Fetch.requestPaused'){const u=v.params.request.url;const allowed=u.startsWith(auth.base_url+'/')||u.startsWith('data:')||u==='about:blank';if(!allowed)blocked.push(new URL(u).origin);await call(allowed?'Fetch.continueRequest':'Fetch.failRequest',allowed?{requestId:v.params.requestId}:{requestId:v.params.requestId,errorReason:'BlockedByClient'});}
  if(v.method==='Runtime.exceptionThrown')errors.push(String(v.params.exceptionDetails.exception?.description||v.params.exceptionDetails.text).slice(0,1800));
};
await call('Page.enable');await call('Runtime.enable');await call('Network.enable');
await call('Network.setCacheDisabled',{cacheDisabled:true});
await call('Fetch.enable',{patterns:[{urlPattern:'*'}]});
await call('Emulation.setDeviceMetricsOverride',{width:1600,height:1100,deviceScaleFactor:1,mobile:false});
await call('Network.setCookie',{name:'sid',value:auth.sid,url:auth.base_url,path:'/',httpOnly:true,secure:false});
await call('Page.navigate',{url:auth.base_url+'/app/project/'+auth.project});
const evaluate=async expression=>(await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true})).result.value;
let ready=false;
for(let i=0;i<60;i++){ready=await evaluate('Boolean(window.cur_frm && cur_frm.doctype === "Project" && cur_frm.doc.name === '+JSON.stringify(auth.project)+')');if(ready)break;await new Promise(r=>setTimeout(r,500));}
const report={checks:[],at:Date.now(),own_browser_only:true,actual_models:false,blocked_external_origins:[...new Set(blocked)],runtime_core_attached:false};
if(!ready){report.passed=false;report.error=await evaluate('({url:location.href,title:document.title,text:document.body.innerText.slice(0,1500)})');}
else{
 const fields=await evaluate(`(()=>{const key='custom_ai_project_manager_task_automation_enabled', f=cur_frm.fields_dict[key];return {key,label:f?.df.label,type:f?.df.fieldtype,default:f?.df.default,no_copy:f?.df.no_copy,current:cur_frm.doc[key],governance:cur_frm.doc.custom_agentic_governance_enabled,buttons:[...document.querySelectorAll('.custom-actions button,.custom-actions a')].map(e=>e.innerText.trim()).filter(Boolean),user:frappe.session.user};})()`);
 report.fields=fields;
 report.checks.push({name:'actual_Project_form_exact_separate_PM_checkbox',passed:fields.label==='Enable AI Project Manager Task Automation'&&fields.type==='Check'&&String(fields.default)==='0'&&fields.no_copy===1});
 const clicked=await evaluate(`(()=>{const buttons=[...document.querySelectorAll('a,button')];const b=buttons.find(e=>e.textContent.trim()==='Governance dashboard');if(b){b.click();return true;}return false;})()`);
 report.checks.push({name:'actual_governance_dashboard_button_loaded',passed:clicked});
 if(clicked){let state;for(let i=0;i<60;i++){state=await evaluate(`(()=>{const v=[...document.querySelectorAll('.modal')].find(e=>e.textContent.includes('Project Agentic Governance')&&getComputedStyle(e).display!=='none');return v?{title:v.querySelector('.modal-title')?.textContent.trim(),headings:[...v.querySelectorAll('h4')].map(e=>e.textContent.trim()),actions:[...v.querySelectorAll('[data-action]')].map(e=>({action:e.dataset.action,disabled:e.disabled})),agents:[...v.querySelectorAll('table')][0]?.querySelectorAll('tbody tr').length}:null;})()`);if(state?.agents===6)break;await new Promise(r=>setTimeout(r,500));}
   report.dashboard=state;report.checks.push({name:'actual_ERP_dashboard_six_agents_and_controls',passed:state?.agents===6&&state.actions.length===5});
   for(let i=0;i<40;i++){report.runtime_message=await evaluate(`document.querySelector('.agov-runtime')?.textContent||''`);if(report.runtime_message)break;await new Promise(r=>setTimeout(r,500));}
   await new Promise(r=>setTimeout(r,500));
   report.visible_modal_titles=await evaluate(`[...document.querySelectorAll('.modal')].filter(e=>getComputedStyle(e).display!=='none').map(e=>e.querySelector('.modal-title')?.textContent.trim()).filter(Boolean)`);
   report.checks.push({name:'runtime_unavailable_renders_inline_without_raw_error_modal',passed:report.runtime_message.includes('unavailable')&&report.visible_modal_titles.length===1&&report.visible_modal_titles[0]==='Project Agentic Governance'});
 }
 report.passed=report.checks.every(x=>x.passed);
}
const shot=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});await fs.writeFile(path.join(d,'project-governance-ui.png'),Buffer.from(shot.data,'base64'));
report.javascript_exceptions=errors;await fs.writeFile(path.join(d,'ui-report.json'),JSON.stringify(report,null,2));
console.log(JSON.stringify(report,null,2));socket.close();
