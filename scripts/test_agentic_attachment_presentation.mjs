import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import test from 'node:test';
import {governedContent} from '../deployment/linux/abit-ai/agentic-gateway/edge.mjs';

const bytes=Buffer.from([0xff,0xd8,0xff,0xe0,0xff,0xd9]); // Protocol framing fixture, not a decoded image claim.
const input={operation:'attachment.present',arguments:{file_id:'FILE-fixture'}};
const result={call_id:'call-fixture-001',state:'completed',replayed:false,result:{
  kind:'image_preview',file_id:'FILE-fixture',context_only:true,derived_preview:true,
  sha256:'a'.repeat(64),preview_mime:'image/jpeg',preview_dimensions:[50,30],
  preview_base64:bytes.toString('base64'),preview_sha256:crypto.createHash('sha256').update(bytes).digest('hex'),
}};

test('trusted completed preview emits actual MCP image and bounded metadata without duplicate base64',()=>{
  const content=governedContent(input,result);
  assert.equal(content.length,2);
  assert.deepEqual(content[1],{type:'image',mimeType:'image/jpeg',data:bytes.toString('base64')});
  assert.equal(JSON.parse(content[0].text).result.preview_base64,undefined);
  assert.equal(JSON.parse(content[0].text).result.sha256,'a'.repeat(64));
});

test('ordinary operations and uncertain original receipts remain text only',()=>{
  for(const [args,value] of [[{...input,operation:'context.read'},result],[input,{...result,state:'uncertain'}]]){
    const content=governedContent(args,value);
    assert.deepEqual(content,[{type:'text',text:JSON.stringify(value)}]);
  }
});

test('crossfile, malformed/oversize preview or digest mismatch never becomes image content',()=>{
  for(const change of [{file_id:'FILE-other'},{preview_sha256:'0'.repeat(64)},
      {preview_base64:bytes.toString('base64')+'\n'},{preview_base64:'A'.repeat(131076)},
      {preview_mime:'text/html'},{preview_dimensions:[1281,1]},{context_only:false}]){
    assert.throws(()=>governedContent(input,{...result,result:{...result.result,...change}}));
  }
});
