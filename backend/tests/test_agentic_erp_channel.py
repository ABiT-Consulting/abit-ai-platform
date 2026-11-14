"""Actual loopback WebSocket + native HTTP + httpx; no production connections."""

import asyncio
import base64
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest

import httpx
from fastapi import FastAPI
import uvicorn
import websockets

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


core = load('controlled_erp_channel', ROOT / 'backend/open_webui/utils/agentic_erp_channel.py')
native = load('controlled_erp_channel_client', ROOT / 'deployment/linux/abit-ai/agentic-gateway/erp_channel_client.py')
errors = load('controlled_erp_status_errors', ROOT / 'backend/open_webui/utils/erp_status_write.py')
SECRET = 'synthetic-private-control-no-production-' + 'x' * 32
TOKEN = 'syntheticApiKey:syntheticApiSecret'
BRIDGE = 'synthetic-native-erp-bridge'


class ControlledHTTPServer(ThreadingHTTPServer):
    # Set before bind/listen: the default backlog5 is smaller than the native
    # eight-request burst; measured queue pressure can exceed the fixed1s deadline.
    request_queue_size = 128


class NativeHTTP:
    def __init__(self):
        self.requests = []
        self.user = core.SERVICE_USER
        self.delay = 0
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()
        self.file = b'\x89PNG\r\n\x1a\n\x00\xff\x80private-exact-bytes'
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.execute('GET')

            def do_POST(self):
                self.execute('POST')

            def execute(self, method):
                body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                owner.requests.append((method, self.path, dict(self.headers), body))
                if self.headers.get('Authorization') != 'token ' + TOKEN or self.headers.get('Host') != core.SITE:
                    self.send_response(403)
                    self.end_headers()
                    return
                code = 200
                if self.path == '/api/method/frappe.auth.get_logged_user':
                    data = json.dumps({'message': owner.user}).encode()
                elif self.path.startswith('/private/files/'):
                    data = owner.file
                elif self.path.endswith('.receive_result'):
                    code = 417
                    data = json.dumps(
                        {
                            'exc_type': 'TimestampMismatchError',
                            '_server_messages': json.dumps([json.dumps({'message': 'Task changed'})]),
                        }
                    ).encode()
                else:
                    with owner.lock:
                        owner.active += 1
                        owner.max_active = max(owner.max_active, owner.active)
                    time.sleep(owner.delay)
                    with owner.lock:
                        owner.active -= 1
                    data = json.dumps({'data': {'name': 'SYNTHETIC-TASK'}}).encode()
                self.send_response(code)
                self.send_header(
                    'Content-Type', 'image/png' if self.path.startswith('/private/') else 'application/json'
                )
                self.send_header('Content-Disposition', 'attachment; filename="synthetic.png"')
                self.send_header('Set-Cookie', 'synthetic-cookie-must-not-forward')
                self.send_header('X-Internal-Secret', 'synthetic-secret-must-not-forward')
                self.end_headers()
                self.wfile.write(data)

        self.server = ControlledHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_address[1])

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class Channel(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        credential = json.dumps(
            {
                'schema_version': 1,
                'erp_user': core.SERVICE_USER,
                'api_key': TOKEN.split(':')[0],
                'api_secret': TOKEN.split(':')[1],
            }
        )
        for name, value in [('api-token', credential), ('control-token', SECRET)]:
            (root / name).write_text(value)
            (root / name).chmod(0o600)
        self.http = NativeHTTP()
        self.channel = core.ReverseERPChannel(core.ChannelProfile(True, BRIDGE), SECRET, request_timeout=1)
        app = FastAPI()
        app.add_api_websocket_route('/api/v1/agentic/erp-channel', self.channel.endpoint)
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        sock.listen(128)
        self.url = 'ws://127.0.0.1:' + str(sock.getsockname()[1]) + '/api/v1/agentic/erp-channel'
        self.server = uvicorn.Server(
            uvicorn.Config(app, log_level='critical', ws_max_size=core.MAX_FRAME, lifespan='off')
        )
        self.server_task = asyncio.create_task(self.server.serve(sockets=[sock]))
        for _ in range(200):
            if self.server.started:
                break
            await asyncio.sleep(0.01)
        self.assertTrue(self.server.started)
        self.config = native.ClientConfig(BRIDGE, self.http.url, str(root / 'api-token'), str(root / 'control-token'))
        self.tasks = []
        self.client = httpx.AsyncClient(
            transport=self.channel.transport(),
            base_url='https://erp.abit.ai',
            headers={
                'Authorization': 'token core-native-token-must-not-forward',
                'Cookie': 'core-cookie-must-not-forward',
            },
            timeout=2,
        )

    async def asyncTearDown(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.client.aclose()
        self.server.should_exit = True
        await asyncio.wait_for(self.server_task, 3)
        await asyncio.to_thread(self.http.close)
        self.temp.cleanup()

    async def connect(self, client=None):
        client = client or native.NativeERPChannelClient(self.config, _test_channel_url=self.url)
        task = asyncio.create_task(client.run_once())
        self.tasks.append(task)
        for _ in range(200):
            if self.channel.status()['connected']:
                return client, task
            if task.done():
                await task
            await asyncio.sleep(0.01)
        self.fail('Controlled native reverse channel failed to connect')

    async def test_real_HTTP_preserves_Frappe_error_and_actual_private_File_bytes(self):
        await self.connect()
        response = await self.client.post(
            '/api/method/abit_ai_assistant.agentic_control.receive_result',
            json={'event': {'event_id': 'synthetic-original-id'}},
            headers={'X-ABIT-Signature': 'a' * 64, 'X-ABIT-Agentic-Bridge': 'synthetic-bridge'},
        )
        self.assertEqual(response.status_code, 417)
        self.assertEqual(response.json()['exc_type'], 'TimestampMismatchError')
        self.assertTrue(errors.ERPResponseError(response).timestamp_conflict)
        self.assertIn('Task changed', str(errors.ERPResponseError(response)))
        response = await self.client.get('/private/files/synthetic.png?fid=fixture-file')
        self.assertEqual(response.content, self.http.file)
        self.assertNotIn('set-cookie', response.headers)
        self.assertNotIn('x-internal-secret', response.headers)
        _, _, headers, body = self.http.requests[-2]
        self.assertEqual(headers['Authorization'], 'token ' + TOKEN)
        self.assertNotIn('Cookie', headers)
        self.assertEqual(
            headers['X-Abit-Signature'] if 'X-Abit-Signature' in headers else headers['x-abit-signature'], 'a' * 64
        )
        self.assertEqual(json.loads(body)['event']['event_id'], 'synthetic-original-id')

    async def test_allowlist_denies_arbitrary_URL_method_SAP_RPC_and_credentials(self):
        await self.connect()
        before = len(self.http.requests)
        for method, url in [
            ('GET', 'https://example.invalid/api/resource/Task'),
            ('GET', 'http://erp.abit.ai/api/resource/Task'),
            ('DELETE', '/api/resource/Task/fixture'),
            ('POST', '/api/method/frappe.client.save'),
            ('GET', '/api/resource/Salary%20Slip'),
            ('GET', '/api/method/frappe.client.get_value?doctype=Salary+Slip'),
            ('GET', '/api/method/sap.execute'),
            ('GET', '/api/resource/Task/%2e%2e/User'),
        ]:
            with self.subTest(url=url), self.assertRaises(core.ERPChannelError):
                await self.client.request(method, url)
        self.assertEqual(len(self.http.requests), before)
        with self.assertRaises(ValueError):
            core.validate_path('GET', '//example.invalid/api/resource/Task')

    async def test_default_off_missing_channel_and_bad_identity_auth_fail_closed(self):
        with self.assertRaises(core.ERPChannelError):
            await self.client.get('/api/resource/Task')
        with self.assertRaises(websockets.exceptions.InvalidStatus):
            async with websockets.connect(self.url, additional_headers={'X-ABIT-ERP-Control': 'bad'}):
                pass
        async with websockets.connect(self.url, additional_headers={'X-ABIT-ERP-Control': SECRET}) as ws:
            await ws.send(
                json.dumps(
                    {
                        'type': 'hello',
                        'protocol_version': 1,
                        'bridge_id': BRIDGE,
                        'site': core.SITE,
                        'service_user': 'wrong@example.invalid',
                    }
                )
            )
            with self.assertRaises(websockets.exceptions.ConnectionClosed):
                await ws.recv()
        self.assertFalse(self.channel.status()['connected'])
        self.channel.profile = core.ChannelProfile()
        with self.assertRaises(websockets.exceptions.InvalidStatus):
            async with websockets.connect(self.url, additional_headers={'X-ABIT-ERP-Control': SECRET}):
                pass

    async def test_client_checks_actual_native_service_user_before_claim(self):
        self.http.user = 'wrong@example.invalid'
        with self.assertRaisesRegex(ValueError, 'Native API user'):
            await native.NativeERPChannelClient(self.config, _test_channel_url=self.url).run_once()
        self.assertFalse(self.channel.status()['connected'])

    async def test_one_active_channel_does_not_replace_original_and_policy_is_identical(self):
        await self.connect()
        async with websockets.connect(self.url, additional_headers={'X-ABIT-ERP-Control': SECRET}) as ws:
            await ws.send(
                json.dumps(
                    {
                        'type': 'hello',
                        'protocol_version': 1,
                        'bridge_id': BRIDGE,
                        'site': core.SITE,
                        'service_user': core.SERVICE_USER,
                    }
                )
            )
            with self.assertRaises(websockets.exceptions.ConnectionClosed):
                await ws.recv()
        self.assertTrue(self.channel.status()['connected'])
        self.assertEqual((await self.client.get('/api/resource/Task')).status_code, 200)
        for name in [
            'GET_RPCS',
            'POST_RPCS',
            'RESOURCE_TYPES',
            'REQUEST_HEADERS',
            'RESPONSE_HEADERS',
            'RESERVED_DISPATCH_FIELDS',
            'MAX_BODY',
            'MAX_FRAME',
        ]:
            self.assertEqual(getattr(core, name), getattr(native, name))

    async def test_lost_write_ACK_disconnect_never_replays_POST_on_reconnection(self):
        class DropAfterNativeWrite(native.NativeERPChannelClient):
            async def dispatch(inner, request):
                response = await super(DropAfterNativeWrite, inner).dispatch(request)
                if request['method'] == 'POST':
                    # Actual native HTTP has completed; deliberately lose only
                    # its reverse-channel ACK, as a disconnected container would.
                    raise RuntimeError('Controlled lost original write ACK')
                return response

        _, task = await self.connect(DropAfterNativeWrite(self.config, _test_channel_url=self.url))
        with self.assertRaises(core.ERPChannelError) as caught:
            await self.client.post(
                '/api/method/abit_ai_assistant.agentic_control.receive_result', json={'event': 'original'}
            )
        self.assertTrue(caught.exception.outcome_uncertain)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        for _ in range(100):
            if not self.channel.status()['connected']:
                break
            await asyncio.sleep(0.01)
        await self.connect()
        await self.client.get('/api/method/abit_ai_assistant.agentic_control.get_result_receipt?event_id=original')
        posts = [row for row in self.http.requests if row[0] == 'POST']
        self.assertEqual(len(posts), 1)

    async def test_body_bounds_and_private_credentials(self):
        await self.connect()
        before = len(self.http.requests)
        with self.assertRaises(core.ERPChannelError):
            await self.client.post('/api/method/upload_file', content=b'x' * (core.MAX_BODY + 1))
        self.assertEqual(len(self.http.requests), before)
        Path(self.config.native_token_file).chmod(0o644)
        with self.assertRaises(ValueError):
            native.private_text(self.config.native_token_file)
        with self.assertRaises(ValueError):
            replace(self.config, local_base='http://example.invalid:8000')

    async def test_native_HTTP_concurrency_is_bounded_to_eight(self):
        self.assertEqual(self.http.server.request_queue_size, 128)
        await self.connect()
        self.http.delay = 0.08
        responses = await asyncio.gather(
            *[self.client.get('/api/resource/Task?limit_start=' + str(i)) for i in range(16)]
        )
        self.assertTrue(all(item.status_code == 200 for item in responses))
        self.assertLessEqual(self.http.max_active, 8)
        self.assertGreater(self.http.max_active, 1)

    async def test_large_native_response_is_bounded_and_never_truncated_to_success(self):
        await self.connect()
        self.http.file = b'x' * (core.MAX_BODY + 1)
        with self.assertRaises(core.ERPChannelError) as caught:
            await self.client.get('/private/files/oversized.txt')
        self.assertFalse(caught.exception.outcome_uncertain)

    async def test_native_dispatch_query_overrides_rejected_on_both_sides(self):
        await self.connect()
        before = len(self.http.requests)
        client = native.NativeERPChannelClient(self.config, _test_channel_url=self.url)
        for path in [
            '/api/method/frappe.auth.get_logged_user?cmd=frappe.client.delete',
            '/api/resource/Task/SYNTHETIC?run_method=delete',
            '/private/files/synthetic.png?c%6Dd=frappe.client.delete',
            '/api/method/upload_file?method=frappe.client.delete',
            '/api/method/upload_file?file_url=https%3A%2F%2Fexample.invalid',
        ]:
            method = 'POST' if 'upload_file' in path else 'GET'
            with self.subTest(path=path):
                with self.assertRaises(core.ERPChannelError):
                    await self.client.request(method, path)
                with self.assertRaises(ValueError):
                    await client.dispatch(
                        {
                            'type': 'request',
                            'id': 'synthetic-request-id',
                            'method': method,
                            'path': path,
                            'headers': {},
                            'body_base64': '',
                        }
                    )
        self.assertEqual(len(self.http.requests), before)

    async def test_JSON_urlencoded_multipart_dispatch_fields_rejected_independently(self):
        await self.connect()
        before = len(self.http.requests)
        client = native.NativeERPChannelClient(self.config, _test_channel_url=self.url)
        url = 'https://erp.abit.ai/api/method/abit_ai_assistant.agentic_control.receive_result'
        requests = [
            httpx.Request('POST', url, json={'cmd': 'frappe.client.delete'}),
            httpx.Request(
                'POST',
                url,
                content=b'{"\\u0063md":"frappe.client.delete"}',
                headers={'content-type': 'application/json'},
            ),
            httpx.Request('POST', url, data={'cmd': 'frappe.client.delete'}),
            httpx.Request('POST', url, data={'run_method': 'delete'}),
            httpx.Request('POST', url, data={'method': 'frappe.client.delete'}),
            httpx.Request(
                'POST',
                'https://erp.abit.ai/api/method/upload_file',
                data={'doctype': 'Task', 'docname': 'SYNTHETIC', 'is_private': '1', 'cmd': 'frappe.client.delete'},
                files={'file': ('synthetic.png', self.http.file, 'image/png')},
            ),
        ]
        for request in requests:
            body = await request.aread()
            with self.subTest(content_type=request.headers.get('content-type')):
                with self.assertRaises(core.ERPChannelError):
                    await self.client.send(request)
                with self.assertRaises(ValueError):
                    await client.dispatch(
                        {
                            'type': 'request',
                            'id': 'synthetic-request-id',
                            'method': 'POST',
                            'path': request.url.raw_path.decode(),
                            'headers': core.safe_headers(dict(request.headers), core.REQUEST_HEADERS),
                            'body_base64': base64.b64encode(body).decode(),
                        }
                    )
        self.assertEqual(len(self.http.requests), before)

    async def test_actual_private_Task_multipart_bytes_preserved_and_upload_overrides_denied(self):
        await self.connect()
        request = self.client.build_request(
            'POST',
            '/api/method/upload_file',
            data={'doctype': 'Task', 'docname': 'SYNTHETIC', 'is_private': '1'},
            files={'file': ('synthetic.png', self.http.file, 'image/png')},
        )
        original = await request.aread()
        response = await self.client.send(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.http.requests[-1][3], original)
        self.assertIn(self.http.file, original)
        before = len(self.http.requests)
        for extra in [
            {'method': 'frappe.client.delete'},
            {'file_url': 'https://example.invalid'},
            {'library_file_name': 'foreign'},
        ]:
            with self.subTest(extra=extra), self.assertRaises(core.ERPChannelError):
                await self.client.post(
                    '/api/method/upload_file',
                    data={'doctype': 'Task', 'docname': 'SYNTHETIC', 'is_private': '1', **extra},
                    files={'file': ('synthetic.png', self.http.file, 'image/png')},
                )
        self.assertEqual(len(self.http.requests), before)


if __name__ == '__main__':
    unittest.main()
