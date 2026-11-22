from pathlib import Path
p=Path(__file__).resolve().parents[3]
f=p/"backend/tests/test_agentic_native_verification.py"
s=f.read_text()
s += '''

    async def test_actual_native_status_route_requires_control_auth_exact_project_and_is_read_only(self):
        import ast, hmac, typing
        import fastapi, pydantic, httpx
        router_file=Path(__file__).resolve().parents[1]/"open_webui/routers/agentic_governance.py"
        tree=ast.parse(router_file.read_text())
        names={"_auth","NativeVerificationReceipt","NativeVerificationStatus","native_verification_status"}
        nodes=[node for node in tree.body if getattr(node,"name",None) in names]
        router=fastapi.APIRouter()
        namespace={"Path":Path,"os":os,"hmac":hmac,"DATA_DIR":Path(self.temp.name),
            "HTTPException":fastapi.HTTPException,"Header":fastapi.Header,
            "BaseModel":pydantic.BaseModel,"Field":pydantic.Field,"ConfigDict":pydantic.ConfigDict,
            "Literal":typing.Literal,"router":router,"PROJECT_ID":self.g.PROJECT_ID,
            "find":lambda invocation:self.g.find(invocation,self.path)}
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(router_file),"exec"),namespace)
        app=fastapi.FastAPI();app.include_router(router,prefix="/api/v1/agentic-governance")
        row,packet,saved=self.bind()
        key=Path(self.temp.name)/"control-token";key.write_text("isolated-native-fixture-"+"x"*40)
        actual_status=self.n.public_status
        with self.g.connect(self.path) as db:
            before=db.execute("SELECT COUNT(*) FROM governance_event").fetchone()[0]
        with patch.dict(os.environ,{"ABIT_ERP_CONTROL_TOKEN_FILE":str(key)}), \
             patch.object(self.n,"public_status",side_effect=lambda invocation:actual_status(invocation,self.path)):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://fixture.invalid") as client:
                url="/api/v1/agentic-governance/projects/"+P+"/native-verification/"+row["invocation_id"]
                self.assertEqual((await client.get(url)).status_code,401)
                self.assertEqual((await client.get(url,headers={"X-ABIT-ERP-Control":"z"*48})).status_code,401)
                response=await client.get(url,headers={"X-ABIT-ERP-Control":key.read_text()})
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.json()["phase"],"bound")
                self.assertNotIn(row["lease_token"],response.text)
                self.assertNotIn(saved["binding"]["lease_token_sha256"],response.text)
                self.assertFalse(response.json()["independent_qa_approved"])
                wrong=url.replace(P,"PROJ-0091")
                self.assertEqual((await client.get(wrong,headers={"X-ABIT-ERP-Control":key.read_text()})).status_code,404)
                self.assertEqual((await client.post(url,headers={"X-ABIT-ERP-Control":key.read_text()})).status_code,405)
        schema=app.openapi()
        operation=schema["paths"][url.replace(P,"{project_id}").replace(row["invocation_id"],"{invocation_id}")]["get"]
        self.assertIn("NativeVerificationStatus",json.dumps(operation))
        self.assertTrue(any(x["name"]=="X-ABIT-ERP-Control" for x in operation["parameters"]))
        with self.g.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM governance_event").fetchone()[0],before)
        self.assertEqual(self.operations,[])

    async def test_actual_delivery_selector_preserves_dot_and_ordinary_it_routes(self):
        import ast,sys
        router_file=Path(__file__).resolve().parents[1]/"open_webui/routers/agentic_governance.py"
        node=next(node for node in ast.parse(router_file.read_text()).body if getattr(node,"name",None)=="_deliver")
        namespace={"json":json,"_config":lambda:{},"_gate":self.gate,"_advance":AsyncMock(),
                   "_reconciliation_wakeup":lambda app:asyncio.Event()}
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(router_file),"exec"),namespace)
        class Dot:
            def __init__(self,app):pass
        executor=types.ModuleType("open_webui.utils.agentic_executor")
        executor.DotAdapter=Dot;executor.execute=AsyncMock(return_value=None)
        cases=[("it_manager","verify_native_source","NativeVerificationAdapter"),
               ("it_manager","restart_service","ITAdapter"),
               ("developer","verify_native_source","Dot"),
               ("project_manager","verify_native_source","Dot")]
        for role,operation,wanted in cases:
            row={"target_role":role,"work_packet":json.dumps({"tool_action":{"operation":operation}})}
            with patch.dict(sys.modules,{"open_webui.utils.agentic_executor":executor}), \
                 patch.object(self.g,"find",return_value=row) as lookup:
                await namespace["_deliver"](None,"original-routing-fixture")
                self.assertEqual(type(executor.execute.await_args.kwargs["adapter"]).__name__,wanted)
                lookup.assert_called_once_with("original-routing-fixture",include_private=True)
        self.assertEqual(self.operations,[])
'''
f.write_text(s)
