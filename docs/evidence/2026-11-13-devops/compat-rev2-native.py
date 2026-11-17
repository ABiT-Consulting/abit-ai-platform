"""PROPOSAL ONLY: fixed-site metadata with guarded imports and read-only SQL.

No invocation is authorized by this file's existence. Run only after Root has
reviewed its exact bytes and the enclosing fixed-container inventory request.
"""
import ast,contextlib,hashlib,io,json,logging,os,re,socket,sys
from pathlib import Path
BENCH=Path('/home/frappe/frappe-bench');SITE='erp.abit.ai';APPS=BENCH/'apps';SITES=BENCH/'sites'
ROLE_KEYS=('solution_architect','project_manager','developer','qa','operations','it_manager')
TASKS=('TASK-2026-05811','TASK-2026-06073')
PHASE='startup';COUNTS={'sql_queries':0,'sql_denied':0,'file_writes_denied':0,'network_denied':0}
DB_ENDPOINTS=set();DB_SOCKET=None;CONNECTIONS=[]
TASK_QUERY=None
READ_FILES={str(SITES/'apps.txt'),str(SITES/'common_site_config.json'),str(SITES/SITE/'site_config.json'),'/etc/hosts','/etc/resolv.conf','/etc/nsswitch.conf','/etc/gai.conf','/etc/localtime','/etc/timezone','/dev/null','/dev/urandom','/dev/random'}
READ_ROOTS=(str(APPS)+'/',str(BENCH/'env')+'/','/usr/','/lib/','/lib64/','/etc/ssl/certs/')
METADATA={'DocType','DocField','DocPerm','Custom Field','Custom DocPerm','Property Setter','DocType Link','DocType Action','DocType State','DocType Layout','Module Def','Workflow','Workflow Document State','Workflow Transition'}
TASK_COLUMNS={'name','status','parent_task','custom_ai_relay_execution_status','custom_ai_relay_execution_id','custom_ai_relay_execution_detail','custom_agov_active_run','custom_agov_implementation_run','custom_agov_invocation_id','custom_agov_executor_role','custom_agentic_managed'}
PROJECT_FIELDS=('custom_agentic_governance_enabled','custom_ai_project_manager_task_automation_enabled','custom_ai_project_srs','custom_ai_project_features','custom_ai_developer_instructions')
TASK_FIELDS=('custom_agov_adoption_id','custom_agov_executor_role','custom_agov_tool_action','custom_agov_repair_depth','custom_agov_project_anchor','custom_agov_dispatch_released','custom_agov_plan_digest','custom_agentic_managed','custom_agov_invocation_id','custom_agov_implementation_run','custom_agov_requirements','custom_agov_requirement','custom_agov_acceptance','custom_agov_implementer','custom_agov_source_run','custom_agov_active_run','custom_agov_srs_revision','custom_agov_approval_required','custom_ai_relay_execution_status','custom_ai_relay_execution_id','custom_ai_relay_execution_detail','status')
SAFE_OPTIONS={'','JSON','User','AI Governance Record','AI Project SRS Revision','AI Project SRS Feature'}
class GuardedReadError(RuntimeError):pass
def blocked(code):raise GuardedReadError(code)
def audit(event,args):
    if event=='open':
        mode=args[1] or '';flags=args[2] or 0
        if isinstance(mode,str) and any(c in mode for c in 'wax+') or flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND):
            COUNTS['file_writes_denied']+=1;blocked('FILE_WRITE_BLOCKED')
        if isinstance(args[0],(str,bytes,os.PathLike)):
            path=os.path.realpath(os.fsdecode(args[0]));base=os.path.basename(path)
            if path not in READ_FILES and (not path.startswith(READ_ROOTS) or base=='.env' or base.endswith('.env') or path.endswith('/.git/config') or '/logs/' in path):blocked('NON_SOURCE_OR_CONFIG_READ_BLOCKED')
    if event in {'os.remove','os.rename','os.rmdir','os.mkdir','os.unlink','os.truncate','os.symlink','os.link','os.chmod','os.chown','os.utime','os.system','subprocess.Popen','os.posix_spawn','os.fork','os.exec','os.putenv','os.unsetenv'}:
        blocked('MUTATING_OR_PROCESS_IMPORT_SIDE_EFFECT')
    if event=='socket.connect':
        address=args[1]
        allowed=isinstance(address,tuple) and tuple(address[:2]) in DB_ENDPOINTS or isinstance(address,str) and address==DB_SOCKET
        if not allowed:COUNTS['network_denied']+=1;blocked('NON_DATABASE_NETWORK_BLOCKED')
sys.addaudithook(audit)
class Discard(io.TextIOBase):
    def write(self,value):return len(value)
    def flush(self):pass
    def fileno(self):return sys.__stdout__.fileno()
class LocalCache:
    """Fresh-process cache only; no Redis connection, expiry or invalidation."""
    def __init__(self):self.values={}
    def __call__(self):return self
    def get_value(self,key,generator=None,*args,**kwargs):
        if key not in self.values and callable(generator):self.values[key]=generator()
        return self.values.get(key)
    def set_value(self,key,value,*args,**kwargs):self.values[key]=value;return value
    def get(self,key,*args,**kwargs):return self.values.get(key)
    def set(self,key,value,*args,**kwargs):self.values[key]=value
    def make_key(self,key,*args,**kwargs):return key
    def hget(self,name,key,generator=None,*args,**kwargs):return self.get_value((name,key),generator)
    def hset(self,name,key,value,*args,**kwargs):self.values[(name,key)]=value
    def hgetall(self,name,*args,**kwargs):return {}
    def delete_value(self,key,*args,**kwargs):self.values.pop(key,None)
    def delete_keys(self,*args,**kwargs):blocked('CACHE_INVALIDATION_NOT_ALLOWED')
    def __getattr__(self,name):blocked('UNSUPPORTED_CACHE_OPERATION')
def word(value,maxlen=180):
    value=str(value or '')
    if len(value)>maxlen or not re.fullmatch(r'[A-Za-z0-9_ .:/@+-]*',value):return '<unsupported-metadata>'
    return value
def module_name(value):
    value=str(value or '')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.]{0,250}',value):blocked('INVALID_HOOK_IDENTIFIER')
    return value
def bounded(value,limit=100):
    if len(value)>limit:blocked('METADATA_ROW_BOUND_EXCEEDED')
    return value
def sql_guard(raw):
    sql=raw.decode('utf-8') if isinstance(raw,bytes) else str(raw)
    sql=sql.strip();upper=re.sub(r'\s+',' ',sql).upper();COUNTS['sql_queries']+=1
    allowed_session=bool(re.fullmatch(r"SET (?:SESSION )?(?:TRANSACTION READ ONLY|AUTOCOMMIT\s*=\s*0|NAMES [A-Z0-9_]+(?: COLLATE [A-Z0-9_]+)?|SQL_MODE\s*=\s*'[A-Z0-9_, ]*'|TIME_ZONE\s*=\s*'[+0-9:-]+')",upper))
    if upper in {'ROLLBACK','START TRANSACTION READ ONLY','START TRANSACTION','BEGIN'} or allowed_session:return
    if upper in {'SELECT @@SESSION.TX_READ_ONLY','SELECT @@TX_READ_ONLY','SELECT VERSION()','SELECT DATABASE()','SELECT 1'}:return
    if len(sql)>65536 or any(v in upper for v in (';','/*','--',' INTO ','OUTFILE','DUMPFILE','LOAD_FILE','SLEEP(','BENCHMARK(','GET_LOCK(','RELEASE_LOCK(',' UNION ')):
        COUNTS['sql_denied']+=1;blocked('SQL_NOT_ALLOWLISTED')
    schema=re.fullmatch(r'(?:SHOW (?:FULL )?COLUMNS FROM|DESC|DESCRIBE) `?tab([^`]+)`?',sql,re.I)
    if schema and schema.group(1) in METADATA|{'Task','Project','File'}:return
    if not upper.startswith('SELECT '):COUNTS['sql_denied']+=1;blocked('NON_READ_SQL_BLOCKED')
    schema_columns=re.fullmatch(r"SELECT COLUMN_NAME FROM INFORMATION_SCHEMA\.COLUMNS WHERE TABLE_SCHEMA=DATABASE\(\) AND TABLE_NAME='TAB([^']+)'",upper)
    if schema_columns and schema_columns.group(1) in {name.upper() for name in METADATA|{'File','Task','Project','ToDo'}}:return
    schema_exists=re.fullmatch(r"SELECT 1 FROM INFORMATION_SCHEMA\.TABLES WHERE TABLE_SCHEMA=DATABASE\(\) AND TABLE_NAME='TAB([^']+)' LIMIT 1",upper)
    if schema_exists and schema_exists.group(1) in {name.upper() for name in METADATA|{'File','Task','Project','ToDo'}}:return
    tables=re.findall(r'(?:FROM|JOIN)\s+`tab([^`]+)`',sql,re.I)
    references=len(re.findall(r'\b(?:FROM|JOIN)\s+',sql,re.I))
    if not tables or references!=len(tables):COUNTS['sql_denied']+=1;blocked('UNMATCHED_TABLE_REFERENCE')
    if all(table in METADATA for table in tables):return
    if tables==['DefaultValue'] and re.fullmatch(r"SELECT `defvalue` FROM `tabDefaultValue` WHERE `defkey`='installed_apps' AND `parent`='__global' LIMIT 1",sql):return
    if tables==['User'] and re.fullmatch(r"SELECT `enabled` FROM `tabUser` WHERE `name`='[A-Za-z0-9_.@+%-]+' LIMIT 1",sql):return
    if tables==['Task'] and TASK_QUERY is not None and sql==TASK_QUERY:return
    COUNTS['sql_denied']+=1;blocked('TABLE_OR_SCOPE_NOT_ALLOWLISTED')
class CursorGuard:
    def __init__(self,cursor,connection):self.raw=cursor;self.connection=connection
    def execute(self,query,args=None):
        self.connection.verify()
        material=self.raw.mogrify(query,args) if args is not None else query
        sql_guard(material)
        return self.raw.execute(query,args)
    def executemany(self,*args,**kwargs):blocked('EXECUTEMANY_NOT_ALLOWLISTED')
    def callproc(self,*args,**kwargs):blocked('STORED_PROCEDURE_NOT_ALLOWLISTED')
    def __getattr__(self,name):
        if name in {'fetchone','fetchmany','fetchall','description','rowcount','close','arraysize','_executed','_result','_rows'}:return getattr(self.raw,name)
        blocked('UNSUPPORTED_CURSOR_OPERATION')
class ConnectionGuard:
    def __init__(self,connection):self.raw=connection
    def verify(self):
        cursor=self.raw.cursor()
        try:
            cursor.execute('SELECT @@session.tx_read_only');value=cursor.fetchone()
            if not value or int(value[0])!=1:blocked('READONLY_SESSION_LOST')
        finally:cursor.close()
    def cursor(self,*args,**kwargs):return CursorGuard(self.raw.cursor(*args,**kwargs),self)
    def commit(self,*args,**kwargs):blocked('COMMIT_NOT_ALLOWED')
    def rollback(self,*args,**kwargs):return self.raw.rollback()
    def close(self):return self.raw.close()
    def query(self,query):self.verify();sql_guard(query);return self.raw.query(query)
    def ping(self,*args,**kwargs):self.raw.ping(False);self.verify()
    def __getattr__(self,name):
        if name in {'escape','escape_string','literal','character_set_name','get_server_info','get_host_info','get_proto_info','thread_id','get_autocommit','server_version','encoding','encoders','decoders','_result','affected_rows','insert_id','warning_count','open'}:return getattr(self.raw,name)
        blocked('UNSUPPORTED_CONNECTION_OPERATION')
def install_database_guard():
    # Frappe 16 may use mysqlclient or PyMySQL. Guard the common Database
    # connection boundary, not just one driver's Python query method.
    from frappe.database.database import Database
    def connect(database):
        raw=database.get_connection()
        cursor=raw.cursor()
        try:
            cursor.execute('SET SESSION TRANSACTION READ ONLY')
            cursor.execute('START TRANSACTION READ ONLY')
            cursor.execute('SELECT @@session.tx_read_only');value=cursor.fetchone()
            if not value or int(value[0])!=1:blocked('READONLY_SESSION_UNVERIFIED')
        finally:cursor.close()
        connection=ConnectionGuard(raw);CONNECTIONS.append(connection)
        database._conn=connection;database._cursor=connection.cursor()
    Database.connect=connect
    def get_columns(database,table):
        if table not in {'tab'+name for name in METADATA|{'File','Task','Project','ToDo'}}:blocked('SCHEMA_TABLE_NOT_ALLOWLISTED')
        return database.sql('SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s',(table,),pluck=True)
    Database.get_db_table_columns=get_columns
    def table_exists(database,doctype):
        if doctype not in METADATA|{'File','Task','Project','ToDo'}:blocked('SCHEMA_TABLE_NOT_ALLOWLISTED')
        return bool(database.sql('SELECT 1 FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s LIMIT 1',('tab'+doctype,)))
    Database.table_exists=table_exists
    # Meta's large-table UI heuristic may otherwise read latest business-row
    # creation timestamps. It has no effect on signatures or controller MRO.
    from frappe.model.meta import Meta
    def skip_ui_row_heuristic(meta):meta.is_large_table=False
    Meta.check_if_large_table=skip_ui_row_heuristic
def rows(db,sql,args=()):
    result=db.sql(sql,args,as_dict=True)
    return bounded(result,200)
def signature(field):
    if field is None:return {'present':False}
    typ=word(field.get('fieldtype'),40);options=str(field.get('options') or '')
    result={'present':True,'fieldtype':typ,'read_only':bool(field.get('read_only')),'no_copy':bool(field.get('no_copy')),
        'hidden':bool(field.get('hidden')),'reqd':bool(field.get('reqd')),'permlevel':int(field.get('permlevel') or 0),
        'default_present':field.get('default') not in (None,''),'options_present':bool(options)}
    if typ=='Check':result['default_boolean']=str(field.get('default') or '0') if str(field.get('default') or '0') in {'0','1'} else '<non-boolean>'
    if options in SAFE_OPTIONS:result['options']=options
    else:result['options_expected_identifier']=False
    return result
RESULT={'schema_version':1,'ok':False,'site':SITE,'purpose':'erp-compatibility-metadata-only','actual_model_requests':0,'fresh_process_only':True,
    'persistent_cache_used':False,'db_writes_permitted':False,'business_task_allowlist':list(TASKS),'large_table_UI_heuristic_disabled':True}
try:
    with contextlib.redirect_stdout(Discard()),contextlib.redirect_stderr(Discard()):
        PHASE='source_paths'
        if not (SITES/SITE).is_dir() or (SITES/SITE).resolve()!=SITES/SITE:blocked('EXACT_SITE_PATH_UNVERIFIED')
        packages=BENCH/'env'/'lib'/f'python{sys.version_info.major}.{sys.version_info.minor}'/'site-packages'
        if not packages.is_dir():blocked('VENV_PACKAGE_PATH_UNVERIFIED')
        sys.path.append(str(packages))
        app_names=bounded([x.strip() for x in (SITES/'apps.txt').read_text().splitlines() if x.strip()],40)
        if any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,79}',x) for x in app_names):blocked('BENCH_APP_NAMES_INVALID')
        for app in app_names:sys.path.append(str(APPS/app))
        RESULT['source_files']={}
        for app,files in {'frappe':['frappe/__init__.py','frappe/model/base_document.py'],
                          'erpnext':['erpnext/__init__.py'],
                          'abit_ai_assistant':['abit_ai_assistant/__init__.py','abit_ai_assistant/hooks.py','abit_ai_assistant/agentic_control.py','abit_ai_assistant/agentic_permissions.py','abit_ai_assistant/agentic_file.py','abit_ai_assistant/project_srs.py','abit_ai_assistant/project_agent_bindings.py','abit_ai_assistant/scheduler.py','abit_ai_assistant/project_task_scheduler.py']}.items():
            for relative in files:
                file=APPS/app/relative;key=app+'/'+relative
                if not file.is_file():RESULT['source_files'][key]={'present':False};continue
                data=file.read_bytes()
                if len(data)>1000000:blocked('SOURCE_FILE_BOUND_EXCEEDED')
                RESULT['source_files'][key]={'present':True,'sha256':hashlib.sha256(data).hexdigest()}
        PHASE='import_frappe'
        import frappe
        PHASE='install_database_guard'
        install_database_guard()
        PHASE='install_memory_caches'
        frappe.cache=LocalCache();frappe.client_cache=LocalCache()
        quiet=logging.getLogger('agov_readonly_inventory');quiet.addHandler(logging.NullHandler());quiet.propagate=False
        frappe.logger=lambda *a,**k:quiet
        PHASE='initialize_fixed_site'
        frappe.init(SITE,sites_path=str(SITES))
        if str(frappe.conf.get('db_type') or 'mariadb')!='mariadb':blocked('ONLY_REVIEWED_MARIADB_SUPPORTED')
        for name in ('db_ssl_ca','db_ssl_cert','db_ssl_key'):
            value=frappe.conf.get(name)
            if isinstance(value,str) and value:READ_FILES.add(os.path.realpath(value))
        DB_SOCKET=frappe.conf.get('db_socket')
        if not DB_SOCKET:
            host=frappe.conf.get('db_host') or 'localhost';port=int(frappe.conf.get('db_port') or 3306)
            DB_ENDPOINTS.update((item[4][0],port) for item in socket.getaddrinfo(host,port,type=socket.SOCK_STREAM))
        PHASE='readonly_connection';frappe.connect(set_admin_as_user=False)
        installed=rows(frappe.db,"SELECT `defvalue` FROM `tabDefaultValue` WHERE `defkey`='installed_apps' AND `parent`='__global' LIMIT 1")
        installed_apps=json.loads(installed[0]['defvalue']) if installed else []
        bounded(installed_apps,40)
        if not installed_apps or any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,79}',x) for x in installed_apps):blocked('INSTALLED_APP_METADATA_INVALID')
        frappe.get_installed_apps=lambda **kwargs:list(installed_apps)
        PHASE='fresh_controller_mro'
        from frappe.model.base_document import get_controller
        controller=get_controller('File')
        RESULT['file_controller_mro']=[module_name(x.__module__+'.'+x.__name__) for x in controller.__mro__]
        RESULT['file_hooks']={}
        for key in ('override_doctype_class','extend_doctype_class'):
            RESULT['file_hooks'][key]=[module_name(x) for x in (frappe.get_hooks(key,{}) or {}).get('File',[])]
        events=frappe.get_hooks('doc_events',{}) or {}
        RESULT['guard_hook_paths']={}
        for doctype in ('File','Task','Project','ToDo','*'):
            found={}
            for method in ('before_insert','validate','before_save','on_trash','on_update','after_insert'):
                value=(events.get(doctype) or {}).get(method,[])
                if isinstance(value,str):value=[value]
                if value:found[method]=[module_name(v) for v in bounded(value,30)]
            RESULT['guard_hook_paths'][doctype]=found
        PHASE='field_and_workflow_metadata';RESULT['fields']={}
        metas={doctype:frappe.get_meta(doctype) for doctype in ('Project','Task','ToDo')}
        for doctype,names in [('Project',PROJECT_FIELDS),('Task',TASK_FIELDS)]:
            RESULT['fields'][doctype]={name:signature(metas[doctype].get_field(name)) for name in names}
        task_options=str(metas['Task'].get_field('status').options or '').splitlines()
        RESULT['task_status_options']=[word(x,80) for x in bounded(task_options,60)]
        setters=rows(frappe.db,"SELECT `field_name`,`property`,`property_type`,`value` FROM `tabProperty Setter` WHERE `doc_type`='Task' AND `field_name`='status' LIMIT 61")
        RESULT['task_status_property_setters']=[{'property':word(x['property'],50),'type':word(x['property_type'],50),
            'options':[word(v,80) for v in bounded(str(x['value'] or '').splitlines(),60)] if x['property']=='options' else None} for x in bounded(setters,60)]
        workflows=rows(frappe.db,"SELECT `name`,`document_type`,`workflow_state_field`,`override_status` FROM `tabWorkflow` WHERE `is_active`=1 AND `document_type` IN ('Task','Project','ToDo') LIMIT 21")
        RESULT['active_workflows']=[{k:word(v,120) if k!='override_status' else bool(v) for k,v in x.items()} for x in bounded(workflows,20)]
        RESULT['workflow_states']=[];RESULT['workflow_transitions']=[]
        for flow in bounded(workflows,20):
            states=rows(frappe.db,'SELECT `state`,`doc_status`,`allow_edit`,`update_field` FROM `tabWorkflow Document State` WHERE `parent`=%s ORDER BY `idx` LIMIT 61',(flow['name'],))
            transitions=rows(frappe.db,'SELECT `state`,`action`,`next_state`,`allowed` FROM `tabWorkflow Transition` WHERE `parent`=%s ORDER BY `idx` LIMIT 101',(flow['name'],))
            RESULT['workflow_states'].extend({'workflow':word(flow['name'],120),**{k:word(v,100) for k,v in x.items()}} for x in bounded(states,60))
            RESULT['workflow_transitions'].extend({'workflow':word(flow['name'],120),**{k:word(v,100) for k,v in x.items()}} for x in bounded(transitions,100))
        PHASE='identity_config_booleans'
        mapping=frappe.conf.get('abit_agentic_role_users') or {}
        if isinstance(mapping,str):
            try:mapping=json.loads(mapping)
            except (ValueError,TypeError):mapping={}
        if not isinstance(mapping,dict):mapping={}
        RESULT['role_config_validity']={}
        def enabled_user(value):
            if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_.+%-]+@[A-Za-z0-9.-]+',value):return False
            found=rows(frappe.db,'SELECT `enabled` FROM `tabUser` WHERE `name`=%s LIMIT 1',(value,))
            return bool(found and found[0]['enabled'])
        for role in ROLE_KEYS:RESULT['role_config_validity'][role]={'configured':bool(mapping.get(role)),'enabled_user':enabled_user(mapping.get(role))}
        RESULT['service_config_validity']={'configured':bool(frappe.conf.get('abit_agentic_service_user')),'enabled_user':enabled_user(frappe.conf.get('abit_agentic_service_user'))}
        RESULT['bridge_configured']=isinstance(frappe.conf.get('abit_agentic_bridge_token'),str) and len(frappe.conf.get('abit_agentic_bridge_token'))>=32
        RESULT['control_configured']=isinstance(frappe.conf.get('abit_agentic_control_token'),str) and len(frappe.conf.get('abit_agentic_control_token'))>=32
        PHASE='authorized_task_status_only'
        columns=['name','status','parent_task']+[name for name in sorted(TASK_COLUMNS-{'name','status','parent_task','custom_ai_relay_execution_detail'}) if metas['Task'].get_field(name)]
        # Do not SELECT the detail blob: only fixed scalar JSON paths, evaluated
        # in SQL, may leave the DB. No summaries/comments/replies are selected.
        detail=metas['Task'].get_field('custom_ai_relay_execution_detail')
        paths={'audit_run_id':'$.run_id','audit_status':'$.status','qa_status':'$.qa.status','qa_run_id':'$.qa.run_id','review_status':'$.review.status','review_run_id':'$.review.run_id'}
        select=[f'`{name}`' for name in columns]
        if detail:
            for alias,pointer in paths.items():select.append("CASE WHEN JSON_VALID(`custom_ai_relay_execution_detail`) THEN JSON_UNQUOTE(JSON_EXTRACT(`custom_ai_relay_execution_detail`,'"+pointer+"')) ELSE NULL END AS "+alias)
        query='SELECT '+','.join(select)+" FROM `tabTask` WHERE `name` IN ('TASK-2026-05811','TASK-2026-06073') LIMIT 2"
        TASK_QUERY=query
        found=rows(frappe.db,query)
        known_status={'Open','Working','Overdue','Pending Review','Completed','Cancelled','Failed','Blocked','QA Reviewing','QA APPROVED','QA REJECTED','Running','Queued','Preparing','pass','fail','blocked','pending','success','error','passed','failed','approved','rejected'}
        def task_scalar(key,value):
            if value is None:return None
            if key=='custom_agentic_managed':return bool(value)
            value=str(value)
            if key=='name' or key=='parent_task':return value if not value or re.fullmatch(r'TASK-[A-Za-z0-9-]{1,60}',value) else '<non-task-identifier>'
            if 'status' in key:return value if value in known_status else '<unrecognized-status>'
            return value if not value or re.fullmatch(r'[A-Za-z0-9_.:@+-]{1,160}',value) else '<non-identifier>'
        RESULT['task_statuses']=[{key:task_scalar(key,value) for key,value in row.items()} for row in found]
        RESULT['task_metadata_scope']='Only two named tasks; fixed status/run scalar JSON paths, never the detail blob. Missing QA metadata is not a verified QA failure or pass.'
        RESULT['installed_apps']=[module_name(x) for x in installed_apps]
        RESULT['installed_app_packages']={}
        for app in installed_apps:
            module=sys.modules.get(app);file=getattr(module,'__file__',None) if module else None
            item={'loaded':module is not None,'module_file':word(os.path.realpath(file),500) if isinstance(file,str) else None}
            if isinstance(file,str):
                location=Path(file)
                if location.is_file() and location.suffix in {'.py','.pyc','.so'}:
                    size=location.stat().st_size;item['source_bytes']=size
                    if size<=1000000:item['source_sha256']=hashlib.sha256(location.read_bytes()).hexdigest()
                    else:item['source_hash_unavailable']='SIZE_BOUND'
            RESULT['installed_app_packages'][module_name(app)]=item
        RESULT['framework_versions']={name:word(getattr(sys.modules.get(name),'__version__','not_loaded'),80) for name in ('frappe','erpnext','abit_ai_assistant')}
        RESULT['readonly_session_verified']=bool(CONNECTIONS);RESULT['ok']=True
except BaseException as error:
    RESULT['error_code']=str(error) if isinstance(error,GuardedReadError) else 'NATIVE_METADATA_UNAVAILABLE'
    RESULT['error_type']=word(type(error).__name__,80);RESULT['failed_phase']=PHASE
    if isinstance(error,AttributeError):
        missing=getattr(error,'name',None)
        RESULT['missing_attribute']=missing if isinstance(missing,str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,79}',missing) else '<unavailable>'
finally:
    for connection in CONNECTIONS:
        try:connection.rollback()
        except BaseException:pass
        try:connection.close()
        except BaseException:pass
    RESULT['safety_counters']=COUNTS
encoded=json.dumps(RESULT,sort_keys=True,separators=(',',':'),allow_nan=False)
if len(encoded.encode())>49152:encoded=json.dumps({'schema_version':1,'ok':False,'error_code':'METADATA_OUTPUT_LIMIT'})
sys.__stdout__.write(encoded+'\n');sys.__stdout__.flush()
raise SystemExit(0 if RESULT['ok'] else 65)
