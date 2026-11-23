"""Real execution-policy route functions, isolated FastAPI app and private ledger."""
import ast
import hmac
import os
from pathlib import Path
import tempfile
from typing import Literal
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import APIRouter, FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field

from test_agentic_controls_events import Fixture, P, OTHER


class API(Fixture):
    def setUp(self):
        super().setUp()
        self.engine = self.load('agentic_execution_engine')
        root = Path(__file__).resolve().parents[1]
        source = root/'open_webui/routers/agentic_governance.py'
        tree = ast.parse(source.read_text())
        names = {'ExecutionPolicyInput','ExecutionPolicyResponse','ExecutorStatusResponse',
                 'get_execution_policy','put_execution_policy','executor_status','_auth'}
        nodes = [n for n in tree.body if isinstance(n,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef))
                 and n.name in names]
        self.secret = 'synthetic-control-token-not-a-real-credential-0000'
        secret_file = Path(self.temp.name)/'control-token'
        secret_file.write_text(self.secret)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, ABIT_ERP_CONTROL_TOKEN_FILE=str(secret_file)).start()
        self.project = AsyncMock(return_value={})
        scope = dict(BaseModel=BaseModel,ConfigDict=ConfigDict,Field=Field,Literal=Literal,
                     Header=Header,HTTPException=HTTPException,Path=Path,os=os,hmac=hmac,
                     DATA_DIR=Path(self.temp.name),_config=lambda:{},_erp_project=self.project,
                     find=lambda ident:self.g.find(ident,self.path),router=APIRouter())
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(source),'exec'),scope)
        app=FastAPI()
        app.include_router(scope['router'])
        self.client=TestClient(app)
        # The real route helpers use their default ledger path.
        patch.object(self.g,'path',return_value=self.path).start()

    async def test_authentication_required_for_every_new_operation(self):
        for verb,path,body in (
            ('GET',f'/projects/{P}/execution-policy',None),
            ('PUT',f'/projects/{P}/execution-policy',{'strategy':'codex_first','fallback_on':['quota']}),
            ('GET',f'/projects/{P}/invocations/not-found/execution',None)):
            response=self.client.request(verb,path,json=body)
            self.assertEqual(response.status_code,401)
        self.project.assert_not_awaited()

    async def test_policy_schema_permissions_and_future_runs_only(self):
        headers={'X-ABIT-ERP-Control':self.secret}
        url=f'/projects/{P}/execution-policy'
        self.assertEqual(self.client.get(url,headers=headers).json(),
                         {'strategy':'codex_first','fallback_on':['quota']})
        for body in (
            {'strategy':'unknown','fallback_on':['quota']},
            {'strategy':'codex_first','fallback_on':['authentication_error']},
            {'strategy':'codex_first','fallback_on':['quota','quota']},
            {'strategy':'codex_first','fallback_on':['quota'],'run_id':'forged'}):
            self.assertEqual(self.client.put(url,headers=headers,json=body).status_code,422)
        response=self.client.put(url,headers=headers,json={'strategy':'chatgpt_dot_first','fallback_on':[]})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json(),{'strategy':'chatgpt_dot_first','fallback_on':[]})
        self.project.side_effect=HTTPException(403,'Project not authorized')
        self.assertEqual(self.client.put(url,headers=headers,json={'strategy':'codex_first'}).status_code,403)
        self.assertEqual(self.engine.policy(P,self.path)['strategy'],'chatgpt_dot_first')

    async def test_status_project_scope_and_openapi_documents_native_auth(self):
        headers={'X-ABIT-ERP-Control':self.secret}
        row=self.enqueue()
        self.assertEqual(self.client.get(
            f"/projects/{OTHER}/invocations/{row['invocation_id']}/execution",headers=headers).status_code,404)
        document=self.client.get('/openapi.json').json()
        operation=document['paths'][f'/projects/{{project_id}}/execution-policy']['put']
        self.assertIn('requestBody',operation)
        self.assertTrue(any(p['name']=='X-ABIT-ERP-Control' for p in operation['parameters']))
        self.assertNotIn(self.secret,str(document))


if __name__=='__main__':
    unittest.main()
