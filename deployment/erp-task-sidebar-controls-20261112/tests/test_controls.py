"""Isolated tests: no live ERPNext, OpenWebUI, or relay side effects."""
import ast
import hmac
import os
import asyncio
import re
import sqlite3
import sys
import types
from pathlib import Path
from urllib.parse import urlsplit
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend/open_webui/routers/erp_task_controls.py"


def helpers():
    tree = ast.parse(BACKEND.read_text(encoding="utf-8"))
    wanted = {
        "safe_chat_url", "task_agent_id", "legacy_state",
        "relay_job", "standard_state", "response_for",
    }
    defs = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in wanted]
    constants = [node for node in tree.body if isinstance(node, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id in ("CHAT_RE", "PROJECT_CHAT_RE", "AGENT_RE", "TASK_RE")
                         for t in node.targets)]
    scope = {"re": re, "sqlite3": sqlite3, "Path": Path, "urlsplit": urlsplit}
    exec(compile(ast.Module(body=constants+defs, type_ignores=[]), str(BACKEND), "exec"), scope)
    return scope


def test_safe_url_whitelist():
    h = helpers()
    yes = "https://abit.ai/c/12345678-1234-1234-1234-123456789abc"
    gpt = "https://chatgpt.com/c/12345678-1234-1234-1234-123456789abc"
    assert h["safe_chat_url"](yes) == yes
    assert h["safe_chat_url"](gpt, chatgpt=True) == gpt
    assert h["safe_chat_url"]("https://chatgpt.com/g/g-p-12345678901234567890123456789012-test/c/12345678-1234-1234-1234-123456789abc", chatgpt=True) is not None
    assert h["safe_chat_url"](yes, chatgpt=True) is None
    for bad in (
        "javascript:alert(1)", "https://evil.example/c/12345678-1234-1234-1234-123456789abc",
        "https://abit.ai.evil.example/c/12345678-1234-1234-1234-123456789abc",
        "https://abit.ai@evil.example/c/12345678-1234-1234-1234-123456789abc",
        yes+"?token=secret", yes+"#settings", "https://abit.ai/"
    ):
        assert h["safe_chat_url"](bad) is None, bad


def test_agent_binding_requires_exact_origin_and_path():
    h = helpers()
    aid = "3ae56223-023a-5cf2-b922-f2f99a3cd1b9"
    assert h["task_agent_id"]({"custom_abit_ai_agent_url": "https://abit.ai/automations/"+aid}) == aid
    assert h["task_agent_id"]({"custom_abit_ai_agent_url": "https://evil.example/automations/"+aid}) is None
    assert h["task_agent_id"]({"custom_abit_ai_agent_url": "https://abit.ai/automations/"+aid+"?x=1"}) is None


def test_legacy_stop_requires_confirmation():
    h = helpers()
    assert h["legacy_state"]({"status": "Cancelled", "synced": False}) == "Stopping"
    assert h["legacy_state"]({"status": "Cancelled", "synced": True, "scope": {"owner_id": "owner"}}) == "Stopped"
    assert h["legacy_state"]({"status": "Working", "synced": False}) == "Running"
    assert h["legacy_state"]({"status": "Preparing", "synced": False}) == "Queued"


def test_relay_unknown_is_not_stop_confirmation(tmp_path, monkeypatch):
    h = helpers()
    p = tmp_path / "aibit-chatgpt-relay.sqlite"
    with sqlite3.connect(p) as db:
        db.execute("CREATE TABLE jobs (owner TEXT, chat_id TEXT, status TEXT, conversation_url TEXT, updated_at INTEGER)")
        db.execute("INSERT INTO jobs VALUES (?,?,?,?,?)",
                   ("owner-1", "chat-1", "unknown", None, 10))
    module = types.ModuleType("open_webui.env")
    module.DATA_DIR = tmp_path
    pkg = types.ModuleType("open_webui")
    monkeypatch.setitem(sys.modules, "open_webui", pkg)
    monkeypatch.setitem(sys.modules, "open_webui.env", module)
    assert h["relay_job"]("chat-1", "owner-1")["terminal"] is False
    assert h["relay_job"]("chat-1", "different-owner") is None
    with sqlite3.connect(p) as db:
        db.execute("UPDATE jobs SET status='completed' WHERE chat_id='chat-1'")
    assert h["relay_job"]("chat-1", "owner-1")["terminal"] is True


def test_standard_stop_does_not_misreport_upstream(tmp_path):
    h = helpers()
    agent = SimpleNamespace(meta={"erp_operator_stop": 123}, data={"model_id":"omniroute.abit-chatgpt-relay/project-management"},
                            is_active=False, next_run_at=None, user_id="owner")
    latest = SimpleNamespace(status="error", chat_id="some-chat")
    h["relay_job"] = lambda cid, owner: {"terminal": False}
    assert h["standard_state"](agent, latest, {}) == "Stopping"
    h["relay_job"] = lambda cid, owner: {"terminal": True}
    assert h["standard_state"](agent, latest, {}) == "Stopped"
    latest.status = "running"
    assert h["standard_state"](agent, latest, {}) == "Stopping"


def test_view_links_are_owner_scoped():
    h = helpers()
    aid = "3ae56223-023a-5cf2-b922-f2f99a3cd1b9"
    chat_id = "12345678-1234-1234-1234-123456789abc"
    agent = SimpleNamespace(id=aid, user_id="owner-a", data={"model_id": "local"},
                            meta={}, is_active=False, next_run_at=None)
    latest = SimpleNamespace(status="success", chat_id=chat_id)
    class Db:
        async def get(self, model, identity):
            return SimpleNamespace(user_id="owner-b")
    h["Chat"] = object
    h["relay_job"] = lambda *args: None
    result = asyncio.run(h["response_for"]({
        "status": "Open", "custom_ai_relay_execution_status": "Completed",
        "custom_ai_relay_conversation_url": "https://evil.example/phishing",
        "custom_chatgpt_chat_url": "https://chatgpt.com/g/g-p-example",
    }, True, agent, None, latest, Db()))
    assert result["abit_chat_url"] is None
    assert result["chatgpt_chat_url"] is None
    assert result["can_start"] is True
    assert result["can_stop"] is False


def test_frontend_and_server_paths_are_connected():
    js = (Path(__file__).resolve().parents[1] / "task_sidebar.js").read_text(encoding="utf-8")
    main = (ROOT / "backend/open_webui/main.py").read_text(encoding="utf-8")
    assert "abit_erp_agent_control" in js
    assert "erp_task_controls.router" in main
    for label in ("▶ Start", "■ Stop", "↻ Restart", "Open ABIT.AI chat", "Open ChatGPT chat"):
        assert label in js


def load_control_route():
    """Exercise the route without initializing OpenWebUI services or a live DB."""
    tree = ast.parse(BACKEND.read_text(encoding="utf-8"))
    func = next(node for node in tree.body
                if isinstance(node, ast.AsyncFunctionDef)
                and node.name == "control_erp_task_agent")
    func.decorator_list = []
    func.args.defaults = []
    func.args.kw_defaults = [None for _ in func.args.kw_defaults]
    class FakeHTTP(Exception):
        def __init__(self, status_code, detail):
            self.status_code, self.detail = status_code, detail
            super().__init__(detail)
    async def owner(email):
        return SimpleNamespace(id="owner", role="admin")
    class FakePath:
        def __init__(self, value):
            self.value = str(value)
        @property
        def parent(self):
            return self
        def __truediv__(self, other):
            return self
        def read_text(self):
            return "signed-token"
        def __str__(self):
            return self.value
    scope = {
        "Request": object, "ControlCommand": object,
        "HTTPException": FakeHTTP, "time": __import__("time"),
        "hmac": hmac, "os": os, "Path": FakePath,
        "DATA_DIR": "/data", "Users": SimpleNamespace(get_user_by_email=owner),
    }
    exec(compile(ast.Module(body=[func], type_ignores=[]), str(BACKEND), "exec"), scope)
    return scope, FakeHTTP


def install_control_modules(monkeypatch, saved):
    mod1 = types.ModuleType("open_webui.utils.erp_task_resolution")
    class Store:
        def __init__(self, path):
            self.path = path
        def put(self, run):
            saved.append(("cancel", dict(run)))
        def enqueue(self, task):
            saved.append(("enqueue", task))
        def queue_reset(self, task):
            saved.append(("reset", task))
    class ERPClient:
        def __init__(self, token_file):
            async def close():
                pass
            self.client = SimpleNamespace(aclose=close)
        async def save(self, task, changes):
            saved.append(("erp_task", changes))
    mod1.Store, mod1.ERPClient = Store, ERPClient
    mod2 = types.ModuleType("open_webui.utils.erp_task_runtime")
    mod2.runtime_state = lambda: "test-store.sqlite"
    monkeypatch.setitem(sys.modules, "open_webui", types.ModuleType("open_webui"))
    monkeypatch.setitem(sys.modules, "open_webui.utils", types.ModuleType("open_webui.utils"))
    monkeypatch.setitem(sys.modules, "open_webui.utils.erp_task_resolution", mod1)
    monkeypatch.setitem(sys.modules, "open_webui.utils.erp_task_runtime", mod2)


def test_managed_stop_is_durable_and_not_falsely_confirmed(monkeypatch):
    saved = []
    install_control_modules(monkeypatch, saved)
    scope, _ = load_control_route()
    run = {"task": "TASK-2026-05810", "agent": "a", "status": "Working",
           "progress": "Running", "synced": False, "scope": {"owner_id": "owner"}}
    agent = SimpleNamespace(user_id="owner", meta={"erp_managed": True})
    async def ctx(*_): return ({"erp_token_file": "test"}, {"status": "Working"},
                              True, agent, run, None)
    async def result(*_): return {"status":"Running", "can_start":False,
                                  "can_stop":True, "can_restart":False}
    scope.update(task_context=ctx, response_for=result)
    value = asyncio.run(scope["control_erp_task_agent"](
        SimpleNamespace(), "TASK-2026-05810", SimpleNamespace(action="stop"),
        control_secret="signed-token", db=SimpleNamespace()))
    assert value["status"] == "Stopping"
    assert "awaiting worker confirmation" in value["message"]
    assert saved[0][0] == "cancel"
    assert saved[0][1]["status"] == "Cancelled"
    assert saved[0][1]["synced"] is False


def test_restart_only_requeues_after_confirmed_cancellation(monkeypatch):
    saved = []
    install_control_modules(monkeypatch, saved)
    scope, FakeHTTP = load_control_route()
    run = {"task":"TASK-2026-05810", "agent":"a", "status":"Cancelled",
           "synced": True, "scope": {"owner_id": "owner"}}
    agent = SimpleNamespace(user_id="owner", meta={"erp_managed":True})
    async def ctx(*_): return ({"erp_token_file":"test"}, {"status":"Working"},
                              True, agent, run, None)
    async def result(*_): return {"status":"Stopped", "can_start":True,
                                  "can_stop":False, "can_restart":True}
    scope.update(task_context=ctx, response_for=result)
    value = asyncio.run(scope["control_erp_task_agent"](
        SimpleNamespace(), "TASK-2026-05810", SimpleNamespace(action="restart"),
        control_secret="signed-token", db=SimpleNamespace()))
    assert value["status"] == "Queued"
    assert ("erp_task", {"status": "Open"}) in saved
    assert ("reset", "TASK-2026-05810") in saved

    run["synced"] = False
    with pytest.raises(FakeHTTP) as err:
        asyncio.run(scope["control_erp_task_agent"](
            SimpleNamespace(), "TASK-2026-05810", SimpleNamespace(action="restart"),
            control_secret="signed-token", db=SimpleNamespace()))
    assert err.value.status_code == 409


def test_route_rejects_cross_owner_stop(monkeypatch):
    saved = []
    install_control_modules(monkeypatch, saved)
    scope, FakeHTTP = load_control_route()
    agent = SimpleNamespace(user_id="another-owner", meta={"erp_managed":True})
    async def ctx(*_): return ({}, {"status":"Working"}, True, agent,
                              {"status":"Working"}, None)
    async def result(*_): return {"status":"Running", "can_stop":True}
    scope.update(task_context=ctx, response_for=result)
    with pytest.raises(FakeHTTP) as err:
        asyncio.run(scope["control_erp_task_agent"](
            SimpleNamespace(), "TASK-2026-05810", SimpleNamespace(action="stop"),
            control_secret="signed-token", db=SimpleNamespace()))
    assert err.value.status_code == 404
    assert not saved