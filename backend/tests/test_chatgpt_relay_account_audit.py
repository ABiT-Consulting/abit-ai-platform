"""Verify the real audit methods never buffer or log ChatGPT sign-in payloads."""
import ast
import logging
import re
import unittest
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from starlette.requests import Request

SOURCE = Path(__file__).resolve().parents[1] / 'open_webui/utils/audit.py'
tree = ast.parse(SOURCE.read_text(encoding='utf-8-sig'))
nodes = []
for node in tree.body:
    if isinstance(node, ast.ClassDef) and node.name == 'AuditContext': nodes.append(node)
    if isinstance(node, ast.ClassDef) and node.name == 'AuditLoggingMiddleware':
        node.body = [n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in {'_audit_context','_log_audit_entry'}]
        nodes.append(node)
module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), *nodes], type_ignores=[])
ns = {'__builtins__':__builtins__, 'MAX_BODY_LOG_SIZE':8192, 'asynccontextmanager':asynccontextmanager,
      'uuid':uuid, 're':re, 'logger':logging.getLogger('isolated-audit'), 'AuditLogEntry':lambda **values: SimpleNamespace(**values)}
exec(compile(ast.fix_missing_locations(module), str(SOURCE), 'exec'), ns)

class TestChatGPTAccountAudit(unittest.IsolatedAsyncioTestCase):
    async def test_signin_input_and_screenshots_never_reach_audit_buffers(self):
        entries=[]
        middleware=ns['AuditLoggingMiddleware']()
        middleware.max_body_size=8192
        middleware.audit_level=SimpleNamespace(value='REQUEST_RESPONSE')
        middleware.audit_logger=SimpleNamespace(write=entries.append)
        async def no_user(request): return None
        middleware._get_authenticated_user=no_user
        for endpoint in ['input','view','start','cancel']:
            request=Request({'type':'http','method':'POST','path':'/api/v1/chatgpt-relay/account/relink/'+endpoint,
                             'scheme':'https','server':('abit.ai',443),'client':('127.0.0.1',1),'headers':[],'query_string':b''})
            async with middleware._audit_context(request) as context:
                context.add_request_chunk(b'{"text":"test-only-sensitive-value"}')
                context.add_response_chunk(b'{"image":"test-only-private-screenshot"}')
                self.assertEqual(bytes(context.request_body),b'')
                self.assertEqual(bytes(context.response_body),b'')
            self.assertEqual(entries[-1].request_object,'[redacted ChatGPT account payload]')
            self.assertEqual(entries[-1].response_object,'[redacted ChatGPT account payload]')
        self.assertEqual(len(entries),4)

    async def test_browser_screenshots_and_prompt_edits_are_private(self):
        entries = []
        middleware = ns['AuditLoggingMiddleware']()
        middleware.max_body_size = 8192
        middleware.audit_level = SimpleNamespace(value='REQUEST_RESPONSE')
        middleware.audit_logger = SimpleNamespace(write=entries.append)
        async def no_user(request): return None
        middleware._get_authenticated_user = no_user
        for operation in ['view', 'open', 'input']:
            request = Request({'type': 'http', 'method': 'POST',
                'path': '/api/v1/chatgpt-relay/browser/' + operation,
                'scheme': 'https', 'server': ('abit.ai', 443),
                'client': ('127.0.0.1', 1), 'headers': [], 'query_string': b''})
            async with middleware._audit_context(request) as context:
                context.add_request_chunk(b'{"text":"private client prompt"}')
                context.add_response_chunk(b'{"image":"private client screenshot"}')
                self.assertEqual(bytes(context.request_body), b'')
                self.assertEqual(bytes(context.response_body), b'')
            self.assertEqual(entries[-1].request_object, '[redacted ChatGPT relay browser payload]')
            self.assertEqual(entries[-1].response_object, '[redacted ChatGPT relay browser payload]')
        self.assertEqual(len(entries), 3)

    async def test_unrelated_audit_behavior_is_preserved(self):
        entries=[]
        middleware=ns['AuditLoggingMiddleware']()
        middleware.max_body_size=8192
        middleware.audit_level=SimpleNamespace(value='REQUEST_RESPONSE')
        middleware.audit_logger=SimpleNamespace(write=entries.append)
        async def no_user(request): return None
        middleware._get_authenticated_user=no_user
        request=Request({'type':'http','method':'POST','path':'/api/v1/folders/', 'scheme':'https',
                         'server':('abit.ai',443),'client':('127.0.0.1',1),'headers':[],'query_string':b''})
        async with middleware._audit_context(request) as context:
            context.add_request_chunk(b'{"name":"test"}')
        self.assertEqual(entries[0].request_object,'{"name":"test"}')

if __name__=='__main__': unittest.main()
