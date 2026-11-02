"""Headless browser fixture on Dev-02; no live API, login, or scheduler."""
import base64
from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from io import BytesIO
import json
from pathlib import Path
import subprocess
import tempfile
import threading

from PIL import Image
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
def image(fmt):
    out=BytesIO()
    Image.new("RGB",(3,3),"blue").save(out,format=fmt)
    return out.getvalue()
PNG,JPEG=image("PNG"),image("JPEG")

with tempfile.TemporaryDirectory(prefix="automation-image-fixture-") as directory:
    subprocess.run(["node",str(ROOT/"scripts/build_automation_attachment_fixture.mjs"),directory],cwd=ROOT,check=True)
    class QuietHandler(SimpleHTTPRequestHandler):
        extensions_map={**SimpleHTTPRequestHandler.extensions_map, '.js':'application/javascript'}
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(("127.0.0.1",0),partial(QuietHandler,directory=directory))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True, channel="chrome")
            page=browser.new_page()
            page.set_default_timeout(10000)
            errors=[]
            page.on("pageerror",lambda error:(errors.append(str(error)),print("PAGE ERROR:",str(error),flush=True)))
            url=f"http://127.0.0.1:{server.server_port}"
            page.route("**/*",lambda route:route.continue_() if route.request.url.startswith(url) else route.abort())
            page.goto(url)
            def new():
                page.get_by_role("button",name="New fixture",exact=True).click()
                page.get_by_placeholder("Automation title").fill("Images")
                page.locator("textarea").fill("Describe")
                page.get_by_label("Model",exact=True).select_option("fixture")
            def paste(formats,text=""):
                page.locator("textarea").evaluate("""(el,args)=>{
                    const dt=new DataTransfer();
                    if(args.text)dt.setData('text/plain',args.text);
                    for(const [mime,data] of args.images){
                        dt.items.add(new File([Uint8Array.from(atob(data),c=>c.charCodeAt(0))],'paste.'+(mime==='image/png'?'png':'jpg'),{type:mime}));
                    }
                    const event=new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true});
                    el.dispatchEvent(event);
                    window.fixture.pastePrevented=event.defaultPrevented;
                    // Synthetic clipboard events have no browser default insertion.
                    if(!event.defaultPrevented&&args.text){el.setRangeText(args.text,el.selectionStart,el.selectionEnd,'end');el.dispatchEvent(new Event('input',{bubbles:true}));}
                }""",{"text":text,"images":[[mime,base64.b64encode(raw).decode()] for mime,raw in formats]})
            def ready(count):
                expect(page.locator('ul[aria-label="Automation attachments"] li')).to_have_count(count)
                expect(page.get_by_role("status")).to_have_count(0)
            new()
            paste([], " ordinary")
            assert not page.evaluate("fixture.pastePrevented")
            assert "ordinary" in page.locator("textarea").input_value()
            paste([("image/png",PNG),("image/jpeg",JPEG)]," mixed")
            ready(2)
            assert not page.evaluate("fixture.pastePrevented")
            assert "mixed" in page.locator("textarea").input_value()
            page.get_by_role("button",name="Create",exact=True).click()
            expect(page.get_by_role("dialog")).to_have_count(0)
            saved=page.evaluate("JSON.parse(localStorage.fixtureSaved)")
            assert len(saved["data"]["attachments"])==2
            assert all(list(ref)==["id"] for ref in saved["data"]["attachments"])
            page.reload()
            page.get_by_role("button",name="Edit fixture",exact=True).click()
            ready(2)
            page.locator('button[aria-label^="Remove "]').first.click()
            page.get_by_role("button",name="Save",exact=True).click()
            expect(page.get_by_role("dialog")).to_have_count(0)
            assert len(page.evaluate("JSON.parse(localStorage.fixtureSaved).data.attachments"))==1
            page.get_by_role("button",name="Clone fixture",exact=True).click()
            ready(1)
            page.get_by_role("button",name="Create",exact=True).click()
            expect(page.get_by_role("dialog")).to_have_count(0)
            print("PASS paste PNG/JPEG/multiple, ordinary/mixed text, preview, save/reload/edit/remove/clone")

            new()
            page.evaluate("fixture.delay=true")
            paste([("image/png",PNG)])
            expect(page.get_by_role("button",name="Create",exact=True)).to_be_disabled()
            page.get_by_role("button",name="Cancel",exact=True).click()
            new()
            page.evaluate("fixture.delay=false;fixture.release()")
            ready(0)
            page.evaluate("fixture.failUpload=true")
            paste([("image/jpeg",JPEG)])
            expect(page.get_by_role("alert")).to_contain_text("Fixture upload error")
            expect(page.get_by_role("button",name="Create",exact=True)).to_be_disabled()
            page.evaluate("fixture.failUpload=false")
            page.get_by_role("button",name="Retry",exact=True).click()
            ready(1)
            page.evaluate("fixture.failSave=true")
            page.get_by_role("button",name="Create",exact=True).click()
            expect(page.get_by_role("dialog")).to_have_count(1)
            page.wait_for_function("fixture.toasts.includes('Fixture save error')")
            ready(1)
            page.evaluate("fixture.failSave=false")
            page.get_by_role("button",name="Create",exact=True).click()
            expect(page.get_by_role("dialog")).to_have_count(0)
            print("PASS pending save block, cancel/reopen race, upload error/retry, save error/retry")

            page.evaluate("fixture.missing=true")
            page.get_by_role("button",name="Edit fixture",exact=True).click()
            expect(page.get_by_role("alert")).to_contain_text("no longer accessible")
            expect(page.get_by_role("button",name="Save",exact=True)).to_be_disabled()
            page.evaluate("fixture.missing=false")
            page.get_by_role("button",name="Retry",exact=True).click()
            ready(1)
            page.get_by_role("button",name="Cancel",exact=True).click()
            new()
            picker=page.get_by_label("Choose automation attachments")
            picker.set_input_files({"name":"bad.svg","mimeType":"image/svg+xml","buffer":b"<svg/>"})
            ready(0)
            picker.set_input_files({"name":"large.png","mimeType":"image/png","buffer":b"x"*(10*1024*1024+1)})
            ready(0)
            picker.set_input_files([{"name":f"{i}.png","mimeType":"image/png","buffer":PNG} for i in range(11)])
            ready(10)
            assert page.evaluate("fixture.toasts.some(s=>s.includes('at most 10'))")
            page.get_by_role("button",name="Cancel",exact=True).click()
            new()
            picker=page.get_by_label("Choose automation attachments")
            picker.set_input_files([
                {"name":"brief.pdf","mimeType":"application/pdf","buffer":b"%PDF-1.7 fixture"},
                {"name":"instructions.txt","mimeType":"text/plain","buffer":b"Run this task"}
            ])
            ready(2)
            expect(page.locator('ul[aria-label="Automation attachments"] img')).to_have_count(0)
            page.get_by_role("button",name="Create",exact=True).click()
            expect(page.get_by_role("dialog")).to_have_count(0)
            assert len(page.evaluate("JSON.parse(localStorage.fixtureSaved).data.attachments"))==2
            page.reload()
            page.get_by_role("button",name="Edit fixture",exact=True).click()
            ready(2)
            expect(page.get_by_text("brief.pdf",exact=True)).to_have_count(1)
            page.get_by_role("button",name="Cancel",exact=True).click()
            print("PASS PDF/TXT picker, save, metadata hydrate and edit")
            new()
            ready(0)
            page.get_by_role("button",name="Create",exact=True).click()
            expect(page.get_by_role("dialog")).to_have_count(0)
            assert page.evaluate("JSON.parse(localStorage.fixtureSaved).data.attachments.length")==0
            print("PASS missing preview/retry, MIME/size/count validation, file picker, text-only save")
            assert errors==[], errors
            browser.close()
            print("PASS browser fixture: no page errors; external network blocked")
    finally:
        server.shutdown()
        server.server_close()
