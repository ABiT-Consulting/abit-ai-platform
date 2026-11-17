"""Create isolated signed source-attributed children and private host API credentials."""
exec(open('/tmp/flow_uat.py').read().split('try:\n pm_packet,pm_run')[0])
from frappe.utils.password import set_encrypted_password
import secrets
try:
    config=ROOT/SITE/'site_config.json';settings=json.loads(config.read_text())
    human='canary-human-approver@example.invalid'
    settings['abit_agentic_approvers']=[human];config.write_text(json.dumps(settings))
    frappe.set_user('Administrator')
    if not frappe.db.exists('User',human):
        person=frappe.get_doc({'doctype':'User','email':human,'first_name':'Synthetic Human Approver','enabled':1,'send_welcome_email':0,'user_type':'System User'})
        person.flags.no_welcome_mail=True;person.insert(ignore_permissions=True);person.add_roles('System Manager','Projects Manager','Projects User')
    frappe.set_user('Administrator')
    source=frappe.get_doc({'doctype':'Task','project':alpha,'subject':'Synthetic authorized PM source '+uuid.uuid4().hex[:8],
        'description':'Human adopted scoped source for protected child fixtures.','status':'Open'}).insert();frappe.db.commit()
    ctrl.configure_existing_task(alpha,source.name,str(source.modified),['REQ-UAT-01'],
        'Protect child creation and independently verify each resulting leaf.',target_role='developer');frappe.db.commit()
    pm_task=source.name;frappe.set_user(SERVICE);request()
    pm_packet,pm_run,pm_receipt=start(alpha,pm_task,'project_manager')
    tasks={name:create_child(pm_run,'Synthetic core HTTP '+name+' '+uuid.uuid4().hex[:8])
           for name in ('pass','rework','gates','it')}
    tokens={}
    for user in (SERVICE,'Administrator',human):
        key=secrets.token_hex(16);secret=secrets.token_hex(24)
        frappe.db.set_value('User',user,'api_key',key,update_modified=False)
        set_encrypted_password('User',user,secret,'api_secret');tokens[user]=key+':'+secret
    frappe.db.commit()
    path=Path('/tmp/agov-core-private.json')
    path.write_text(json.dumps({'base_url':'http://172.21.0.5:8080','project':alpha,'architecture_task':base['architecture_task'],
        'pm_task':pm_task,'pm_run':pm_run,'tasks':tasks,'tokens':tokens,'bridge_secret':SECRET,'role_users':ROLES}))
    os.chmod(path,0o600)
    print('CORE_HTTP_FIXTURE_CREATED',len(tasks),flush=True)
finally:
    frappe.destroy()
