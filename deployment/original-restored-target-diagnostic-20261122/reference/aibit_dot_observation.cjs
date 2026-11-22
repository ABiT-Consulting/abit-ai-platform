'use strict';
const {validateDotHandoff,dotResponseLocationMatches}=require('./aibit_dot_handoff.cjs');
const {exactConversationUrl,selectorLocation}=require('./original_dot_bindings.cjs');
const {recoverOriginalNativeSource,boundedSelection}=require('./original_dot_native_source.cjs');
const REPLY_DIAGNOSTIC_CODES=new Map([
 ['protocol_unrecognized','ORIGINAL_REPLY_PROTOCOL_UNRECOGNIZED'],
 ['reply_rows_absent','ORIGINAL_REPLY_ROWS_ABSENT'],
 ['root_linked_candidate_outside_turn','ORIGINAL_REPLY_ROOT_LINK_CANDIDATE_OUTSIDE_TURN'],
 ['root_link_selection_rejected','ORIGINAL_REPLY_ROOT_LINK_REJECTED'],
 ['contract_absent','ORIGINAL_REPLY_CONTRACT_ABSENT'],
 ['contract_multiple','ORIGINAL_REPLY_CONTRACT_MULTIPLE'],
 ['contract_malformed','ORIGINAL_REPLY_CONTRACT_MALFORMED'],
 ['contract_identity_mismatch','ORIGINAL_REPLY_CONTRACT_IDENTITY_MISMATCH'],
 ['contract_oversized','ORIGINAL_REPLY_CONTRACT_OVERSIZED'],
 ['contract_indented','ORIGINAL_REPLY_CONTRACT_INDENTED'],
 ['terminal_unstable','ORIGINAL_REPLY_TERMINAL_UNSTABLE'],
 ['native_copy_control_missing','ORIGINAL_COPY_CONTROL_MISSING'],
 ['native_copy_control_ambiguous','ORIGINAL_COPY_CONTROL_AMBIGUOUS'],
 ['native_copy_selection_changed','ORIGINAL_COPY_PROBE_CHANGED'],
 ['native_copy_permission_granted','ORIGINAL_COPY_CAPABILITY_PRESENT'],
 ['native_copy_permission_prompt','ORIGINAL_COPY_PERMISSION_PROMPT'],
 ['native_copy_permission_denied','ORIGINAL_COPY_PERMISSION_DENIED'],
 ['native_copy_permission_unavailable','ORIGINAL_COPY_PERMISSION_UNAVAILABLE'],
 ['native_copy_unavailable','ORIGINAL_COPY_PROBE_UNAVAILABLE'],
 ['native_copy_control_disabled','ORIGINAL_COPY_CONTROL_DISABLED'],
 ['native_copy_browser_busy','ORIGINAL_COPY_BROWSER_BUSY'],
 ['native_copy_ack_stale','ORIGINAL_COPY_ACK_STALE'],
 ['native_copy_ack_unavailable','ORIGINAL_COPY_ACK_UNAVAILABLE'],
 ['native_copy_read_unavailable','ORIGINAL_COPY_READ_UNAVAILABLE'],
 ['native_copy_source_empty','ORIGINAL_COPY_SOURCE_EMPTY'],
 ['native_copy_source_oversized','ORIGINAL_COPY_SOURCE_OVERSIZED'],
 ['native_copy_source_unrepresentable','ORIGINAL_COPY_SOURCE_UNREPRESENTABLE'],
 ['native_copy_source_changed','ORIGINAL_COPY_SOURCE_CHANGED'],
 ['native_copy_raw_contract_absent','ORIGINAL_COPY_RAW_CONTRACT_ABSENT'],
 ['native_copy_raw_contract_multiple','ORIGINAL_COPY_RAW_CONTRACT_MULTIPLE'],
 ['native_copy_raw_contract_malformed','ORIGINAL_COPY_RAW_CONTRACT_MALFORMED'],
 ['native_copy_raw_contract_identity_mismatch','ORIGINAL_COPY_RAW_CONTRACT_IDENTITY_MISMATCH'],
 ['native_copy_raw_contract_oversized','ORIGINAL_COPY_RAW_CONTRACT_OVERSIZED'],
 ['native_copy_raw_contract_indented','ORIGINAL_COPY_RAW_CONTRACT_INDENTED'],
 ['native_copy_raw_unverified','ORIGINAL_COPY_RAW_UNVERIFIED']
]);
const observationError=(observationCode,statusCode)=>Object.assign(
 Error('The original Dot observation could not be verified.'),{observationCode,statusCode});
function createDotObserver({tabs,Session,accountFingerprint,config,onTerminal,onRejected,
 bindings,originalIdentity,scopeVerified=false,nativeSourceLock,readFeed=require('./aibit_dot_feed.cjs').readDotFeed}) {
 return async function observe(body) {
  const verified=validateDotHandoff(body,config.url,config.owner);
  const release=tabs.hold?tabs.hold(verified.thread_id):()=>{};
  let session,binding=null;
  try {
   if(originalIdentity && (scopeVerified!==true ||
      originalIdentity.owner!==verified.owner || originalIdentity.job_id!==body.job_id ||
      originalIdentity.account_id!==body.account_id || originalIdentity.thread_id!==verified.thread_id ||
      originalIdentity.url!==verified.url))
    throw observationError('ORIGINAL_BINDING_CONFLICT',409);
   if(bindings && originalIdentity) binding=bindings.lookup(originalIdentity);
   let target=await tabs.existing(verified.thread_id);
   // Only an immutable, server-observed exact conversation URL permits opening a
   // replacement read tab. Missing legacy state cannot invent this authority.
   if(!target && binding?.conversation_url && binding.root_id && tabs.recover)
    target=await tabs.recover(verified.thread_id,binding);
   if(!target) throw observationError('ORIGINAL_TAB_MISSING',404);
   if(await accountFingerprint(target)!==body.account_id)
    throw observationError('ORIGINAL_ACCOUNT_MISMATCH',403);
   if(binding && target.id!==binding.target_id && !binding.conversation_url)
    throw observationError('ORIGINAL_BINDING_CONFLICT',409);
   session=await new Session(target).open();
   const location=await session.location();
   let promotingLocation=Boolean(binding?.root_id && !binding.conversation_url &&
     target.id===binding.target_id && binding.location===verified.url &&
     exactConversationUrl(location));
   function verifyLocation(actual, allowPromotion=false) {
    if(!dotResponseLocationMatches(actual,verified.url))
     throw observationError('ORIGINAL_CONVERSATION_MISMATCH',409);
    if(binding?.root_id && selectorLocation(actual,verified.url)!==binding.location &&
       !(allowPromotion && promotingLocation))
     throw observationError('ORIGINAL_CONVERSATION_MISMATCH',409);
   }
   verifyLocation(location,true);
   // A URL transition requires a fresh full-envelope match; cached/virtualized
   // text cannot authorize the first durable conversation URL.
   if(binding?.root_id && !promotingLocation)
    session.dotFeedBinding={expected:verified.message,root_id:binding.root_id};
   let feed=await readFeed(session,verified.message);
   // A newly selected root-linked terminal must retain the same message ID and
   // exact bytes across the normal DOM completion stability window.
   if(feed.terminal_pending===true){
    await new Promise(resolve=>setTimeout(resolve,1000));
    feed=await readFeed(session,verified.message);
   }
   // Only the admitted original observation can use a native message Copy.
   // Initial development submission/valid rendered-result behavior is unchanged.
   if(scopeVerified===true && originalIdentity && feed.present===true && feed.complete!==true &&
      (feed.reply_diagnostic_reason==='contract_malformed' ||
       String(feed.reply_diagnostic_reason||'').startsWith('native_copy_'))) {
    const guard=async()=>{
     const current=await tabs.existing(verified.thread_id);
     if(!current || current.id!==target.id || await accountFingerprint(target)!==body.account_id)
      throw observationError('ORIGINAL_ACCOUNT_MISMATCH',403);
     if(await session.location()!==location)throw observationError('ORIGINAL_CONVERSATION_MISMATCH',409);
    };
    feed=await recoverOriginalNativeSource(session,verified.message,feed,{guard,withLock:nativeSourceLock});
   }
   if(feed.reason==='different_original_root')
    throw observationError('ORIGINAL_ROOT_MISMATCH',409);
   if(binding?.root_id && feed.present && feed.root_id!==binding.root_id)
    throw observationError('ORIGINAL_ROOT_MISMATCH',409);
   if(await session.location()!==location)
    throw observationError('ORIGINAL_CONVERSATION_MISMATCH',409);
   if(promotingLocation && !(feed.present===true && feed.full_envelope_matched===true &&
      feed.root_id===binding.root_id))
    throw observationError('ORIGINAL_CONVERSATION_MISMATCH',409);
   // Adopt a legacy original only from its already-owned mapped tab, verified
   // account/scope and one FULL matching visible envelope, never a supplied ID.
   if(bindings && originalIdentity && feed.present===true && feed.root_id) {
    if(!binding) {
     if(feed.full_envelope_matched===true) binding=bindings.adoptVisible(originalIdentity,{
       target_id:target.id,root_id:feed.root_id,conversation_url:location,
       full_envelope_matched:true,owned_mapping:true,account_verified:true,scope_verified:scopeVerified});
    } else binding=bindings.rememberRoot(originalIdentity,{
      target_id:target.id,root_id:feed.root_id,conversation_url:location});
   }
   const observed=await session.evaluate(`(() => {
    const expected=${JSON.stringify(verified.message)};
    const norm=s=>String(s||'').replace(/\\s+/g,' ').trim();
    const visible=n=>{const r=n.getBoundingClientRect();const s=getComputedStyle(n);return r.width>0&&r.height>0&&s.visibility!=='hidden'&&s.display!=='none'};
    const composer=document.querySelector('#prompt-textarea')||document.querySelector('[contenteditable=true][role=textbox]')||document.querySelector('textarea');
    const draft=composer?String('value'in composer?composer.value:composer.innerText||composer.textContent||''):'';
    const users=Array.from(document.querySelectorAll('[data-message-author-role=user],[data-content-search-unit-key$=":user"]'));
    const assistants=Array.from(document.querySelectorAll('[data-message-author-role=assistant],[data-content-search-unit-key$=":assistant"]'));
    const dialogs=Array.from(document.querySelectorAll('[role=alert],[role=dialog]')).filter(visible).map(n=>String(n.innerText||'').slice(0,1000));
    const marker=(expected.match(/"idempotency_key"\s*:\s*"([a-f0-9]{64})"/)||[])[1]||'';
    function ancestry(needle){
      const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);let found=[];let n;
      while(n=walker.nextNode())if(needle&&String(n.nodeValue||'').includes(needle))found.push(n.parentElement);
      let node=found[found.length-1],rows=[];
      for(let i=0;node&&i<9;i++,node=node.parentElement){
        const text=String(node.innerText||node.textContent||'');
        rows.push({tag:node.tagName,attributes:Object.fromEntries(Array.from(node.attributes).filter(a=>/^(id|class|role|data-(message|post|thread|item|testid|author|content|node|id))/.test(a.name)).map(a=>[a.name,a.value.slice(0,250)])),length:text.length,prefix:text.slice(0,120),suffix:text.slice(-200),children:node.children.length});
      }return rows;
    }
    return {url:location.href,title:document.title,
      feed:Array.from(document.querySelectorAll('.message-body[data-message-id]')).slice(-6).map(n=>{
        let p=n,parents=[];for(let i=0;p&&i<8;i++,p=p.parentElement)parents.push({tag:p.tagName,cls:p.className,attrs:Object.fromEntries(Array.from(p.attributes).filter(a=>/^(data-|role|id)/.test(a.name)).map(a=>[a.name,a.value.slice(0,220)])),prefix:String(p.innerText||'').slice(0,100),children:p.children.length});
        return {id:n.getAttribute('data-message-id'),match:norm(n.innerText||n.textContent)===norm(expected),prefix:String(n.innerText||'').slice(0,100),tail:String(n.innerText||'').slice(-180),parents};}),marker_ancestors:ancestry(marker),result_ancestors:ancestry('ERP RESULT: READY FOR REVIEW'),composer_length:draft.length,composer_matches:norm(draft)===norm(expected),
      generating:Boolean(document.querySelector('[data-testid="stop-button"],button[aria-label*="Stop"],button[title*="Stop"],button[data-testid*="stop"]')),
      users:users.length,assistants:assistants.length,
      original_user_visible:users.some(n=>norm(n.innerText||n.textContent)===norm(expected)),
      user_tail:users.slice(-1).map(n=>String(n.innerText||'').slice(-300)),
      assistant_tail:assistants.slice(-1).map(n=>String(n.innerText||'').slice(-5000)),
      buttons:Array.from(document.querySelectorAll('button')).filter(visible).slice(-60).map(n=>({label:n.getAttribute('aria-label'),text:String(n.innerText||'').slice(0,100),testid:n.getAttribute('data-testid'),disabled:n.disabled})),
      dialogs,body_tail:String(document.body.innerText||'').slice(-12000)};
   })()`,10000);
   verifyLocation(await session.location());
   const observationCode=observed.generating===true?'ORIGINAL_GENERATING':
     feed.reason==='ambiguous_run_binding'?'ORIGINAL_ENVELOPE_AMBIGUOUS':
     !feed.present?(bindings&&originalIdentity&&!binding?'ORIGINAL_BINDING_MISSING':'ORIGINAL_ROOT_NOT_VISIBLE'):
     feed.complete===true?'ORIGINAL_TERMINAL_VERIFIED':
     REPLY_DIAGNOSTIC_CODES.get(feed.reply_diagnostic_reason)||'ORIGINAL_REPLY_NOT_VERIFIED';
   const result={ok:true,read_only:true,owner:verified.owner,job_id:body.job_id,account_id:body.account_id,
     dot_handoff:feed.complete===true&&observed.generating===false, response:feed.complete===true&&observed.generating===false?feed.response||'':'', conversation_url:location,
     root_message_id:feed.root_id||null,reply_message_id:feed.reply_id||null,
     observation_code:observationCode,binding_present:Boolean(binding),tab_present:true,
     root_present:feed.present===true,
     ...(boundedSelection(feed.selection)?{selection:boundedSelection(feed.selection)}:{})};
   // Raw DOM, composer contents and other replies stay inside this observation.
   // Only a verified original response can enter the normal response contract.
   const dialog='The message you submitted was too long, please edit it and resubmit.';
   if(feed.present===true && feed.root_failure && feed.root_failure.root_id===feed.root_id &&
      feed.root_failure.original_envelope_matched===true && feed.root_failure.failed_to_send===true &&
      feed.root_failure.failed_send_label==='Failed to send. Click to retry' &&
      feed.root_failure.generating===false && feed.complete!==true && !feed.reply_id && !feed.response &&
      observed.generating===false && Array.isArray(observed.dialogs) &&
      observed.dialogs.some(s=>String(s).replace(/\s+/g,' ').trim()===dialog)) {
    result.protocol='v4_outgoing_rejection';result.root_failure=feed.root_failure;
    result.rejection={code:'CHATGPT_MESSAGE_TOO_LONG',not_submitted:true,dialog};
    if(onRejected) onRejected({target_id:target.id,thread_id:verified.thread_id,result,idle:true});
   }
   else if(feed.present===false && feed.complete!==true && !feed.root_id && !feed.reply_id &&
      observed.original_user_visible===false && observed.generating===false &&
      observed.users===0 && observed.assistants===0 && Array.isArray(observed.dialogs) &&
      observed.dialogs.some(s=>String(s).replace(/\s+/g,' ').trim()===dialog)) {
    result.rejection={code:'CHATGPT_MESSAGE_TOO_LONG',not_submitted:true,dialog,
      original_user_visible:false,feed_present:false,generating:false,users:0,assistants:0};
    if(onRejected) onRejected({target_id:target.id,thread_id:verified.thread_id,result,idle:true});
   }
   if(result.rejection) result.observation_code='ORIGINAL_REJECTED_BEFORE_MODEL';
   if(feed.complete===true&&observed.generating===false) {
    if(bindings && originalIdentity && binding) bindings.terminal(originalIdentity,{
      root_id:feed.root_id,reply_id:feed.reply_id,response:feed.response,
      conversation_url:location});
    if(onTerminal) onTerminal({target_id:target.id,thread_id:verified.thread_id,result,idle:true});
   }
   return result;
  } catch(error) {
   if(error.observationCode) throw error;
   if(error.code && /^DOT_BINDING_|^DOT_ORIGINAL_ALREADY_BOUND$/.test(error.code)) {
    const unavailable=/STORE_/.test(error.code);
    throw observationError(unavailable?'ORIGINAL_BINDING_STORE_UNAVAILABLE':'ORIGINAL_BINDING_CONFLICT',
      unavailable?503:409);
   }
   throw error;
  } finally {if(session)session.close();release();}
 };
}
module.exports={createDotObserver};
