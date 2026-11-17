"""Read-only, owner-scoped Context map. No prompts, credentials or message bodies."""
import time
import asyncio
import sqlite3
from pathlib import Path
from open_webui.env import DATA_DIR
from sqlalchemy import select
from open_webui.models.automations import Automation, AutomationRuns
from open_webui.models.chats import Chat
from open_webui.models.folders import Folder
from open_webui.tasks import item_tasks, tasks, redis_list_tasks, REDIS_TASKS_KEY

def agent_state(enabled, running, last_activity, created_at, now, stale_seconds):
    if not enabled:
        return 'disabled'
    if running:
        return 'running'
    if now - (last_activity or created_at or now) >= stale_seconds:
        return 'stale'
    return 'idle'

def communication_edges(records, aliases, allowed):
    """Only explicit recorded communications; co-location does not imply a link."""
    edges = {}
    for source, metadata in records:
        if not isinstance(metadata, dict):
            continue
        if metadata.get('type') == 'subagent':
            parent = aliases.get(metadata.get('parent_chat_id'))
            child = aliases.get(source)
            if parent in allowed and child in allowed and parent != child:
                edges[(parent, child)] = {'source': parent, 'target': child, 'label': 'Delegated task'}
        for record in metadata.get('agent_communications', []) if isinstance(metadata.get('agent_communications'), list) else []:
            if not isinstance(record, dict) or record.get('status') not in ('sent', 'delivered', 'received'):
                continue
            target = aliases.get(record.get('target_chat_id') or record.get('target_agent_id'))
            source_id = aliases.get(source)
            if source_id not in allowed or target not in allowed or source_id == target:
                continue
            key = (source_id, target)
            edges[key] = {'source': source_id, 'target': target, 'label': 'Agent communication'}
    return list(edges.values())

def read_relay_activity(path, user_id, now):
    """Fresh running heartbeats only; queued or orphaned ledger rows are not running."""
    if not path.is_file():
        return set()
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2) as ledger:
        return {row[0] for row in ledger.execute(
            "SELECT chat_id FROM jobs WHERE owner=? AND status='running' AND updated_at>=?",
            (user_id, now - 30),
        )}

async def get_context_map(request, user, db, stale_hours=24):
    now = int(time.time())
    automations = (await db.execute(select(Automation).where(Automation.user_id == user.id))).scalars().all()
    owned_folders = (await db.execute(select(Folder.id, Folder.name, Folder.parent_id).where(Folder.user_id == user.id))).all()
    # Project metadata only: loading thousands of full conversations would make polling expensive.
    chats = (await db.execute(select(
        Chat.id, Chat.title, Chat.folder_id, Chat.created_at, Chat.updated_at,
        Chat.chat['meta'].label('run_meta'), Chat.meta
    ).where(Chat.user_id == user.id, Chat.archived == False))).all()
    latest = await AutomationRuns.get_latest_batch([a.id for a in automations], db=db) if automations else {}
    redis = getattr(request.app.state, 'redis', None)
    if redis:
        active_task_ids = await redis_list_tasks(redis)
        values = await redis.hmget(REDIS_TASKS_KEY, active_task_ids) if active_task_ids else []
        active_chats = {v.decode() if isinstance(v, bytes) else v for v in values if v}
    else:
        active_chats = {cid for cid, tids in list(item_tasks.items()) if any(tid in tasks and not tasks[tid].done() for tid in list(tids))}
    active_chats.update(await asyncio.to_thread(
        read_relay_activity, Path(DATA_DIR) / 'aibit-chatgpt-relay.sqlite', user.id, now
    ))
    folder_map = {f.id: f for f in owned_folders}
    auto_map = {a.id: a for a in automations}
    aliases = {a.id: 'automation:' + a.id for a in automations}
    running_automations = set()
    latest_chats = {}
    records = [(a.id, a.meta) for a in automations]
    context_by_chat = {}
    for c in chats:
        run_meta = c.run_meta if isinstance(c.run_meta, dict) else {}
        aid = run_meta.get('automation_id')
        if aid in auto_map:
            aliases[c.id] = 'automation:' + aid
            context_by_chat[c.id] = auto_map[aid].folder_id if auto_map[aid].folder_id in folder_map else None
            if c.id in active_chats:
                running_automations.add(aid)
            if aid not in latest_chats or c.created_at > latest_chats[aid].created_at:
                latest_chats[aid] = c
        elif c.folder_id in folder_map:
            aliases[c.id] = 'chat:' + c.id
            context_by_chat[c.id] = c.folder_id
        records.extend([(c.id, c.meta), (c.id, run_meta)])
    # Built-in delegate_task persists parent_chat_id in the child metadata.
    # Inherit its owner's visible context without exposing a foreign parent.
    for c in chats:
        meta = c.meta if isinstance(c.meta, dict) else {}
        parent = meta.get('parent_chat_id')
        if meta.get('type') == 'subagent' and parent in aliases:
            aliases[c.id] = 'chat:' + c.id
            context_by_chat[c.id] = context_by_chat.get(parent)
    nodes = []
    for a in automations:
        last = latest.get(a.id)
        chat = latest_chats.get(a.id)
        last_at = int(a.last_run_at / 1_000_000_000) if a.last_run_at else None
        # A current task may predate the run log, so use all owned run chats above.
        running = a.id in running_automations
        nodes.append({
            'id': aliases[a.id], 'name': a.name, 'kind': 'scheduled',
            'context_id': a.folder_id if a.folder_id in folder_map else None,
            'status': 'stale' if a.is_stale else agent_state(a.is_active, running, last_at, int(a.created_at / 1_000_000_000), now, stale_hours * 3600),
            'last_activity': last_at, 'next_run': int(a.next_run_at / 1_000_000_000) if a.next_run_at else None,
            'model': (a.data or {}).get('model_id', ''), 'enabled': a.is_active and not a.is_stale,
            'href': '/automations/' + a.id,
            'chat_href': '/c/' + chat.id if chat else None,
            'last_error': bool(last and last.status == 'error'),
        })
    for c in chats:
        if aliases.get(c.id) != 'chat:' + c.id:
            continue
        meta = c.meta if isinstance(c.meta, dict) else {}
        enabled = meta.get('is_active', True) is not False and meta.get('disabled') is not True
        nodes.append({
            'id': aliases[c.id], 'name': c.title, 'kind': 'chat',
            'context_id': context_by_chat.get(c.id),
            'status': agent_state(enabled, c.id in active_chats, c.updated_at, c.created_at, now, stale_hours * 3600),
            'last_activity': c.updated_at, 'next_run': None, 'model': '',
            'enabled': enabled, 'href': '/c/' + c.id, 'chat_href': '/c/' + c.id, 'last_error': False,
        })
    used_folders = {n['context_id'] for n in nodes}
    def folder_path(fid):
        names, seen = [], set()
        while fid in folder_map and fid not in seen:
            seen.add(fid)
            f = folder_map[fid]
            names.insert(0, f.name)
            fid = f.parent_id
        return ' / '.join(names)
    contexts = [{'id': f.id, 'name': folder_path(f.id)} for f in owned_folders if f.id in used_folders]
    if None in used_folders:
        contexts.append({'id': None, 'name': 'Unassigned context'})
    contexts.sort(key=lambda c: (c['id'] is None, c['name'].casefold()))
    nodes.sort(key=lambda n: (n['status'] != 'running', n['name'].casefold()))
    allowed = {n['id'] for n in nodes}
    return {'contexts': contexts, 'agents': nodes, 'edges': communication_edges(records, aliases, allowed),
            'generated_at': now, 'stale_hours': stale_hours}
