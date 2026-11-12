const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const code = fs.readFileSync(path.resolve(__dirname, '../task_sidebar.js'), 'utf8');
const agent = 'https://abit.ai/automations/7831722b-6262-5ec9-b589-56e040b31cb4';
const chat = 'https://abit.ai/c/cb92d18d-75d2-4ff2-9031-e8c0205237c9';

async function setup(call) {
  let refresh;
  const side = {panel:null,
    querySelector(selector) { return selector === '.abit-task-agent-controls' ? this.panel : null; },
    appendChild(element) { this.panel = element; }};
  const root = {querySelector(selector) { return selector === '.layout-side-section' ? side : null; }};
  const document = {
    getElementById() {return null;},
    createElement() {return {innerHTML:'', className:'', setAttribute(){}, querySelectorAll(){return [];}};},
    head:{appendChild(){}},
  };
  const id='TASK-2026-05655';
  const frappe={get_route:()=>['Form','Task',id],ui:{form:{on(dt, handlers){refresh=handlers.refresh;}}},call};
  const window={};
  vm.runInNewContext(code,{window,document,frappe,URL,setInterval:()=>1,clearInterval:()=>{}});
  const frm={doctype:'Task',doc:{name:id,status:'Open',custom_abit_ai_agent_url:'',
    custom_ai_relay_execution_status:'Completed',custom_ai_relay_execution_detail:JSON.stringify({agent,evidence_url:chat})},
    is_new:()=>false,page:{wrapper:root}};
  refresh(frm);
  await new Promise(resolve=>setImmediate(resolve));
  return side.panel.innerHTML;
}

test('lost agent link remains visible during ERP status failure, actions remain disabled',async()=>{
  const html=await setup(async()=>{throw Error('temporarily unavailable');});
  assert.match(html,/Needs Work/);
  assert.ok(html.includes(agent));
  assert.ok(html.includes(chat));
  assert.match(html,/data-agent-action="restart" disabled/);
  assert.match(html,/Retrying automatically/);
});

test('verified running state enables stop and retains link',async()=>{
  const html=await setup(async()=>({message:{connected:true,status:'Running',can_stop:true,
    can_start:false,can_restart:false,agent_url:agent,abit_chat_url:chat}}));
  assert.match(html,/Status:<\/strong> Running/);
  assert.match(html,/data-agent-action="stop" >/);
  assert.match(html,/data-agent-action="restart" disabled/);
  assert.ok(html.includes(agent));
});

test('malicious audit links are never rendered',async()=>{
  const html=await setup(async()=>({message:{connected:false,agent_url:'javascript:evil()',
    abit_chat_url:'https://evil.example/c/ff'}}));
  assert.ok(html.includes(agent)); // benign Task evidence is preserved
  assert.ok(!html.includes('javascript:evil'));
  assert.ok(!html.includes('evil.example'));
});
