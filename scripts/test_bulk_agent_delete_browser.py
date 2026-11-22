"""Isolated browser acceptance of the compiled bulk-delete implementation.

Runs the real agent page/map/bulk controls/confirmation/API client with a local
synthetic backend. Never authenticates to or deletes data from a live system.
Unrelated controls are documented stubs in browser-source-manifest.json.
"""
from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import json
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import traceback
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "deployment" / "bulk-agent-delete-20261122"
EVIDENCE.mkdir(parents=True, exist_ok=True)
HARNESS = {name: hashlib.sha256((ROOT / 'scripts' / name).read_bytes()).hexdigest() for name in ['build_bulk_agent_delete_fixture.mjs', 'test_bulk_agent_delete_browser.py']}
results = []

with tempfile.TemporaryDirectory(prefix="bulk-agent-delete-fixture-") as directory:
    subprocess.run(
        ["node", str(ROOT / "scripts/build_bulk_agent_delete_fixture.mjs"), directory],
        cwd=ROOT, check=True,
    )
    shutil.copyfile(Path(directory) / "source-manifest.json", EVIDENCE / "browser-source-manifest.json")

    class QuietHandler(SimpleHTTPRequestHandler):
        extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".js": "application/javascript"}
        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=directory))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, channel="chrome")
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.set_default_timeout(10000)
            errors = []
            blocked = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            def network(route):
                if urlparse(route.request.url).netloc == urlparse(origin).netloc:
                    route.continue_()
                else:
                    blocked.append(route.request.url)
                    route.abort()
            page.route("**/*", network)

            def reset(query=""):
                errors.clear()
                page.set_viewport_size({"width": 1280, "height": 900})
                page.goto(origin + "/" + query)
                expect(page.get_by_test_id("context-map")).to_be_visible()
                expect(page.get_by_role("checkbox", name=re.compile("^Select all filtered"))).to_be_visible()

            def checkbox(name):
                return page.get_by_role("checkbox", name="Select agent " + name, exact=True)

            def master():
                return page.get_by_role("checkbox", name=re.compile("^Select (all filtered|this page)"))

            def delete_button():
                return page.get_by_test_id("bulk-agent-delete")

            def open_dialog():
                delete_button().click()
                dialog = page.get_by_role("dialog")
                expect(dialog).to_be_visible()
                return dialog

            def confirm(dialog=None):
                dialog = dialog or page.get_by_role("dialog")
                dialog.get_by_role("button", name=re.compile("^Delete")).click()

            def deletes():
                return page.evaluate("window.fixture.deletes")

            def no_errors():
                assert not errors, f"Browser errors: {errors}"
                assert not blocked, f"Unexpected external network attempted: {blocked}"

            def run(name, test):
                try:
                    test()
                    no_errors()
                    results.append({"name": name, "status": "passed"})
                    print("PASS:", name, flush=True)
                except Exception as exc:
                    results.append({"name": name, "status": "failed", "error": str(exc), "traceback": traceback.format_exc()})
                    print("FAIL:", name, "-", str(exc), flush=True)
                    page.screenshot(path=str(EVIDENCE / ("failure-" + re.sub(r"[^a-z0-9]+", "-", name.lower())[:70] + ".png")), full_page=False)

            def empty_running_clear():
                reset()
                expect(delete_button()).to_be_disabled()
                expect(checkbox("Running Worker")).to_be_disabled()
                master().check()
                expect(delete_button()).to_have_text(re.compile(r"3"))
                assert not checkbox("Running Worker").is_checked()
                expect(checkbox("Alpha Planner")).to_be_checked()
                expect(checkbox("Beta Cleanup")).to_be_checked()
                page.get_by_role("button", name="Clear selection", exact=True).click()
                expect(delete_button()).to_be_disabled()
                assert deletes() == []
            run("Empty selection, running-agent exclusion and clear selection", empty_running_clear)

            def filter_scope():
                reset()
                page.get_by_role("textbox", name="Search agents", exact=True).fill("Alpha")
                master().check()
                expect(delete_button()).to_have_text(re.compile(r"2"))
                page.get_by_role("textbox", name="Search agents", exact=True).fill("Beta")
                expect(delete_button()).to_be_disabled()
                expect(checkbox("Beta Cleanup")).not_to_be_checked()
                page.get_by_role("textbox", name="Search agents", exact=True).fill("no matching fixture")
                expect(master()).to_be_disabled()
                assert deletes() == []
            run("Filtered select-all and filter changes never retain hidden targets", filter_scope)

            def context_filter_scope():
                reset()
                page.get_by_label("Filter by context", exact=True).select_option("context-alpha")
                master().check()
                expect(delete_button()).to_have_text(re.compile(r"2"))
                dialog = open_dialog()
                expect(dialog).to_contain_text("Alpha Planner")
                expect(dialog).to_contain_text("Alpha Developer")
                expect(dialog).not_to_contain_text("Beta Cleanup")
                dialog.get_by_role("button", name="Cancel", exact=True).click()
                assert deletes() == []
            run("Context filtering bounds the exact confirmation target list", context_filter_scope)

            def cancellation():
                reset()
                checkbox("Alpha Planner").check()
                dialog = open_dialog()
                expect(dialog).to_contain_text(re.compile("run history", re.I))
                expect(dialog).to_contain_text(re.compile("cannot be undone|permanent", re.I))
                dialog.get_by_role("button", name="Cancel", exact=True).click()
                expect(page.get_by_role("dialog")).not_to_be_visible()
                assert deletes() == []
                expect(checkbox("Alpha Planner")).to_be_checked()
                open_dialog()
                page.keyboard.press("Escape")
                expect(page.get_by_role("dialog")).not_to_be_visible()
                assert deletes() == []
            run("Cancel and Escape make zero deletion requests", cancellation)

            def exact_raw_ids():
                reset()
                checkbox("Alpha Planner").check()
                checkbox("Beta Cleanup").check()
                confirm(open_dialog())
                page.wait_for_function("window.fixture.deletes.length===2 && window.fixture.activeDeletes===0")
                expect(checkbox("Alpha Planner")).to_have_count(0)
                expect(checkbox("Beta Cleanup")).to_have_count(0)
                assert sorted(deletes()) == ["disabled-c", "idle-a"]
                assert len(set(deletes())) == 2
                expect(checkbox("Alpha Developer")).to_be_visible()
                expect(checkbox("Running Worker")).to_be_visible()
            run("Confirmed targets use raw API IDs exactly once; unselected agents remain", exact_raw_ids)

            def refresh_no_expansion():
                reset()
                master().check()
                page.evaluate("window.fixture.agents.push(window.fixture.makeAgent('new-e','New Arriving Agent'))")
                page.get_by_role("button", name="Refresh", exact=True).click()
                expect(checkbox("New Arriving Agent")).to_be_visible()
                expect(checkbox("New Arriving Agent")).not_to_be_checked()
                expect(delete_button()).to_have_text(re.compile(r"3"))
                dialog = open_dialog()
                expect(dialog).not_to_contain_text("New Arriving Agent")
                confirm(dialog)
                page.wait_for_function("window.fixture.deletes.length===3 && window.fixture.activeDeletes===0")
                assert sorted(deletes()) == ["disabled-c", "idle-a", "idle-b"]
                expect(checkbox("New Arriving Agent")).to_be_visible()
            run("Live refresh never expands an existing selection", refresh_no_expansion)

            def confirmation_snapshot():
                reset()
                checkbox("Alpha Planner").check()
                dialog = open_dialog()
                page.evaluate("window.fixture.agents.push(window.fixture.makeAgent('new-e','New Arriving Agent'))")
                # Invoke the same refresh handler the periodic poll uses while
                # the modal holds focus, to emulate a concurrent server update.
                page.get_by_role("button", name="Refresh", exact=True).dispatch_event("click")
                page.wait_for_function("window.fixture.mapReads>=2")
                expect(dialog).to_contain_text("Alpha Planner")
                expect(dialog).not_to_contain_text("New Arriving Agent")
                confirm(dialog)
                page.wait_for_function("window.fixture.deletes.length===1 && window.fixture.activeDeletes===0")
                assert deletes() == ["idle-a"]
            run("Confirmation is bound to an immutable target snapshot", confirmation_snapshot)

            def busy_duplicate():
                reset()
                page.evaluate("window.fixture.failures['idle-a']='delay'")
                checkbox("Alpha Planner").check()
                confirm(open_dialog())
                page.wait_for_function("Boolean(window.fixture.pending['idle-a'])")
                expect(delete_button()).to_be_disabled()
                delete_button().dispatch_event("click")
                expect(page.get_by_role("dialog")).to_have_count(0)
                assert deletes() == ["idle-a"]
                page.evaluate("window.fixture.release('idle-a')")
                page.wait_for_function("window.fixture.activeDeletes===0")
                expect(checkbox("Alpha Planner")).to_have_count(0)
                assert deletes() == ["idle-a"]
                assert page.evaluate("window.fixture.maxActiveDeletes") == 1
            run("In-progress deletion blocks repeat submission", busy_duplicate)

            def partial_failure_retry():
                reset()
                page.evaluate("window.fixture.failures={'idle-b':'403','disabled-c':'false'}")
                master().check()
                confirm(open_dialog())
                page.wait_for_function("window.fixture.deletes.length===3 && window.fixture.activeDeletes===0")
                expect(checkbox("Alpha Planner")).to_have_count(0)
                expect(checkbox("Alpha Developer")).to_be_checked()
                expect(checkbox("Beta Cleanup")).to_be_checked()
                expect(delete_button()).to_have_text(re.compile(r"2"))
                page.evaluate("window.fixture.failures={}")
                confirm(open_dialog())
                page.wait_for_function("window.fixture.deletes.length===5 && window.fixture.activeDeletes===0")
                assert deletes().count("idle-a") == 1
                assert deletes().count("idle-b") == 2
                assert deletes().count("disabled-c") == 2
                expect(checkbox("Alpha Developer")).to_have_count(0)
                expect(checkbox("Beta Cleanup")).to_have_count(0)
            run("Partial success reports failures and retries only retained failed targets", partial_failure_retry)

            def ambiguous_network_failure():
                reset()
                page.evaluate("window.fixture.failures={'idle-a':'network','idle-b':'null'}")
                checkbox("Alpha Planner").check()
                checkbox("Alpha Developer").check()
                confirm(open_dialog())
                page.wait_for_function("window.fixture.deletes.length===2 && window.fixture.activeDeletes===0")
                expect(checkbox("Alpha Planner")).to_be_checked()
                expect(checkbox("Alpha Developer")).to_be_checked()
                expect(delete_button()).to_have_text(re.compile(r"2"))
                assert page.evaluate("window.fixture.agents.length") == 4
            run("Network errors and null responses are not reported as successful deletion", ambiguous_network_failure)

            def running_refresh():
                reset()
                checkbox("Alpha Planner").check()
                page.evaluate("const a=window.fixture.agents.find(a=>a.id==='idle-a');a.state='running';a.last_run={status:'running'};")
                page.get_by_role("button", name="Refresh", exact=True).click()
                expect(checkbox("Alpha Planner")).to_be_disabled()
                expect(checkbox("Alpha Planner")).not_to_be_checked()
                expect(delete_button()).to_be_disabled()
                assert deletes() == []
            run("A refreshed running agent is removed from selection", running_refresh)

            def keyboard_list():
                reset()
                page.get_by_role("button", name="Manage agents", exact=True).click()
                expect(page.get_by_role("checkbox", name=re.compile("^Select this page"))).to_be_visible()
                control = checkbox("Alpha Planner")
                control.focus()
                page.keyboard.press("Space")
                expect(control).to_be_checked()
                assert page.evaluate("window.fixture.navigations") == []
                expect(master()).not_to_be_checked()
                assert master().evaluate("(el)=>el.indeterminate")
                page.keyboard.press("Space")
                expect(control).not_to_be_checked()
                assert page.evaluate("window.fixture.navigations") == []
                assert deletes() == []
            run("List checkbox Space handling and mixed select-all state preserve navigation", keyboard_list)

            def page_scope():
                reset("?count=35")
                page.get_by_role("button", name="Manage agents", exact=True).click()
                expect(checkbox("Agent 01")).to_be_visible()
                master().check()
                expect(delete_button()).to_have_text(re.compile(r"30"))
                dialog = open_dialog()
                expect(dialog).to_contain_text("Agent 30")
                expect(dialog).not_to_contain_text("Agent 31")
                dialog.get_by_role("button", name="Cancel", exact=True).click()
                page.get_by_role("button", name="Next page", exact=True).click()
                expect(checkbox("Agent 31")).to_be_visible()
                expect(delete_button()).to_be_disabled()
                master().check()
                expect(delete_button()).to_have_text(re.compile(r"5"))
                confirm(open_dialog())
                page.wait_for_function("window.fixture.deletes.length===5 && window.fixture.activeDeletes===0")
                assert sorted(deletes()) == [f"page-{n:02d}" for n in range(31,36)]
                assert page.evaluate("window.fixture.agents.length") == 30
            run("Manage agents selects only the labeled page and resets on page change", page_scope)

            def clear_on_view_switch():
                reset()
                page.get_by_role("button", name="Manage agents", exact=True).click()
                checkbox("Alpha Planner").check()
                expect(delete_button()).to_have_text(re.compile(r"1"))
                page.get_by_role("button", name="Agent map", exact=True).click()
                expect(delete_button()).to_be_disabled()
                page.get_by_role("button", name="Manage agents", exact=True).click()
                expect(checkbox("Alpha Planner")).not_to_be_checked()
                expect(delete_button()).to_be_disabled()
                assert deletes() == []
            run("Switching views clears selection before returning to Manage agents", clear_on_view_switch)

            def stale_map_response():
                reset()
                page.evaluate("window.fixture.failures['idle-a']='delay'")
                checkbox("Alpha Planner").check()
                confirm(open_dialog())
                page.wait_for_function("Boolean(window.fixture.pending['idle-a'])")
                baseline = page.evaluate("window.fixture.mapResponses")
                page.evaluate("window.fixture.delayNextMap=true")
                # The real refresh handler also runs periodically. Dispatching
                # it here makes the concurrent-response order deterministic.
                page.get_by_role("button", name="Refresh", exact=True).dispatch_event("click")
                page.wait_for_function("Boolean(window.fixture.pendingMap)")
                page.evaluate("window.fixture.release('idle-a')")
                page.wait_for_function("window.fixture.activeDeletes===0")
                expect(checkbox("Alpha Planner")).to_have_count(0)
                page.evaluate("window.fixture.pendingMap();window.fixture.pendingMap=null")
                page.wait_for_function("(n)=>window.fixture.mapResponses>n", arg=baseline)
                expect(checkbox("Alpha Planner")).to_have_count(0)
                expect(checkbox("Alpha Developer")).to_be_visible()
                assert deletes() == ["idle-a"]
            run("Delayed predelete map responses cannot resurrect confirmed-deleted agents", stale_map_response)

            def individual_delete_regression():
                reset()
                page.get_by_role("button", name="Delete agent Alpha Planner", exact=True).click()
                dialog = page.get_by_role("dialog", name="Delete agent?", exact=True)
                expect(dialog).to_contain_text("Alpha Planner")
                dialog.get_by_role("button", name="Confirm", exact=True).click()
                page.wait_for_function("window.fixture.deletes.length===1 && window.fixture.activeDeletes===0")
                expect(checkbox("Alpha Planner")).to_have_count(0)
                assert deletes() == ["idle-a"]
            run("Existing individual-delete confirmation remains functional", individual_delete_regression)

            def layout_evidence():
                reset()
                checkbox("Alpha Planner").check()
                checkbox("Beta Cleanup").check()
                page.screenshot(path=str(EVIDENCE / "bulk-delete-map-desktop-light.png"), full_page=False)
                page.evaluate("document.documentElement.classList.add('dark')")
                page.screenshot(path=str(EVIDENCE / "bulk-delete-map-desktop-dark.png"), full_page=False)
                for width in [320, 375, 430]:
                    page.set_viewport_size({"width":width,"height":900})
                    delete_button().scroll_into_view_if_needed()
                    box=delete_button().bounding_box()
                    assert box and box["x"] >= -1 and box["x"]+box["width"] <= width+1, (width,box)
                page.set_viewport_size({"width":375,"height":900})
                dialog=open_dialog()
                box=dialog.bounding_box()
                assert box and box["x"] >= -1 and box["x"]+box["width"] <= 376,box
                page.screenshot(path=str(EVIDENCE / "bulk-delete-confirm-mobile-dark.png"), full_page=False)
                dialog.get_by_role("button", name="Cancel", exact=True).click()
                page.get_by_role("button", name="Manage agents", exact=True).click()
                checkbox("Alpha Planner").check()
                page.screenshot(path=str(EVIDENCE / "bulk-delete-list-mobile-dark.png"), full_page=False)
                assert deletes()==[]
            run("Desktop light/dark and 320/375/430px bulk-control layout", layout_evidence)

            browser.close()
    finally:
        server.shutdown()
        server.server_close()

report = {
    "scope": "Isolated compiled-source browser acceptance with synthetic localhost backend; not a live deployment",
    "harness_sha256": HARNESS,
    "cases": len(results),
    "passed": sum(r["status"]=="passed" for r in results),
    "failed": sum(r["status"]=="failed" for r in results),
    "results": results
}
(EVIDENCE / "browser-results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps({key:report[key] for key in ["scope","cases","passed","failed"]}), flush=True)
raise SystemExit(1 if report["failed"] else 0)
