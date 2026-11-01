import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {pipeline} from 'node:stream/promises';

const root = String.raw`C:\ProgramData\ABiT\OpenWebUIBackups\cutover-20261101`;
const input = path.join(root, 'final-snapshot.tar.gz');
const publicKey = path.join(root, 'recipient-public.pem');
const ciphertext = path.join(root, 'final-snapshot.encrypted-tmp');
const output = path.join(root, 'final-snapshot.sealed');

if (!fs.existsSync(input) || !fs.existsSync(publicKey)) throw Error('Missing verified final archive or recipient public key');
if (fs.existsSync(output) || fs.existsSync(ciphertext)) throw Error('Refusing to replace existing encrypted archive');
const key=crypto.randomBytes(32),iv=crypto.randomBytes(12);
const cipher=crypto.createCipheriv('aes-256-gcm',key,iv);
await pipeline(fs.createReadStream(input),cipher,fs.createWriteStream(ciphertext,{flags:'wx',mode:0o600}));
const wrapped=crypto.publicEncrypt({key:fs.readFileSync(publicKey),oaepHash:'sha256'},key);
const header='ABIT-AI-MIG1\n'+JSON.stringify({key:wrapped.toString('base64'),iv:iv.toString('base64'),tag:cipher.getAuthTag().toString('base64')})+'\n';
await fs.promises.writeFile(output,header,{flag:'wx',mode:0o600});
await pipeline(fs.createReadStream(ciphertext),fs.createWriteStream(output,{flags:'a'}));
await fs.promises.unlink(ciphertext);
const hash=crypto.createHash('sha256');
await pipeline(fs.createReadStream(output),hash);
const digest=hash.digest('hex');
const size=fs.statSync(output).size;
const result=await new Promise((resolve,reject)=>{
 const req=http.request({host:'192.168.253.20',port:28683,path:'/abit-ai-final-20261101',method:'POST',headers:{'Content-Length':size}},res=>{
  let data='';res.on('data',c=>data+=c.toString());res.on('end',()=>{
   if(res.statusCode!==200)return reject(Error('Transfer HTTP status '+res.statusCode));
   try{resolve(JSON.parse(data));}catch(e){reject(e);}
  });
 });
 req.on('error',reject);req.setTimeout(1200000,()=>req.destroy(Error('Transfer timed out')));
 pipeline(fs.createReadStream(output),req).catch(reject);
});
if(result.sha256!==digest||result.bytes!==size) throw Error('Transfer integrity verification failed');
console.log(JSON.stringify({finalEncryptedTransferVerified:true,sha256:digest,bytes:size}));
