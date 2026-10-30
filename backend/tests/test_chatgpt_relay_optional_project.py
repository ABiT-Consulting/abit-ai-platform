"""Project-optional relay tests, isolated from live data and browser."""
import ast, asyncio, hashlib, logging, re, sqlite3, tempfile, time, unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse
from fastapi import HTTPException
import importlib.util
input_spec = importlib.util.spec_from_file_location(
    "relay_input_test", Path(__file__).resolve().parents[1] / "open_webui/utils/chatgpt_relay_input.py"
)
relay_input = importlib.util.module_from_spec(input_spec)
input_spec.loader.exec_module(relay_input)

ROUTER = Path(__file__).resolve().parents[1] / "open_webui/routers/chatgpt_relay.py"
WANTED = {"_safe_conversation_url", "_verified_conversation_url", "_job_record", "_job_record_by_id",
          "_legacy_worker", "complete_chat"}
tree = ast.parse(ROUTER.read_text(encoding="utf-8-sig"))
nodes = [n for n in tree.body
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in WANTED]
compiled = compile(ast.Module(body=nodes, type_ignores=[]), str(ROUTER), "exec")
PROJECT = "g-p-" + "a"*32
ACCOUNT = "b"*64
CONV = "conversation123"

class TestOptionalProject(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "ledger.sqlite"
        self.folder = None
        self.meta = {}
        self.project_in_result = None
        self.last_payload = None
        self.catalog_requests = 0
        self.reject_catalog = False

        @contextmanager
        def db():
            c = sqlite3.connect(self.path)
            c.row_factory = sqlite3.Row
            try: yield c
            finally: c.close()
        self.db = db
        with db() as c:
            c.executescript("""
            CREATE TABLE projects(owner TEXT, account TEXT, project_id TEXT, folder_id TEXT, name TEXT);
            CREATE TABLE sync_state(owner TEXT, account TEXT, updated_at INTEGER, count INTEGER);
            CREATE TABLE bindings(owner TEXT, account TEXT, chat_id TEXT, project_id TEXT,
                conversation_id TEXT, conversation_url TEXT, PRIMARY KEY(owner, chat_id));
            CREATE TABLE jobs(owner TEXT, account TEXT, job_id TEXT PRIMARY KEY,
                chat_id TEXT, project_id TEXT NOT NULL, message_hash TEXT, status TEXT,
                response TEXT, error TEXT, response_message_id TEXT, phase TEXT, conversation_url TEXT,
                created_at INTEGER, updated_at INTEGER);
            """)
        async def authorized(user): return user
        async def context(): return "fake-token", ACCOUNT, False
        async def owner(chat_id, user_id): return chat_id == "chat1" and user_id == "u1"
        async def folder(chat_id, user_id): return self.folder
        async def folder_info(folder_id, user_id): return SimpleNamespace(meta=self.meta)
        async def relay(method, route, token, timeout):
            self.catalog_requests += 1
            return {"complete": not self.reject_catalog, "account_id": ACCOUNT,
                    "projects": [{"id": PROJECT}]}
        async def snapshots(token, payload, handler):
            self.last_payload = payload.copy()
            target = payload.get("chatgpt_project_id", "")
            actual = target if self.project_in_result is None else self.project_in_result
            url = (f"https://chatgpt.com/g/{PROJECT}-slug/c/{CONV}"
                   if actual else f"https://chatgpt.com/c/{CONV}")
            return {"success": True, "account_id": ACCOUNT, "project": {"id": actual},
                    "response": "ROUTE_OK", "conversation_id": CONV,
                    "conversation_url": url}
        async def recover(job): return True
        ns = {
            "__builtins__": __builtins__, "_db": db, "hashlib": hashlib, "time": time,
            "log": logging.getLogger("test-relay"), "urlparse": urlparse,
            "HTTPException": HTTPException, "MODEL_ID": "test-model",
            "PROJECT_ID": re.compile(r"^g-p-[A-Za-z0-9_-]{5,190}$"),
            "CONVERSATION_ID": re.compile(r"^[A-Za-z0-9_-]{5,190}$"),
            "GENERAL_PROJECT_ID": "", "MESSAGE_TOKEN_FILE": "test-token-path",
            "ACCOUNT_ID": re.compile(r"^[a-f0-9]{64}$"),
            "asyncio": SimpleNamespace(create_task=asyncio.create_task,
                                       sleep=lambda _: asyncio.sleep(0)),
            "_authorized": authorized, "_account_context": context,
            "_relay": relay, "_verify_account": lambda x: x["account_id"],
            "_credentials": lambda _: ("fake-token", "fake-hash"),
            "_relay_snapshots": snapshots, "_recover_completed_reply": recover,
            "_persist_job_progress": lambda *args: None,
            "parse_explicit_user_turn": relay_input.parse_explicit_user_turn,
            "stage_images": lambda images, job_id: [
                {"path": str(self.path.parent / f"screenshot-{i}.{ext}"),
                 "name": f"screenshot-{i}.{ext}"}
                for i, (ext, _) in enumerate(images, 1)
            ],
            "cleanup_images": lambda *_: None,
            "_reconcile_stale": lambda j: j,
            "Chats": SimpleNamespace(is_chat_owner=owner, get_chat_folder_id=folder),
            "Folders": SimpleNamespace(get_folder_by_id_and_user_id=folder_info),
        }
        exec(compiled, ns)
        self.ns = ns

    def bind(self, project_id):
        url = (f"https://chatgpt.com/g/{PROJECT}-slug/c/{CONV}"
               if project_id else f"https://chatgpt.com/c/{CONV}")
        with self.db() as db:
            db.execute("INSERT INTO bindings VALUES (?,?,?,?,?,?)",
                       ("u1", ACCOUNT, "chat1", project_id, CONV, url))
            db.commit()

    def map_project(self):
        self.folder = "project_folder"
        with self.db() as db:
            db.execute("INSERT INTO projects VALUES (?,?,?,?,?)",
                       ("u1", ACCOUNT, PROJECT, "project_folder", "Project Alpha"))
            db.commit()

    def get_binding(self):
        with self.db() as db:
            row = db.execute("SELECT * FROM bindings").fetchone()
            return dict(row) if row else None

    async def send(self, reply="reply1", extra=None, content="routing check"):
        return await self.ns["complete_chat"](
            {"messages": [{"role": "user", "content": content}]},
            {"chat_id": "chat1", "message_id": reply, **(extra or {})},
            SimpleNamespace(id="u1"),
        )
    async def test_multimodal_image_is_forwarded_in_owned_relay_request(self):
        import base64
        png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"synthetic").decode("ascii")
        await self.send(content=[
            {"type": "text", "text": "Please inspect this image"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{png}"}},
        ])
        self.assertEqual(self.last_payload["message"], "Please inspect this image")
        self.assertEqual(self.last_payload["input_attachments"][0]["name"], "screenshot-1.png")

    async def test_general_chat_ignores_spoofed_folder_and_saves_binding(self):
        r = await self.send(extra={"folder_id": "spoofed-project"})
        self.assertEqual(r["choices"][0]["message"]["content"], "ROUTE_OK")
        self.assertNotIn("chatgpt_project_id", self.last_payload)
        self.assertNotIn("project_name", self.last_payload)
        self.assertEqual(self.catalog_requests, 0)
        self.assertEqual(self.get_binding()["project_id"], "")
        self.assertEqual(self.get_binding()["conversation_url"],
                         f"https://chatgpt.com/c/{CONV}")

    async def test_general_followup_uses_original_chat(self):
        self.bind("")
        await self.send("reply2")
        self.assertEqual(self.last_payload["conversation_url"],
                         f"https://chatgpt.com/c/{CONV}")
        self.assertEqual(self.get_binding()["project_id"], "")

    async def test_selected_project_uses_owner_scoped_mapping_without_catalog_fetch(self):
        self.map_project()
        await self.send()
        self.assertEqual(self.catalog_requests, 0)
        self.assertEqual(self.last_payload["chatgpt_project_id"], PROJECT)
        self.assertEqual(self.last_payload["project_name"], "Project Alpha")
        self.assertEqual(self.get_binding()["project_id"], PROJECT)

    async def test_general_binding_cannot_migrate_to_project(self):
        self.map_project()
        self.bind("")
        with self.assertRaises(HTTPException) as raised:
            await self.send()
        self.assertEqual(raised.exception.status_code, 409)
        self.assertIsNone(self.last_payload)

    async def test_project_binding_survives_removed_folder(self):
        self.map_project()
        self.bind(PROJECT)
        self.folder = None
        await self.send()
        self.assertEqual(self.last_payload["chatgpt_project_id"], PROJECT)
        self.assertEqual(self.get_binding()["project_id"], PROJECT)

    async def test_stale_project_folder_never_goes_to_general_chat(self):
        self.folder = "stale-project"
        self.meta = {"chatgpt_project_id": PROJECT}
        with self.assertRaises(HTTPException) as raised:
            await self.send()
        self.assertEqual(raised.exception.status_code, 409)
        self.assertIsNone(self.last_payload)

    async def test_redundant_catalog_unavailability_does_not_block_mapped_project(self):
        self.map_project()
        self.reject_catalog = True
        async def unavailable_account_context():
            raise AssertionError('Per-message remote account verification must not run')
        self.ns['_account_context'] = unavailable_account_context
        result = await self.send()
        self.assertEqual(result['choices'][0]['message']['content'], 'ROUTE_OK')
        self.assertEqual(self.catalog_requests, 0)
        self.assertEqual(self.last_payload['account_id'], ACCOUNT)

    async def test_mismatched_project_account_is_not_silently_rerouted(self):
        self.map_project()
        self.bind(PROJECT)
        with self.db() as db:
            db.execute('UPDATE projects SET account=? WHERE owner=?', ('c'*64, 'u1'))
            db.commit()
        with self.assertRaises(HTTPException) as raised:
            await self.send()
        self.assertEqual(raised.exception.status_code, 403)
        self.assertIsNone(self.last_payload)

    async def test_general_reply_in_a_project_is_rejected(self):
        self.project_in_result = PROJECT
        with self.assertRaises(HTTPException) as raised:
            await self.send()
        self.assertEqual(raised.exception.status_code, 502)
        self.assertIsNone(self.get_binding())

    async def test_general_and_project_urls_are_strictly_separate(self):
        verify = self.ns["_verified_conversation_url"]
        normal = f"https://chatgpt.com/c/{CONV}"
        scoped = f"https://chatgpt.com/g/{PROJECT}-slug/c/{CONV}"
        self.assertTrue(verify(normal, "", CONV))
        self.assertFalse(verify(scoped, "", CONV))
        self.assertFalse(verify(normal, PROJECT, CONV))
        self.assertTrue(verify(scoped, PROJECT, CONV))
        self.assertFalse(verify(f"https://evil.com/c/{CONV}", "", CONV))
