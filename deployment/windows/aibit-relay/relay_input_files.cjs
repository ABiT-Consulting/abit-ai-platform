'use strict';
const fs=require('fs'),path=require('path');
const ROOT='C:/Code/ABiT-Consulting/AI_DEVELOPER_AGENT-AIERP/.runtime/chat-input-files';
function attachmentScope() {
 const composer=[...document.querySelectorAll('#prompt-textarea,textarea,[contenteditable="true"][role="textbox"]')].find(n=>n.getClientRects().length);
 let node=composer;
 while(node&&node!==document.body){
  if(node.querySelector('input[type="file"]'))return node;
  node=node.parentElement;
 }
 return composer?.closest('form')||composer?.closest('[data-composer-body]')||null;
}
const fileInputExpression='(()=>{const root=('+attachmentScope.toString()+')();return root&&[...root.querySelectorAll(\'input[type="file"]\')].find(n=>!n.accept)})()';
function validate(files) {
 if(!Array.isArray(files)||files.length>10)throw Error('Attach at most 10 files.');
 let total=0;
 return files.map(item=>{
  if(!item||typeof item.path!=='string')throw Error('Attachment file is missing.');
  const real=fs.realpathSync(item.path),relative=path.relative(fs.realpathSync(ROOT),real);
  if(relative.startsWith('..')||path.isAbsolute(relative))throw Error('Attachment is outside relay staging.');
  const stat=fs.statSync(real);total+=stat.size;
  if(!stat.isFile()||stat.size<=0||stat.size>20*1024*1024||total>40*1024*1024)throw Error('Attachment exceeds relay limits.');
  if(path.basename(real)!==item.name)throw Error('Attachment name mismatch.');
  return {path:real,name:item.name};
 });
}
async function upload(session,attachments) {
 const files=validate(attachments);
 if(!files.length)return;
 // This is the owned relay composer. A failed preparation can leave an unsent
 // file chip behind; replace that draft with this request's explicit files.
 await session.evaluate('(()=>{const root=('+attachmentScope.toString()+')();if(!root)return;for(const button of root.querySelectorAll(\'button[aria-label^="Remove "]\'))button.click()})()');
 await new Promise(r=>setTimeout(r,250));
 let input=await session.call('Runtime.evaluate',{expression:fileInputExpression,returnByValue:false});
 if(!input.result.objectId){
  await session.evaluate(`(()=>{const b=document.querySelector('[data-testid="composer-plus-btn"],button[aria-label="Add files and more"],button[aria-label="Add photos & files"]');if(b)b.click();})()`);
  await new Promise(r=>setTimeout(r,400));
  // A file input can be created only after opening the attachment menu.
  input=await session.call('Runtime.evaluate',{expression:fileInputExpression,returnByValue:false});
 }
 if(!input.result.objectId)throw Error('ChatGPT file upload control unavailable; message was not sent.');
 try {
  await session.call('DOM.setFileInputFiles',{files:files.map(f=>f.path),objectId:input.result.objectId});
 } finally {await session.call('Runtime.releaseObject',{objectId:input.result.objectId}).catch(()=>{});}
 const start=Date.now();let readySince=0;
 while(Date.now()-start<90000){
  const state=await session.evaluate('('+function(names,root){
   if(!root)return {ready:false};
   const text=root.innerText||'';
   const error=[...root.querySelectorAll('[role="alert"]')].map(n=>n.textContent).join(' ');
   const canonical=name=>String(name).replace(/\s*\(\d+\)(?=\.[^.]+$)/,'');
   const chipNames=[...root.querySelectorAll('button[aria-label^="Remove "]')].map(n=>canonical(n.getAttribute('aria-label').slice(7)));
   const namesPresent=names.every(name=>text.includes(name)||chipNames.includes(canonical(name))||[...root.querySelectorAll('[title],[aria-label]')].some(n=>(n.getAttribute('title')||'').includes(name)||(n.getAttribute('aria-label')||'').includes(name)));
   const loading=[...root.querySelectorAll('[role="progressbar"],[aria-busy="true"],.animate-spin')].some(n=>n.getClientRects().length);
   return {ready:namesPresent&&!loading,error};
  }.toString()+')('+JSON.stringify(files.map(f=>f.name))+',('+attachmentScope.toString()+')())');
  if(state.error)throw Error('ChatGPT attachment upload failed: '+state.error.slice(0,200));
  if(state.ready){if(!readySince)readySince=Date.now();if(Date.now()-readySince>=2000)return;}
  else readySince=0;
  await new Promise(r=>setTimeout(r,500));
 }
 throw Error('ChatGPT attachment upload was not confirmed; message was not sent.');
}
module.exports={validate,upload};
