"""Named host-backed Codex CLI models. No CLI credentials enter WebUI."""
import asyncio
import json
import os
import time
import uuid
from pathlib import Path

import httpx
from fastapi import HTTPException
from starlette.responses import StreamingResponse

MODEL_IDS = ('codex-cli-linux-dev', 'codex-cli-dev-02')
CONFIG_PATH = '/srv/abit-dev-linux/abit-ai/config/codex-cli.json'


def settings():
    path = Path(os.environ.get('ABIT_CODEX_CLI_CONFIG', CONFIG_PATH))
    if not path.exists():
        return {}
    config = json.loads(path.read_text())
    return {key: config[key] for key in MODEL_IDS if config.get(key, {}).get('enabled')}


def get_models():
    return [{'id': key, 'name': key, 'owned_by': 'openai', 'object': 'model',
             'created': 0, 'codex_cli': True, 'connection_type': 'external',
             'info': {'meta': {'description': 'Codex CLI text responses on ' + key.removeprefix('codex-cli-'),
                               'capabilities': {'vision': False, 'function_calling': False}}}}
            for key in settings()]


async def complete(request, form_data, user):
    # These endpoints use the administrator's existing host logins.
    if user.role != 'admin':
        raise HTTPException(403, 'Codex CLI host models are available to administrators only')
    model = form_data['model']
    config = settings().get(model)
    if not config:
        raise HTTPException(503, 'This Codex CLI host is not configured')
    if form_data.get('tools'):
        raise HTTPException(400, 'Codex CLI models support text chat; native function calling is unavailable')
    headers = {'Authorization': 'Bearer ' + Path(config['token_file']).read_text().strip()}
    client = httpx.AsyncClient(base_url=config['url'], headers=headers,
                               timeout=20, follow_redirects=False, trust_env=False)
    payload = {'model': model, 'messages': form_data.get('messages', [])}
    try:
        health = await client.get('/health')
        if health.status_code != 200 or health.json().get('model') != model:
            raise ValueError('Codex host unavailable')
    except Exception as exc:
        await client.aclose()
        raise HTTPException(503, 'Codex CLI host is unavailable before submission') from exc
    try:
        response = await client.post('/jobs', json=payload)
        if response.status_code != 202:
            status = response.status_code if response.status_code in (400, 413, 429) else 503
            raise HTTPException(status, 'Codex CLI host rejected the request before execution')
        key = response.json()['id']
        if not isinstance(key, str) or len(key) != 32 or any(c not in '0123456789abcdef' for c in key):
            raise ValueError('Invalid worker job id')
    except HTTPException:
        await client.aclose()
        raise
    except Exception as exc:
        await client.aclose()
        # A POST transport failure may have submitted work; do not blindly retry.
        raise HTTPException(504, 'Codex CLI host connection failed; request outcome is unknown') from exc

    async def poll():
        deadline = time.monotonic() + config.get('timeout', 660)
        while time.monotonic() < deadline:
            try:
                result = await client.get('/jobs/' + key)
                if result.status_code != 200:
                    raise HTTPException(504, 'Codex CLI host lost the active request')
                job = result.json()
            except httpx.HTTPError as exc:
                raise HTTPException(504, 'Connection to the active Codex CLI request was lost') from exc
            if job.get('status') == 'completed':
                yield job
                return
            if job.get('status') in ('failed', 'cancelled'):
                raise HTTPException(504, job.get('error') or 'Codex CLI request cancelled')
            yield None
            await asyncio.sleep(2)
        raise HTTPException(504, 'Codex CLI host timed out')

    async def cleanup():
        try:
            await client.delete('/jobs/' + key)
        except Exception:
            pass
        await client.aclose()

    completion_id = 'chatcmpl-' + uuid.uuid4().hex
    created = int(time.time())

    def result(job):
        raw = job.get('usage', {})
        return {'id': completion_id, 'object': 'chat.completion', 'created': created, 'model': model,
                'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': job['content']},
                             'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': raw.get('input_tokens', 0),
                          'completion_tokens': raw.get('output_tokens', 0),
                          'total_tokens': raw.get('input_tokens', 0) + raw.get('output_tokens', 0)}}

    if form_data.get('stream'):
        async def stream():
            try:
                async for job in poll():
                    if job is None:
                        yield ': codex-cli working\n\n'
                        continue
                    answer = result(job)
                    answer['object'] = 'chat.completion.chunk'
                    answer['choices'] = [{'index': 0, 'delta': {'role': 'assistant', 'content': job['content']},
                                          'finish_reason': None}]
                    yield 'data: ' + json.dumps(answer) + '\n\n'
                    answer['choices'] = [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]
                    yield 'data: ' + json.dumps(answer) + '\n\n'
                yield 'data: [DONE]\n\n'
            except HTTPException as exc:
                yield 'data: ' + json.dumps({'error': {'message': exc.detail, 'code': exc.status_code}}) + '\n\n'
                yield 'data: [DONE]\n\n'
            finally:
                await cleanup()
        return StreamingResponse(stream(), media_type='text/event-stream')
    try:
        async for job in poll():
            if job is not None:
                return result(job)
    finally:
        await cleanup()
