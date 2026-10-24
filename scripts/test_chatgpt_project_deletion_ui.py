"""Headless deletion-control tests using the real menu and protection helper.

Only local component fixtures are used; no live project or API is changed.
"""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import subprocess
import tempfile
import threading
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
BUILDER=r'''
const fs=require('node:fs'),path=require('node:path');
const {build}=require('esbuild'),{compile}=require('svelte/compiler');
(async()=>{
const output=process.argv[1];
const result=await build({
stdin:{contents:`
import {mount,unmount,tick} from 'svelte';
import {writable} from 'svelte/store';
import Menu from './src/lib/components/layout/Sidebar/Folders/FolderMenu.svelte';
import {isProjectDeletionProtected} from './src/lib/utils/chatgptProject.ts';
let menu;window.deleted=0;window.edited=0;window.exported=0;
window.render=async(folder)=>{
 if(menu)await unmount(menu);document.getElementById('fixture').replaceChildren();
 menu=mount(Menu,{target:document.getElementById('fixture'),props:{
  deletionDisabled:isProjectDeletionProtected(folder),onDelete:()=>window.deleted++,
  onEdit:()=>window.edited++,onExport:()=>window.exported++
 },context:new Map([['i18n',writable({t:value=>value})]])});await tick();
};window.protected=isProjectDeletionProtected;window.fixtureReady=true;
`,resolveDir:process.cwd(),sourcefile:'deletion-fixture.js'},
bundle:true,platform:'browser',format:'iife',conditions:['browser'],write:false,
alias:{'$lib':path.join(process.cwd(),'src/lib')},
plugins:[{name:'isolated-menu',setup(b){
 b.onResolve({filter:/\.svelte$/},args=>{
  if(args.path.endsWith('FolderMenu.svelte'))return;
  return {path:args.path,namespace:'stub'};
 });
 b.onLoad({filter:/.*/,namespace:'stub'},args=>({
  contents:compile(args.path.endsWith('/Dropdown.svelte')?
   '<script>export let show=false;export let align="start";export let onOpenChange=()=>{};</script><slot/><slot name="content"/>':
   '<span><slot/></span>',{generate:'client'}).js.code,loader:'js',resolveDir:process.cwd()
 }));
 b.onLoad({filter:/\.svelte$/},args=>({contents:compile(fs.readFileSync(args.path,'utf8'),{
  filename:args.path,generate:'client',css:'injected'
 }).js.code,loader:'js',resolveDir:path.dirname(args.path)}));
}}]});
fs.writeFileSync(path.join(output,'fixture.js'),result.outputFiles[0].text);
fs.writeFileSync(path.join(output,'index.html'),'<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><main id="fixture"></main><script src="/fixture.js"></script>');
})().catch(e=>{console.error(e);process.exit(1)});
'''

class Quiet(SimpleHTTPRequestHandler):
    def log_message(self,*args):pass


def main():
    passed=[]
    # Both real menu callers must forward the computed flag and protect their
    # deletion callbacks too, including previously opened confirmation dialogs.
    for relative in ['src/lib/components/layout/Sidebar/RecursiveFolder.svelte',
                     'src/lib/components/chat/Placeholder/FolderTitle.svelte']:
        source=(ROOT/relative).read_text(encoding='utf-8-sig')
        assert 'deletionDisabled={deletionProtected}' in source
        assert 'deletionProtected = isProjectDeletionProtected(' in source
        handler=source.split('const deleteHandler = async () => {',1)[1].split('const res = await deleteFolderById',1)[0]
        assert 'if (deletionProtected)' in handler and 'return;' in handler
        assert 'if (!deletionProtected) showDeleteConfirm = true;' in source
    passed.append('sidebar_and_project_header_wire_policy_and_guard_confirmation')
    with tempfile.TemporaryDirectory(prefix='aibit-delete-ui-') as output:
        subprocess.run(['node','-e',BUILDER,output],cwd=ROOT,check=True,timeout=90)
        server=ThreadingHTTPServer(('127.0.0.1',0),partial(Quiet,directory=output))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True,executable_path=r'C:\Program Files\Google\Chrome\Application\chrome.exe')
                context=browser.new_context();page=context.new_page();errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.server_port}/');page.wait_for_function('window.fixtureReady')
                cases=[('synced_project',{'meta':{'chatgpt_project_id':'g-p-synced123456'}}),
                       ('abit_created_project',{'meta':{'chatgpt_project_id':'g-p-created123456'}}),
                       ('legacy_mapping',{'deletion_protected':True}),
                       ('containing_folder',{'id':'parent','deletion_protected':True})]
                for label,folder in cases:
                    page.evaluate('folder=>window.render(folder)',folder)
                    button=page.get_by_role('button',name='Delete',exact=True)
                    expect(button).to_be_disabled()
                    expect(button).to_have_attribute('title','ChatGPT-linked projects cannot be deleted from ABIT.ai.')
                    button.evaluate('(b)=>{b.click();b.dispatchEvent(new MouseEvent("click",{bubbles:true}));}')
                    assert page.evaluate('window.deleted')==0
                    passed.append(label+'_delete_disabled_and_programmatic_event_blocked')
                page.get_by_role('button',name='Edit',exact=True).click()
                page.get_by_role('button',name='Export',exact=True).click()
                assert page.evaluate('window.edited===1&&window.exported===1')
                passed.append('project_edit_and_export_remain_available')
                page.evaluate("window.render({id:'ordinary',meta:{},deletion_protected:false})")
                expect(page.get_by_role('button',name='Delete',exact=True)).to_be_enabled()
                page.get_by_role('button',name='Delete',exact=True).click()
                assert page.evaluate('window.deleted')==1
                passed.append('ordinary_folder_delete_unchanged')
                assert not errors,errors
                context.close();browser.close()
        finally:
            server.shutdown();server.server_close();thread.join(timeout=5)
    print(json.dumps({'deletion_ui_checks_passed':len(passed),'checks':passed,'live_deletions':0}))

if __name__=='__main__':main()
