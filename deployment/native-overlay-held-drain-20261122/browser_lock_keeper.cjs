'use strict';
// Root-only, fixed-purpose keeper. It retains the existing application's shared
// lock across an interrupted operator, until an exact protected release receipt.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const STAGE = '/var/lib/abit-ai-native-overlay-held-drain-20261122';
const MODULE = '/srv/abit-dev-linux/relay-runtime/service/shared_chatgpt_browser_lock.cjs';
const LOCK = '/srv/abit-dev-linux/relay-runtime/state/browser.lock';
const MODULE_SHA = 'a3625394a8cfb684d742434f17feb0af66292279801e7012eb2ddb7419ba8081';
const digest = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
function requireThat(ok, code) { if (!ok) throw Error(code); }
function regular(file) {
  const fd=fs.openSync(file,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);
  try {
    const first=fs.fstatSync(fd);requireThat(first.isFile()&&first.nlink===1&&first.size<=65536,'BOUNDED_INPUT');
    const bytes=fs.readFileSync(fd);const after=fs.fstatSync(fd), current=fs.lstatSync(file);
    requireThat(['dev','ino','size','mtimeMs','ctimeMs','uid','gid','mode'].every(key=>first[key]===after[key]&&first[key]===current[key]),'INPUT_CHANGED');
    return {bytes,stat:first};
  } finally {fs.closeSync(fd);}
}
function protectedJson(file) {
  const value=regular(file);requireThat(value.stat.uid===0&&value.stat.gid===0&&(value.stat.mode&0o777)===0o600,'PROTECTED_INPUT');
  return JSON.parse(value.bytes);
}
function save(file,value) {
  const fd=fs.openSync(file,fs.constants.O_WRONLY|fs.constants.O_CREAT|fs.constants.O_EXCL|fs.constants.O_NOFOLLOW,0o600);
  try {fs.writeFileSync(fd,JSON.stringify(value)+'\n');fs.fsyncSync(fd);}finally{fs.closeSync(fd);}
  const dir=fs.openSync(path.dirname(file),fs.constants.O_RDONLY|fs.constants.O_DIRECTORY|fs.constants.O_NOFOLLOW);
  try{fs.fsyncSync(dir);}finally{fs.closeSync(dir);}
}
function processIdentity(pid) {
  const base='/proc/'+pid;const parts=fs.readFileSync(base+'/stat','utf8').split(')').slice(1).join(')').trim().split(/\s+/);
  return {pid,start_ticks:Number(parts[19]),uid:fs.statSync(base).uid,
    cmdline_sha256:digest(fs.readFileSync(base+'/cmdline'))};
}
function originalAbsent(expected) {
  try {return processIdentity(expected.pid).start_ticks!==expected.start_ticks;}
  catch(error){if(error.code==='ENOENT')return true;throw error;}
}
async function hold({withLock,onHeld,canRelease,onReleased,wait=pause}) {
  await withLock(async()=>{
    onHeld();
    for (;;) {
      let release=false;try{release=canRelease();}catch(_){}
      if (release) break;
      await wait(100);
    }
  },{waitMs:30000});
  onReleased();
}
async function main(args) {
  requireThat(process.getuid()===0&&process.getgid()===0,'ROOT_OPERATOR_REQUIRED');
  requireThat(args.length===2&&/^[a-f0-9]{64}$/.test(args[0])&&['apply','rollback'].includes(args[1]),'FIXED_INPUTS_REQUIRED');
  const [manifest,cycle]=args;
  const prefix=STAGE+'/lock-'+cycle;
  const info=fs.lstatSync(STAGE);requireThat(info.isDirectory()&&info.uid===0&&info.gid===0&&(info.mode&0o777)===0o700,'PROTECTED_STAGE');
  const input=protectedJson(prefix+'-input.json');
  requireThat(input.manifest_sha256===manifest&&input.cycle===cycle,'INPUT_BINDING');
  const moduleBytes=regular(MODULE).bytes;
  requireThat(digest(moduleBytes)===MODULE_SHA,'SHARED_LOCK_SOURCE_CHANGED');
  requireThat(process.env.ABIT_SHARED_CHATGPT_LOCK_FILE===LOCK,'FIXED_SHARED_LOCK_REQUIRED');
  // Compile the verified bytes once; never re-open service-owned code as root.
  const Module=require('node:module');const verified=new Module(MODULE);
  verified.filename=MODULE;verified.paths=[];verified._compile(moduleBytes.toString('utf8'),MODULE);
  const {withSharedBrowserLock}=verified.exports;
  let held;
  // No automatic unlock after a disconnected operator or repeated signal.
  process.on('SIGTERM',()=>{});process.on('SIGINT',()=>{});
  await hold({
    withLock:withSharedBrowserLock,
    onHeld(){
      const lock=regular(LOCK);const value=JSON.parse(lock.bytes);
      requireThat(value.pid===process.pid&&typeof value.owner==='string','OWNED_LOCK_REQUIRED');
      held={manifest_sha256:manifest,cycle,keeper:processIdentity(process.pid),
        lock_sha256:digest(lock.bytes),lock_device:lock.stat.dev,lock_inode:lock.stat.ino,
        original_node:input.original_node,acquired_utc:new Date().toISOString()};
      save(prefix+'-held.json',held);
    },
    canRelease(){
      const file=prefix+'-release.json';
      if(!fs.existsSync(file))return false;
      let release;
      try{release=protectedJson(file);}catch(_){return false;}
      if(release.manifest_sha256!==manifest||release.cycle!==cycle||
         release.held_sha256!==digest(regular(prefix+'-held.json').bytes))return false;
      if(release.reason==='original_ceased')return originalAbsent(input.original_node);
      if(release.reason==='rollback_without_signal')return release.original_node_unchanged===true&&
        Object.entries(input.original_node).every(([key,value])=>processIdentity(input.original_node.pid)[key]===value);
      return false;
    },
    onReleased(){
      // Only the existing lock module removes its own owner; never unlink a foreign lock.
      save(prefix+'-released.json',{manifest_sha256:manifest,cycle,keeper:held.keeper,
        held_sha256:digest(regular(prefix+'-held.json').bytes),released_utc:new Date().toISOString()});
    },
  });
}
module.exports={hold};
if(require.main===module)main(process.argv.slice(2)).catch(error=>{
  process.stderr.write(JSON.stringify({state:'lock_keeper_held_or_failed',error_type:error.name})+'\n');
  process.exitCode=78;
});
