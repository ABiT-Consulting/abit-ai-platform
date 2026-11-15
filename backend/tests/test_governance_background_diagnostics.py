"""Actual _spawn completion callback diagnostics; no application imports/actions."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from fastapi import HTTPException

HERE=Path(__file__).resolve().parent
if HERE.name=='tests':
    BASE=HERE.parent.parent
    ROUTER=BASE/'docs/evidence/2026-11-15-qa-preflight-retry-v2/runtime/routers/agentic_governance.py'
else:
    ROUTER=HERE/'agentic_governance.py'

class Log:
    def __init__(self):self.lines=[]
    def error(self,message,*args,**kwargs):
        assert not kwargs.get('exc_info')
        self.lines.append(message%args)

class Diagnostics(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        nodes=[n for n in ast.parse(ROUTER.read_bytes()).body if isinstance(n,ast.FunctionDef) and n.name in {'_background_failure','_spawn'}]
        self.assertEqual(len(nodes),2)
        self.log=Log();ns={'asyncio':asyncio,'HTTPException':HTTPException,'log':self.log}
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(ROUTER),'exec'),ns)
        self.spawn=ns['_spawn'];self.app=SimpleNamespace(state=SimpleNamespace())

    async def emit(self,error):
        async def operation():raise error
        task=self.spawn(self.app,operation());await asyncio.gather(task,return_exceptions=True);await asyncio.sleep(0)
        self.assertFalse(self.app.state.agentic_execution_tasks)
        return self.log.lines[-1]

    async def test_actual_http_callback_preserves_status_and_server_diagnostic(self):
        text=await self.emit(HTTPException(403,'ERP Project AI Project Manager Task Automation checkbox is disabled'))
        self.assertIn('HTTPException status=403',text);self.assertIn('Task Automation checkbox is disabled',text)
        text=await self.emit(HTTPException(503,{'server_messages':['Native scope unavailable','Retry retained'],'untrusted_payload':{'secret':'DO_NOT_LOG'}}))
        self.assertIn('status=503',text);self.assertIn('Native scope unavailable; Retry retained',text);self.assertNotIn('DO_NOT_LOG',text)

    async def test_secret_assignments_bearer_url_and_length_are_bounded(self):
        values=['SECRET_A','SECRET_B','SECRET_C','SECRET_D','SECRET_E','SECRET_F','SECRET_G']
        detail='password="SECRET_A" token=SECRET_B api_key:\'SECRET_C\' client_secret=SECRET_D capability_mac=SECRET_E Bearer SECRET_F https://user:SECRET_G@example.invalid/path\n'+'x'*2000
        text=await self.emit(HTTPException(409,detail))
        for value in values:self.assertNotIn(value,text)
        self.assertIn('[REDACTED]',text);self.assertNotIn('\n',text);self.assertLess(len(text),850)

    async def test_headers_cookies_traceback_and_unknown_structures_are_omitted(self):
        for detail in ['Traceback (most recent call last): SECRET', '{"headers":{"Cookie":"SECRET"}}','Authorization Bearer SECRET','prompt="PRIVATE_PROMPT"']:
            text=await self.emit(HTTPException(424,detail));self.assertIn('status=424',text);self.assertIn('omitted',text);self.assertNotIn('SECRET',text);self.assertNotIn('PRIVATE_PROMPT',text)
        text=await self.emit(HTTPException(409,{'raw_response':'DO_NOT_LOG'}));self.assertIn('structured detail omitted',text);self.assertNotIn('DO_NOT_LOG',text)

    async def test_non_http_type_only_without_stringifying_exception(self):
        class PrivateError(Exception):
            def __str__(self):raise AssertionError('Arbitrary exception text must not be read')
        text=await self.emit(PrivateError());self.assertTrue(text.endswith('PrivateError'));self.assertNotIn('detail=',text)

    async def test_cancelled_and_successful_tasks_cleanup_without_error_log(self):
        async def hold():await asyncio.Future()
        task=self.spawn(self.app,hold());task.cancel();await asyncio.gather(task,return_exceptions=True);await asyncio.sleep(0)
        async def succeed():return 'safe'
        task=self.spawn(self.app,succeed());await task;await asyncio.sleep(0)
        self.assertFalse(self.app.state.agentic_execution_tasks);self.assertEqual(self.log.lines,[])

if __name__=='__main__':unittest.main(verbosity=2)
