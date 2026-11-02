// Bundle the real editor and API clients with isolated UI dependencies.
import fs from 'node:fs';
import path from 'node:path';
import { build } from 'esbuild';
import { compile } from 'svelte/compiler';
const out = process.argv[2];
if (!out) throw new Error('Pass a temporary output directory');
fs.mkdirSync(out, { recursive: true });
const stubs = {
	'$lib/constants': "export const WEBUI_API_BASE_URL='/api/v1';",
	'$lib/utils': "export const splitStream=()=>new TransformStream();",
	'$lib/stores': "import {writable} from 'svelte/store'; export const folders=writable([]), channels=writable([]);",
	'$lib/apis/folders': "export const getFolders=async()=>[];",
	'$lib/apis/channels': "export const getChannels=async()=>[];",
	'svelte-sonner': "export const toast={error:s=>window.fixture.toasts.push(s),success:s=>window.fixture.toasts.push(s)};",
	'$lib/components/common/Modal.svelte': '<script>export let show=false; export let size;</script>{#if show}<div role="dialog"><slot /></div>{/if}',
	'$lib/components/icons/XMark.svelte': '<script>export let className;</script><span>X</span>',
	'$lib/components/common/Spinner.svelte': '<span>Loading</span>',
	'$lib/components/automations/ScheduleDropdown.svelte': '<script>export let side,align;export let frequency="DAILY",onceDate="",onceTime="";export function buildRrule(){return "FREQ=DAILY"}export function parseRrule(value){}</script>',
	'$lib/components/automations/ModelDropdown.svelte': '<script>export let model_id="",side,align;</script><select aria-label="Model" bind:value={model_id}><option value="">Select</option><option value="fixture">Fixture</option></select>',
	'$lib/components/automations/DestinationDropdown.svelte': '<script>export let target_type,channel_id,folder_id,folders,channels,side,align;</script>',
	'fixture:App.svelte': `<script>
import {setContext} from 'svelte';import {writable} from 'svelte/store';
import Editor from '$lib/components/AutomationModal.svelte';
setContext('i18n',writable({t:s=>s}));
let show=false,automation=null,cloneFrom=null;
function open(mode){automation=mode==='edit'?JSON.parse(localStorage.fixtureSaved||'null'):null;cloneFrom=mode==='clone'?JSON.parse(localStorage.fixtureSaved||'null'):null;show=true;}
</script>
<button on:click={()=>open('new')}>New fixture</button><button on:click={()=>open('edit')}>Edit fixture</button><button on:click={()=>open('clone')}>Clone fixture</button>
<Editor bind:show {automation} {cloneFrom} />`
};
await build({
	stdin:{contents:"import {mount} from 'svelte';import App from 'fixture:App.svelte';mount(App,{target:document.getElementById('app')});",resolveDir:process.cwd()},
	bundle:true, format:'esm', platform:'browser', conditions:['browser'],
	outfile:path.join(out,'bundle.js'),
	plugins:[{name:'fixture', setup(b){
		b.onResolve({filter:/.*/},args=>{
			if(stubs[args.path]!==undefined)return {path:args.path,namespace:'stub'};
			if(args.path.startsWith('$lib/')) {
				let p=path.resolve('src/lib',args.path.slice(5));
				if(!path.extname(p)) p=path.join(p,'index.ts');
				return {path:p};
			}
		});
		b.onLoad({filter:/.*/,namespace:'stub'},args=>{
			const source=stubs[args.path];
			return {contents:args.path.endsWith('.svelte')?compile(source,{filename:args.path,generate:'client'}).js.code:source,
				loader:'js',resolveDir:process.cwd()};
		});
		b.onLoad({filter:/\.svelte$/},args=>({contents:compile(fs.readFileSync(args.path,'utf8'),{filename:args.path,generate:'client'}).js.code,loader:'js',resolveDir:path.dirname(args.path)}));
	}}]
});
fs.writeFileSync(path.join(out,'index.html'), `<!doctype html><html><body><div id="app"></div><script>
localStorage.token='isolated-fixture-token';
window.fixture={toasts:[],failUpload:false,failSave:false,delay:false,missing:false,uploads:0,saves:0};
window.fetch=async (url,options={})=>{
 const f=window.fixture;
 if(!String(url).startsWith('/api/v1/')) throw new Error('Fixture blocked network');
 if(String(url).includes('/files/?')) {
  f.uploads++; if(f.delay) await new Promise(r=>f.release=r);
  if(f.failUpload)return Response.json({detail:'Fixture upload error'},{status:500});
  const file=options.body.get('file'); const bytes=new Uint8Array(await file.arrayBuffer());
  const id='fixture-'+f.uploads+'-'+Date.now();
  const files=JSON.parse(localStorage.fixtureFiles||'{}');
  files[id]={type:file.type,name:file.name,data:btoa(String.fromCharCode(...bytes)),
   status:'completed',content:file.type.startsWith('image/')?'':'Extracted content of '+file.name};
  localStorage.fixtureFiles=JSON.stringify(files);
  return Response.json({id,meta:{content_type:file.type,size:bytes.length},
   data:{status:'completed',content:files[id].content}});
 }
 if(String(url).includes('/files/')) {
  const id=String(url).split('/')[4], file=JSON.parse(localStorage.fixtureFiles||'{}')[id];
  if(!file||f.missing)return Response.json({detail:'Missing'},{status:404});
  if(String(url).includes('/process/status')) return new Response(
    'data: {"status":"completed"}'+String.fromCharCode(10,10),
    {headers:{'Content-Type':'text/event-stream'}});
  if(!String(url).includes('/content')) return Response.json({
    id,filename:file.name,meta:{content_type:file.type,size:atob(file.data).length},
    data:{status:file.status,content:file.content}
  });
  return new Response(Uint8Array.from(atob(file.data),c=>c.charCodeAt(0)),{headers:{'Content-Type':file.type}});
 }
 if(String(url).includes('/automations/')) {
  if(f.failSave)return Response.json({detail:'Fixture save error'},{status:400});
  const record={...JSON.parse(options.body),id:'automation-fixture',user_id:'fixture-owner'};
  localStorage.fixtureSaved=JSON.stringify(record);f.saves++;
  return Response.json(record);
 }
 throw new Error('Unknown fixture route');
};
</script><script type="module" src="/bundle.js"></script></body></html>`);
console.log('Fixture bundled into temporary directory');
