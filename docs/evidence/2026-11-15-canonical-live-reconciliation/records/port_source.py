from pathlib import Path
import ast, copy, hashlib, json
ROOT=Path(__file__).resolve().parents[2]
E=Path(__file__).resolve().parent
baseline=json.loads((E/'baseline.json').read_text())
for rel,pin in baseline['source'].items():
    assert hashlib.sha256((ROOT/rel).read_bytes()).hexdigest()==pin['sha256'], rel
router_path=ROOT/'backend/open_webui/routers/agentic_governance.py'
recovery_path=ROOT/'backend/open_webui/utils/agentic_recovery.py'
old_router=router_path.read_text();old_recovery=recovery_path.read_text()
incoming_router=(E/'incoming/runtime-agentic_governance.py').read_text()
incoming_recovery=(E/'incoming/runtime-agentic_recovery.py').read_text()
def nodes(src): return {n.name:n for n in ast.parse(src).body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))}
def segment(src,name): return ast.get_source_segment(src,nodes(src)[name])
def replace_once(src,old,new):
    assert src.count(old)==1,old[:100]
    return src.replace(old,new,1)
router=replace_once(old_router,segment(old_router,'_spawn'),
    segment(incoming_router,'_background_failure')+'\n\n\n'+segment(incoming_router,'_spawn'))
old_model=nodes(old_router)['ProjectControlInput'];new_model=nodes(incoming_router)['ProjectControlInput']
minus=copy.deepcopy(new_model)
minus.body=[n for n in minus.body if not(isinstance(n,ast.AnnAssign) and isinstance(n.target,ast.Name) and n.target.id=='control_sequence')]
assert ast.dump(minus,include_attributes=False)==ast.dump(old_model,include_attributes=False)
router=replace_once(router,segment(old_router,'ProjectControlInput'),segment(incoming_router,'ProjectControlInput'))
old_control=segment(old_router,'control_project')
incoming_control=segment(incoming_router,'control_project')
control=replace_once(old_control,"'payload': body.model_dump(),","'payload': {**body.model_dump(), 'task_id': body.task_id or ''},")
start=incoming_control.index('    # Read the server-owned human intent')
stop=incoming_control.index("    try:\n        row, created = receive_event",start)
native=incoming_control[start:stop]
control=replace_once(control,"    try:\n        row, created = receive_event",native+"    try:\n        row, created = receive_event")
doc="""    Mirror an existing human ERP control intent using its exact sequence, command,
    actor and Task scope. Legacy requests without control_sequence fail validation;
    mismatched or superseded native intents fail before durable inbox mutation.
    Create controls in ERP; this route never creates an intent or substitutes actors.

"""
control=replace_once(control,"    Start/Resume require the current opt-in checkbox,",doc+"    Start/Resume require the current opt-in checkbox,")
# This paragraph documents the already-deployed branch without adding a new API.
control=replace_once(control,"    One additional frozen-delivery unit after a consumed generic recovery needs",
"""    An explicit Retry may create one new independent QA unit only after the
    original published an empty preflight Blocked result, performed no workspace
    preparation or tests, and a newer human native Retry is acknowledged. The
    current Pending Review packet must preserve the exact implementation; the
    prior result, uncertain read and task history remain unchanged.

    One additional frozen-delivery unit after a consumed generic recovery needs""")
canon_control=nodes(control)['control_project'];expected_control=nodes(incoming_control)['control_project']
canon_control.body=canon_control.body[1:];expected_control.body=expected_control.body[1:]
assert ast.dump(canon_control,include_attributes=False)==ast.dump(expected_control,include_attributes=False),'Non-doc control body must match deployed intent checks'
router=replace_once(router,old_control,control)
old_recover=segment(old_router,'_recover_project')
new_recover=replace_once(old_recover,
 'from open_webui.utils.agentic_recovery import recover_original',
 'from open_webui.utils.agentic_recovery import recover_original, resume_qa_preflight')
incoming_recover=segment(incoming_router,'_recover_project')
start=incoming_recover.index("                if saved:\n")+len("                if saved:\n")
stop=incoming_recover.index("                    if saved['phase'] == 'pending':",start)
new_recover=replace_once(new_recover,"                if saved:\n",
                        "                if saved:\n"+incoming_recover[start:stop])
assert ast.dump(nodes(new_recover)['_recover_project'],include_attributes=False)==ast.dump(nodes(incoming_recover)['_recover_project'],include_attributes=False)
router=replace_once(router,old_recover,new_recover)
addition=incoming_recovery[incoming_recovery.index("QA_PREFLIGHT_POLICY = 'qa-preflight-retry.v1'"):]
recovery=old_recovery.rstrip()+'\n\n\n'+addition
compile(router,str(router_path),'exec');compile(recovery,str(recovery_path),'exec')
allowed={'_background_failure','_spawn','ProjectControlInput','_recover_project','control_project'}
rn=nodes(router);ro=nodes(old_router)
unchanged_router=[n for n in ro if n not in allowed]
assert all(ast.dump(ro[n],include_attributes=False)==ast.dump(rn[n],include_attributes=False) for n in unchanged_router)
oldtree=ast.parse(old_recovery).body;newtree=ast.parse(recovery).body
assert [ast.dump(n,include_attributes=False)for n in oldtree]==[ast.dump(n,include_attributes=False)for n in newtree[:len(oldtree)]]
for name in ['_qa_preflight_proof','resume_qa_preflight']:
    assert ast.dump(nodes(recovery)[name],include_attributes=False)==ast.dump(nodes(incoming_recovery)[name],include_attributes=False)
router_path.write_text(router);recovery_path.write_text(recovery)
proof={'schema_version':1,'source_commit':'37ae81eafceecd165e33a263d3967255a276968f',
 'router_deployed_functions_exact':['_background_failure','_spawn','ProjectControlInput','_recover_project'],
 'control_project_deployed_body_exact_except_documentation':True,
 'router_unchanged_functions_and_classes':unchanged_router,
 'canonical_delivery_control_event_function_unchanged':'_process_event',
 'all_prior_recovery_top_level_nodes_preserved':len(oldtree),
 'added_deployed_recovery_functions_exact':['_qa_preflight_proof','resume_qa_preflight'],
 'operations_ownership_unchanged':{rel:pin for rel,pin in baseline['source'].items() if rel not in [str(router_path.relative_to(ROOT)),str(recovery_path.relative_to(ROOT))]},
 'updated_pins':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [router_path,recovery_path]}}
(E/'source-preservation.json').write_text(json.dumps(proof,indent=2,sort_keys=True)+'\n')
print(json.dumps(proof,indent=2))
