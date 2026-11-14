"""Fixed native ABiT ERP reverse HTTP client; no shell/SQL/model execution.

Requires existing httpx and websockets packages. A reviewed deployment supplies
private API/control-token files. This program neither creates credentials nor
prints native requests, replies, credentials or exception details.
"""

from __future__ import annotations
import asyncio
import base64
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default as mime_policy
import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import parse_qs, parse_qsl, unquote, urlsplit

import httpx
import websockets

# This policy is repeated on both sides. The focused suite verifies exact parity.
MAX_FRAME = 4 * 1024 * 1024

MAX_BODY = 3_000_000

MAX_CONCURRENCY = 8

SITE = 'erp.abit.ai'

SERVICE_USER = 'ai-developer@abitcons.com'

IDENTITY = re.compile(r'[A-Za-z0-9_.:-]{8,160}')

REQUEST_HEADERS = frozenset(
    {'accept', 'content-type', 'x-abit-agentic-bridge', 'x-abit-timestamp', 'x-abit-event-id', 'x-abit-signature'}
)

RESPONSE_HEADERS = frozenset({'content-type', 'content-disposition'})

RESOURCE_TYPES = frozenset({'Project', 'Task', 'ToDo', 'File', 'User'})
RESERVED_DISPATCH_FIELDS = frozenset({'cmd', 'run_method', 'method'})

GET_RPCS = frozenset(
    {
        'frappe.auth.get_logged_user',
        'frappe.client.get_value',
        'abit_ai_assistant.agentic_control.get_work_packet',
        'abit_ai_assistant.agentic_control.get_project_backlog',
        'abit_ai_assistant.agentic_control.get_project_task_snapshot',
        'abit_ai_assistant.agentic_control.get_result_receipt',
        'abit_ai_assistant.agentic_control.get_task_request_receipt',
        'abit_ai_assistant.agentic_control.get_backlog_release_receipt',
        'abit_ai_assistant.agentic_control_fence.get_control_intent',
        'abit_ai_assistant.agentic_control_fence.get_control_application_receipt',
        'abit_ai_assistant.agentic_approval_actions.get_action_approval',
        'abit_ai_assistant.agentic_pm_review.get_review_packet',
        'abit_ai_assistant.agentic_pm_review.get_review_evidence_receipt',
        'abit_ai_assistant.agentic_pm_review.download_review_evidence',
        'abit_ai_assistant.agentic_gateway_boundary.verify_boundary',
        'abit_ai_assistant.agentic_attachment_context.read_attachment_context',
        'abit_ai_assistant.agentic_it_plan.get_it_plan_binding_receipt',
        'abit_ai_assistant.agentic_it_plan.get_it_implementation_receipt',
        'abit_ai_assistant.project_agent_bindings.get_project_onboarding_context',
    }
)

POST_RPCS = frozenset(
    {
        'upload_file',
        'abit_ai_assistant.agentic_control.receive_result',
        'abit_ai_assistant.agentic_control.create_agent_task',
        'abit_ai_assistant.agentic_it_plan.bind_it_plan',
        'abit_ai_assistant.agentic_control.release_backlog',
        'abit_ai_assistant.agentic_control.prepare_project_start',
        'abit_ai_assistant.agentic_control.prepare_task_start',
        'abit_ai_assistant.agentic_control.recover_outbox',
        'abit_ai_assistant.agentic_control_fence.acknowledge_control',
        'abit_ai_assistant.agentic_approval_actions.request_action_approval',
        'abit_ai_assistant.agentic_approval_actions.get_action_approval',
        'abit_ai_assistant.agentic_approval_actions.consume_action_approval',
        'abit_ai_assistant.agentic_pm_review.receive_result',
        'abit_ai_assistant.agentic_pm_review.upload_review_evidence',
        'abit_ai_assistant.project_agent_bindings.bind_project_agent',
        'abit_ai_assistant.project_agent_bindings.verify_project_agent_binding',
    }
)


def _json(value):
    return json.dumps(value, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def safe_headers(headers, allowed):
    result = {}
    for key, value in headers.items():
        key = key.lower()
        if key not in allowed:
            continue
        if not isinstance(value, str) or len(value.encode()) > 2048 or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError('ERP channel header exceeds the fixed protocol')
        result[key] = value
    return result


def validate_path(method, path):
    """Exact native resource/RPC allowlist, repeated independently by the client."""
    if method not in {'GET', 'POST'} or not isinstance(path, str) or len(path) > 20000:
        raise ValueError('ERP channel method or path is not allowed')
    parts = urlsplit(path)
    if parts.scheme or parts.netloc or parts.fragment or not path.startswith('/') or '\\' in path:
        raise ValueError('ERP channel accepts only a fixed native relative path')
    decoded = unquote(parts.path)
    if any(ord(c) < 32 or ord(c) == 127 for c in decoded) or any(x in {'.', '..'} for x in decoded.split('/')):
        raise ValueError('ERP channel path is malformed')
    query = parse_qsl(parts.query, keep_blank_values=True, errors='strict', max_num_fields=200)
    if any(key.casefold() in RESERVED_DISPATCH_FIELDS for key, _ in query):
        raise ValueError('ERP channel reserved native dispatcher field is not allowed')
    if method == 'GET' and re.fullmatch(r'/private/files/[A-Za-z0-9_.-]+', decoded):
        return path
    if decoded.startswith('/api/resource/') and method == 'GET':
        segments = decoded[len('/api/resource/') :].split('/')
        if 1 <= len(segments) <= 2 and segments[0] in RESOURCE_TYPES and all(segments):
            return path
    prefix = '/api/method/'
    rpc = decoded[len(prefix) :] if decoded.startswith(prefix) else ''
    if rpc in (GET_RPCS if method == 'GET' else POST_RPCS):
        if rpc == 'upload_file' and parts.query:
            raise ValueError('ERP channel upload cannot carry query overrides')
        if rpc == 'frappe.client.get_value':
            query = parse_qs(parts.query)
            if query.get('doctype') not in [[x] for x in RESOURCE_TYPES]:
                raise ValueError('ERP channel get_value resource is not allowed')
        return path
    raise ValueError('ERP channel native operation is not allowed')


def validate_body(method, path, headers, body):
    """Match Frappe form_dict inputs before its cmd/resource/upload dispatch.

    Native Frappe16 dispatches form_dict.cmd before URL routing, run_method
    during resource reads, and upload_file.method inside its upload handler.
    Only supported unambiguous JSON/form formats cross this transport.
    """
    if method == 'GET':
        if body:
            raise ValueError('ERP channel GET cannot carry a body')
        return
    content_type = headers.get('content-type', '')
    media_type = content_type.split(';', 1)[0].strip().lower()
    fields = []
    files = []
    if media_type == 'application/json' or media_type.startswith('application/') and media_type.endswith('+json'):

        class Pairs(list):
            pass

        def invalid_constant(value):
            raise ValueError('Nonfinite JSON is unsupported')

        value = json.loads(body.decode('utf-8'), object_pairs_hook=Pairs, parse_constant=invalid_constant)
        if not isinstance(value, Pairs):
            raise ValueError('ERP channel RPC body must be a JSON object')
        fields = value
    elif media_type == 'application/x-www-form-urlencoded':
        fields = parse_qsl(body.decode('utf-8'), keep_blank_values=True, errors='strict', max_num_fields=200)
    elif media_type == 'multipart/form-data':
        message = BytesParser(policy=mime_policy).parsebytes(
            ('Content-Type: ' + content_type + '\r\nMIME-Version: 1.0\r\n\r\n').encode('ascii') + body
        )
        boundary = message.get_boundary()
        if (
            not message.is_multipart()
            or message.defects
            or not isinstance(boundary, str)
            or not re.fullmatch(r'[A-Za-z0-9\x27()+_,./:=?-]{1,200}', boundary)
            or not body.startswith(b'--' + boundary.encode() + b'\r\n')
            or not body.endswith(b'\r\n--' + boundary.encode() + b'--\r\n')
        ):
            raise ValueError('ERP channel multipart boundary is malformed')
        parts = list(message.iter_parts())
        if not 1 <= len(parts) <= 20:
            raise ValueError('ERP channel multipart field count exceeds its bound')
        for part in parts:
            dispositions = [value for key, value in part.raw_items() if key.lower() == 'content-disposition']
            if (
                part.is_multipart()
                or part.defects
                or len(dispositions) != 1
                or part.get_content_disposition() != 'form-data'
                or part.get('Content-Transfer-Encoding') is not None
                or re.search(r'(?:^|;)\s*(?:name|filename)\*', str(dispositions[0]), re.I)
            ):
                raise ValueError('ERP channel multipart part is unsupported')
            # Werkzeug and MIME parsers differ on malformed escaping/RFC2231.
            # Accept only the unambiguous shape emitted by our HTTP uploader.
            if not re.fullmatch(
                r'form-data;\s*name="[A-Za-z0-9_]+"(?:;\s*filename="[^"\\\r\n]*")?', dispositions[0], re.I
            ):
                raise ValueError('ERP channel multipart disposition is ambiguous')
            name = part.get_param('name', header='content-disposition')
            if not isinstance(name, str) or not name or len(name) > 128:
                raise ValueError('ERP channel multipart field name is malformed')
            if part.get_filename() is not None:
                files.append(name)
                fields.append((name, None))
            else:
                data = part.get_payload(decode=True)
                if not isinstance(data, bytes) or len(data) > 16384:
                    raise ValueError('ERP channel multipart scalar exceeds its bound')
                fields.append((name, data.decode('utf-8')))
    else:
        raise ValueError('ERP channel body encoding is unsupported')
    keys = [key for key, _ in fields]
    if any(key.casefold() in RESERVED_DISPATCH_FIELDS for key in keys):
        raise ValueError('ERP channel reserved native dispatcher field is not allowed')
    if len(keys) != len(set(keys)):
        raise ValueError('ERP channel duplicate body fields are not allowed')
    if urlsplit(path).path == '/api/method/upload_file':
        values = dict(fields)
        if (
            media_type != 'multipart/form-data'
            or set(values) != {'doctype', 'docname', 'is_private', 'file'}
            or files != ['file']
            or values['doctype'] != 'Task'
            or values['is_private'] != '1'
            or not isinstance(values['docname'], str)
            or not 1 <= len(values['docname']) <= 160
            or any(ord(c) < 32 for c in values['docname'])
        ):
            raise ValueError('ERP channel upload requires the exact private Task attachment envelope')


def private_text(path):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('ERP channel credential path must be server absolute')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 16384:
            raise ValueError('ERP channel credentials require a private regular server file')
        return os.read(fd, 16385).decode('utf-8').strip()
    finally:
        os.close(fd)


@dataclass(frozen=True)
class ClientConfig:
    bridge_id: str
    local_base: str
    native_token_file: str
    control_token_file: str
    site: str = SITE
    service_user: str = SERVICE_USER

    def __post_init__(self):
        local = urlsplit(self.local_base)
        if (
            not IDENTITY.fullmatch(self.bridge_id)
            or self.site != SITE
            or self.service_user != SERVICE_USER
            or local.scheme != 'http'
            or local.hostname != '127.0.0.1'
            or not local.port
            or local.username
            or local.password
            or local.query
            or local.fragment
            or local.path not in {'', '/'}
        ):
            raise ValueError('ERP channel requires its reviewed fixed native localhost identity')


class NativeERPChannelClient:
    def __init__(self, config: ClientConfig, *, _test_channel_url=None):
        self.config = config
        self.channel_url = 'wss://abit.ai/api/v1/agentic/erp-channel'
        if _test_channel_url is not None:
            parsed = urlsplit(_test_channel_url)
            if (
                parsed.scheme != 'ws'
                or parsed.hostname != '127.0.0.1'
                or not parsed.port
                or parsed.path != '/api/v1/agentic/erp-channel'
            ):
                raise ValueError('Controlled protocol tests require a loopback channel')
            self.channel_url = _test_channel_url
        self._http = None

    async def dispatch(self, request):
        ident = request.get('id')
        if (
            set(request) != {'type', 'id', 'method', 'path', 'headers', 'body_base64'}
            or request.get('type') != 'request'
            or not isinstance(ident, str)
            or not IDENTITY.fullmatch(ident)
        ):
            raise ValueError('Malformed native ERP request frame')
        path = validate_path(request['method'], request['path'])
        if not isinstance(request['headers'], dict) or set(request['headers']) - REQUEST_HEADERS:
            raise ValueError('Unexpected native ERP request headers')
        headers = safe_headers(request['headers'], REQUEST_HEADERS)
        body = base64.b64decode(request['body_base64'], validate=True)
        if len(body) > MAX_BODY:
            raise ValueError('Native ERP request exceeds its fixed body protocol')
        validate_body(request['method'], path, headers, body)
        try:
            async with self._http.stream(request['method'], path, content=body, headers=headers) as response:
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_BODY:
                        raise ValueError('Native ERP response exceeds its fixed body limit')
                return {
                    'type': 'response',
                    'id': ident,
                    'status': response.status_code,
                    'headers': safe_headers(dict(response.headers), RESPONSE_HEADERS),
                    'body_base64': base64.b64encode(data).decode('ascii'),
                }
        except (httpx.HTTPError, ValueError):
            # A native write might have committed. Do not retry or expose raw
            # exception/body/header data. Existing RPC receipts reconcile it.
            return {'type': 'error', 'id': ident, 'code': 'native_http_outcome_unavailable'}

    async def run_once(self):
        value = json.loads(private_text(self.config.native_token_file))
        if (
            not isinstance(value, dict)
            or set(value) != {'schema_version', 'erp_user', 'api_key', 'api_secret'}
            or type(value['schema_version']) is not int
            or value['schema_version'] != 1
            or value['erp_user'] != self.config.service_user
            or any(
                not isinstance(value[key], str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,256}', value[key])
                for key in ['api_key', 'api_secret']
            )
        ):
            raise ValueError('Private native channel identity is unavailable')
        native = value['api_key'] + ':' + value['api_secret']
        control = private_text(self.config.control_token_file)
        if not re.fullmatch(r'[A-Za-z0-9_-]{8,256}:[A-Za-z0-9_-]{8,256}', native) or len(control) < 32:
            raise ValueError('Private native channel credentials are unavailable')
        tasks = set()

        async def authenticate(request):
            request.headers.pop('cookie', None)
            request.headers['Authorization'] = 'token ' + native
            request.headers['Host'] = self.config.site

        async with httpx.AsyncClient(
            base_url=self.config.local_base,
            timeout=8,
            trust_env=False,
            follow_redirects=False,
            headers={'Authorization': 'token ' + native, 'Host': self.config.site},
            event_hooks={'request': [authenticate]},
        ) as client:
            self._http = client
            async with client.stream('GET', '/api/method/frappe.auth.get_logged_user') as identity:
                data = bytearray()
                async for chunk in identity.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 16384:
                        raise ValueError('Native API identity exceeded its fixed response bound')
                if identity.status_code != 200 or json.loads(data) != {'message': self.config.service_user}:
                    raise ValueError('Native API user did not match the configured service identity')
            try:
                async with websockets.connect(
                    self.channel_url,
                    additional_headers={'X-ABIT-ERP-Control': control},
                    proxy=None,
                    max_size=MAX_FRAME,
                    max_queue=8,
                    compression=None,
                    open_timeout=10,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=2,
                ) as channel:
                    await channel.send(
                        _json(
                            {
                                'type': 'hello',
                                'protocol_version': 1,
                                'bridge_id': self.config.bridge_id,
                                'site': self.config.site,
                                'service_user': self.config.service_user,
                            }
                        )
                    )
                    if json.loads(await asyncio.wait_for(channel.recv(), 5)) != {
                        'type': 'ready',
                        'protocol_version': 1,
                    }:
                        raise ValueError('Core did not accept the exact native ERP identity')
                    send_lock = asyncio.Lock()
                    seen = set()

                    async def process(request):
                        response = await self.dispatch(request)
                        async with send_lock:
                            await channel.send(_json(response))

                    async for raw in channel:
                        if not isinstance(raw, str) or len(raw.encode()) > MAX_FRAME or len(tasks) >= MAX_CONCURRENCY:
                            raise ValueError('Native ERP channel frame/concurrency exceeds its limit')
                        request = json.loads(raw)
                        if not isinstance(request, dict):
                            raise ValueError('Malformed native request')
                        ident = request.get('id')
                        if (
                            not isinstance(ident, str)
                            or not IDENTITY.fullmatch(ident)
                            or ident in seen
                            or len(seen) >= 4096
                        ):
                            raise ValueError('Native request identity is duplicate or exceeds the connection bound')
                        seen.add(ident)
                        task = asyncio.create_task(process(request))
                        tasks.add(task)
                        task.add_done_callback(tasks.discard)
                        # Observe completed protocol failures without logging
                        # raw native state or leaving unbounded task exceptions.
                        task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                self._http = None


async def main(config_path):
    value = json.loads(private_text(config_path))
    config = ClientConfig(**value)
    client = NativeERPChannelClient(config)
    while True:
        try:
            await client.run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Transport reconnect never resubmits prior native requests.
            print('ERP reverse channel unavailable; reconnecting', flush=True)
        await asyncio.sleep(5)


if __name__ == '__main__':
    import sys

    if len(sys.argv) != 2:
        raise SystemExit('Usage: erp_channel_client.py /fixed/private/channel-config.json')
    asyncio.run(main(sys.argv[1]))
