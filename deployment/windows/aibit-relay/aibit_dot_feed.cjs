'use strict';
// Fixed DOM reader for the owner-authenticated Dot message feed. It never sends.
function dotFeedSnapshotDOM(expected, markdown, knownRootId) {
 const marker=(String(expected).match(/"idempotency_key"\s*:\s*"([a-f0-9]{64})"/)||[])[1];
 const run=(String(expected).match(/"run_id"\s*:\s*"([A-Za-z0-9_-]{1,160})"/)||[])[1];
 if(!marker||!run)return {present:false,reason:'missing_run_binding'};
 // Both the model context and packet can have trusted instructions after JSON.
 function jsonObject(source) {
  source=String(source).trimStart();
  let depth=0, quoted=false, escaped=false;
  for(let i=0;source[0]==='{'&&i<source.length;i++) {
   const c=source[i];
   if(quoted) {if(escaped)escaped=false;else if(c==='\\')escaped=true;else if(c==='"')quoted=false;}
   else if(c==='"')quoted=true;
   else if(c==='{')depth++;
   else if(c==='}'&&--depth===0)return JSON.parse(source.slice(0,i+1));
  }
  throw Error('Incomplete governed context');
 }
 // The public model envelope excludes capability proofs. Read only its existing
 // governed identity and packet; recognizing a reply grants no action authority.
 let governed=null, governedMode=false;
 try {
  const label='idempotency key (JSON):\n', offset=String(expected).indexOf(label);
  if(offset>=0) {
   const context=jsonObject(String(expected).slice(offset+label.length));
   const meta=context.task?.meta, prompt=context.task?.original_prompt;
   if(meta?.governance_managed===true&&typeof prompt==='string') {
    governedMode=true;
    const heading='GOVERNANCE WORK PACKET\n', start=prompt.indexOf(heading);
    const source=start>=0?prompt.slice(start+heading.length):'';
    const packet=jsonObject(source);
    const preamble=prompt.split('EXACT IMPLEMENTATION IDENTITY:')[0];
    const identity=preamble.match(/Its invocation_id must be ([A-Za-z0-9_-]{1,160}) and work_packet_digest ([a-f0-9]{64})\./);
    if(packet&&identity&&context.idempotency_key===marker&&context.run_id===run&&
       packet.content_sha256===identity[2]&&packet.content_sha256===meta.governance_packet_digest&&
       packet.project_id===meta.governance_project&&packet.task_id===meta.erp_task&&
       packet.binding?.project_id===packet.project_id&&packet.binding?.agent_id===context.automation_id&&
       packet.binding?.role===meta.erp_role&&typeof packet.binding?.erp_user==='string')
     governed={invocation:identity[1],packet};
   }
  }
 }catch(_){}
 const rows=Array.from(document.querySelectorAll('article.message-row[data-message-id]'));
 let roots=rows.filter(row=>row.classList.contains('self')&&String(row.textContent||'').includes(marker));
 if(!roots.length&&knownRootId)roots=rows.filter(row=>row.classList.contains('self')&&row.getAttribute('data-message-id')===knownRootId);
 if(roots.length!==1)return {present:false,reason:roots.length?'ambiguous_run_binding':'not_visible'};
 const root=roots[0],start=rows.indexOf(root),rootId=root.getAttribute('data-message-id');
 let reply=null;
 for(const row of rows.slice(start+1)) {
  if(row.classList.contains('self'))break; // Never attach a later unrelated reply.
  const body=row.querySelector('.message-body[data-message-id]')||row;
  const text=String(body.innerText||body.textContent||'').trim();
  if(!text)continue;
  const lines=text.split('\n').map(s=>s.trim()).filter(Boolean);
  const developer=/^ERP RESULT: (READY FOR REVIEW|CONTINUE|BLOCKED)$/.test(lines.at(-1)||'');
  let qa=false;
  for(const line of lines)if(line.startsWith('ERP QA RESULT:'))try{
   const result=JSON.parse(line.slice('ERP QA RESULT:'.length).trim());
   qa=result.run_id===run&&['pass','fail','blocked'].includes(result.status);
  }catch(_){}
  let agov=false;
  const contracts=lines.filter(line=>line.startsWith('AGOV RESULT:'));
  if(governed&&contracts.length===1&&contracts[0].length<=200000)try{
   const result=JSON.parse(contracts[0].slice('AGOV RESULT:'.length).trim());
   const packet=governed.packet;
   agov=result.schema_version===1&&result.invocation_id===governed.invocation&&
    result.project_id===packet.project_id&&result.task_id===packet.task_id&&
    result.task_revision===packet.task_revision&&result.work_packet_digest===packet.content_sha256&&
    ['continue','ready_for_review','pass','fail','blocked','reviewed'].includes(result.outcome)&&
    ['agent_id','erp_user','project_id','role'].every(key=>result.actor?.[key]===packet.binding[key])&&
    (result.run_id===undefined||result.run_id===run);
  }catch(_){}
  // Governed contracts are plain JSON lines. Markdown escaping would change the
  // JSON bytes; the Core parser performs the complete result/schema validation.
  if(governedMode?agov:(developer||qa))reply={response:agov?text:(typeof markdown==='function'?markdown(body):text),reply_id:row.getAttribute('data-message-id')};
 }
 return {present:true,root_id:rootId,run_id:run,...(reply||{}),complete:Boolean(reply)};
}
async function readDotFeed(session,message,markdown) {
 const marker=(String(message).match(/"idempotency_key"\s*:\s*"([a-f0-9]{64})"/)||[])[1];
 const known=session.dotFeedBinding&&session.dotFeedBinding.marker===marker?session.dotFeedBinding.root_id:'';
 const result=await session.evaluate(`(${dotFeedSnapshotDOM.toString()})(${JSON.stringify(String(message))},${typeof markdown==='function'?markdown.toString():'null'},${JSON.stringify(known)})`,10000);
 if(result?.present&&result.root_id)session.dotFeedBinding={marker,root_id:result.root_id};
 return result||{present:false};
}
module.exports={dotFeedSnapshotDOM,readDotFeed};
