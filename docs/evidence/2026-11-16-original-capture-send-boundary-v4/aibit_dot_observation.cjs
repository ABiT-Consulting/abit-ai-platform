'use strict';
const {validateDotHandoff,dotResponseLocationMatches}=require('./aibit_dot_handoff.cjs');
function createDotObserver({tabs,Session,accountFingerprint,config,onTerminal}) {
 return async function observe(body) {
  const verified=validateDotHandoff(body,config.url,config.owner);
  const target=await tabs.existing(verified.thread_id);
  if(!target) throw Object.assign(Error('The original owned Dot tab is unavailable.'),{statusCode:404});
  if(await accountFingerprint(target)!==body.account_id)
   throw Object.assign(Error('The original Dot account no longer matches.'),{statusCode:403});
  const session=await new Session(target).open();
  try {
   const location=await session.location();
   if(!dotResponseLocationMatches(location,verified.url))
    throw Object.assign(Error('The original Dot tab moved outside its approved conversation.'),{statusCode:409});
   const feed=await require('./aibit_dot_feed.cjs').readDotFeed(session,verified.message);
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
   if(!dotResponseLocationMatches(await session.location(),verified.url))
    throw Object.assign(Error('The original Dot tab changed during observation.'),{statusCode:409});
   const result={ok:true,read_only:true,owner:verified.owner,job_id:body.job_id,account_id:body.account_id,
     dot_handoff:feed.complete===true, response:feed.response||'', conversation_url:location,
     root_message_id:feed.root_id||null,reply_message_id:feed.reply_id||null,observed};
   if(onTerminal&&feed.complete===true&&observed.generating===false)
    onTerminal({target_id:target.id,thread_id:verified.thread_id,result,idle:true});
   return result;
  } finally {session.close();}
 };
}
module.exports={createDotObserver};
