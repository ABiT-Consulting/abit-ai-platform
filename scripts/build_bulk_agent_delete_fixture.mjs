// Compile the real agent page, map, bulk controls and deletion client against
// a synthetic localhost-only backend. No live account, scheduler or database.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { compile } from 'svelte/compiler';
import postcss from 'postcss';
import tailwind from '@tailwindcss/postcss';

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const out = process.argv[2];
if (!out) throw new Error('Pass a temporary output directory');
fs.mkdirSync(out, { recursive: true });
const actualSources = new Map();
const slot = '<slot />';
const stubs = {
  '$lib/constants': "export const WEBUI_API_BASE_URL='/api/v1';",
  '$lib/stores': [
    "import {writable} from 'svelte/store';",
    "export const folders=writable([{id:'context-alpha',name:'Alpha Project'},{id:'context-beta',name:'Beta Project'}]);",
    "export const channels=writable([]),settings=writable({keyboardShortcuts:true}),user=writable({id:'fixture-owner',role:'admin'});",
    "export const WEBUI_NAME=writable('ABiT AI'),config=writable({features:{enable_automations:true}});"
  ].join('\n'),
  '$lib/apis/folders': "export const getFolders=async()=>[{id:'context-alpha',name:'Alpha Project'},{id:'context-beta',name:'Beta Project'}];",
  '$lib/apis/channels': "export const getChannels=async()=>[];",
  '$app/navigation': "export const goto=(href)=>window.fixture.navigations.push(href); export const beforeNavigate=(fn)=>window.fixture.navigationGuards.push(fn); export const afterNavigate=()=>{};",
  '$lib/shortcuts': "export const matchKeybinding=()=>null;export const Shortcut={CLOSE_MODAL:'fixture-close'};",
  '$lib/utils/transitions': "export const flyAndScale=()=>({duration:0});",
  'svelte-sonner': "export const toast={error:s=>window.fixture.toasts.push({kind:'error',text:s}),success:s=>window.fixture.toasts.push({kind:'success',text:s}),info:s=>window.fixture.toasts.push({kind:'info',text:s})};",
  'file-saver': "export default {saveAs:()=>{}};",
  '$lib/components/AutomationModal.svelte': '<script>export let show=false;export let automation=null,cloneFrom=null;</script>',
  '$lib/components/automations/AutomationListHeaderActions.svelte': slot,
  '$lib/components/automations/AutomationMenu.svelte': [
    '<script>export let show=false;export let editHandler,cloneHandler,runHandler,deleteHandler,onClose;</script>',
    '<slot />{#if show}<button on:click={deleteHandler}>Fixture individual delete</button>{/if}'
  ].join('\n'),
  '$lib/components/common/Tooltip.svelte': '<script>export let content="",className="",placement;</script><span class={className}><slot /></span>',
  '$lib/components/common/Spinner.svelte': '<script>export let className;</script><span role="status">Loading</span>',
  '$lib/components/common/Select.svelte': '<script>export let value,items=[],onChange=()=>{},align,triggerClass;</script><select aria-label="Agent status filter" bind:value on:change={onChange}>{#each items as item}<option value={item.value}>{item.label}</option>{/each}</select>',
  '$lib/components/common/Dropdown.svelte': '<script>export let align;</script><slot /><slot name="content" />',
  '$lib/components/common/DropdownMenu.svelte': '<script>export let className;</script><div class={className}><slot /></div>',
  '$lib/components/common/Switch.svelte': '<script>import {createEventDispatcher} from "svelte";export let state=false;const dispatch=createEventDispatcher();</script><input type="checkbox" aria-label="Agent enabled" bind:checked={state} on:change={()=>dispatch("change")} />',
  '$lib/components/common/Pagination.svelte': '<script>export let page=1,count=0,perPage=30;</script><nav aria-label="Pagination"><button disabled={page<=1} on:click={()=>page-=1}>Previous page</button><span>Page {page}</span><button disabled={page*perPage>=count} on:click={()=>page+=1}>Next page</button></nav>',
  'fixture:App.svelte': [
    '<script>',
    "import {setContext} from 'svelte';import {writable} from 'svelte/store';",
    "import Page from 'fixture:Page.svelte';",
    "setContext('i18n',writable({t:(s,p={})=>s.replace(/{{\\s*([^}]+?)\\s*}}/g,(_,k)=>String(p[k]??''))}));",
    "setContext('automationsLayout',{setHeader:()=>{},setTotal:(n)=>window.fixture.total=n});",
    '</script>',
    '<main class="fixture-app"><Page /></main>'
  ].join('\n')
};

function resolveFile(candidate) {
  if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) return candidate;
  for (const suffix of ['.ts', '.js', '/index.ts', '/index.js']) {
    if (fs.existsSync(candidate + suffix)) return candidate + suffix;
  }
  return candidate;
}
function compileSource(source, filename) {
  return compile(source, {filename, generate:'client', css:'injected'}).js.code;
}

await build({
  stdin: {
    contents: "import {mount} from 'svelte';import App from 'fixture:App.svelte';mount(App,{target:document.getElementById('app')});",
    resolveDir: root
  },
  bundle: true, format: 'esm', platform: 'browser', conditions: ['browser'],
  outfile: path.join(out, 'bundle.js'),
  plugins: [{
    name: 'bulk-agent-fixture',
    setup(b) {
      b.onResolve({filter: /.*/}, (args) => {
        if (stubs[args.path] !== undefined) return {path: args.path, namespace: 'stub'};
        if (args.path === 'fixture:Page.svelte') return {path: path.join(root, 'src/routes/(app)/automations/+page.svelte')};
        if (args.path.startsWith('$lib/')) return {path: resolveFile(path.join(root, 'src/lib', args.path.slice(5)))};
      });
      b.onLoad({filter: /.*/, namespace: 'stub'}, (args) => ({
        contents: args.path.endsWith('.svelte') ? compileSource(stubs[args.path], args.path) : stubs[args.path],
        loader: 'js', resolveDir: root
      }));
      b.onLoad({filter: /\.(svelte|ts)$/}, (args) => {
        const bytes = fs.readFileSync(args.path);
        actualSources.set(path.relative(root, args.path).replaceAll('\\','/'), crypto.createHash('sha256').update(bytes).digest('hex'));
        return {
          contents: args.path.endsWith('.svelte') ? compileSource(bytes.toString('utf8'), args.path) : bytes.toString('utf8'),
          loader: args.path.endsWith('.svelte') ? 'js' : 'ts',
          resolveDir: path.dirname(args.path)
        };
      });
    }
  }]
});

const cssInput = [
  '@import "tailwindcss" source(none);',
  '@source "../src/routes/(app)/automations/+page.svelte";',
  '@source "../src/lib/components/automations/ContextMap.svelte";',
  '@source "../src/lib/components/automations/BulkAgentActions.svelte";',
  '@source "../src/lib/components/common/ConfirmDialog.svelte";',
  '@custom-variant dark (&:where(.dark, .dark *));',
  '@theme { --color-gray-850: #202126; --font-sans: Arial, sans-serif; }',
  'html,body{margin:0;font-family:Arial,sans-serif;font-size:14px;background:#fff;color:#111827;}',
  'html.dark,html.dark body{background:#171717;color:#e5e7eb;}',
  'body{min-height:100vh}.fixture-app{padding:16px;min-height:100vh;}',
  'button,input,select{font:inherit}button{cursor:pointer}button:disabled{cursor:default}',
  '.fixture-app>div{min-height:calc(100vh - 32px)}'
].join('\n');
const css = await postcss([tailwind({base:root})]).process(cssInput, {from:path.join(root,'scripts/bulk-agent-fixture.css')});
fs.writeFileSync(path.join(out, 'fixture.css'), css.css);

function installMock() {
  localStorage.token = 'isolated-fixture-token';
  function makeAgent(id, name, state='idle', context='context-alpha') {
    return {
      id, user_id:'fixture-owner', name, folder_id:context, state,
      is_active:state!=='disabled', data:{rrule:'FREQ=DAILY',prompt:'Isolated acceptance fixture',model_id:'fixture-model',target:{type:'chat'}},
      meta:{}, last_run_at:null, next_run_at:null, created_at:0, updated_at:0,
      last_run:state==='running'?{id:'run-'+id,automation_id:id,chat_id:null,status:'running',error:null,created_at:0}:null,
      next_runs:[]
    };
  }
  window.fixture = {
    agents: [
      makeAgent('idle-a','Alpha Planner'),
      makeAgent('idle-b','Alpha Developer','paused'),
      makeAgent('disabled-c','Beta Cleanup','disabled','context-beta'),
      makeAgent('running-d','Running Worker','running','context-beta')
    ],
    makeAgent, deletes:[], requests:[], toasts:[], navigations:[], navigationGuards:[],
    failures:{}, pending:{}, activeDeletes:0, maxActiveDeletes:0, mapReads:0, mapResponses:0, delayNextMap:false, pendingMap:null, total:null,
    release(id) { const release=this.pending[id]; delete this.pending[id]; if(release)release(); }
  };
  const count=Number(new URLSearchParams(location.search).get('count')||0);
  if(count>0&&count<=100)window.fixture.agents=Array.from({length:count},(_,index)=>makeAgent('page-'+String(index+1).padStart(2,'0'),'Agent '+String(index+1).padStart(2,'0')));
  window.fetch = async (input, options={}) => {
    const raw = String(input);
    if (!raw.startsWith('/api/v1/automations/')) throw new Error('Fixture blocked unexpected network: '+raw);
    const f = window.fixture;
    const url = new URL(raw, location.origin);
    const method = options.method || 'GET';
    f.requests.push({url:raw,method});
    if (options.headers?.authorization !== 'Bearer isolated-fixture-token') throw new Error('Fixture authentication header missing');
    if (url.pathname === '/api/v1/automations/context-map') {
      f.mapReads++;
      const snapshot = {
        contexts:[{id:'context-alpha',name:'Alpha Project'},{id:'context-beta',name:'Beta Project'}],
        agents:f.agents.map(a=>({
          id:'automation:'+a.id,name:a.name,kind:'scheduled',context_id:a.folder_id,
          status:a.state,last_activity:null,next_run:null,model:a.data.model_id,enabled:a.is_active,
          href:'/automations/'+a.id,chat_href:null,last_error:false,order:null,handoffs:[]
        })),
        edges:[],recorded_count:0,generated_at:Date.now()/1000
      };
      if(f.delayNextMap) { f.delayNextMap=false; await new Promise(resolve=>f.pendingMap=resolve); }
      f.mapResponses++;
      return Response.json(snapshot);
    }
    if (url.pathname === '/api/v1/automations/list') {
      const query=(url.searchParams.get('query')||'').toLowerCase();
      const status=url.searchParams.get('status')||'all';
      const page=Number(url.searchParams.get('page')||1);
      const matching=f.agents.filter(a=>(!query||a.name.toLowerCase().includes(query))&&(status==='all'||(status==='active'?a.is_active:!a.is_active)));
      return Response.json({items:matching.slice((page-1)*30,page*30),total:matching.length});
    }
    if (url.pathname.endsWith('/latest-message')) return Response.json({text:'',at:null,chat_href:null,source_chat:null});
    if (url.pathname.endsWith('/runs')) return Response.json([]);
    const parts=url.pathname.split('/');
    const id=decodeURIComponent(parts[4]||'');
    if (method==='DELETE' && parts[5]==='delete') {
      f.deletes.push(id); f.activeDeletes++; f.maxActiveDeletes=Math.max(f.maxActiveDeletes,f.activeDeletes);
      try {
        const failure=f.failures[id];
        if(failure==='delay')await new Promise(resolve=>f.pending[id]=resolve);
        if(failure==='network')throw new TypeError('Synthetic network interruption');
        if(failure==='403')return Response.json({detail:'Permission denied for fixture agent'},{status:403});
        if(failure==='false')return Response.json(false);
        if(failure==='null')return Response.json(null);
        if(!f.agents.some(a=>a.id===id))return Response.json({detail:'Fixture agent missing'},{status:404});
        f.agents=f.agents.filter(a=>a.id!==id);
        return Response.json(true);
      } finally {f.activeDeletes--;}
    }
    if(method==='GET'&&parts.length===5) {
      const agent=f.agents.find(a=>a.id===id);
      return agent?Response.json(agent):Response.json({detail:'Fixture agent missing'},{status:404});
    }
    throw new Error('Fixture does not implement '+method+' '+url.pathname);
  };
}
const html='<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/fixture.css"><title>Bulk agent delete acceptance fixture</title></head><body><div id="app"></div><script>('+installMock.toString()+')();</script><script type="module" src="/bundle.js"></script></body></html>';
fs.writeFileSync(path.join(out,'index.html'),html);
fs.writeFileSync(path.join(out,'source-manifest.json'),JSON.stringify({
  purpose:'Isolated compiled-source browser acceptance; no live deployment or backend execution',
  harnessSha256:crypto.createHash('sha256').update(fs.readFileSync(fileURLToPath(import.meta.url))).digest('hex'),
  actualSources:Object.fromEntries([...actualSources].sort()),
  stubbedModules:Object.keys(stubs).filter(x=>!x.startsWith('fixture:')),
  css:'Real Tailwind utility styles for actual tested page/map/bulk/confirmation source; fixture shell and unrelated controls are synthetic'
},null,2));
console.log('Compiled actual agent page, map, bulk actions, confirmation and API client into isolated fixture.');
