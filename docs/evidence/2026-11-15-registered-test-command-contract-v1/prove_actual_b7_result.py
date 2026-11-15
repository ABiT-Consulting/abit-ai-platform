"""Pure actual captured result verification; no application startup or business writes."""
from pathlib import Path
import ast
import hashlib
import importlib.util
import json
import socket
import sqlite3
import sys
import types

HERE=Path(__file__).resolve().parent
LIVE=Path('/srv/abit-dev-linux/abit-ai/releases/20261031-live-0.11.4/runtime/open_webui/utils')
INV='agov-parent-f33a6a0515b70ccd0f31bbf872851b4184ddc15327c42a35'
REPLY=HERE.parent/'governed-returned-reply-format-correction-v1-proposal-20261115/private-actual-correction/abit-f33-corrected-code-reply-20261115.txt'
sha=lambda value:hashlib.sha256(value).hexdigest()
raw=REPLY.read_bytes()
assert sha(raw)=='b7c29aa3e2d7fc11a504b508770b272eea784a0a7a0d46a7dd43b73f24ef38ad'
text=raw.decode('utf-8')
db=sqlite3.connect('file:/srv/abit-dev-linux/abit-ai/data/agentic-governance.sqlite?mode=ro',uri=True)
db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
row=dict(db.execute('SELECT * FROM governance_invocation WHERE invocation_id=?',(INV,)).fetchone())
packet=json.loads(row['work_packet'])
bindings={x['role']:{'agent_id':x['agent_id'],'erp_user':x['erp_user']}for x in db.execute('SELECT * FROM governance_agent WHERE erp_project=?',(row['erp_project'],))}
before=db.total_changes
# No OpenWebUI __init__, env, ORM, routers, default DB, network or subprocess.
def package(name,paths):
 m=types.ModuleType(name);m.__path__=[str(p)for p in paths];sys.modules[name]=m;return m
package('open_webui',[]);package('open_webui.utils',[LIVE])
package('open_webui.utils.agentic_contracts',[HERE/'agentic_contracts',LIVE/'agentic_contracts'])
def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m
def extracted_module(name,path,names):
 raw=path.read_bytes();nodes=[x for x in ast.parse(raw).body if isinstance(x,ast.FunctionDef)and x.name in names]
 assert {x.name for x in nodes}==set(names)
 m=types.ModuleType(name);m.json=json;m.hashlib=hashlib;sys.modules[name]=m
 exec(compile(ast.Module(body=nodes,type_ignores=[]),str(path),'exec'),m.__dict__)
 return m,sha(raw)
socket.socket=lambda *a,**k:(_ for _ in ()).throw(AssertionError('NETWORK_DENIED'))
sqlite3.connect=lambda *a,**k:(_ for _ in ()).throw(AssertionError('NEW_DATABASE_CONNECTION_DENIED'))
helper_path=HERE/'agentic_contracts/developer_delivery.py'
schema_path=HERE/'agentic_contracts/schemas/governance_v1.schema.json'
assert sha(helper_path.read_bytes())=='90d7f6ef6eeac7b44395ddccbd3e0427197c90a19287a4e1cf3f98873b9f7210'
assert sha(schema_path.read_bytes())=='5131fd838234f34173b4a7b30360d3c53d3d18f1fcdbc2afb079ba15db8d897c'
helper=load('open_webui.utils.agentic_contracts.developer_delivery',helper_path)
validator=load('open_webui.utils.agentic_contracts.validator',LIVE/'agentic_contracts/validator.py')
validator.SCHEMA_PATH=schema_path
parser,parser_sha=extracted_module('open_webui.utils.erp_dot_workflow',LIVE/'erp_dot_workflow.py',['parse_contract_json'])
provenance,provenance_sha=extracted_module('open_webui.utils.agentic_result_provenance',LIVE/'agentic_result_provenance.py',['parse_original_reply','reply_sha256'])
provenance.project_provenance=lambda *a,**k:(_ for _ in ()).throw(AssertionError('NO_PROVENANCE_PROJECTION_ALLOWED'))
decoder,executor_sha=extracted_module('actual_unchanged_decoder',LIVE/'agentic_executor.py',['_decode_original_result'])
baseline=json.loads(next(line[len('AGOV RESULT:'):].strip()for line in text.splitlines()if line.startswith('AGOV RESULT:')))
# Actual captured JSON already parses directly: legacy punctuation repair not used.
assert provenance.parse_original_reply(text)==baseline
old_schema=validator.SCHEMA_PATH;validator.SCHEMA_PATH=HERE/'governance_v1.schema.json.before'
old_errors=validator.validate_result(baseline,packet,registered_bindings=bindings)
validator.SCHEMA_PATH=old_schema
work_errors=validator.validate_work_packet(packet,registered_bindings=bindings)
errors=validator.validate_result(baseline,packet,registered_bindings=bindings)
shared_errors=helper.validate_developer_delivery(baseline,packet)
decoded,projection=decoder._decode_original_result(text,packet,INV,registered_bindings=bindings)
assert decoded==baseline and projection is None
assert not work_errors and not errors and not shared_errors
command=decoded['delivery']['test_runs'][0]['command']
assert len(command)==4409 and len(command.encode())==4420
assert sha(command.encode())=='7563887192862206e23b5ad0f2eb08362f1936a7c752c9494ceee9b93708d527'
assert before==db.total_changes==0
out={'schema_version':1,'state':'PASS','actual_reply_sha256':sha(raw),'actual_reply_bytes':len(raw),
     'invocation_id':INV,'run_id':row['automation_run_id'],'packet_sha256':row['packet_hash'],
     'packet_content_sha256':packet['content_sha256'],'actor_exact':decoded['actor']==packet['binding'],
     'task_revision_exact':decoded['task_revision']==packet['task_revision'],'outcome':decoded['outcome'],
     'result_sha256':sha(json.dumps(decoded,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()),
     'work_packet_errors':work_errors,'result_errors':errors,'shared_Core_Native_helper_errors':shared_errors,
     'old_schema_error_paths':[x.split(':',1)[0]for x in old_errors],
     'command_characters':len(command),'command_UTF8_bytes':len(command.encode()),'command_sha256':sha(command.encode()),
     'unchanged_decoder_source_sha256':executor_sha,'unchanged_parser_source_sha256':parser_sha,
     'unchanged_provenance_parser_source_sha256':provenance_sha,
     'validator_source_sha256':sha((LIVE/'agentic_contracts/validator.py').read_bytes()),
     'helper_sha256':sha(helper_path.read_bytes()),'schema_sha256':sha(schema_path.read_bytes()),
     'result_bytes_or_fields_changed':False,'projection':None,'DB_changes':db.total_changes,
     'new_default_DB_connections':0,'network_calls':0,'application_imports':0,
     'acceptance_claim':'Pure full actual schema/semantics only; real artifact/publication/Native/QA gates remain mandatory.'}
p=HERE/'actual-b7-full-result-proof.private.json'
p.write_text(json.dumps(out,sort_keys=True,indent=2)+'\n');p.chmod(0o600)
db.rollback();db.close()
print(json.dumps({'proof_path':str(p),'proof_sha256':sha(p.read_bytes()),'state':out['state'],'old_schema_paths':out['old_schema_error_paths'],'new_errors':errors,'shared_helper_errors':shared_errors,'DB_changes':out['DB_changes']}))
