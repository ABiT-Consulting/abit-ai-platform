"""Finite fixture boundaries. No live files, DBs, subprocesses or HTTP are used."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]


def load(name, path):
    value = types.ModuleType(name)
    value.__file__ = str(path)
    sys.modules[name] = value
    exec(compile(path.read_bytes(), str(path), 'exec'), value.__dict__)
    return value


app = load('scope_fixture_controller', HERE / 'apply_scope_repair.py')
preserve = load('scope_fixture_preservation', HERE / 'preserved_scope.py')
core = load('scope_fixture_reviewed_core', REPO / 'deployment/linux/core-hardening-20261115/apply_core_hardening.py')
capacity = load('scope_fixture_reviewed_capacity', REPO / 'deployment/linux/core-command-capacity-20261115/apply_command_capacity.py')
GATE = {'state': 'draining', 'epoch': 31, 'active_admissions': 0}


def clone(value):
    return copy.deepcopy(value)


def facts():
    return {
        'f33': {'invocation_id': preserve.F33, 'run_id': preserve.F33_RUN, 'role': 'developer',
            'state': 'verified', 'dispatch_attempts': 1, 'result_published': True,
            'provider_status': 'unknown', 'provider_response_is_null': True,
            'provider_receipt_is_null': True, 'provider_scope_receipt_is_null': True,
            'provider_envelope_sha256': preserve.F33_ENVELOPE},
        'qa': {'invocation_id': preserve.QA, 'run_id': preserve.QA_RUN, 'role': 'qa',
            'state': 'verified', 'qa_verdict': 'pass', 'result_sha256': preserve.QA_RESULT,
            'native_ack_sha256': preserve.QA_ACK, 'provider_status': 'delegated',
            'reply_sha256': preserve.QA_REPLY, 'envelope_sha256': preserve.QA_ENVELOPE,
            'result_native_identity_valid': True},
        'pm': {'invocation_id': preserve.PM, 'source_invocation_id': preserve.QA,
            'role': 'project_manager', 'state': 'blocked', 'run_id': None,
            'submitted_at': None, 'dispatch_attempts': 0, 'packet_hash': preserve.PM_PACKET,
            'stored_packet_hash_valid': True, 'session_count': 0, 'result_count': 0,
            'local_result_count': 0},
        'selection': {'count': 1, 'event_hash': preserve.SELECTION_HASH, 'event_hash_valid': True,
            'policy': 'returned-reply-format.v1', 'protected_file_sha256': preserve.CAPTURE_SHA,
            'corrected_reply_sha256': preserve.CORRECTED_SHA, 'control_sequence': 14,
            'control_command': preserve.CONTROL},
        'active_governance_count': 0, 'active_automation_count': 0,
        'other_unresolved_provider_count': 0, 'qa_non_done': 0}


def simple_snapshot():
    return {'facts': facts(), 'gate_facts': clone(GATE), 'gate': [clone(GATE)],
            'provider_tables_sha256': '1' * 64, 'leases': [], 'maintenance_audit': []}


def passive_pair():
    lease = {'id': 'fixture-passive', 'kind': 'qa', 'reference': preserve.PASSIVE_QA,
        'state': 'finished', 'epoch': GATE['epoch'], 'parent_id': None,
        'receipt_sha256': preserve.PASSIVE_RECEIPT, 'pid': 200, 'process_start': '300'}
    events = [
        {'kind': 'admitted_original_recovery', 'payload': json.dumps({'lease': lease['id'],
            'kind': 'qa', 'reference': preserve.PASSIVE_QA, 'epoch': GATE['epoch'],
            'parent': None, 'original_receipt': preserve.PASSIVE_RECEIPT})},
        {'kind': 'original_receipt_settled', 'payload': json.dumps({'lease': lease['id'],
            'kind': 'qa', 'reference': preserve.PASSIVE_QA, 'receipt_sha256': preserve.PASSIVE_RECEIPT})}]
    return lease, events


class Preservation(unittest.TestCase):
    def test_exact_pass_and_unsubmitted_pm_accept_without_settling_f33(self):
        value = facts()
        preserve.validate_facts(value)
        self.assertEqual(value['f33']['provider_status'], 'unknown')
        self.assertTrue(value['f33']['provider_receipt_is_null'])

    def test_provider_fabrication_and_selection_upgrade_deny(self):
        for group, field, replacement in [('f33', 'provider_status', 'delegated'),
            ('f33', 'provider_receipt_is_null', False), ('selection', 'policy', 'returned-reply-format.v2')]:
            with self.subTest(field=field):
                value = facts(); value[group][field] = replacement
                with self.assertRaises(RuntimeError): preserve.validate_facts(value)

    def test_qa_or_pm_progress_denies(self):
        for group, field, replacement in [('qa', 'native_ack_sha256', '0'*64),
            ('qa', 'qa_verdict', 'fail'), ('pm', 'dispatch_attempts', 1),
            ('pm', 'run_id', 'new-run'), ('pm', 'session_count', 1)]:
            with self.subTest(field=field):
                value = facts(); value[group][field] = replacement
                with self.assertRaises(RuntimeError): preserve.validate_facts(value)

    def test_open_or_active_or_epoch_drift_denies(self):
        for value in [{'state': 'open', 'epoch': 30, 'active_admissions': 0},
                      {'state': 'draining', 'epoch': 31, 'active_admissions': 1},
                      {'state': 'sealed', 'epoch': True, 'active_admissions': 0}]:
            with self.assertRaises(RuntimeError): app.exact_gate(value)
        with self.assertRaises(RuntimeError):
            preserve.gate_facts([{'state':'draining','epoch':32}], [], GATE)
        with self.assertRaises(RuntimeError):
            preserve.gate_facts([{'state':'draining','epoch':31}], [{'state':'unresolved'}], GATE)

    def test_unknown_and_lease_records_are_immutable(self):
        before = simple_snapshot()
        for field in ('provider_tables_sha256', 'facts', 'gate'):
            after = clone(before); after[field] = 'changed'
            with self.assertRaises(RuntimeError):
                preserve.preserve(before, after, qa_owner=(200,'300'))
        before['leases'] = [{'id':'original','state':'finished'}]
        after = clone(before); after['leases'][0]['state'] = 'settled'
        with self.assertRaises(RuntimeError):
            preserve.preserve(before, after, qa_owner=(200,'300'))

    def test_only_exact_terminal_passive_pair_can_append(self):
        before, after = simple_snapshot(), simple_snapshot()
        lease, events = passive_pair()
        after['leases'] = [lease]; after['maintenance_audit'] = events
        self.assertEqual(preserve.preserve(before, after, qa_owner=(200,'300')), 1)
        self.assertEqual(before['leases'], [])
        for field, replacement in [('pid',201),('state','active'),('reference',preserve.QA),
                                   ('receipt_sha256','0'*64),('epoch',32)]:
            with self.subTest(field=field):
                changed=clone(after);changed['leases'][0][field]=replacement
                with self.assertRaises(RuntimeError):
                    preserve.preserve(before, changed, qa_owner=(200,'300'))
        after['maintenance_audit'].pop()
        with self.assertRaises(RuntimeError):
            preserve.preserve(before, after, qa_owner=(200,'300'))


class SQLiteCapture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='scope-db-fixture-')
        self.root = Path(self.temp.name)
        self.opened = []
        self.patches = []
        self.build()

    def tearDown(self):
        for value in reversed(self.patches): value.stop()
        self.temp.cleanup()

    def pin(self, name, value):
        item = patch.object(preserve, name, value); item.start(); self.patches.append(item)

    def write_db(self, name, tables):
        path = self.root / name;path.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(path) as db:
            for table, rows in tables.items():
                keys = list(rows[0]) if rows else ['invocation_id']
                db.execute('CREATE TABLE "'+table+'" ('+','.join('"'+key+'"' for key in keys)+')')
                for row in rows:
                    db.execute('INSERT INTO "'+table+'" VALUES('+','.join('?'for _ in keys)+')',
                               [row.get(key) for key in keys])

    def build(self):
        qpacket={'binding':{'role':'qa'},'content_sha256':'q'*64}
        ppacket={'qa_review':{'source':preserve.QA}}
        self.pin('PM_PACKET',preserve.digest(ppacket))
        qresult={'invocation_id':preserve.QA,'outcome':'pass','project_id':preserve.PROJECT,
                 'task_id':preserve.TASK,'actor':qpacket['binding'],'work_packet_digest':qpacket['content_sha256']}
        qack={'durable':True,'status':'QA APPROVED','event_id':'fixture-q','task_id':preserve.TASK,
              'run_id':preserve.QA_RUN}
        self.pin('QA_RESULT',preserve.digest(qresult));self.pin('QA_ACK',preserve.digest(qack))
        qevent={'invocation_id':preserve.QA,'run_id':preserve.QA_RUN,'result_contract':qresult,
                'type':'qa.passed','acceptance_passed':True,'event_id':'fixture-q'}
        fresult={'invocation_id':preserve.F33,'outcome':'ready_for_review'}
        fack={'durable':True,'task_id':preserve.TASK,'run_id':preserve.F33_RUN}
        base={'erp_project':preserve.PROJECT,'erp_task':preserve.TASK,
              'qa_verdict':None,'source_invocation_id':None,'submitted_at':1}
        f={**base,'invocation_id':preserve.F33,'automation_run_id':preserve.F33_RUN,
           'target_role':'developer','state':'verified','dispatch_attempts':1,
           'work_packet':'{}','packet_hash':preserve.digest({})}
        q={**base,'invocation_id':preserve.QA,'automation_run_id':preserve.QA_RUN,
           'target_role':'qa','state':'verified','dispatch_attempts':1,'qa_verdict':'pass',
           'work_packet':preserve.canonical(qpacket).decode(),'packet_hash':preserve.digest(qpacket)}
        p={**base,'invocation_id':preserve.PM,'automation_run_id':None,'submitted_at':None,
           'source_invocation_id':preserve.QA,'target_role':'project_manager','state':'blocked',
           'dispatch_attempts':0,'work_packet':preserve.canonical(ppacket).decode(),'packet_hash':preserve.PM_PACKET}
        result_rows=[
            {'invocation_id':preserve.F33,'run_id':preserve.F33_RUN,'phase':'published',
             'result_json':preserve.canonical(fresult).decode(),'result_sha256':preserve.digest(fresult),
             'event_json':'{}','receipt_json':preserve.canonical(fack).decode()},
            {'invocation_id':preserve.QA,'run_id':preserve.QA_RUN,'phase':'published',
             'result_json':preserve.canonical(qresult).decode(),'result_sha256':preserve.QA_RESULT,
             'event_json':preserve.canonical(qevent).decode(),'receipt_json':preserve.canonical(qack).decode()}]
        body=facts()['selection'];body={key:body[key]for key in
            ('policy','protected_file_sha256','corrected_reply_sha256','control_sequence','control_command')}
        event={'event_id':'fixture-selection','erp_project':preserve.PROJECT,'invocation_id':preserve.F33,
            'kind':'returned_reply_format_selected','actor':'fixture','payload':preserve.canonical(body).decode(),
            'created_at':1,'previous_hash':''}
        event['event_hash']=preserve.digest([event[key]for key in
            ('event_id','erp_project','invocation_id','kind','actor','payload','created_at','previous_hash')])
        self.pin('SELECTION_HASH',event['event_hash'])
        self.write_db('agentic-governance.sqlite',{'governance_invocation':[f,q,p],
            'governance_result':result_rows,'governance_local_result':[result_rows[1]],
            'governance_gateway_session':[{'invocation_id':preserve.QA}],
            'governance_event':[event]})
        self.pin('F33_ENVELOPE',preserve.text_sha('f-envelope'))
        self.pin('QA_ENVELOPE',preserve.text_sha('q-envelope'))
        self.pin('QA_REPLY',preserve.text_sha('q-reply'))
        self.write_db('abitdot-primary.sqlite',{'dot_primary_runs':[
            {'original_run_id':preserve.F33_RUN,'status':'unknown','response_text':None,
             'receipt':None,'scope_receipt_json':None,'envelope_json':'f-envelope'},
            {'original_run_id':preserve.QA_RUN,'status':'delegated','response_text':'q-reply',
             'receipt':'fixture-receipt','scope_receipt_json':'fixture-scope','envelope_json':'q-envelope'}]})
        self.write_db('webui.db',{'automation_run':[{'id':preserve.F33_RUN,'status':'success'},
                                                        {'id':preserve.QA_RUN,'status':'success'}]})
        self.write_db('erp-pending-review-qa.sqlite',{'review':[{'phase':'done'}]})
        self.write_db('maintenance-admission/gate.sqlite',{'gate':[{'state':'draining','epoch':31}],
            'lease':[{'id':'old','state':'finished'}],'audit':[{'kind':'fixture-history'}]})

    def hold(self):
        def db(name):
            path=self.root/name
            value=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
            value.row_factory=sqlite3.Row;value.execute('PRAGMA query_only=ON')
            self.opened.append(name)
            return value
        return types.SimpleNamespace(db=db)

    def test_actual_query_only_capture_preserves_all_database_bytes(self):
        before={str(p):p.read_bytes()for p in self.root.rglob('*.sqlite')}
        before[str(self.root/'webui.db')]=(self.root/'webui.db').read_bytes()
        captured=preserve.capture(self.hold(),GATE)
        self.assertEqual(len(self.opened),5)
        self.assertEqual(captured['facts']['f33']['provider_status'],'unknown')
        self.assertEqual(captured['facts']['pm']['session_count'],0)
        for name,raw in before.items():self.assertEqual(Path(name).read_bytes(),raw)

    def test_actual_capture_rejects_qa_ack_corruption(self):
        with sqlite3.connect(self.root/'agentic-governance.sqlite')as db:
            db.execute('UPDATE governance_result SET receipt_json=? WHERE invocation_id=?',
                       (json.dumps({'durable':False}),preserve.QA))
        with self.assertRaises((RuntimeError,KeyError)):
            preserve.capture(self.hold(),GATE)

    def test_actual_capture_rejects_f33_null_receipt_replacement(self):
        with sqlite3.connect(self.root/'abitdot-primary.sqlite')as db:
            db.execute('UPDATE dot_primary_runs SET receipt=? WHERE original_run_id=?',
                       ('fabricated',preserve.F33_RUN))
        with self.assertRaises(RuntimeError):preserve.capture(self.hold(),GATE)


class FileBoundaries(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='scope-source-fixture-')
        self.root=Path(self.temp.name)
        self.runtime=self.root/'open_webui';(self.runtime/'utils').mkdir(parents=True)
        self.target=self.runtime/app.TARGET
        original=REPO/'docs/evidence/2026-11-15-closing-pm-scope/validation/agentic_relay_scope.original.py'
        self.raw=(REPO/'backend/open_webui'/app.TARGET).read_bytes()
        self.assertEqual(app.sha(original.read_bytes()),app.ORIGINAL_SHA)
        self.assertEqual(app.sha(self.raw),app.CANDIDATE_SHA)
        self.target.write_bytes(original.read_bytes())
        os.chmod(self.target,0o640);os.utime(self.target,ns=(1_500_000_000_123_456_789,)*2)
        self.item={'relative_path':app.TARGET,'candidate_sha256':app.CANDIDATE_SHA,
            'original':core.file_proof(self.target),'parent':core.parent_proof(self.target.parent)}
        self.patch=patch.object(app,'RUNTIME',self.runtime);self.patch.start()

    def tearDown(self):
        self.patch.stop();self.temp.cleanup()

    def test_actual_atomic_install_preserves_old_nanosecond_metadata(self):
        app.atomic_scope_leaf(core,self.target,self.raw,self.item)
        expected={**self.item['original'],'sha256':app.CANDIDATE_SHA,'size':len(self.raw)}
        self.assertEqual(core.file_proof(self.target),expected)

    def test_foreign_target_and_changed_source_deny_without_write(self):
        foreign=self.target.with_name('agentic_recovery.py')
        with self.assertRaises(RuntimeError):app.atomic_scope_leaf(core,foreign,self.raw,self.item)
        self.assertFalse(foreign.exists())
        self.target.write_bytes(b'concurrent edit')
        with self.assertRaises(RuntimeError):app.atomic_scope_leaf(core,self.target,self.raw,self.item)
        self.assertEqual(self.target.read_bytes(),b'concurrent edit')

    def test_race_after_temp_creation_denies_rename_and_cleans_temp(self):
        original=core.prepare_temp
        def race(*args):
            path=original(*args);self.target.write_bytes(b'concurrent edit');return path
        with patch.object(core,'prepare_temp',race):
            with self.assertRaises(RuntimeError):app.atomic_scope_leaf(core,self.target,self.raw,self.item)
        self.assertEqual(list(self.target.parent.glob('.core-source-*')),[])
        self.assertEqual(self.target.read_bytes(),b'concurrent edit')

    def test_only_selected_python_cache_is_quarantined(self):
        cache=self.target.parent/'__pycache__';cache.mkdir()
        own=cache/(self.target.stem+'.cpython-311.pyc');own.write_bytes(b'old selected')
        other=cache/'agentic_recovery.cpython-311.pyc';other.write_bytes(b'untouched')
        records=[]
        core.quarantine_bytecode([self.target],self.root/'quarantine',lambda n,v:records.append(n))
        self.assertFalse(own.exists());self.assertEqual(other.read_bytes(),b'untouched')
        self.assertEqual(core.selected_caches([self.target]),[])

    def test_selected_symlink_cache_denies(self):
        cache=self.target.parent/'__pycache__';cache.mkdir()
        (cache/(self.target.stem+'.cpython-311.pyc')).symlink_to(self.target)
        with self.assertRaises(RuntimeError):core.selected_caches([self.target])


class MainLifecycle(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='scope-lifecycle-fixture-')
        self.root=Path(self.temp.name);self.events=[];self.records={}
        self.units={app.MAIN:{'fixed':'main'},app.QA:{'fixed':'qa'}}
        self.original={'pid':100,'process_start_ticks':'1','entrypoint_sha256':'e'*64}
        self.baseline={'processes':{'main':{'pid':100,'start_ticks':'1'}},
                       'environments':{app.MAIN:{'environment':'same'}}}
        self.counters={'Main_Stop_calls':0,'Main_Start_calls':0,
                       'source_replacements_started':0,'source_replacements_completed':0}
        self.stop_unsettled=False;self.start_unsettled=False;self.cache_reappears=False
        self.ack='zero';self.changed_readback=None;self.expired_browser=False
        self.finish=types.SimpleNamespace(DROPIN=self.root/'fence.conf',MARKER=self.root/'hold',
            FENCE_BYTES=b'fence',marker_bytes=lambda *a:b'marker',
            root_file=lambda p,m,raw:self.assertEqual(p.read_bytes(),raw))
        def fence(*a):
            self.finish.DROPIN.write_bytes(b'fence');self.finish.MARKER.write_bytes(b'marker')
            self.events.append('fence')
        self.finish.install_start_fence=fence
        def unit_check(before,**kw):
            self.events.append(('unit',tuple(before),kw))
            if app.QA in before:self.assertEqual(kw,{})
        def guards(path,raw):path.write_bytes(raw);self.events.append('guard')
        def quarantine(*a):self.events.append('cache');return []
        self.core=types.SimpleNamespace(
            NOKILL={app.MAIN:self.root/'no-kill.conf',app.QA:self.root/'never-qa.conf'},NOKILL_BYTES=b'no-kill',
            create_root_guard=guards,check_units=unit_check,once_service=core.once_service,
            quarantine_bytecode=quarantine,selected_caches=lambda _:['changed']if self.cache_reappears else [],
            fsync_directory=lambda _:None,checked_reload=lambda *a:self.events.append('reload'),
            replacement_process=lambda before,after:self.assertEqual(after,{'pid':101,'start_ticks':'2'}),
            environment_proof=lambda _:self.baseline['environments'][app.MAIN])
        def attempt(action,unit,timeout):
            self.assertEqual(unit,app.MAIN);self.events.append(action)
            return {'acknowledgement':self.ack,'returncode':None if self.ack!='zero'else 0}
        self.lifecycle=types.SimpleNamespace(service_attempt=attempt,security=lambda:None,
            started_state=lambda *_:None if self.start_unsettled else {'MainPID':'101'},
            identity=lambda *_:{**self.original,'pid':101,'process_start_ticks':'2'})
        def stopped(*a):
            self.events.append('stop-readback')
            if self.stop_unsettled:raise RuntimeError('ORIGINAL_STOP_UNSETTLED_NO_REPLAY')
            return {'original_process_gone':True}
        self.capacity=types.SimpleNamespace(wait_stopped=stopped,
            ensure_service_identity=lambda *a:(clone(self.original),{'pid':200}))
        self.relay=types.SimpleNamespace(process=lambda _: {'pid':101,'start_ticks':'2'})
        self.common=types.SimpleNamespace(main_restarts_zero=lambda:None)
        self.patch=patch.object(app,'atomic_scope_leaf',lambda *a:self.events.append('write'))
        self.patch.start()

    def tearDown(self):
        self.patch.stop();self.temp.cleanup()

    def record(self,name,value):
        if name in self.records:raise RuntimeError('CREATE_ONLY_RECORD_EXISTS')
        self.records[name]=clone(value);self.events.append(name)

    def run_graph(self):
        def readback():
            self.events.append('readback')
            if self.changed_readback and self.changed_readback(self.events):raise RuntimeError('PRESERVED_STATE_CHANGED')
        def browser_check():
            if self.expired_browser:raise RuntimeError('EXPIRED_BROWSER_PROOF')
        return app.install_main_only(self.core,self.capacity,self.lifecycle,self.finish,None,self.relay,self.common,
            record=self.record,destination=self.root,item={'original':{'sha256':app.ORIGINAL_SHA}},candidate=b'fixture',baseline=self.baseline,
            binding={},units=self.units,original=self.original,readback=readback,
            source_check=lambda installed:self.events.append(('source',installed)),browser_check=browser_check,counters=self.counters)

    def test_positive_graph_main_only_and_metadata_check_before_start(self):
        self.run_graph()
        self.assertEqual(self.events.count('stop'),1);self.assertEqual(self.events.count('start'),1)
        self.assertLess(self.events.index('stop-readback'),self.events.index('cache'))
        self.assertLess(self.events.index('write'),self.events.index('start'))
        self.assertFalse(self.core.NOKILL[app.QA].exists())
        self.assertEqual(self.counters['source_replacements_completed'],1)

    def test_unknown_stop_ack_positive_readback_is_not_replayed(self):
        self.ack='timeout_unknown';self.run_graph()
        self.assertEqual(self.events.count('stop'),1);self.assertEqual(self.events.count('start'),1)
        self.assertEqual(self.records['main-stop-ack.json']['acknowledgement'],'timeout_unknown')

    def test_main_identity_change_after_guards_denies_stop(self):
        self.capacity.ensure_service_identity=lambda *a:({'pid':999}, {'pid':200})
        with self.assertRaisesRegex(RuntimeError,'IMMEDIATELY_BEFORE_STOP'):self.run_graph()
        self.assertNotIn('stop',self.events);self.assertNotIn('write',self.events);self.assertNotIn('start',self.events)

    def test_browser_expiration_after_guards_denies_stop(self):
        self.expired_browser=True
        with self.assertRaisesRegex(RuntimeError,'EXPIRED_BROWSER'):self.run_graph()
        self.assertNotIn('stop',self.events);self.assertNotIn('write',self.events)

    def test_unsettled_stop_holds_before_cache_write_start(self):
        self.stop_unsettled=True
        with self.assertRaises(RuntimeError):self.run_graph()
        self.assertEqual(self.events.count('stop'),1)
        self.assertNotIn('cache',self.events);self.assertNotIn('write',self.events);self.assertNotIn('start',self.events)
        self.assertTrue(self.core.NOKILL[app.MAIN].exists());self.assertTrue(self.finish.DROPIN.exists())

    def test_unsettled_start_holds_after_one_attempt(self):
        self.start_unsettled=True
        with patch.object(app.time,'monotonic',side_effect=[0,46]),patch.object(app.time,'sleep',lambda _:None):
            with self.assertRaises(RuntimeError):self.run_graph()
        self.assertEqual(self.events.count('start'),1);self.assertEqual(self.events.count('stop'),1)
        self.assertTrue(self.core.NOKILL[app.MAIN].exists())

    def test_preservation_drift_before_stop_denies_all_lifecycle(self):
        self.changed_readback=lambda events:'stop'not in events
        with self.assertRaises(RuntimeError):self.run_graph()
        self.assertNotIn('stop',self.events);self.assertNotIn('write',self.events);self.assertNotIn('start',self.events)

    def test_cache_reappearance_holds_before_start(self):
        self.cache_reappears=True
        with self.assertRaises(RuntimeError):self.run_graph()
        self.assertIn('write',self.events);self.assertNotIn('start',self.events)
        self.assertTrue(self.finish.DROPIN.exists())

    def test_existing_intent_cannot_repeat_service_action(self):
        self.records['main-stop-intent.json']={}
        with self.assertRaises(RuntimeError):self.run_graph()
        self.assertNotIn('stop',self.events);self.assertNotIn('write',self.events)


class RelayHealthBoundary(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='scope-health-fixture-')
        self.root=Path(self.temp.name);self.state=self.root/'state';self.state.mkdir()
        self.target=self.state/'aibit-relay-target-id.txt';self.target.write_bytes(b'prior-id')
        self.catalog=self.state/'aibit-relay-projects.json';self.catalog.write_bytes(b'{"items":[]}')
        self.registry=self.state/'chat-thread-targets.json';self.registry.write_bytes(b'{}')
        self.launcher=self.root/'start-relay.sh';self.launcher.write_bytes(b'fixture-launcher')
        self.dest=self.root/'private';self.dest.mkdir()
        self.patches=[]
        for key,value in [('HEALTH_TARGET_FILE',self.target),('HEALTH_CATALOG_FILE',self.catalog),
                          ('THREAD_REGISTRY',self.registry),('RELAY_LAUNCHER',self.launcher),
                          ('RELAY_LAUNCHER_SHA',app.sha(self.launcher.read_bytes())),
                          ('HEALTH_SOURCE_PINS',{str(self.launcher):app.sha(self.launcher.read_bytes())})]:
            item=patch.object(app,key,value);item.start();self.patches.append(item)
        self.configuration={'ABIT_ERP_CHATGPT_TARGET_FILE':str(self.target),
            'ABIT_ERP_CHATGPT_PROJECT_CACHE':str(self.catalog),'ABIT_ERP_CHATGPT_THREAD_TARGET_FILE':None}
        self.baseline={'relay_health_inputs':{'configuration':self.configuration,
            'launcher':core.file_proof(self.launcher),'files':{
                str(path):{'present':True,'parent':core.parent_proof(path.parent),'file':core.file_proof(path)}
                for path in (self.target,self.catalog)}},
            'target_registry':core.file_proof(self.registry),'relay_code':{str(self.launcher):core.file_proof(self.launcher)},
            'processes':{'main':{'pid':10},'node':{'pid':20},'qa':{'pid':30}},
            'protected_configuration':{},'units':{}}
        item=patch.object(app,'health_path_configuration',return_value=self.configuration)
        item.start();self.patches.append(item)
        self.records={};self.calls=0;self.effect=None;self.result={'active_relay_turns':0,'ready':True,'authenticated':True}
        self.counters={'relay_health_call_attempts':0,'relay_health_positive_acknowledgments':0,
                       'relay_health_failed_or_unknown_acknowledgments':0}
        def health():
            self.calls+=1
            self.assertTrue((self.dest/'relay-health-inputs-before-first-call.json').exists())
            if self.effect:self.effect()
            return clone(self.result)
        self.relay=types.SimpleNamespace(health=health,process=lambda pid:{'pid':pid},service_state=lambda _:None)
        self.adapter=types.SimpleNamespace(file_proof=core.file_proof,parent_proof=core.parent_proof,
            read=core.read,protected_runtime=lambda proxy,baseline:proxy.health())
        self.history=types.SimpleNamespace(private_new=lambda path,raw:self.private_new(path,raw))

    def tearDown(self):
        for item in reversed(self.patches):item.stop()
        self.temp.cleanup()

    def private_new(self,path,raw):
        with path.open('xb')as stream:stream.write(raw)
        os.chmod(path,0o600)

    def record(self,name,value):
        self.assertNotIn(name,self.records);self.records[name]=clone(value)
        self.private_new(self.dest/name,app.canonical(value))

    def guard(self):
        return app.RelayHealthGuard(self.adapter,self.relay,self.baseline,self.record,self.counters)

    def test_first_call_requires_exact_private_retention_and_qualified_success(self):
        guard=self.guard()
        with self.assertRaises(RuntimeError):guard.protected_runtime()
        self.assertEqual(self.calls,0)
        guard.retain(self.history,self.dest)
        self.assertEqual(guard.protected_runtime()['active_relay_turns'],0)
        self.assertEqual(self.calls,1)
        receipt=self.records['relay-health-1-readback.json']
        self.assertTrue(receipt['no_persisted_input_change_observed'])
        self.assertFalse(receipt['zero_tab_allocation_proven'])
        self.assertEqual(sum(path.read_bytes()==b'prior-id' for path in self.dest.glob('*.private')),1)

    def test_catalog_change_holds_without_restore_or_second_call(self):
        guard=self.guard();guard.retain(self.history,self.dest)
        self.effect=lambda:self.catalog.write_bytes(b'cleared-by-handler')
        with self.assertRaises(RuntimeError):guard.protected_runtime()
        self.assertEqual(self.calls,1);self.assertEqual(self.catalog.read_bytes(),b'cleared-by-handler')
        self.assertEqual(self.counters['relay_health_positive_acknowledgments'],1)
        self.assertIsNotNone(self.records['relay-health-1-readback.json']['postcheck_error_class'])

    def test_absent_target_proof_retained_then_creation_holds(self):
        self.target.unlink()
        self.baseline['relay_health_inputs']['files'][str(self.target)].update(present=False,file=None)
        guard=self.guard();guard.retain(self.history,self.dest)
        self.effect=lambda:self.target.write_bytes(b'new-target')
        with self.assertRaises(RuntimeError):guard.protected_runtime()
        self.assertFalse(self.records['relay-health-inputs-before-first-call.json']['files'][str(self.target)]['present'])
        self.assertEqual(self.calls,1);self.assertEqual(self.target.read_bytes(),b'new-target')

    def test_unknown_ack_is_counted_bracketed_and_not_retried(self):
        guard=self.guard();guard.retain(self.history,self.dest)
        def fail():raise TimeoutError('fixture')
        self.effect=fail
        with self.assertRaises(RuntimeError):guard.protected_runtime()
        self.assertEqual(self.calls,1)
        self.assertEqual(self.counters['relay_health_failed_or_unknown_acknowledgments'],1)
        self.assertEqual(self.records['relay-health-1-readback.json']['response_error_class'],'TimeoutError')
        self.assertTrue(self.records['relay-health-1-readback.json']['no_persisted_input_change_observed'])

    def test_bound_or_busy_denies_without_extra_request(self):
        guard=self.guard();guard.retain(self.history,self.dest)
        self.counters['relay_health_call_attempts']=app.MAX_HEALTH_CALLS
        with self.assertRaises(RuntimeError):guard.protected_runtime()
        self.assertEqual(self.calls,0)
        self.counters['relay_health_call_attempts']=0;self.result['active_relay_turns']=1
        with self.assertRaises(RuntimeError):guard.protected_runtime()
        self.assertEqual(self.calls,1)

    def test_registry_or_process_change_is_not_hidden_by_health_success(self):
        guard=self.guard();guard.retain(self.history,self.dest)
        self.effect=lambda:self.registry.write_bytes(b'changed')
        with self.assertRaises(RuntimeError):guard.protected_runtime()
        self.assertEqual(self.calls,1)
        self.assertIsNotNone(self.records['relay-health-1-readback.json']['postcheck_error_class'])

    def test_protected_configuration_change_after_get_holds(self):
        config=self.root/'protected.conf';config.write_bytes(b'original')
        self.baseline['protected_configuration']={str(config):core.file_proof(config)}
        guard=self.guard();guard.retain(self.history,self.dest)
        self.effect=lambda:config.write_bytes(b'changed')
        with self.assertRaises(RuntimeError):guard.protected_runtime()
        self.assertEqual(self.calls,1);self.assertEqual(config.read_bytes(),b'changed')

    def test_preserved_unit_drift_after_get_holds(self):
        self.baseline['units']={app.QA:{'pid':30}}
        state={'pid':30};self.relay.service_state=lambda _:clone(state)
        guard=self.guard();guard.retain(self.history,self.dest)
        self.effect=lambda:state.update(pid=31)
        with self.assertRaises(RuntimeError):guard.protected_runtime()
        self.assertEqual(self.calls,1)

    def test_unreviewed_health_source_digest_denies_before_call(self):
        self.baseline['relay_code'][str(self.launcher)]['sha256']='0'*64
        with self.assertRaisesRegex(RuntimeError,'HEALTH_EFFECT_SOURCE'):self.guard()
        self.assertEqual(self.calls,0)

    def test_foreign_override_or_symlink_denies_before_call(self):
        self.configuration['ABIT_ERP_CHATGPT_THREAD_TARGET_FILE']='/unreviewed/path'
        with self.assertRaises(RuntimeError):self.guard()
        self.configuration['ABIT_ERP_CHATGPT_THREAD_TARGET_FILE']=None
        guard=self.guard()
        self.target.unlink();self.target.symlink_to(self.catalog)
        with self.assertRaises(RuntimeError):guard.retain(self.history,self.dest)
        self.assertEqual(self.calls,0)


class ProductEvidence(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='scope-product-evidence-')
        self.package=Path(self.temp.name);(self.package/'evidence').mkdir()
        source=REPO/'docs/evidence/2026-11-15-closing-pm-scope'
        paths={'focused1-receipt.json':source/'validation/focused1-receipt.json',
               'corrected-foreign-registration-receipt.json':source/'validation/corrected-foreign-registration-receipt.json',
               'candidate-live-input-proof.json':source/'proof/candidate-live-input-proof.json'}
        pins={}
        for name,path in paths.items():
            raw=path.read_bytes();(self.package/'evidence'/name).write_bytes(raw);pins[name]=app.sha(raw)
        self.receipt={'schema_version':1,'kind':'DISTINCT_PASSES_ACROSS_RETAINED_29_PLUS_1',
            'candidate_sha256':app.CANDIDATE_SHA,'distinct_passed':30,'initial_fixture_errors':1,
            'clean_thirty_case_run_claimed':False,'evidence':pins}
        proof=json.loads(paths['candidate-live-input-proof.json'].read_text())
        self.baseline={'core_files':{name:{'sha256':pin}for name,pin in proof['source_pins'].items()
                                    if name.startswith(str(app.RUNTIME)+'/')}}

    def tearDown(self):
        self.temp.cleanup()

    def test_actual_split_receipts_derive_thirty_without_clean_run_claim(self):
        result=app.validate_product_tests(core,self.package,self.receipt,{'test_count':30},self.baseline)
        self.assertEqual(result,{'distinct_passed':30,'initial_fixture_errors_retained':1,
                                'clean_thirty_case_run_claimed':False})

    def test_clean_run_claim_or_attempt_rewrite_denies(self):
        value=clone(self.receipt);value['initial_fixture_errors']=0
        with self.assertRaises(RuntimeError):
            app.validate_product_tests(core,self.package,value,{'test_count':30},self.baseline)
        p=self.package/'evidence/focused1-receipt.json';value=json.loads(p.read_text())
        value['errors']=0;p.write_text(json.dumps(value))
        with self.assertRaises(RuntimeError):
            app.validate_product_tests(core,self.package,self.receipt,{'test_count':30},self.baseline)

    def test_changed_actual_live_dependency_invalidates_retained_input_proof(self):
        value=clone(self.baseline);next(iter(value['core_files'].values()))['sha256']='0'*64
        with self.assertRaises(RuntimeError):
            app.validate_product_tests(core,self.package,self.receipt,{'test_count':30},value)


class ReleaseInputs(unittest.TestCase):
    def test_unfrozen_plan_denies_before_support_import_or_actions(self):
        args=types.SimpleNamespace(execute=True,preflight_only=False)
        with patch.object(app.os,'geteuid',return_value=0),patch.object(app.os,'uname',
             return_value=types.SimpleNamespace(nodename='abit-dev-linux')),patch.object(app,'pinned_module')as load:
            with self.assertRaisesRegex(RuntimeError,'NOT_FROZEN'):app.main(args)
            load.assert_not_called()

    def test_browser_requires_actual_admin_exact_main_and_fresh_zero_tasks(self):
        from datetime import datetime,timezone
        now=datetime(2026,11,15,19,0,0,tzinfo=timezone.utc)
        main={'pid':100,'process_start_ticks':'123','entrypoint_sha256':'a'*64}
        value={'source':'authenticated_browser_ui','url':'https://abit.ai/api/tasks',
            'actual_task_count':0,'main_pid':100,'main_process_start_ticks':'123',
            'credential_extracted':False,'admin_authenticated':True,'observed_at':now.isoformat()}
        capacity.validate_browser(core,value,main,now=now)
        for key,bad in [('admin_authenticated',False),('actual_task_count',1),
                        ('main_pid',101),('credential_extracted',True),
                        ('observed_at','2026-11-15T18:57:00+00:00')]:
            changed={**value,key:bad}
            with self.assertRaises(RuntimeError):capacity.validate_browser(core,changed,main,now=now)


if __name__=='__main__':
    unittest.main(verbosity=2)
