"""Headless tests of real task components. Provider calls are isolated fixtures."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import subprocess
import tempfile
import threading
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
BUILDER = r'''
const fs=require('node:fs'), path=require('node:path');
const {build}=require('esbuild'), {compile}=require('svelte/compiler');
(async()=>{
const output=process.argv[1];
const virtual={
  stores: `import {writable} from 'svelte/store'; export const user=writable({id:'fixture-admin',role:'admin'}); export const config=writable({features:{}});`,
  constants: `export const WEBUI_API_BASE_URL='/api/v1';`,
  app: `import {writable} from 'svelte/store'; export const page=writable({url:new URL('http://localhost/')}); export const goto=async()=>{};`,
  toast: `export const toast={success:v=>(window.toasts||=[]).push(v),error:v=>(window.toasts||=[]).push(v)};`,
  folders: `export const getFolderById=async()=>null;`,
};
const result=await build({
stdin:{contents:`
import {mount,unmount,tick} from 'svelte'; import {writable} from 'svelte/store';
import Lock from './src/lib/components/chat/ChatGPTModelLock.svelte';
import Account from './src/lib/components/chat/Settings/Connections/ChatGPTAccount.svelte';
import Project from './src/lib/components/layout/Sidebar/Folders/FolderModal.svelte';
import {isSyncedChatGPTProject,projectCreationRequest,filterProjectFoldersForAccount} from './src/lib/utils/chatgptProject.ts';
import {user} from '$lib/stores';
let instance; window.toasts=[];window.submitted=[];window.accountEvents=0;
window.addEventListener('aibit-chatgpt-account-switched',()=>window.accountEvents++);
window.render=async(mode,role='admin')=>{if(instance)await unmount(instance);instance=null;user.set({id:'fixture-admin',role});const target=document.getElementById('fixture');target.replaceChildren();const component={lock:Lock,account:Account,project:Project}[mode];if(component)instance=mount(component,{target,props:mode==='project'?{show:true,onSubmit:async data=>window.submitted.push(data)}:{},context:new Map([['i18n',writable({t:value=>value})]])});await tick();};
window.isSyncedChatGPTProject=isSyncedChatGPTProject;window.filterProjectFoldersForAccount=filterProjectFoldersForAccount;window.projectCreationRequest=projectCreationRequest;
localStorage.token='fixture-not-a-live-token';window.fixtureReady=true;
`,resolveDir:process.cwd(),sourcefile:'project-ui-fixture.js'},
bundle:true,platform:'browser',format:'iife',conditions:['browser'],write:false,
alias:{'$lib':path.join(process.cwd(),'src/lib')},
plugins:[{name:'fixture',setup(b){
b.onResolve({filter:/^\$lib\/stores$/},()=>({path:'stores',namespace:'fixture'}));
b.onResolve({filter:/^\$lib\/constants$/},()=>({path:'constants',namespace:'fixture'}));
b.onResolve({filter:/^\$app\//},()=>({path:'app',namespace:'fixture'}));
b.onResolve({filter:/^svelte-sonner$/},()=>({path:'toast',namespace:'fixture'}));
b.onResolve({filter:/^\$lib\/apis\/folders$/},()=>({path:'folders',namespace:'fixture'}));
b.onLoad({filter:/.*/,namespace:'fixture'},args=>({contents:virtual[args.path],loader:'js',resolveDir:process.cwd()}));
b.onResolve({filter:/(?:Modal|Spinner|XMark|Textarea|Knowledge)\.svelte$/},args=>{
 if(args.path.endsWith('FolderModal.svelte')) return;
 return {path:args.path.endsWith('Modal.svelte')?'modal':'aux',namespace:'fixture-svelte'};
});
b.onLoad({filter:/.*/,namespace:'fixture-svelte'},args=>({contents:compile(args.path==='modal'?'<script>export let show=true;</script>{#if show}<section role="dialog"><slot/></section>{/if}':'<span><slot/></span>',{generate:'client'}).js.code,loader:'js',resolveDir:process.cwd()}));
b.onLoad({filter:/\.svelte$/},args=>({contents:compile(fs.readFileSync(args.path,'utf8'),{filename:args.path,generate:'client',css:'injected'}).js.code,loader:'js',resolveDir:path.dirname(args.path)}));
}}]});
fs.writeFileSync(path.join(output,'fixture.js'),result.outputFiles[0].text);
fs.writeFileSync(path.join(output,'index.html'),'<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{font-family:Arial;margin:16px}input,textarea{max-width:100%;box-sizing:border-box}button{margin:3px}#fixture{max-width:680px}img{max-width:100%}</style><main id="fixture"></main><script src="/fixture.js"></script>');
})().catch(e=>{console.error(e);process.exit(1)});
'''

class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args): pass


def main():
    passed=[]
    with tempfile.TemporaryDirectory(prefix='aibit-project-ui-') as output:
        subprocess.run(['node','-e',BUILDER,output],cwd=ROOT,check=True,timeout=90)
        server=ThreadingHTTPServer(('127.0.0.1',0),partial(Quiet,directory=output))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True,executable_path=r'C:\Program Files\Google\Chrome\Application\chrome.exe')
                context=browser.new_context()
                page=context.new_page();errors=[];calls=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.on('dialog',lambda dialog:dialog.accept())
                state={'active':False,'complete':False,'fail_create':True}
                def route_api(route):
                    route_name=route.request.url.split('/chatgpt-relay/')[-1]
                    body=route.request.post_data_json if route.request.post_data else None
                    calls.append((route_name,body))
                    status=200
                    if route_name=='account/status': data={'connected':True,'account_hint':'test123456','linking':state['active'],'owned':state['active']}
                    elif route_name=='account/relink/start': state['active']=True;data={'ok':True}
                    elif route_name=='account/relink/view':
                        if state['complete']: state['active']=False;data={'connected':True,'account_hint':'new1234567'}
                        else:data={'active':True,'image':'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l2cAAAAASUVORK5CYII=','width':1200,'height':700}
                    elif route_name=='account/relink/cancel':state['active']=False;data={'ok':True}
                    elif route_name=='account/relink/input':data={'ok':True}
                    elif route_name=='sync':data={'synced':0}
                    elif route_name=='projects/create':
                        if state['fail_create']:status=502;data={'detail':'Simulated timeout; retry the same request.'}
                        else:data={'ok':True,'folder_id':'fixture-folder'}
                    else:status=404;data={'detail':'Unexpected fixture route'}
                    route.fulfill(status=status,content_type='application/json',body=json.dumps(data))
                page.route('**/api/v1/chatgpt-relay/**',route_api)
                page.goto(f'http://127.0.0.1:{server.server_port}/');page.wait_for_function('window.fixtureReady')
                page.evaluate("window.render('lock')")
                expect(page.get_by_role('button',name='Selected model: ChatGPT Relay (locked for this project)')).to_be_disabled()
                passed.append('real_locked_model_button_disabled')
                for width in (390,1280):
                    page.set_viewport_size({'width':width,'height':800})
                    for theme in ('','dark'):
                        page.evaluate('(theme)=>document.documentElement.className=theme',theme)
                        expect(page.get_by_text('ChatGPT Relay',exact=True)).to_be_visible()
                        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                passed.append('locked_model_visible_mobile_desktop_light_dark')
                assert page.evaluate("window.isSyncedChatGPTProject({meta:{chatgpt_project_id:'g-p-123456'}})")
                assert not page.evaluate("window.isSyncedChatGPTProject({meta:{}})")
                passed.append('ordinary_project_not_locked')
                rows=[{'id':'local'}, {'id':'old','meta':{'chatgpt_project_id':'g-p-old123','chatgpt_account':'old'}}, {'id':'child','parent_id':'old'}, {'id':'new','meta':{'chatgpt_project_id':'g-p-new123','chatgpt_account':'new'}}]
                assert page.evaluate("rows=>window.filterProjectFoldersForAccount(rows,'new').map(r=>r.id)",rows)==['local','new']
                passed.append('switched_account_sidebar_hides_old_projects_without_deleting_them')
                page.evaluate("window.render('project')")
                expect(page.locator('#folder-name')).to_have_attribute('maxlength','120')
                expect(page.get_by_role('textbox',name='Project Instructions')).to_have_attribute('maxlength','12000')
                assert page.locator('#folder-background-image-input').count()==0
                passed.append('create_form_only_supported_name_and_instructions')
                page.locator('#folder-name').fill('UI fixture project')
                page.get_by_role('textbox',name='Project Instructions').fill('UI fixture instructions')
                page.get_by_role('button',name='Create project',exact=True).click()
                page.wait_for_timeout(700)
                expect(page.get_by_role('alert')).to_contain_text('Simulated timeout')
                expect(page.get_by_role('dialog')).to_be_visible()
                first=next(body for name,body in calls if name=='projects/create')
                page.evaluate("window.render('project')")
                page.locator('#folder-name').fill('UI fixture project')
                page.get_by_role('textbox',name='Project Instructions').fill('UI fixture instructions')
                state['fail_create']=False
                page.get_by_role('button',name='Create project',exact=True).click()
                page.wait_for_function('window.submitted.length===1')
                creates=[body for name,body in calls if name=='projects/create']
                assert len(creates)==2 and creates[0]['request_id']==creates[1]['request_id']
                assert creates[-1]['instructions']=='UI fixture instructions'
                passed.append('failed_create_reopen_reuses_request_id_and_preserves_instructions')
                page.evaluate("window.render('account')")
                expect(page.get_by_role('button',name='Change ChatGPT account')).to_be_visible()
                page.get_by_role('button',name='Change ChatGPT account').click()
                expect(page.get_by_role('img')).to_be_visible()
                field=page.get_by_placeholder('Email, password or verification code')
                expect(field).to_have_attribute('type','password')
                field.fill('test-only-input');page.get_by_role('button',name='Type',exact=True).click()
                expect(field).to_have_value('')
                assert any(name=='account/relink/input' and body.get('text')=='test-only-input' for name,body in calls)
                passed.append('account_input_masked_forwarded_then_cleared')
                page.evaluate("window.render('lock')")
                page.wait_for_timeout(200)
                assert not state['active'] and any(name=='account/relink/cancel' for name,body in calls)
                passed.append('leaving_settings_releases_relink_session')
                page.evaluate("window.render('account')")
                page.get_by_role('button',name='Change ChatGPT account').click()
                expect(page.get_by_role('img')).to_be_visible()
                state['complete']=True
                page.get_by_role('button',name='Refresh view').click()
                page.wait_for_function('window.accountEvents===1')
                assert any(name=='sync' for name,body in calls)
                passed.append('new_account_auto_syncs_even_zero_projects_and_notifies_sidebar')
                page.evaluate("window.render('account','user')")
                assert page.get_by_text('Linked ChatGPT account',exact=True).count()==0
                passed.append('regular_user_cannot_access_shared_account_controls')
                assert not errors, errors
                context.close();browser.close()
        finally:
            server.shutdown();server.server_close();thread.join(timeout=5)
    print(json.dumps({'browser_checks_passed':len(passed),'checks':passed,'live_provider_requests':0}))

if __name__=='__main__':main()
