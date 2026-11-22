'use strict';
// Diagnostic only. No click, focus, navigation, clipboard access, permission
// request/grant, handler inspection, prompt submission or result acceptance.
async function nativeCopyCapabilityDOM(expected, knownRoot, candidate, expectedUrl, snapshot) {
  const result = status => ({status});
  try {
    const exposed = node => {
      if (!node || typeof node.getBoundingClientRect !== 'function') return false;
      const rect=node.getBoundingClientRect();
      if (!(rect.width>0 && rect.height>0)) return false;
      for (let current=node;current;current=current.parentElement) {
        if (current.hidden || current.inert || current.getAttribute?.('aria-hidden')==='true') return false;
        const style=getComputedStyle(current);
        if (style.display==='none' || ['hidden','collapse'].includes(style.visibility) || style.opacity==='0') return false;
      }
      return true;
    };
    const select = () => {
      if (location.href!==expectedUrl || !candidate || !candidate.root_id || !candidate.reply_id) return null;
      const feed=snapshot(expected,null,knownRoot);
      if (feed?.complete===true || feed?.reply_diagnostic_reason!=='contract_malformed' ||
          feed?.copy_probe_candidate?.root_id!==candidate.root_id ||
          feed?.copy_probe_candidate?.reply_id!==candidate.reply_id ||
          document.querySelector('[data-testid="stop-button"],button[aria-label*="Stop"],button[title*="Stop"],button[data-testid*="stop"]')) return null;
      const matches=Array.from(document.querySelectorAll('article.message-row[data-message-id]'))
        .filter(row=>row.getAttribute('data-message-id')===candidate.reply_id);
      if(matches.length!==1)return null;
      const row=matches[0],body=row.querySelector('.message-body[data-message-id]');
      if(!body || !exposed(row))return null;
      const controls=Array.from(row.querySelectorAll('button')).filter(button=>{
        if (!exposed(button) || button.disabled || button.getAttribute('aria-disabled')==='true' ||
            button.closest('article.message-row[data-message-id]')!==row || body.contains(button) ||
            button.closest('pre,code,blockquote,[data-code-block],[data-artifact],[data-artifact-id],.code-block,.artifact,[data-testid*="artifact"],[data-testid*="code-block"]'))return false;
        const label=String(button.getAttribute('aria-label') || button.getAttribute('title') || button.textContent || '')
          .replace(/\s+/g,' ').trim().toLowerCase();
        return ['copy','copy message','copy response','copy markdown','copy as markdown'].includes(label);
      });
      return {row,body,text:String(body.innerText||body.textContent||''),controls};
    };
    const before=select();
    if(!before)return result('selection_changed');
    if(!before.controls.length)return result('control_missing');
    if(before.controls.length!==1)return result('control_ambiguous');
    let permission='unavailable';
    if(typeof navigator?.permissions?.query==='function' && typeof navigator?.clipboard?.readText==='function'){
      let timer;
      try {
        const answer=await Promise.race([
          navigator.permissions.query({name:'clipboard-read'}),
          new Promise(resolve=>{timer=setTimeout(()=>resolve(null),1200);})
        ]);
        if(['granted','prompt','denied'].includes(answer?.state))permission=answer.state;
      } catch (_) { /* Unavailable, never request or grant permissions. */ }
      finally {if(timer!==undefined)clearTimeout(timer);}
    }
    const after=select();
    if(!after || after.row!==before.row || after.body!==before.body || after.text!==before.text ||
        after.controls.length!==1 || after.controls[0]!==before.controls[0])return result('selection_changed');
    return result('permission_'+permission);
  } catch (_) {return result('unavailable');}
}
const STATUS=new Set(['control_missing','control_ambiguous','selection_changed','permission_granted',
 'permission_prompt','permission_denied','permission_unavailable','unavailable']);
async function probeOriginalCopyCapability(session,expected,knownRoot,candidate,snapshot) {
  try {
    const location=await session.location();
    const script='('+nativeCopyCapabilityDOM.toString()+')('+[String(expected),String(knownRoot||''),candidate,location]
      .map(value=>JSON.stringify(value)).join(',')+',('+snapshot.toString()+'))';
    const found=await session.evaluate(script,5000);
    if(await session.location()!==location)return {status:'selection_changed'};
    return {status:STATUS.has(found?.status)?found.status:'unavailable'};
  } catch (_) {return {status:'unavailable'};}
}
module.exports={nativeCopyCapabilityDOM,probeOriginalCopyCapability};
