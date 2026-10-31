import fs from 'node:fs';
import https from 'node:https';
import {pipeline} from 'node:stream/promises';
const [manifestPath,authPath]=process.argv.slice(2);
if(!manifestPath||!authPath)throw new Error('Usage: upload-models.mjs manifest.json transfer-auth.json');
const manifest=JSON.parse(fs.readFileSync(manifestPath,'utf8'));
const auth=JSON.parse(fs.readFileSync(authPath,'utf8'));
for(let i=0;i<manifest.files.length;i++){
 const f=manifest.files[i];
 if(!fs.statSync(f.source).isFile()||fs.statSync(f.source).size!==f.size)throw new Error('Source changed since inventory');
 const response=new Promise((resolve,reject)=>{
  const req=https.request(new URL('/files/'+i,auth.url),{method:'PUT',ca:auth.ca,rejectUnauthorized:true,headers:{
   'Authorization':'Bearer '+auth.token,'Content-Length':String(f.size),'Connection':'close'
  }},res=>{
   let body='';res.setEncoding('utf8');
   res.on('data',chunk=>{body+=chunk;if(body.length>1024)res.destroy(new Error('Invalid response size'));});
   res.on('error',reject);
   res.on('end',()=>res.statusCode===200&&body===f.sha256?resolve():reject(new Error('Transfer rejected: '+res.statusCode)));
  });
  req.setTimeout(60000,()=>req.destroy(new Error('Transfer idle timeout')));
  req.on('error',reject);
  pipeline(fs.createReadStream(f.source),req).catch(reject);
 });
 await response;
 console.log(JSON.stringify({completed:i+1,total:manifest.files.length,bytes:f.size}));
}
console.log('All model and identity files transferred and verified.');
