"""Owner-only, factual end-of-day automation run roll-up.

The run status describes scheduler execution, not verified business completion.
The assistant snippets are attributed as reported updates, never audit proof.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from open_webui.models.automations import Automation, AutomationRun
from open_webui.models.chats import Chat, ChatMessage

MAX_RUNS = 3000
MAX_CANDIDATE_CHATS_PER_AGENT = 16
MAX_SNIPPETS = 3


def normalize_reported_update(content) -> str:
    """Convert a stored assistant message to a short, text-only attributed note."""
    if isinstance(content, dict):
        content = content.get('content') or content.get('text') or ''
    if isinstance(content, list):
        content = ' '.join(x for x in content if isinstance(x, str))
    if not isinstance(content, str):
        return ''
    text = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL | re.I)
    text = re.sub(r'```.*?```', '', text, flags=re.DOTALL)
    text = re.sub(r'!\[[^]]*\]\([^)]+\)', '', text)
    text = re.sub(r'\[([^]]+)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'<https?://[^>]+>', '', text)
    text = re.sub(r'(?m)^\s{0,5}#{1,6}\s+', '', text)
    text = re.sub(r'[*_`]', '', text)
    text = ' '.join(text.split()).strip()
    return text[:277] + ('…' if len(text) > 277 else '') if len(text) > 7 else ''


def date_bounds(day: date | None, timezone: str) -> tuple[str, int, int, int, int]:
    if not isinstance(timezone, str) or len(timezone) > 64:
        raise ValueError('Invalid timezone')
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError('Invalid timezone') from exc
    d = day or datetime.now(zone).date()
    begin = datetime.combine(d, time.min, tzinfo=zone)
    end = datetime.combine(d + timedelta(days=1), time.min, tzinfo=zone)
    return d.isoformat(), int(begin.timestamp() * 1_000_000_000), int(end.timestamp() * 1_000_000_000), int(begin.timestamp()), int(end.timestamp())


async def daily_activity(user_id: str, db, day: date | None, timezone: str):
    day_string, begin_ns, end_ns, begin_s, end_s = date_bounds(day, timezone)
    rows = (await db.execute(
        select(
            AutomationRun.automation_id, Automation.name, AutomationRun.chat_id,
            AutomationRun.status, AutomationRun.error, AutomationRun.created_at,
        )
        .join(Automation, Automation.id == AutomationRun.automation_id)
        .where(Automation.user_id == user_id,
               AutomationRun.created_at >= begin_ns,
               AutomationRun.created_at < end_ns)
        .order_by(AutomationRun.created_at.desc())
        .limit(MAX_RUNS + 1)
    )).all()
    truncated = len(rows) > MAX_RUNS
    rows = rows[:MAX_RUNS]
    groups: dict[str, dict] = {}
    chat_to_agent: dict[str, str] = {}
    candidate_count: dict[str, int] = {}
    totals = {'runs': 0, 'succeeded': 0, 'failed': 0, 'agents': 0}
    for aid, name, chat_id, status, error, created_at in rows:
        if aid not in groups:
            groups[aid] = {
                'id': aid, 'name': name, 'runs': 0, 'succeeded': 0,
                'failed': 0, 'last_at': int(created_at // 1_000_000_000),
                'last_status': status, 'last_chat_href': None,
                'reported_updates': [],
            }
        agent = groups[aid]
        agent['runs'] += 1
        totals['runs'] += 1
        if status == 'success':
            agent['succeeded'] += 1
            totals['succeeded'] += 1
        elif status == 'error':
            agent['failed'] += 1
            totals['failed'] += 1
        # Each run row is owned by this account. Require matching Chat owner before linking.
        if chat_id and candidate_count.get(aid, 0) < MAX_CANDIDATE_CHATS_PER_AGENT and chat_id not in chat_to_agent:
            chat_to_agent[chat_id] = aid
            candidate_count[aid] = candidate_count.get(aid, 0) + 1
            if agent['last_chat_href'] is None:
                agent['last_chat_href'] = '/c/' + chat_id
        if status == 'error' and error and not agent.get('last_error'):
            agent['last_error'] = normalize_reported_update(error) or 'Execution error'
    totals['agents'] = len(groups)

    # Confirm referenced chats belong to the requesting user, independently of run IDs.
    valid_chat_ids = set()
    if chat_to_agent:
        valid_chat_ids = {id for (id,) in (await db.execute(
            select(Chat.id).where(Chat.user_id == user_id, Chat.id.in_(list(chat_to_agent)))
        )).all()}
    for agent in groups.values():
        if agent['last_chat_href'] and agent['last_chat_href'][3:] not in valid_chat_ids:
            agent['last_chat_href'] = None
    # Fetch only candidate run chats, only owner-authored assistant results from the date.
    # Messages added on a later day must not rewrite yesterday's history.
    if valid_chat_ids:
        messages = (await db.execute(
            select(ChatMessage.chat_id, ChatMessage.content, ChatMessage.created_at)
            .join(Chat, Chat.id == ChatMessage.chat_id)
            .where(
                Chat.user_id == user_id,
                ChatMessage.user_id == user_id,
                ChatMessage.chat_id.in_(list(valid_chat_ids)),
                ChatMessage.role == 'assistant',
                ChatMessage.done == True,
                ChatMessage.created_at >= begin_s,
                ChatMessage.created_at < end_s,
                ChatMessage.updated_at < end_s,
            )
            .order_by(ChatMessage.created_at.desc())
            .limit(MAX_RUNS)
        )).all()
        for chat_id, content, created_at in messages:
            aid = chat_to_agent.get(chat_id)
            if not aid:
                continue
            agent = groups[aid]
            if len(agent['reported_updates']) >= MAX_SNIPPETS:
                continue
            note = normalize_reported_update(content)
            if note and not any(x['text'] == note for x in agent['reported_updates']):
                agent['reported_updates'].append({'text': note, 'at': int(created_at), 'chat_href': '/c/' + chat_id})

    for agent in groups.values():
        agent['reported_updates'] = agent['reported_updates'][:MAX_SNIPPETS]
    return {
        'date': day_string, 'timezone': timezone,
        'totals': totals, 'truncated': truncated,
        'agents': list(groups.values()),
        'source': 'automation_run and owner-scoped assistant messages',
        'note': 'Success means the automation ran, not that external work was independently verified.',
    }
