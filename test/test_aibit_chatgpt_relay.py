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


TEST_ACCOUNT = "a" * 64


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
            return {"ok": True, "complete": True, "account_id": TEST_ACCOUNT, "projects": names}

        with (patch.object(relay, "_authorized", AsyncMock(return_value=self.user)),
              patch.object(relay, "_credentials", return_value=("test-token", "legacy")),
              patch.object(relay, "_account_context", AsyncMock(return_value=("test-token", TEST_ACCOUNT, False))),
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

    async def test_incomplete_sync_preserves_existing_catalog(self):
        with relay._db() as db:
            db.execute(
                "INSERT INTO projects(owner,account,project_id,folder_id,name,url,synced_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (self.user.id, TEST_ACCOUNT, "g-p-AAAAAAAAAA", "folder1", "Old", "https://chatgpt.com/g/g-p-AAAAAAAAAA/project", 42),
            )
            db.commit()
        with (patch.object(relay, "_authorized", AsyncMock(return_value=self.user)),
              patch.object(relay, "_credentials", return_value=("test-token", "legacy")),
              patch.object(relay, "_relay", AsyncMock(return_value={
                  "ok": True, "account_id": TEST_ACCOUNT,
                  "projects": [{"id": "g-p-BBBBBBBBBB", "name": "Incomplete"}],
              }))):
            with self.assertRaises(HTTPException) as error:
                await relay.sync_projects(self.user)
            self.assertEqual(error.exception.status_code, 502)
        with relay._db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM projects").fetchone()[0], 1)

    async def test_legacy_account_migrates_existing_folder_by_project_id(self):
        project_id = "g-p-AAAAAAAAAA"
        folder = SimpleNamespace(id="existing-folder", name="ChatGPT \u00c2\u00b7 Earlier",
                                 meta={"chatgpt_project_id": project_id, "chatgpt_account": "old"},
                                 data={"model_ids": [relay.MODEL_ID]})
        with relay._db() as db:
            db.execute("INSERT INTO projects(owner,account,project_id,folder_id,name,url,synced_at)"
                       " VALUES(?,?,?,?,?,?,?)",
                       (self.user.id, "old", project_id, folder.id, "Earlier", "old", 12))
            db.commit()
        async def lookup(folder_id, owner_id):
            return folder if folder_id == folder.id and owner_id == self.user.id else None
        async def update(folder_id, owner_id, form):
            assert folder_id == folder.id
            folder.name = form.name
            folder.meta.update(form.meta or {})
            return folder
        async def unexpected_insert(*args, **kwargs):
            raise AssertionError("Existing synced project must reuse the original folder")
        with (patch.object(relay, "_authorized", AsyncMock(return_value=self.user)),
              patch.object(relay, "_credentials", return_value=("test", "legacy")),
              patch.object(relay, "_relay", AsyncMock(return_value={
                  "ok": True, "complete": True, "account_id": TEST_ACCOUNT,
                  "projects": [{"id": project_id, "name": "New Unicode - \u0627\u0644\u062a\u062c\u0631\u0628\u0629"}],
              })),
              patch.object(relay.Folders, "get_folder_by_id_and_user_id", side_effect=lookup),
              patch.object(relay.Folders, "update_folder_by_id_and_user_id", side_effect=update),
              patch.object(relay.Folders, "insert_new_folder", side_effect=unexpected_insert)):
            synced = await relay.sync_projects(self.user)
        self.assertEqual(synced["count"], 1)
        self.assertTrue(folder.name.startswith("ChatGPT - New Unicode - "))
        self.assertIn("\u0627\u0644\u062a\u062c\u0631\u0628\u0629", folder.name)
        self.assertEqual(folder.meta["chatgpt_account"], TEST_ACCOUNT)
        with relay._db() as db:
            mapped = db.execute("SELECT folder_id,account FROM projects WHERE owner=? AND project_id=?",
                                (self.user.id, project_id)).fetchone()
        self.assertEqual((mapped["folder_id"], mapped["account"]), (folder.id, TEST_ACCOUNT))

    async def test_account_mismatch_rejects_reply(self):
        job = "aibit_" + "c" * 48
        with relay._db() as db:
            db.execute("INSERT INTO jobs(owner,account,job_id,chat_id,project_id,message_hash,status,created_at,updated_at) "
                       "VALUES(?,?,?,?,?,?,?,?,?)",
                       (self.user.id, TEST_ACCOUNT, job, "chat3", "g-p-AAAAAAAAAA", "hash", "queued", 1, 1))
            db.commit()
        with patch.object(relay, "_relay", AsyncMock(return_value={
            "success": True, "account_id": "b" * 64, "project": {"id": "g-p-AAAAAAAAAA"},
            "response": "mismatch", "conversation_id": "abcdefghij",
            "conversation_url": "https://chatgpt.com/g/g-p-AAAAAAAAAA/c/abcdefghij",
        })):
            await relay._legacy_worker(job, "test-token", TEST_ACCOUNT, {"message": "test"})
        self.assertEqual(relay._job_record(self.user.id, job)["status"], "failed")

    async def test_worker_persists_exact_project_and_conversation(self):
        job = "aibit_" + "a" * 48
        project = "g-p-AAAAAAAAAA"
        with relay._db() as db:
            db.execute(
                "INSERT INTO jobs(owner,account,job_id,chat_id,project_id,message_hash,status,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (self.user.id, TEST_ACCOUNT, job, "aibit-chat-1", project,
                 "hash", "queued", 1, 1),
            )
            db.commit()
        result = {
            "success": True, "account_id": TEST_ACCOUNT, "response": "Verified ChatGPT reply",
            "project": {"id": project}, "conversation_id": "abcdefghij",
            "conversation_url": f"https://chatgpt.com/g/{project}/c/abcdefghij",
        }
        with patch.object(relay, "_relay", AsyncMock(return_value=result)) as mocked:
            await relay._legacy_worker(job, "test-token", TEST_ACCOUNT,
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

    def test_slugged_project_conversation_urls_are_verified_by_exact_id(self):
        project = "g-p-" + "a" * 32
        other = "g-p-" + "b" * 32
        conversation = "4d457840-1234-4234-9234-123456789abc"
        valid = "https://chatgpt.com/g/" + project + "-harmless-project/c/" + conversation
        self.assertTrue(relay._verified_conversation_url(valid, project, conversation))
        self.assertTrue(relay._verified_conversation_url(
            "https://chatgpt.com/g/" + project + "/c/" + conversation,
            project, conversation,
        ))
        self.assertFalse(relay._verified_conversation_url(valid, other, conversation))
        self.assertFalse(relay._verified_conversation_url(
            valid.replace("chatgpt.com", "example.com"), project, conversation,
        ))
        self.assertFalse(relay._verified_conversation_url(
            valid.replace(conversation, "different-conversation"), project, conversation,
        ))

    async def test_completed_reply_recovery_is_idempotent_and_owner_scoped(self):
        job = {"owner": self.user.id, "chat_id": "test-chat", "project_id": "g-p-AAAAAAAAAA",
               "response_message_id": "assistant-123", "status": "completed",
               "response": "Actual upstream reply"}
        saved = {"id": "assistant-123", "role": "assistant", "content": "", "parentId": "user-1"}
        owned_chat = SimpleNamespace(id="test-chat", user_id=self.user.id)
        with (patch.object(relay.Chats, "get_chat_by_id_and_user_id", AsyncMock(return_value=owned_chat)) as owner_check,
              patch.object(relay.Chats, "get_message_by_id_and_message_id", AsyncMock(return_value=saved)),
              patch.object(relay.Chats, "upsert_message_to_chat_by_id_and_message_id", AsyncMock(return_value=owned_chat)) as write):
            self.assertTrue(await relay._recover_completed_reply(job))
            owner_check.assert_awaited_once_with("test-chat", self.user.id)
            self.assertEqual(write.await_args.args[2]["content"], job["response"])
            self.assertEqual(write.await_args.args[2]["parentId"], "user-1")
            self.assertTrue(write.await_args.args[2]["done"])
        with (patch.object(relay.Chats, "get_chat_by_id_and_user_id", AsyncMock(return_value=None)),
              patch.object(relay.Chats, "upsert_message_to_chat_by_id_and_message_id", AsyncMock()) as write):
            self.assertFalse(await relay._recover_completed_reply(job))
            write.assert_not_awaited()
        with (patch.object(relay.Chats, "get_chat_by_id_and_user_id", AsyncMock(return_value=owned_chat)),
              patch.object(relay.Chats, "get_message_by_id_and_message_id", AsyncMock(return_value={**saved, "content": job["response"], "done": True})),
              patch.object(relay.Chats, "upsert_message_to_chat_by_id_and_message_id", AsyncMock()) as write):
            self.assertTrue(await relay._recover_completed_reply(job))
            write.assert_not_awaited()

    async def test_timeout_does_not_repeat_upstream_post(self):
        job = "aibit_" + "b" * 48
        with relay._db() as db:
            db.execute(
                "INSERT INTO jobs(owner,account,job_id,chat_id,project_id,message_hash,status,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (self.user.id, TEST_ACCOUNT, job, "aibit-chat-2",
                 "g-p-AAAAAAAAAA", "hash", "queued", 1, 1),
            )
            db.commit()
        with patch.object(relay, "_relay", AsyncMock(side_effect=HTTPException(
            504, "Request outcome uncertain"))) as mocked:
            await relay._legacy_worker(job, "test-token", TEST_ACCOUNT,
                                       {"message": "Harmless test message"})
            self.assertEqual(mocked.await_count, 1)
        self.assertEqual(relay._job_record(self.user.id, job)["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
