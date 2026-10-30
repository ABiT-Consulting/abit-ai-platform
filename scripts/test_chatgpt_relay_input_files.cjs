'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {test}=require('node:test');
const {validate}=require('../deployment/windows/aibit-relay/relay_input_files.cjs');
const root='C:/ProgramData/ABiT/OpenWebUI/relay-input-files';
test('ABiT screenshot staging accepted, other filesystem access rejected',()=>{
 fs.mkdirSync(root,{recursive:true});
 const folder=fs.mkdtempSync(path.join(root,'aibit_unit_'));
 const out=fs.mkdtempSync(path.join(os.tmpdir(),'aibit_denied_'));
 try{
  const ok=path.join(folder,'screenshot-01.png'),bad=path.join(out,'wrong.png');
  fs.writeFileSync(ok,'synthetic fixture');fs.writeFileSync(bad,'not authorized');
  assert.equal(validate([{path:ok,name:'screenshot-01.png'}])[0].path,fs.realpathSync(ok));
  assert.throws(()=>validate([{path:bad,name:'wrong.png'}]),/outside relay staging/);
  assert.throws(()=>validate([{path:ok,name:'wrong.png'}]),/name mismatch/);
 }finally{fs.rmSync(folder,{recursive:true,force:true});fs.rmSync(out,{recursive:true,force:true})}
});
