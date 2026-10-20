"""Targeted safety tests for AIBIT's ChatGPT relay adapter."""
import asyncio
import os
from pathlib import Path
import secrets
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

# Keep test imports and their automatic migrations outside production data.
_TMP = tempfile.TemporaryDirectory(prefix="aibit-relay-unit-")
os.environ["WEBUI_SECRET_KEY"] = secrets.token_hex(40)
os.environ["DATA_DIR"] = _TMP.name

from fastapi import HTTPException
from open_webui.routers import chatgpt_relay as relay


class RelayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="aibit-ledger-")
        self.original_ledger = relay.LEDGER
        relay.LEDGER = Path(self.tmp.name) / "relay.sqlite"
        self.user = SimpleNamespace(id="aibit-owner-test", role="admin")

    def tearDown(self):
        relay.LEDGER = self.original_ledger
        self.tmp.cleanup()

    async def test_authorization_allows_admin_other_than_model_owner(self):
        model = SimpleNamespace(user_id="different-admin")
        with patch.object(relay.Models, "get_model_by_id", AsyncMock(return_value=model)):
            self.assertIs(await relay._authorized(self.user), self.user)

    async def test_authorization_rejects_non_admin_even_if_model_owner(self):
        model = SimpleNamespace(user_id=self.user.id)
        with patch.object(relay.Models, "get_model_by_id", AsyncMock(return_value=model)):
            for role in ("user", "pending"):
                with self.assertRaises(HTTPException) as raised:
                    await relay._authorized(SimpleNamespace(id=self.user.id, role=role))
                self.assertEqual(raised.exception.status_code, 403)

    async def test_authorization_requires_configured_model(self):
        with patch.object(relay.Models, "get_model_by_id", AsyncMock(return_value=None)):
            with self.assertRaises(HTTPException) as raised:
                await relay._authorized(self.user)
            self.assertEqual(raised.exception.status_code, 403)

    async def test_sync_is_idempotent_and_matches_ids_not_names(self):
        folders = {}
        index = [0]

        async def lookup(folder_id, user_id):
            return folders.get(folder_id)

        async def insert(user_id, form):
            index[0] += 1
            folder = SimpleNamespace(id=f"folder-{index[0]}", name=form.name,
                                     meta=form.meta, data=form.data)
            folders[folder.id] = folder
            return folder

        async def update(folder_id, user_id, form):
            folder = folders[folder_id]
            folder.name = form.name
            return folder

        names = [
            {"id": "g-p-AAAAAAAAAA", "name": "Same"},
            {"id": "g-p-BBBBBBBBBB", "name": "Same"},
            {"id": "g-p-CCCCCCCCCC", "name": "Other"},
        ]
        async def upstream(*args, **kwargs):
            return {"ok": True, "projects": names}

        with (patch.object(relay, "_authorized", AsyncMock(return_value=self.user)),
              patch.object(relay, "_credentials", return_value=("test-token", "account1")),
              patch.object(relay, "_relay", side_effect=upstream),
              patch.object(relay.Folders, "get_folder_by_id_and_user_id", side_effect=lookup),
              patch.object(relay.Folders, "insert_new_folder", side_effect=insert),
              patch.object(relay.Folders, "update_folder_by_id_and_user_id", side_effect=update)):
            first = await relay.sync_projects(self.user)
            self.assertEqual(first["synced"], 3)
            self.assertEqual(len(folders), 3)
            self.assertEqual(len({f.name for f in folders.values()}), 3)
            names[0]["name"] = "Renamed"
            second = await relay.sync_projects(self.user)
            self.assertEqual(second["synced"], 3)
            self.assertEqual(len(folders), 3)
            self.assertTrue(any("Renamed" in f.name for f in folders.values()))
            catalog = await relay.get_projects(self.user)
            self.assertEqual(len(catalog["projects"]), 3)
            self.assertEqual(len({row["folder_id"] for row in catalog["projects"]}), 3)

    async def test_worker_persists_exact_project_and_conversation(self):
        job = "aibit_" + "a" * 48
        project = "g-p-AAAAAAAAAA"
        with relay._db() as db:
            db.execute(
                "INSERT INTO jobs(owner,account,job_id,chat_id,project_id,message_hash,status,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (self.user.id, "account1", job, "aibit-chat-1", project,
                 "hash", "queued", 1, 1),
            )
            db.commit()
        result = {
            "success": True, "response": "Verified ChatGPT reply",
            "project": {"id": project}, "conversation_id": "abcdefghij",
            "conversation_url": f"https://chatgpt.com/g/{project}/c/abcdefghij",
        }
        with patch.object(relay, "_relay", AsyncMock(return_value=result)) as mocked:
            await relay._legacy_worker(job, "test-token", "account1",
                                       {"message": "Harmless test message"})
            self.assertEqual(mocked.await_count, 1)
        item = relay._job_record(self.user.id, job)
        self.assertEqual(item["status"], "completed")
        self.assertEqual(item["response"], result["response"])
        with relay._db() as db:
            binding = db.execute(
                "SELECT * FROM bindings WHERE owner=? AND chat_id=?",
                (self.user.id, "aibit-chat-1"),
            ).fetchone()
        self.assertEqual(binding["project_id"], project)
        self.assertEqual(binding["conversation_id"], "abcdefghij")

    async def test_timeout_does_not_repeat_upstream_post(self):
        job = "aibit_" + "b" * 48
        with relay._db() as db:
            db.execute(
                "INSERT INTO jobs(owner,account,job_id,chat_id,project_id,message_hash,status,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (self.user.id, "account1", job, "aibit-chat-2",
                 "g-p-AAAAAAAAAA", "hash", "queued", 1, 1),
            )
            db.commit()
        with patch.object(relay, "_relay", AsyncMock(side_effect=HTTPException(
            504, "Request outcome uncertain"))) as mocked:
            await relay._legacy_worker(job, "test-token", "account1",
                                       {"message": "Harmless test message"})
            self.assertEqual(mocked.await_count, 1)
        self.assertEqual(relay._job_record(self.user.id, job)["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
