"""Optional authenticated reverse HTTP channel to the fixed native ABiT ERP.

This carries existing ERP HTTP requests, not commands or model traffic. Request
IDs are ephemeral; durable mutation identities remain in the native RPC bodies.
A disconnected/expired write is uncertain and is never replayed here.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default as mime_policy
import hmac
import json
import re
import secrets
from urllib.parse import parse_qs, parse_qsl, unquote, urlsplit

import httpx
from starlette.websockets import WebSocket, WebSocketDisconnect

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
        'abit_ai_assistant.agentic_it_plan.get_it_routine_implementation_receipt',
        'abit_ai_assistant.agentic_operations.get_controls_audit_receipt',
        'abit_ai_assistant.agentic_operations.get_controls_audit_current',
        'abit_ai_assistant.agentic_operations.get_controls_audit_for_qa',
        'abit_ai_assistant.project_agent_bindings.get_project_onboarding_context',
    }
)
POST_RPCS = frozenset(
    {
        'upload_file',
        'abit_ai_assistant.agentic_control.receive_result',
        'abit_ai_assistant.agentic_control.create_agent_task',
        'abit_ai_assistant.agentic_it_plan.bind_it_plan',
        'abit_ai_assistant.agentic_operations.record_controls_audit',
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


class ERPChannelError(httpx.TransportError):
    def __init__(self, message, *, request=None, outcome_uncertain=False):
        super().__init__(message, request=request)
        self.outcome_uncertain = outcome_uncertain


@dataclass(frozen=True)
class ChannelProfile:
    enabled: bool = False
    bridge_id: str = ''
    site: str = SITE
    service_user: str = SERVICE_USER

    def __post_init__(self):
        if (
            type(self.enabled) is not bool
            or self.enabled
            and (
                not isinstance(self.bridge_id, str)
                or not IDENTITY.fullmatch(self.bridge_id)
                or self.site != SITE
                or self.service_user != SERVICE_USER
            )
        ):
            raise ValueError('Reverse ERP channel requires its exact configured native identity')


class _Connection:
    def __init__(self, websocket):
        self.websocket = websocket
        self.pending = {}
        self.send_lock = asyncio.Lock()


class ReverseERPChannel:
    def __init__(self, profile: ChannelProfile, control_secret: str, *, request_timeout=8):
        if profile.enabled and (not isinstance(control_secret, str) or len(control_secret) < 32):
            raise ValueError('Reverse ERP channel authentication is unavailable')
        self.profile = profile
        self._secret = control_secret
        self._connection = None
        self._registration = asyncio.Lock()
        self._slots = asyncio.Semaphore(MAX_CONCURRENCY)
        self.request_timeout = min(30, max(0.05, float(request_timeout)))

    def transport(self):
        return ReverseERPTransport(self)

    def status(self):
        return {
            'enabled': self.profile.enabled,
            'connected': self._connection is not None,
            'bridge_id': self.profile.bridge_id,
            'site': self.profile.site,
            'service_user': self.profile.service_user,
            'inflight': len(self._connection.pending) if self._connection else 0,
        }

    async def endpoint(self, websocket: WebSocket):
        supplied = websocket.headers.get('x-abit-erp-control', '')
        if not self.profile.enabled or not hmac.compare_digest(str(supplied).encode(), self._secret.encode()):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        connection = None
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), 5)
            if len(raw.encode()) > 2048:
                raise ValueError('Oversized ERP channel identity')
            hello = json.loads(raw)
            expected = {
                'type': 'hello',
                'protocol_version': 1,
                'bridge_id': self.profile.bridge_id,
                'site': self.profile.site,
                'service_user': self.profile.service_user,
            }
            if not isinstance(hello, dict) or type(hello.get('protocol_version')) is not int or hello != expected:
                raise ValueError('ERP channel native identity differs')
            async with self._registration:
                if self._connection is not None:
                    raise ValueError('An authenticated ERP channel is already active')
                connection = _Connection(websocket)
                self._connection = connection
            await websocket.send_text(_json({'type': 'ready', 'protocol_version': 1}))
            while True:
                raw = await websocket.receive_text()
                if len(raw.encode()) > MAX_FRAME:
                    raise ValueError('Oversized ERP response frame')
                response = json.loads(raw)
                if not isinstance(response, dict) or response.get('type') not in {'response', 'error'}:
                    raise ValueError('Malformed ERP response frame')
                ident = response.get('id')
                if not isinstance(ident, str) or not IDENTITY.fullmatch(ident):
                    raise ValueError('Malformed ERP response identity')
                pending = connection.pending.get(ident)
                if pending and not pending.done():
                    pending.set_result(response)
        except (WebSocketDisconnect, asyncio.TimeoutError, ValueError, TypeError, RuntimeError):
            try:
                await websocket.close(code=1008)
            except (RuntimeError, WebSocketDisconnect):
                pass
        finally:
            if connection is not None:
                async with self._registration:
                    if self._connection is connection:
                        self._connection = None
                for pending in connection.pending.values():
                    if not pending.done():
                        pending.set_result(None)

    async def request(self, request):
        url = request.url
        if url.scheme != 'https' or url.host != SITE or url.port not in (None, 443) or url.userinfo or url.fragment:
            raise ERPChannelError('Reverse channel destination is not the fixed ABiT ERP', request=request)
        try:
            path = validate_path(request.method, url.raw_path.decode('ascii'))
            headers = safe_headers(dict(request.headers), REQUEST_HEADERS)
        except (ValueError, UnicodeError) as error:
            raise ERPChannelError(str(error), request=request) from None
        body = bytearray()
        async for chunk in request.stream:
            body.extend(chunk)
            if len(body) > MAX_BODY:
                raise ERPChannelError('ERP channel request exceeds its 3MB bound', request=request)
        try:
            validate_body(request.method, path, headers, bytes(body))
        except (ValueError, UnicodeError, TypeError):
            raise ERPChannelError(
                'ERP channel body or native dispatcher field is not allowed', request=request
            ) from None
        async with self._slots:
            connection = self._connection
            if not self.profile.enabled or connection is None:
                raise ERPChannelError('Authenticated native ERP channel is unavailable', request=request)
            ident = 'erp-http-' + secrets.token_hex(24)
            future = asyncio.get_running_loop().create_future()
            connection.pending[ident] = future
            payload = {
                'type': 'request',
                'id': ident,
                'method': request.method,
                'path': path,
                'headers': headers,
                'body_base64': base64.b64encode(body).decode('ascii'),
            }
            try:
                async with connection.send_lock:
                    if self._connection is not connection:
                        raise ERPChannelError('Original native ERP channel disconnected', request=request)
                    await connection.websocket.send_text(_json(payload))
                response = await asyncio.wait_for(future, self.request_timeout)
                if not response or response.get('type') == 'error':
                    raise ERPChannelError('Original native ERP HTTP outcome is unavailable', request=request)
                if set(response) != {'type', 'id', 'status', 'headers', 'body_base64'}:
                    raise ValueError('Malformed native ERP response')
                status = response['status']
                if not isinstance(status, int) or isinstance(status, bool) or not 200 <= status <= 599:
                    raise ValueError('Invalid native ERP HTTP status')
                if not isinstance(response['headers'], dict):
                    raise ValueError('Malformed native ERP response headers')
                result_headers = safe_headers(response['headers'], RESPONSE_HEADERS)
                data = base64.b64decode(response['body_base64'], validate=True)
                if len(data) > MAX_BODY:
                    raise ValueError('Native ERP response exceeds its 3MB bound')
                return httpx.Response(status, headers=result_headers, content=data, request=request)
            except asyncio.CancelledError:
                raise
            except (asyncio.TimeoutError, OSError, RuntimeError, ValueError, TypeError, ERPChannelError):
                raise ERPChannelError(
                    'Original native ERP HTTP outcome is unavailable; reconcile its original receipt',
                    request=request,
                    outcome_uncertain=request.method == 'POST',
                ) from None
            finally:
                connection.pending.pop(ident, None)
                if not future.done():
                    future.cancel()


class ReverseERPTransport(httpx.AsyncBaseTransport):
    def __init__(self, channel):
        self.channel = channel

    async def handle_async_request(self, request):
        return await self.channel.request(request)

    async def aclose(self):
        # ERPClient instances share a long-lived server channel. Closing one
        # ordinary HTTP client must not close another client's authenticated WS.
        pass
