"""Real automation schema, persistence, save handlers and runners; isolated SQLite.

Application startup, scheduler, external completions and notifications never run.
"""
import ast
import asyncio
import base64
import logging
import time
import unittest
from contextlib import asynccontextmanager
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace, ModuleType
from typing import Optional, Literal
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import sqlalchemy
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from fastapi import HTTPException, Depends, Request, status
from PIL import Image

SOURCE = Path(__file__).resolve().parents[1] / "open_webui"


def source_exec(path, ns, names=None, skip_imports=True):
    tree = ast.parse((SOURCE / path).read_text(encoding="utf-8-sig"))
    nodes = []
    for node in tree.body:
        if skip_imports and isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if names is not None and getattr(node, "name", None) not in names:
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            node.decorator_list = []
        nodes.append(node)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE/path), "exec"), ns)


class AttachmentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.addAsyncCleanup(self.engine.dispose)
        sessions = async_sessionmaker(self.engine, expire_on_commit=False)

        @asynccontextmanager
        async def context(db=None):
            if db is not None:
                yield db
            else:
                async with sessions() as session:
                    yield session

        base = declarative_base()
        ns = dict(vars(sqlalchemy))
        ns.update(globals())
        ns.update(Base=base, get_async_db_context=context, log=logging.getLogger("fixture"))
        source_exec("models/automations.py", ns)
        self.ns = ns
        self.forms = ns["AutomationForm"]
        self.table = ns["AutomationTable"]()
        async with self.engine.begin() as conn:
            await conn.run_sync(base.metadata.create_all)
        self.owner = SimpleNamespace(id="owner", role="user", name="Fixture", timezone="UTC")
        self.files = {}
        self.images = {}
        for key, fmt in [("png", "PNG"), ("jpeg", "JPEG")]:
            blob = BytesIO()
            Image.new("RGB", (2, 2), "red").save(blob, format=fmt)
            raw = blob.getvalue()
            mime = Image.MIME[fmt]
            self.files[key] = SimpleNamespace(id=key, user_id="owner", filename=key,
                meta={"content_type": mime, "size": len(raw)})
            self.images[key] = f"data:{mime};base64," + base64.b64encode(raw).decode()
        for file_id, filename, mime, content in [
            ("pdf", "sample.pdf", "application/pdf", "Monthly finance report"),
            ("txt", "checklist.txt", "text/plain", "Project checklist"),
            ("docx", "spec.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "Requirements"),
        ]:
            self.files[file_id] = SimpleNamespace(
                id=file_id, user_id="owner", filename=filename,
                meta={"content_type": mime, "size": 128},
                data={"status": "completed", "content": content},
            )
        self.grant = AsyncMock(return_value=False)
        self.read_image = AsyncMock(side_effect=lambda id, user: self.images.get(id))
        ns.update(Files=SimpleNamespace(get_file_by_id=AsyncMock(side_effect=lambda id, db=None: self.files.get(id))),
            has_access_to_file=self.grant, get_image_base64_from_file_id=self.read_image)
        source_exec("utils/automation_attachments.py", ns)
        self.resolve = ns["resolve_automation_attachments"]
        ns.update(Automations=self.table, check_automations_permission=AsyncMock(),
            check_automation_folder_access=AsyncMock(), check_automation_channel_access=AsyncMock(),
            check_automation_limits=AsyncMock(), validate_rrule=AsyncMock(),
            next_run_ns=AsyncMock(return_value=123), enrich_automation=AsyncMock(side_effect=lambda a, db, tz=None:a),
            publish_event=AsyncMock(), get_verified_user=lambda:None, get_async_session=lambda:None,
            EVENTS=SimpleNamespace(AUTOMATION_CREATED="created", AUTOMATION_UPDATED="updated",
                AUTOMATION_RUN_COMPLETED="completed", AUTOMATION_RUN_FAILED="failed"),
            ERROR_MESSAGES=SimpleNamespace(NOT_FOUND="not found"))
        source_exec("routers/automations.py", ns,
            {"create_new_automation", "update_automation_by_id", "check_automation_access"})
        self.context = context

    def form(self, ids=("png", "jpeg")):
        return self.forms(name="Fixture", is_active=False, data={
            "prompt":"Describe the images", "model_id":"fixture", "rrule":"FREQ=DAILY",
            "attachments":[{"id":id} for id in ids]})

    async def test_create_reload_edit_clone_and_text_compatibility(self):
        async with self.context() as db:
            created = await self.ns["create_new_automation"](None, self.form(), self.owner, db)
        reloaded = await self.table.get_by_id(created.id)
        self.assertEqual(reloaded.data["attachments"], [{"id":"png"},{"id":"jpeg"}])
        async with self.context() as db:
            await self.ns["update_automation_by_id"](None, created.id, self.form(("jpeg",)), self.owner, db)
            clone = await self.ns["create_new_automation"](None, self.forms(name="Clone", data=reloaded.data), self.owner, db)
        self.assertEqual((await self.table.get_by_id(created.id)).data["attachments"], [{"id":"jpeg"}])
        self.assertEqual(clone.data["attachments"], reloaded.data["attachments"])
        legacy = self.forms(name="Text", data={"prompt":"Hello","model_id":"fixture","rrule":"FREQ=DAILY"})
        self.assertEqual(legacy.data.attachments, [])
        self.assertEqual(self.ns["automation_message_content"]("Hello", []), "Hello")

    async def test_documents_are_saved_and_sent_to_chat_and_channel(self):
        form = self.form(("png", "pdf", "txt"))
        async with self.context() as db:
            record = await self.ns["create_new_automation"](None, form, self.owner, db)
        saved = await self.table.get_by_id(record.id)
        self.assertEqual([ref["id"] for ref in saved.data["attachments"]], ["png", "pdf", "txt"])

        history, _, payloads, _ = await self.run_fixture(ids=("png", "pdf", "txt"))
        self.assertEqual(len(payloads[0]["messages"][0]["content"]), 2)
        runtime_text = payloads[0]["messages"][0]["content"][0]["text"]
        self.assertIn("Monthly finance report", runtime_text)
        self.assertIn("Project checklist", runtime_text)
        self.assertEqual(len(history[0]["messages"][0]["files"]), 3)

        _, messages, channel_payloads, _ = await self.run_fixture(
            target="channel", ids=("pdf", "docx")
        )
        self.assertIn("Requirements", channel_payloads[0]["messages"][1]["content"])
        self.assertEqual(len(messages[0].data["files"]), 2)

    async def test_document_processing_security_and_limits(self):
        file = self.files["pdf"]
        original = dict(file.data)
        for bad in [
            {"status": "processing", "content": "not ready"},
            {"status": "failed", "content": "not ready"},
            {"status": "completed", "content": ""},
            {"status": "completed", "content": "a" * 50_001},
        ]:
            file.data = bad
            with self.assertRaises(HTTPException):
                await self.resolve([{"id": "pdf"}], self.owner)
        file.data = original
        file.meta["content_type"] = "application/x-msdownload"
        with self.assertRaises(HTTPException):
            await self.resolve([{"id": "pdf"}], self.owner)
        file.meta["content_type"] = "application/pdf"
        file.user_id = "other"
        with self.assertRaises(HTTPException):
            await self.resolve([{"id": "pdf"}], self.owner)
        file.user_id = "owner"
        file.data["content"] = "q" * 50_000
        self.files["txt"].data["content"] = "w" * 50_000
        self.files["docx"].data["content"] = "e" * 30_000
        with self.assertRaises(HTTPException):
            await self.resolve([{"id": "pdf"}, {"id": "txt"}, {"id": "docx"}], self.owner)

    async def test_schema_rejects_urls_extra_fields_and_count(self):
        for ref in [{"id":"https://evil/image.png"}, {"id":"blob:abc"}, {"id":"data:image/png;base64,AA"},
                    {"id":"png","url":"https://evil"}, {"id":"../secret"}]:
            with self.subTest(ref=ref), self.assertRaises(ValidationError):
                self.forms(name="Bad", data={"prompt":"x","model_id":"x","rrule":"x","attachments":[ref]})
        with self.assertRaises(ValidationError):
            self.form(tuple(str(i) for i in range(11)))

    async def test_missing_cross_user_and_revoked_rejected_at_save(self):
        self.files["png"].user_id = "other"
        for ids in [("missing",), ("png",)]:
            with self.assertRaises(HTTPException):
                async with self.context() as db:
                    await self.ns["create_new_automation"](None, self.form(ids), self.owner, db)
        self.grant.return_value = True
        await self.resolve([{"id":"png"}], self.owner)
        self.grant.return_value = False
        with self.assertRaises(HTTPException):
            await self.resolve([{"id":"png"}], self.owner)
        self.assertEqual(await self.table.count_by_user("owner"), 0)

    async def test_limits_duplicates_corrupt_and_missing_bytes(self):
        for mutate in [
            lambda: self.files["png"].meta.update(size=0),
            lambda: self.files["png"].meta.update(size=11*1024*1024),
            lambda: self.files["png"].meta.update(content_type="image/svg+xml"),
        ]:
            original = dict(self.files["png"].meta)
            mutate()
            with self.assertRaises(HTTPException):
                await self.resolve([{"id":"png"}], self.owner)
            self.files["png"].meta = original
        for value in [None, "data:image/png;base64,bm90IGFuIGltYWdl"]:
            self.images["png"] = value
            with self.assertRaises(HTTPException):
                await self.resolve([{"id":"png"}], self.owner)
        with self.assertRaises(HTTPException):
            await self.resolve([{"id":"jpeg"},{"id":"jpeg"}], self.owner)

    async def run_fixture(self, target="chat", ids=("png","jpeg"), revoked=False):
        ns = self.ns
        form = self.form(ids)
        if target == "channel":
            form.data.target = ns["AutomationTarget"](type="channel", channel_id="fixture-channel")
        automation = await self.table.insert("owner", form, 123)
        if revoked:
            self.files["png"].user_id = "other"
        history = []
        channel_messages = []
        payloads = []
        async def insert(chat_id, owner_id, form):
            history.append(form.chat)
            return SimpleNamespace(id=chat_id)
        async def complete(request, payload, user):
            payloads.append(payload)
        async def message(request, channel_id, form, user, db):
            channel_messages.append(form)
            return SimpleNamespace(id=str(uuid4()),parent_id=None), SimpleNamespace(id=channel_id)
        channels = SimpleNamespace(set_file_message_id_in_channel_by_id=AsyncMock(side_effect=[False, True, False, True]),
            add_file_to_channel_by_id=AsyncMock())
        modules = {}
        for name, attrs in {
            "open_webui.utils.access_control":{"has_permission":AsyncMock(return_value=True)},
            "open_webui.routers.channels":{"new_message_handler":message},
            "open_webui.models.channels":{"Channels":channels},
            "open_webui.socket.main":{"sio":SimpleNamespace(emit=AsyncMock())},
        }.items():
            module = ModuleType(name)
            module.__dict__.update(attrs)
            modules[name] = module
        ns.update(Users=SimpleNamespace(get_user_by_id=AsyncMock(return_value=self.owner)),
            Config=SimpleNamespace(get=AsyncMock(side_effect=lambda key, default=None: default if default is not None else True)),
            prompt_template=AsyncMock(side_effect=lambda p,u:p), parse_duration=lambda s:timedelta(hours=1),
            create_token=lambda **kwargs:"fixture-token", ChatForm=lambda **kw:SimpleNamespace(**kw),
            MessageForm=lambda **kw:SimpleNamespace(**kw), Chats=SimpleNamespace(insert_new_chat=insert),
            _resolve_model_defaults=AsyncMock(return_value={}), _build_request=lambda *a,**kw:None,
            get_async_db=self.context, Folders=SimpleNamespace(), _record_run=AsyncMock())
        source_exec("utils/automations.py", ns, {"execute_automation","_execute_channel_automation"})
        app = SimpleNamespace(state=SimpleNamespace(MODELS={"fixture":{"name":"Fixture"}},
            CHAT_COMPLETION_HANDLER=complete))
        with patch.dict("sys.modules", modules):
            await ns["execute_automation"](app, automation)
        return history, channel_messages, payloads, channels

    async def test_chat_run_images_in_payload_and_saved_history(self):
        history, _, payloads, _ = await self.run_fixture()
        self.assertEqual(len(payloads), 1)
        self.assertEqual(len(payloads[0]["messages"][0]["content"]), 3)
        self.assertTrue(payloads[0]["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;"))
        self.assertEqual(len(payloads[0]["user_message"]["files"]), 2)
        user_message = next(m for m in history[0]["history"]["messages"].values() if m["role"]=="user")
        self.assertEqual([f["id"] for f in user_message["files"]], ["png","jpeg"])
        self.assertEqual(len(history[0]["messages"][0]["files"]), 2)

    async def test_channel_run_images_in_payload_history_and_file_association(self):
        _, messages, payloads, channels = await self.run_fixture("channel")
        self.assertEqual(len(messages[0].data["files"]), 2)
        self.assertEqual(len(payloads[0]["messages"][1]["content"]), 3)
        self.assertEqual(channels.add_file_to_channel_by_id.await_count, 2)

    async def test_revoked_run_fails_before_chat_or_channel_side_effects(self):
        for target in ["chat","channel"]:
            history, messages, payloads, channels = await self.run_fixture(target, revoked=True)
            self.assertEqual((history,messages,payloads), ([],[],[]))
            channels.add_file_to_channel_by_id.assert_not_awaited()
            self.assertEqual(self.ns["_record_run"].call_args.args[1], "error")

    async def test_failed_update_preserves_saved_attachments_and_admin_is_owner_scoped(self):
        created = await self.table.insert("owner", self.form(("png",)), 123)
        async with self.context() as db:
            with self.assertRaises(HTTPException):
                await self.ns["update_automation_by_id"](None, created.id, self.form(("missing",)), self.owner, db)
        self.assertEqual((await self.table.get_by_id(created.id)).data["attachments"], [{"id":"png"}])
        self.owner.role = "admin"
        self.files["png"].user_id = "other"
        with self.assertRaises(HTTPException):
            await self.resolve([{"id":"png"}], self.owner)

    async def test_missing_run_fails_before_any_side_effects(self):
        for target in ["chat", "channel"]:
            history, messages, payloads, _ = await self.run_fixture(target, ids=("missing",))
            self.assertEqual((history, messages, payloads), ([], [], []))
            self.assertEqual(self.ns["_record_run"].call_args.args[1], "error")

    async def test_text_only_run_remains_text(self):
        history, messages, payloads, _ = await self.run_fixture(ids=())
        self.assertEqual(payloads[0]["messages"][0]["content"], "Describe the images")
        self.assertEqual(payloads[0]["user_message"]["files"], [])


if __name__ == "__main__":
    unittest.main()
