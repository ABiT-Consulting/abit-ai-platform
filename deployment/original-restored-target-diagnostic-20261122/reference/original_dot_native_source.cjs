'use strict';
// Original-result observation only. This never submits a model message, changes
// permissions, reads private app state, repairs JSON, or creates a new run.
const crypto=require('crypto');
const {dotFeedSnapshotDOM}=require('./aibit_dot_feed.cjs');
const COPY_LABELS=['copy','copy message','copy response','copy markdown','copy as markdown'];
const ACK_LABELS=['copied','copied!','copied to clipboard','copied to clipboard!'];
// Serialized into the admitted original page; all dependencies are arguments.
async function nativeOriginalSourceDOM(expected,knownRoot,candidate,expectedUrl,snapshot,copyLabels,ackLabels) {
 const deadline=Date.now()+6500,maxBytes=250000;
 let selection={stage:'selection',copy_controls:0,copy_acknowledged:false};
 const result=(status,extra={})=>({status,selection,...extra});
 const bounded=async (fn,ms)=>{
  const remaining=Math.min(ms,deadline-Date.now());
  if(remaining<=0)throw Error('deadline');
  let timer;
  try{return await Promise.race([Promise.resolve().then(fn),new Promise((_,reject)=>{
   timer=setTimeout(()=>reject(Error('deadline')),remaining);
  })]);}finally{if(timer!==undefined)clearTimeout(timer);}
 };
 const pause=ms=>bounded(()=>new Promise(resolve=>setTimeout(resolve,ms)),ms+100);
 const label=node=>String(node.getAttribute('aria-label')||node.getAttribute('title')||node.textContent||'')
  .replace(/\s+/g,' ').trim().toLowerCase();
 const exposed=node=>{
  if(!node||typeof node.getBoundingClientRect!=='function')return false;
  const r=node.getBoundingClientRect();if(!(r.width>0&&r.height>0))return false;
  for(let current=node;current;current=current.parentElement){
   if(current.hidden||current.inert||current.getAttribute?.('aria-hidden')==='true')return false;
   const style=getComputedStyle(current);
   if(style.display==='none'||['hidden','collapse'].includes(style.visibility)||style.opacity==='0')return false;
  }return true;
 };
 function select(){
  if(Date.now()>=deadline||location.href!==expectedUrl||
   document.querySelector('[data-testid="stop-button"],button[aria-label*="Stop"],button[title*="Stop"],button[data-testid*="stop"]'))return null;
  const feed=snapshot(expected,null,knownRoot,{enabled:true});
  if(!feed?.native_source_candidate||JSON.stringify(feed.native_source_candidate)!==JSON.stringify(candidate))return null;
  const rows=Array.from(document.querySelectorAll('article.message-row[data-message-id]'));
  const roots=rows.filter(row=>row.getAttribute('data-message-id')===candidate.root_id);
  const matches=rows.filter(row=>row.getAttribute('data-message-id')===candidate.reply_id);
  if(roots.length!==1||matches.length!==1)return null;
  const root=roots[0],row=matches[0],body=row.querySelector('.message-body[data-message-id]');
  if(!body||!exposed(row)||!exposed(body))return null;
  const controls=Array.from(row.querySelectorAll('button')).filter(button=>
   exposed(button)&&button.closest('article.message-row[data-message-id]')===row&&!body.contains(button)&&
   !button.closest('pre,code,blockquote,[data-code-block],[data-artifact],[data-artifact-id],.code-block,.artifact,[data-testid*="artifact"],[data-testid*="code-block"]')&&
   [...copyLabels,...ackLabels].includes(label(button)));
  return {root,row,body,rootText:String(root.textContent||''),text:String(body.innerText||body.textContent||''),
   controls,selection:feed.selection};
 }
 function unchanged(before,control){
  const after=select();
  if(!after||after.root!==before.root||after.row!==before.row||after.body!==before.body||
   after.rootText!==before.rootText||after.text!==before.text||after.controls.length!==1||
   after.controls[0]!==control||JSON.stringify(after.selection)!==JSON.stringify(before.selection))return null;
  return after;
 }
 // Fixed metadata from the already-proven original row only. This diagnostic
 // neither selects a control nor authorizes an action; the reader above stays
 // unchanged. No names, text, selectors, IDs or other-row content leave here.
 function controlSnapshot(before){
  const nodes=Array.from(before.row.querySelectorAll('button,[role="button"]'));
  if(nodes.length>512)return null;
  const counts={buttons:0,role_buttons:0,visible:0,copy:0,ack:0,copy_visible:0,
   body:0,nested:0,foreign:0,unlabelled:0,disabled:0,accepted:before.controls.length,
   copy_body:0,copy_nested:0,copy_hidden:0,copy_opacity:0,menus:0};
  for(const node of nodes){
   if(String(node.tagName||'').toUpperCase()==='BUTTON')counts.buttons++;
   else counts.role_buttons++;
   // Count the structural exclusion without reading a nested message's name.
   if(node.closest('article.message-row[data-message-id]')!==before.row){counts.foreign++;continue;}
   const name=label(node),isCopy=copyLabels.includes(name),isVisible=exposed(node);
   const inBody=before.body.contains(node),nested=Boolean(node.closest(
    'pre,code,blockquote,[data-code-block],[data-artifact],[data-artifact-id],.code-block,.artifact,[data-testid*="artifact"],[data-testid*="code-block"]'));
   if(isVisible)counts.visible++;
   if(inBody)counts.body++;
   if(nested)counts.nested++;
   if(!name)counts.unlabelled++;
   if(node.disabled||node.getAttribute('aria-disabled')==='true')counts.disabled++;
   if(node.getAttribute('aria-haspopup')==='menu')counts.menus++;
   if(ackLabels.includes(name))counts.ack++;
   if(isCopy){
    counts.copy++;if(isVisible)counts.copy_visible++;else counts.copy_hidden++;
    if(inBody)counts.copy_body++;if(nested)counts.copy_nested++;
    for(let parent=node;parent;parent=parent.parentElement){
     if(getComputedStyle(parent).opacity==='0'){counts.copy_opacity++;break;}
    }
   }
  }
  return {nodes,counts};
 }
 async function controlFailure(before,status){
  const first=controlSnapshot(before);
  if(!first)return result(status); // Bounded diagnostic, unchanged failure.
  let state='unavailable';
  // Permissions.query observes the existing state and cannot request/grant it.
  // Never access clipboard.readText here, even when it is already granted.
  if(typeof navigator?.permissions?.query==='function')try{
   const found=await bounded(()=>navigator.permissions.query({name:'clipboard-read'}),800);
   if(['granted','prompt','denied'].includes(found?.state))state=found.state;
  }catch(_){}
  const after=select(),second=after&&controlSnapshot(after);
  if(!after||!second||after.root!==before.root||after.row!==before.row||after.body!==before.body||
   after.rootText!==before.rootText||after.text!==before.text||
   JSON.stringify(after.selection)!==JSON.stringify(before.selection)||
   first.nodes.length!==second.nodes.length||first.nodes.some((node,index)=>node!==second.nodes[index])||
   JSON.stringify(first.counts)!==JSON.stringify(second.counts))return result('selection_changed');
  selection={...selection,control_diagnostic:{...first.counts,permission:state}};
  return result(status);
 }
 async function permission(){
  if(typeof navigator?.permissions?.query!=='function'||typeof navigator?.clipboard?.readText!=='function')return 'unavailable';
  try{const found=await bounded(()=>navigator.permissions.query({name:'clipboard-read'}),800);
   return ['granted','prompt','denied'].includes(found?.state)?found.state:'unavailable';
  }catch(_){return 'unavailable';}
 }
 try{
  const before=select();
  if(!before)return result('selection_changed');
  selection={...before.selection,stage:'control',copy_controls:Math.min(65535,before.controls.length),copy_acknowledged:false};
  if(before.controls.some(control=>ackLabels.includes(label(control))))return await controlFailure(before,'ack_stale');
  if(!before.controls.length)return await controlFailure(before,'control_missing');
  if(before.controls.length!==1)return await controlFailure(before,'control_ambiguous');
  const control=before.controls[0];
  if(control.disabled||control.getAttribute('aria-disabled')==='true')return await controlFailure(before,'control_disabled');
  selection.stage='permission';
  let granted=await permission();if(granted!=='granted')return result('permission_'+granted);
  if(!unchanged(before,control)||!copyLabels.includes(label(control)))return result('selection_changed');
  // Invoke the attested message control through its ordinary .click() method.
  // This is a programmatic DOM action, not trusted OS pointer input. No crafted
  // event dispatch, private handler invocation, clipboard sentinel, permission
  // request/grant, focus/navigation or global interception is used.
  selection.stage='acknowledgement';
  control.click();
  const ackDeadline=Math.min(deadline,Date.now()+1600);
  let acknowledged=false;
  do{
   if(!unchanged(before,control))return result('selection_changed');
   if(ackLabels.includes(label(control))){acknowledged=true;break;}
   if(Date.now()>=ackDeadline)break;
   await pause(80);
  }while(Date.now()<ackDeadline);
  if(!acknowledged)return result('ack_unavailable');
  selection.copy_acknowledged=true;selection.stage='read';
  const reads=[];
  for(let i=0;i<2;i++){
   if(i)await pause(200);
   granted=await permission();if(granted!=='granted')return result('permission_'+granted);
   if(!unchanged(before,control))return result('selection_changed');
   let raw;
   try{raw=await bounded(()=>navigator.clipboard.readText(),1000);}catch(_){return result('read_unavailable');}
   if(!unchanged(before,control))return result('selection_changed');
   if(typeof raw!=='string'||!raw.length)return result('source_empty');
   if(raw.length>maxBytes||new TextEncoder().encode(raw).length>maxBytes)return result('source_oversized');
   if(new TextDecoder('utf-8',{fatal:true}).decode(new TextEncoder().encode(raw))!==raw)return result('source_unrepresentable');
   reads.push(raw);
  }
  if(reads[0]!==reads[1])return result('source_changed');
  selection.stage='validation';
  // Raw text exists only in this internal return until the exact original
  // contract is checked. A nonterminal diagnostic never contains it.
  return result('source_observed',{response:reads[0],witness:{root_text:before.rootText,body_text:before.text}});
 }catch(_){return result('unavailable');}
}
// A visible literal code block is a different source from native Copy: these
// are exact observed DOM literal bytes, never the whole provider message. The
// admitted original candidate, mutex and complete strict contract stay intact.
async function literalOriginalSourceDOM(expected,knownRoot,candidate,expectedUrl,snapshot) {
 const deadline=Date.now()+6500,maxBytes=250000,maxBlocks=128;
 let meta={literal_status:'unavailable',literal_blocks:0,literal_contracts:0};
 const result=(status,extra={})=>({status,meta:{...meta,literal_status:status},...extra});
 const excluded='blockquote,[data-artifact],[data-artifact-id],.artifact,[data-testid*="artifact"],'+
  'button,a,input,select,textarea,summary,details,[role="button"],[role="link"],[role="textbox"]';
 const exposed=node=>{
  if(!node||typeof node.getBoundingClientRect!=='function')return false;
  const r=node.getBoundingClientRect();if(!(r.width>0&&r.height>0))return false;
  for(let n=node;n;n=n.parentElement){
   if(n.hidden||n.inert||n.getAttribute?.('aria-hidden')==='true')return false;
   const st=getComputedStyle(n);
   if(st.display==='none'||['hidden','collapse'].includes(st.visibility)||st.opacity==='0')return false;
  }return true;
 };
 const canonicalJSON=value=>JSON.stringify(value);
 function select(){
  if(Date.now()>=deadline||location.href!==expectedUrl||document.querySelector(
   '[data-testid="stop-button"],button[aria-label*="Stop"],button[title*="Stop"],button[data-testid*="stop"]'))return null;
  const feed=snapshot(expected,null,knownRoot,{enabled:true});
  if(!feed?.native_source_candidate||canonicalJSON(feed.native_source_candidate)!==canonicalJSON(candidate))return null;
  const rows=Array.from(document.querySelectorAll('article.message-row[data-message-id]'));
  const roots=rows.filter(n=>n.getAttribute('data-message-id')===candidate.root_id);
  const replies=rows.filter(n=>n.getAttribute('data-message-id')===candidate.reply_id);
  if(roots.length!==1||replies.length!==1)return null;
  const root=roots[0],row=replies[0],bodies=Array.from(row.querySelectorAll('.message-body[data-message-id]'))
   .filter(n=>n.closest('article.message-row[data-message-id]')===row);
  if(bodies.length!==1)return null;
  const body=bodies[0];
  if(body!==row.querySelector('.message-body[data-message-id]')||!exposed(row)||!exposed(body))return null;
  return {root,row,body,rootText:String(root.textContent||''),bodyText:String(body.innerText||body.textContent||''),
   selection:feed.selection};
 }
 function read(before){
  const blocks=Array.from(before.body.querySelectorAll('pre > code'));
  meta.literal_blocks=Math.min(maxBlocks,blocks.length);meta.literal_contracts=0;
  if(blocks.length>maxBlocks)return {status:'block_limit'};
  const matches=[];
  for(const code of blocks){
   // A nested foreign message can never become a source, even a hidden one.
   if(code.closest('article.message-row[data-message-id]')!==before.row)return {status:'block_unsafe'};
   const text=code.textContent;
   if(typeof text!=='string')return {status:'block_unsafe'};
   if(text.length>maxBytes||new TextEncoder().encode(text).length>maxBytes)return {status:'source_oversized'};
   if(text.includes('AGOV RESULT:'))matches.push(code);
  }
  meta.literal_contracts=matches.length;
  if(!matches.length)return {status:'block_absent'};
  if(matches.length!==1)return {status:'block_multiple'};
  const code=matches[0],pre=code.parentElement;
  if(String(pre?.tagName||'').toUpperCase()!=='PRE'||code.closest(excluded)||pre.closest(excluded))return {status:'block_unsafe'};
  if(!exposed(pre)||!exposed(code))return {status:'block_unexposed'};
  const descendants=Array.from(code.querySelectorAll('*'));
  if(descendants.length>4096)return {status:'block_limit'};
  for(const n of [pre,code,...descendants]){
   if(n!==pre&&n!==code&&String(n.tagName||'').toUpperCase()!=='SPAN')return {status:'block_unsafe'};
   if(n.isContentEditable||n.getAttribute('contenteditable')==='true'||n.hasAttribute('tabindex')||
      n.hasAttribute('role')||n.hasAttribute('aria-label')||n.closest(excluded))return {status:'block_unsafe'};
   if(n!==pre&&n!==code&&Array.from(n.attributes).some(a=>!['class','style'].includes(a.name)))return {status:'block_unsafe'};
   if(!exposed(n))return {status:'block_unexposed'};
   const style=getComputedStyle(n);
   if(style.textTransform!=='none'||!['pre','pre-wrap','break-spaces'].includes(style.whiteSpace))return {status:'text_transformed'};
   for(const pseudo of ['::before','::after']){
    if(!['none','normal','','""',"''"].includes(getComputedStyle(n,pseudo).content))return {status:'text_transformed'};
   }
   if(n!==pre&&Array.from(n.childNodes).some(child=>child.nodeType!==3&&
       !(child.nodeType===1&&String(child.tagName||'').toUpperCase()==='SPAN')))return {status:'block_unsafe'};
  }
  // No line-number or decoration siblings are included or silently discarded.
  if(Array.from(pre.childNodes).some(n=>n!==code&&!(n.nodeType===3&&/^\s*$/.test(n.nodeValue||''))))return {status:'block_unsafe'};
  const text=code.textContent;
  if(typeof code.innerText!=='string'||text!==code.innerText)return {status:'text_mismatch'};
  if(new TextDecoder('utf-8',{fatal:true}).decode(new TextEncoder().encode(text))!==text)return {status:'source_unrepresentable'};
  // Do not promote/trim/join a marker, repair JSON or broaden the one-line
  // contract. A single terminal newline is retained exactly when present.
  if(!/^AGOV RESULT:[^\r\n\v\f\x1c-\x1e\x85\u2028\u2029]+(?:\r\n|\n)?$/.test(text))return {status:'contract_not_single_line'};
  return {status:'source_observed',code,pre,text,blocks};
 }
 const same=(a,b,x,y)=>Boolean(b&&y&&y.status==='source_observed'&&a.root===b.root&&a.row===b.row&&a.body===b.body&&
  a.rootText===b.rootText&&a.bodyText===b.bodyText&&canonicalJSON(a.selection)===canonicalJSON(b.selection)&&
  x.code===y.code&&x.pre===y.pre&&x.text===y.text&&x.blocks.length===y.blocks.length&&x.blocks.every((n,i)=>n===y.blocks[i]));
 try{
  const before=select();if(!before)return result('selection_changed');
  const first=read(before);if(first.status!=='source_observed')return result(first.status);
  const witness={root_text:before.rootText,body_text:before.bodyText};
  await new Promise(resolve=>setTimeout(resolve,200));
  const after=select(),second=after&&read(after);
  if(!same(before,after,first,second))return result('selection_changed');
  const verify=()=>{
   const current=select(),observed=current&&read(current);
   if(!same(before,current,first,observed))return result('selection_changed');
   const validated=snapshot(expected,null,knownRoot,{enabled:true,reply:{
    root_id:candidate.root_id,reply_id:candidate.reply_id,text:first.text,witness}});
   // This observer-created closure retains only these original DOM objects.
   // A short-lived remote handle, never page-global/private app state, keeps
   // their identity across external owner/account/URL revalidation.
   const final=select(),last=final&&read(final);
   if(!same(before,final,first,last))return result('selection_changed');
   return result('source_observed',{response:first.text,witness,validated});
  };
  return result('source_observed',{response:first.text,witness,verify});
 }catch(_){return result('unavailable');}
}
const LITERAL_FALLBACK=new Set(['control_missing','control_ambiguous','control_disabled',
 'permission_prompt','permission_denied','permission_unavailable','ack_stale','ack_unavailable','read_unavailable']);
const LITERAL_STATUSES=new Set(['block_absent','block_multiple','block_unexposed','block_unsafe','block_limit',
 'text_transformed','text_mismatch','contract_not_single_line','source_oversized','source_unrepresentable',
 'selection_changed','source_observed','unavailable']);

const STATUSES=new Set(['selection_changed','control_missing','control_ambiguous','control_disabled',
 'permission_granted','permission_prompt','permission_denied','permission_unavailable',
 'ack_stale','ack_unavailable','read_unavailable','source_empty','source_oversized',
 'source_unrepresentable','source_changed','unavailable']);
const STAGES=new Set(['selection','control','permission','acknowledgement','read','validation','complete','busy']);
function boundedSelection(value){
 if(!value||typeof value!=='object'||Array.isArray(value))return null;
 const keys=['mode','root_source','turn_rows','selected_rows','visible_rows','root_references',
  'unmatched_references','agov_markers','alternate_attributes','candidate','stage'];
 const optional=['copy_controls','copy_acknowledged','source_bytes','source_sha256','control_diagnostic',
  'source_kind','literal_status','literal_blocks','literal_contracts','copy_failure','copy_permission'];
 if(Object.keys(value).some(key=>!keys.includes(key)&&!optional.includes(key))||keys.some(key=>!Object.hasOwn(value,key))||
  !['linked_terminal','linked_selection_rejected','bounded_original_turn_candidate'].includes(value.mode)||
  !['full_envelope','restored_root'].includes(value.root_source)||!STAGES.has(value.stage)||typeof value.candidate!=='boolean')return null;
 for(const key of ['turn_rows','selected_rows','visible_rows','root_references','unmatched_references','agov_markers','alternate_attributes','copy_controls'])
  if(Object.hasOwn(value,key)&&(!Number.isInteger(value[key])||value[key]<0||value[key]>(key==='alternate_attributes'?15:65535)))return null;
 if(Object.hasOwn(value,'copy_acknowledged')&&typeof value.copy_acknowledged!=='boolean')return null;
 const source=Object.hasOwn(value,'source_bytes')||Object.hasOwn(value,'source_sha256');
 const literal=Object.hasOwn(value,'literal_status');
 if(literal){
  if(!LITERAL_STATUSES.has(value.literal_status)||!LITERAL_FALLBACK.has(value.copy_failure)||
   !Number.isInteger(value.literal_blocks)||value.literal_blocks<0||value.literal_blocks>128||
   !Number.isInteger(value.literal_contracts)||value.literal_contracts<0||value.literal_contracts>value.literal_blocks||
   (Object.hasOwn(value,'copy_permission')&&!['granted','prompt','denied','unavailable'].includes(value.copy_permission)))return null;
  if(source?(value.source_kind!=='literal_pre_code'||value.literal_status!=='source_observed'||value.literal_contracts!==1||
    value.copy_acknowledged!==false):Object.hasOwn(value,'source_kind')||value.literal_status==='source_observed')return null;
 }else if(['source_kind','literal_blocks','literal_contracts','copy_failure','copy_permission'].some(k=>Object.hasOwn(value,k)))return null;
 if(source&&(!Number.isInteger(value.source_bytes)||value.source_bytes<1||value.source_bytes>250000||
  !/^[a-f0-9]{64}$/.test(value.source_sha256)||(!literal&&value.copy_acknowledged!==true)||!['validation','complete'].includes(value.stage)))return null;
 if(Object.hasOwn(value,'control_diagnostic')){
  const d=value.control_diagnostic,counts=['buttons','role_buttons','visible','copy','ack','copy_visible',
   'body','nested','foreign','unlabelled','disabled','accepted','copy_body','copy_nested','copy_hidden','copy_opacity','menus'];
  if(!d||typeof d!=='object'||Array.isArray(d)||Object.keys(d).length!==counts.length+1||
   counts.some(key=>!Object.hasOwn(d,key)||!Number.isInteger(d[key])||d[key]<0||d[key]>512)||
   !['granted','prompt','denied','unavailable'].includes(d.permission)||value.stage!=='control'||
   value.candidate!==true||value.copy_acknowledged!==false||source)return null;
  return {...value,control_diagnostic:{...d}};
 }
 return {...value};
}
async function recoverOriginalNativeSource(session,expected,initial,{guard,withLock}={}){
 const known=session.dotFeedBinding?.expected===expected?session.dotFeedBinding.root_id:'';
 const safeFailure=(reason,selection)=>{
  const {reply_id,response,copy_probe_candidate,native_source_candidate,...base}=initial;
  return {...base,complete:false,reply_diagnostic_reason:reason,
   ...(boundedSelection(selection)?{selection:boundedSelection(selection)}:{})};
 };
 if(typeof guard!=='function'||typeof withLock!=='function')return safeFailure('native_copy_unavailable',initial.selection);
 try{return await withLock(async()=>{
  await guard();
  const location=await session.location();
  const args=[String(expected),null,String(known),{enabled:true}].map(JSON.stringify).join(',');
  const feed=await session.evaluate('('+dotFeedSnapshotDOM.toString()+')('+args+')',10000);
  const candidate=feed?.native_source_candidate;
  if(!candidate||feed.root_id!==initial.root_id)return safeFailure(feed?.reply_diagnostic_reason||'native_copy_selection_changed',feed?.selection);
  await guard();
  let source=await session.evaluate('('+nativeOriginalSourceDOM.toString()+')('+[
   String(expected),String(known),candidate,location].map(JSON.stringify).join(',')+',('+dotFeedSnapshotDOM.toString()+'),'+
   JSON.stringify(COPY_LABELS)+','+JSON.stringify(ACK_LABELS)+')',10000);
  await guard();
  if(await session.location()!==location)return safeFailure('native_copy_selection_changed',feed.selection);
  let selection=boundedSelection(source?.selection)||feed.selection,literal=false,validated;
  if(source?.status!=='source_observed'&&LITERAL_FALLBACK.has(source?.status)){
   const copyFailure=source.status,copySelection=selection;
   let observed,objectId;
   try{
    if(typeof session.call!=='function')throw Error('Scoped object observation unavailable');
    const remote=await session.call('Runtime.evaluate',{
     expression:'('+literalOriginalSourceDOM.toString()+')('+[
      String(expected),String(known),candidate,location].map(JSON.stringify).join(',')+',('+dotFeedSnapshotDOM.toString()+'))',
     returnByValue:false,awaitPromise:true,userGesture:false},5000);
    objectId=remote?.result?.objectId;
    if(remote?.exceptionDetails||typeof objectId!=='string'||!objectId)throw Error('Original literal handle unavailable');
    const verify=async()=>{
     const read=await session.call('Runtime.callFunctionOn',{objectId,
      functionDeclaration:'function(){return typeof this.verify==="function"?this.verify():{status:this.status,meta:this.meta};}',
      returnByValue:true,awaitPromise:true,userGesture:false},2500);
     if(read?.exceptionDetails)throw Error('Original literal check unavailable');
     return read?.result?.value;
    };
    await guard();
    if(await session.location()!==location)return safeFailure('native_copy_selection_changed',copySelection);
    observed=await verify();
    if(observed?.status==='source_observed'){
     await guard();
     if(await session.location()!==location)return safeFailure('native_copy_selection_changed',copySelection);
     observed=await verify();
     // Preserve the same owner/account postcondition as the native Copy path.
     // The original object remains held until this guard passes or fails.
     await guard();
     if(await session.location()!==location)return safeFailure('native_copy_selection_changed',copySelection);
    }
   }catch(error){
    if(error?.observationCode)throw error;
    observed={status:'unavailable',meta:{literal_status:'unavailable',literal_blocks:0,literal_contracts:0}};
   }finally{
    // Release only this observation's handle in the same admitted Session.
    if(objectId)try{await session.call('Runtime.releaseObject',{objectId},1000);}catch(_){}
   }
   const meta=observed?.meta;
   const diagnostic={...copySelection,...(meta&&LITERAL_STATUSES.has(meta.literal_status)?meta:
    {literal_status:'unavailable',literal_blocks:0,literal_contracts:0}),copy_failure:copyFailure};
   if(observed?.status!=='source_observed')return safeFailure(
    observed?.status==='selection_changed'?'native_copy_selection_changed':'native_copy_'+copyFailure,diagnostic);
   const {control_diagnostic,...compact}=diagnostic;
   selection={...compact,stage:'validation',copy_acknowledged:false,source_kind:'literal_pre_code',
    ...(control_diagnostic?{copy_permission:control_diagnostic.permission}:{})};
   source=observed;validated=observed.validated;literal=true;
  }
  if(source?.status!=='source_observed')return safeFailure('native_copy_'+(STATUSES.has(source?.status)?source.status:'unavailable'),selection);
  if(typeof source.response!=='string'||source.response.length>250000||Buffer.byteLength(source.response,'utf8')>250000||
   (!literal&&selection?.copy_acknowledged!==true))return safeFailure('native_copy_unavailable',selection);
  if(!literal){
   const exactSource={root_id:candidate.root_id,reply_id:candidate.reply_id,text:source.response,witness:source.witness};
   validated=await session.evaluate('('+dotFeedSnapshotDOM.toString()+')('+[
    String(expected),null,String(known),{enabled:true,reply:exactSource}].map(JSON.stringify).join(',')+')',10000);
   await guard();
  }
  if(await session.location()!==location||validated?.native_source_matches!==true||JSON.stringify(validated?.native_source_candidate)!==JSON.stringify(candidate))
   return safeFailure('native_copy_selection_changed',selection);
  const proof={...selection,source_bytes:Buffer.byteLength(source.response,'utf8'),
   source_sha256:crypto.createHash('sha256').update(source.response,'utf8').digest('hex')};
  if(validated?.complete!==true||validated.response!==source.response||validated.reply_id!==candidate.reply_id){
   const reason=['contract_absent','contract_multiple','contract_malformed','contract_identity_mismatch','contract_oversized','contract_indented'].includes(validated?.reply_diagnostic_reason)
    ?validated.reply_diagnostic_reason:'unverified';
   return safeFailure(literal?(reason==='unverified'?'contract_malformed':reason):'native_copy_raw_'+reason,proof);
  }
  const {native_source_candidate,copy_probe_candidate,native_source_matches,...result}=validated;
  return {...result,native_source_verified:true,selection:{...proof,stage:'complete'}};
 },{waitMs:500});}catch(error){
  if(error?.observationCode)throw error;
  return safeFailure(error?.code==='CHATGPT_BROWSER_LOCK_BUSY'?'native_copy_browser_busy':'native_copy_unavailable',
   initial.selection&&{...initial.selection,stage:error?.code==='CHATGPT_BROWSER_LOCK_BUSY'?'busy':initial.selection.stage});
 }
}
module.exports={nativeOriginalSourceDOM,literalOriginalSourceDOM,recoverOriginalNativeSource,boundedSelection,COPY_LABELS,ACK_LABELS};
