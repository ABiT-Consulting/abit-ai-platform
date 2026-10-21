"""One-shot, read-only-verified recovery of a previously failed AIBIT relay job.

No send or navigation. Requires an exact, stable visible reply in the AIBIT
owned Chrome target, owner/account/project/prompt match and empty original
assistant placeholder. Does not print prompts, answers or credentials.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time

import requests
import websocket

os.environ["DATA_DIR"] = r"C:\Program Files\Python311\Lib\site-packages\open_webui\data"
os.environ.setdefault("WEBUI_SECRET_KEY", "aibit-isolated-cli-import-only")

from open_webui.models.chats import Chats
from open_webui.routers import chatgpt_relay as relay

DATA = Path(os.environ["DATA_DIR"])
RUNTIME = Path(r"C:\ProgramData\ABiT\OpenWebUI")

if len(sys.argv) != 2 or not relay.JOB_ID.fullmatch(sys.argv[1]):
    raise SystemExit("Provide one explicit existing AIBIT job ID; no automatic selection")

with relay._db() as db:
    row = db.execute(
        "SELECT * FROM jobs WHERE job_id=? AND status IN ('failed','unknown') "
        "AND phase='waiting_for_response' "
        "AND error LIKE '%Timed out waiting for the ChatGPT response%'",
        (sys.argv[1],),
    ).fetchone()
    if not row:
        raise RuntimeError("The intended failed job is unavailable; no recovery attempted")
    job = dict(row)

with sqlite3.connect(str(DATA / "webui.db")) as db:
    source = db.execute(
        "SELECT chat FROM chat WHERE id=? AND user_id=?",
        (job["chat_id"], job["owner"]),
    ).fetchone()
if not source:
    raise RuntimeError("AIBIT chat owner mismatch")
saved = json.loads(source[0])
history = saved.get("history", {}).get("messages", {})
placeholder = history.get(job["response_message_id"], {})
prompt = history.get(placeholder.get("parentId"), {}).get("content")
if (
    placeholder.get("role") != "assistant"
    or placeholder.get("model") != relay.MODEL_ID
    or placeholder.get("content")
    or not isinstance(prompt, str)
    or not prompt
    or hashlib.sha256(prompt.strip().encode()).hexdigest() != job["message_hash"]
):
    raise RuntimeError("Original assistant placeholder or submitted prompt mismatch")

target_id = (RUNTIME / "aibit-relay-target-id.txt").read_text().strip()
targets = requests.get("http://127.0.0.1:9227/json/list", timeout=12).json()
target = next((entry for entry in targets if entry.get("id") == target_id), None)
if not target or target.get("type") != "page":
    raise RuntimeError("AIBIT-owned browser target unavailable")

expression = """
(async () => {
  const prompt = %s;
  const userNodes = Array.from(document.querySelectorAll(
    '[data-content-search-unit-key$=":user"],[data-message-author-role="user"]'
  ));
  const assistantNodes = Array.from(document.querySelectorAll(
    '[data-content-search-unit-key$=":assistant"],[data-message-author-role="assistant"]'
  ));
  const user = userNodes.filter(x => String(x.innerText || '').trim() === prompt);
  const assistant = assistantNodes.length === 1 ? assistantNodes[0] : null;
  const followed = Boolean(
    user.length === 1 && assistant &&
    (user[0].compareDocumentPosition(assistant) & Node.DOCUMENT_POSITION_FOLLOWING)
  );
  let accountHash = '';
  try {
    const sessionResponse = await fetch('/api/auth/session', {
      credentials:'same-origin',cache:'no-store',
      signal:AbortSignal.timeout(12000)
    });
    if (sessionResponse.ok) {
      const session = await sessionResponse.json();
      const email = String(session.user?.email || '').toLowerCase().trim();
      if (email) {
        const digest = await crypto.subtle.digest(
          'SHA-256', new TextEncoder().encode(email)
        );
        accountHash = Array.from(new Uint8Array(digest))
          .map(b => b.toString(16).padStart(2,'0')).join('');
      }
    }
  } catch (_) {}
  return {
    accountHash,
    url: location.href,
    promptMatched: user.length === 1,
    assistantCount: assistantNodes.length,
    followed,
    generating: Boolean(document.querySelector('button[aria-label="Stop"]')),
    visibleText: assistant ? String(assistant.innerText || '').trim() : '',
  };
})()
""" % json.dumps(prompt)

def snapshot():
    ws = websocket.create_connection(
        target["webSocketDebuggerUrl"], timeout=35, suppress_origin=True
    )
    try:
        ws.send(json.dumps({
            "id": 1, "method": "Runtime.evaluate",
            "params": {"expression": expression, "awaitPromise": True,
                       "returnByValue": True}
        }))
        for _ in range(80):
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                result = msg.get("result", {}).get("result", {})
                if msg.get("error") or result.get("exceptionDetails"):
                    raise RuntimeError("Browser read-only evaluation failed")
                return result.get("value", {})
        raise RuntimeError("Browser result not returned")
    finally:
        ws.close()

first = snapshot()
time.sleep(3)
second = snapshot()
url = second.get("url", "")
matched = re.fullmatch(
    r"https://chatgpt\.com/g/(g-p-[0-9a-f]{32})(?:-[^/]*)?/c/([A-Za-z0-9_-]{5,190})",
    url.split("?")[0].split("#")[0], flags=re.I,
)
content = second.get("visibleText", "")
if not all([
    matched, matched.group(1) == job["project_id"] if matched else False,
    first.get("url") == url, first.get("visibleText") == content,
    first.get("accountHash") == job["account"],
    second.get("accountHash") == job["account"],
    first.get("promptMatched"), second.get("promptMatched"),
    first.get("followed"), second.get("followed"),
    first.get("assistantCount") == 1, second.get("assistantCount") == 1,
    not first.get("generating"), not second.get("generating"),
    isinstance(content, str), len(content) >= 20,
]):
    raise RuntimeError("Upstream identity, message order or completion evidence insufficient")

async def reconcile():
    owned_chat = await Chats.get_chat_by_id_and_user_id(
        job["chat_id"], job["owner"]
    )
    original = await Chats.get_message_by_id_and_message_id(
        job["chat_id"], job["response_message_id"]
    )
    if not owned_chat or not original or original.get("role") != "assistant":
        raise RuntimeError("AIBIT ownership or assistant record changed")
    if original.get("content"):
        raise RuntimeError("Original reply already contains content; preserve it")
    patched = {
        **original,
        "content": content,
        "done": True,
        "model": relay.MODEL_ID,
        "id": job["response_message_id"],
    }
    saved_reply = await Chats.upsert_message_to_chat_by_id_and_message_id(
        job["chat_id"], job["response_message_id"], patched
    )
    if not saved_reply:
        raise RuntimeError("Unable to persist original assistant reply")
    with relay._db() as db:
        current = db.execute(
            "SELECT status,response FROM jobs WHERE owner=? AND job_id=?",
            (job["owner"], job["job_id"]),
        ).fetchone()
        if not current or current["status"] not in ("failed", "unknown") or current["response"]:
            raise RuntimeError("Job changed; do not overwrite current job")
        db.execute(
            "INSERT INTO bindings(owner,account,chat_id,project_id,conversation_id,conversation_url) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(owner,chat_id) DO UPDATE SET "
            "conversation_id=excluded.conversation_id,conversation_url=excluded.conversation_url",
            (job["owner"], job["account"], job["chat_id"], job["project_id"],
             matched.group(2), url),
        )
        db.execute(
            "UPDATE jobs SET status='completed',phase='completed',response=?,error=NULL,updated_at=? "
            "WHERE owner=? AND job_id=?",
            (content, int(time.time()), job["owner"], job["job_id"]),
        )
        db.commit()
    final = await Chats.get_message_by_id_and_message_id(
        job["chat_id"], job["response_message_id"]
    )
    if not final or final.get("content") != content or not final.get("done"):
        raise RuntimeError("Reply write verification failed")
    print("RECOVERED_EXISTING_FAILED_JOB", True,
          "project_verified", True,
          "account_verified", True,
          "conversation_verified", True,
          "stable_visible_chars", len(content),
          "same_original_message", True,
          "submitted_again", False)

asyncio.run(reconcile())
