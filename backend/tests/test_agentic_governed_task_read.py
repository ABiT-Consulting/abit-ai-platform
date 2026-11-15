"""Actual ERPClient and reverse-channel contract, with a controlled native peer."""
import ast,asyncio,base64,hashlib,importlib.util,json,sys,types,unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote
import httpx
HERE=Path(__file__).resolve().parent
LIVE=Path(__file__).resolve().parents[1]/'open_webui/utils'
def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m
channel=load('actual_task_read_erp_channel',LIVE/'agentic_erp_channel.py')
source=(LIVE/'erp_task_resolution.py').read_bytes();tree=ast.parse(source);node=next(n for n in tree.body if isinstance(n,ast.ClassDef)and n.name=='ERPClient')
module=types.ModuleType('open_webui.utils.erp_task_resolution');module.ERP='https://erp.abit.ai';module.AGENT_URL='custom_abit_ai_agent_url';module.__dict__.update(httpx=httpx,Path=Path,asyncio=asyncio,json=json,quote=quote)
unit=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),node],type_ignores=[]);exec(compile(ast.fix_missing_locations(unit),str(LIVE/'erp_task_resolution.py'),'exec'),module.__dict__)
runtime=load('proposed_governed_task_read_runtime',LIVE/'agentic_erp_runtime.py')
class Peer:
 def __init__(self):self.sent=[];self.connection=None
 async def send_text(self,raw):
  frame=json.loads(raw);self.sent.append(frame)
  if frame['path'].startswith('/api/resource/Task/'):
   data={'name':'TASK-CONTROLLED','status':'Working','custom_agentic_managed':1,'project':'PROJ-CONTROLLED'}
  elif frame['path'].startswith('/api/resource/Project/'):data={'name':'PROJ-CONTROLLED'}
  else:raise AssertionError('Unexpected native operation')
  self.connection.pending[frame['id']].set_result({'type':'response','id':frame['id'],'status':200,'headers':{'content-type':'application/json'},'body_base64':base64.b64encode(json.dumps({'data':data}).encode()).decode()})
def make_channel():
 ch=channel.ReverseERPChannel(channel.ChannelProfile(enabled=True,bridge_id='controlled-contract-bridge'), 'controlled-test-only-no-live-key-00000000')
 peer=Peer();connection=channel._Connection(peer);peer.connection=connection;ch._connection=connection;return ch,peer
class GovernedRead(unittest.IsolatedAsyncioTestCase):
 async def test_actual_legacy_get_reproduces_pre_send_denial_without_model_dispatch(self):
  ch,peer=make_channel();client=module.ERPClient.__new__(module.ERPClient);client.client=httpx.AsyncClient(base_url=module.ERP,transport=ch.transport(),trust_env=False);dispatch=[]
  try:
   with self.assertRaises(channel.ERPChannelError)as got:
    await client.get('Task','TASK-CONTROLLED');dispatch.append('must-not-run')
   self.assertEqual(str(got.exception),'ERP channel native operation is not allowed');self.assertFalse(got.exception.outcome_uncertain)
   self.assertEqual([v['path']for v in peer.sent],['/api/resource/Task/TASK-CONTROLLED']);self.assertEqual(dispatch,[])
  finally:await client.client.aclose()
 async def test_governed_actual_task_read_uses_one_real_channel_GET_and_no_metadata(self):
  ch,peer=make_channel()
  with patch.dict(sys.modules,{'open_webui.utils.erp_task_resolution':module}),patch.object(runtime,'configured_transport',return_value=ch.transport()):client=runtime.governed_client('/not-read-by-private-channel')
  try:
   value=await client.get('Task','TASK-CONTROLLED');self.assertEqual(value['name'],'TASK-CONTROLLED');self.assertNotIn(module.AGENT_URL,value)
   self.assertEqual([v['path']for v in peer.sent],['/api/resource/Task/TASK-CONTROLLED']);self.assertEqual(ch.status()['inflight'],0)
   with self.assertRaises(channel.ERPChannelError):await client.get('Custom Field','Task-'+module.AGENT_URL)
   self.assertEqual(len(peer.sent),1)
  finally:await client.client.aclose()
 async def test_nonTask_channel_behavior_and_generic_metadata_write_denial_unchanged(self):
  ch,peer=make_channel()
  with patch.dict(sys.modules,{'open_webui.utils.erp_task_resolution':module}),patch.object(runtime,'configured_transport',return_value=ch.transport()):client=runtime.governed_client('/not-read-by-private-channel')
  try:
   self.assertEqual((await client.get('Project','PROJ-CONTROLLED'))['name'],'PROJ-CONTROLLED')
   with self.assertRaises(channel.ERPChannelError):await client.request('POST','/api/method/frappe.client.save',json={'doc':{}})
   self.assertEqual([v['path']for v in peer.sent],['/api/resource/Project/PROJ-CONTROLLED'])
  finally:await client.client.aclose()
 async def test_default_legacy_ERPClient_still_performs_optional_enrichment(self):
  sent=[]
  async def respond(request):
   sent.append(request.url.path)
   data={'name':'TASK-CONTROLLED'}if len(sent)==1 else {'dt':'Task','fieldname':module.AGENT_URL,'fieldtype':'Data','options':'URL','hidden':0}
   return httpx.Response(200,json={'data':data})
  client=module.ERPClient.__new__(module.ERPClient);client.client=httpx.AsyncClient(base_url=module.ERP,transport=httpx.MockTransport(respond),trust_env=False)
  try:
   doc=await client.get('Task','TASK-CONTROLLED');self.assertIn(module.AGENT_URL,doc);self.assertIsNone(doc[module.AGENT_URL]);self.assertEqual(sent,['/api/resource/Task/TASK-CONTROLLED','/api/resource/Custom Field/Task-'+module.AGENT_URL])
  finally:await client.client.aclose()
if __name__=='__main__':unittest.main()
