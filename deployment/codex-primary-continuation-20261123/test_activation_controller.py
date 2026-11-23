"""Isolated Core release-controller contracts; no real systemd/ERP/provider calls.

The full approved 13-file manifest shape and source-lineage contract are
exercised against temporary filesystem and SQLite identities. Tests never
supply root authorization or target the installed Core.
"""
from __future__ import annotations
import hashlib
import importlib.util
import json
from pathlib import Path
import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('p05_activation_controller',HERE/'activation_controller.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def sha(data):return hashlib.sha256(data).hexdigest()


class ControllerQA(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)
        self.repo=root/'git';self.runtime=root/'runtime';self.data=root/'data';self.stage=root/'stage'
        self.dropin=root/'systemd/50-agentic-codex.conf';self.override=root/'systemd/zzzz-p05.conf'
        self.dropin.parent.mkdir(parents=True)
        self.unit_file=root/'systemd/abit-ai.service'
        self.unit_file.write_bytes(b'[Service]\nUser=mcpdev\n')
        self.runtime.mkdir();self.data.mkdir();self.stage.mkdir()
        self.release=self.repo/'deployment'/module.RELEASE_NAME;self.release.mkdir(parents=True)
        self.staged_files=[]
        for i in range(13):
            path='open_webui/utils/' + str(i).zfill(2) + '.py'
            existing=i<8
            before=(f'RETAINED_BASELINE={i}\n').encode()
            after=(f'REVIEWED_NEW={i}\n').encode()
            src=self.repo/'backend'/path;src.parent.mkdir(parents=True,exist_ok=True);src.write_bytes(after)
            live=self.runtime/path;live.parent.mkdir(parents=True,exist_ok=True)
            if existing: live.write_bytes(before)
            self.staged_files.append({'path':path,'operation':'replace' if existing else 'create',
                        'before_sha256':sha(before) if existing else None,
                        'candidate_sha256':sha(after),'candidate_size':len(after)})
        self.lineage=[]
        for i in range(18):
            path='open_webui/utils/lineage_' + str(i).zfill(2)+'.py'
            content=f'PRESERVED_INSTALLED_LINEAGE={i}\n'.encode()
            for base in (self.repo/'backend',self.runtime):
                p=base/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(content)
            self.lineage.append({'path':path,'sha256':sha(content)})
        conf=b'[Service]\nEnvironment=CODEX_HOME=/safe/owned/auth\n'
        (self.release/'50-agentic-codex.conf').write_bytes(conf)
        manifest={'files':self.staged_files,'installed':False,'approval_for_this_package':False,
                  'config_files':[{'before_sha256':None,'candidate_sha256':sha(conf)}]}
        (self.release/'release-manifest.json').write_text(json.dumps(manifest))
        (self.release/'source-parity-manifest.json').write_text(json.dumps({'modules':self.lineage}))
        db=self.data/'isolated-governance.sqlite';with_con=sqlite3.connect(db)
        with with_con:
            with_con.execute('CREATE TABLE audit (id INTEGER PRIMARY KEY, untouched TEXT)')
            with_con.execute("INSERT INTO audit(untouched) VALUES('original kept')")
        with_con.close()
        gate=self.tmp.name+'/gate.sqlite';g=sqlite3.connect(gate)
        with g:
            g.execute('CREATE TABLE gate(id INTEGER PRIMARY KEY,state TEXT,epoch INTEGER)')
            g.execute("INSERT INTO gate VALUES(1,'sealed',100)")
            g.execute('CREATE TABLE lease(id TEXT,kind TEXT,reference TEXT,pid INTEGER,process_start TEXT,state TEXT)')
        g.close()
        self.running=True;self.calls=[]
        def fake_command(*args,timeout=25):
            self.calls.append(args)
            if args[:3]==('/usr/bin/systemctl','show','abit-ai.service'):
                return 'User=mcpdev\nGroup=mcpdev'
            if args[:3]==('/usr/bin/systemctl','start','abit-ai.service'):
                self.running=True
            return ''
        self.c=module.Controller(repository=self.repo,runtime=self.runtime,data=self.data,
                stage=self.stage,dropin=self.dropin,override=self.override,operator=root/'operator.json',
                gate=Path(gate),command=fake_command,test_mode=True,
                unit_paths=[self.unit_file])
        self.c.approval=lambda:{'operator':{'owner':'isolated-fixture'},'gate':{'epoch':100}}
        self.c.service_stop=lambda:setattr(self,'running',False)
        self.c.graceful_override=lambda:self.override.write_bytes(module.OVERRIDE_BYTES)
        self.c.core_health=lambda:{'status':True}
        def copy_replacement(path,body,**kwargs):
            path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
            temp=path.parent/'.safe-new';temp.write_bytes(body);os.replace(temp,path)
        self.c.replace_file=copy_replacement
        self.addCleanup(patch.stopall)
        patch.object(module,'root_dir',lambda _:None).start()

    def prepare(self):
        r=self.c.stage_backup()
        self.assertEqual(r['file_count'],13)
        self.assertEqual(r['verified_backup_count'],1)
        # Production uses Root 0600; this isolated fake test reads its own files.
        self.c.stage_info=lambda:json.loads((self.stage/'journal.json').read_text())
        return self.c.stage_info()

    def test_approval_absent_stays_fail_closed(self):
        self.assertTrue(self.c.preflight()['no_release_write'])
        strict=module.Controller(repository=self.repo,runtime=self.runtime,data=self.data,
            stage=self.stage,dropin=self.dropin,override=self.override,test_mode=True)
        with self.assertRaises(module.ReleaseDenied):
            strict.approval()
        self.assertFalse(self.override.exists())

    def test_stage_uses_online_sqlite_and_verified_rollback_bytes(self):
        self.prepare()
        self.c.verify_backups(self.c.stage_info())
        copy=self.stage/'db-backups/isolated-governance.sqlite'
        with sqlite3.connect(copy) as db:
            self.assertEqual(db.execute('PRAGMA quick_check').fetchone()[0],'ok')
            self.assertEqual(db.execute('SELECT untouched FROM audit').fetchone()[0],'original kept')
        self.assertFalse(self.override.exists())
        self.assertTrue(self.c.preflight('before')['no_release_write'])

    def test_apply_and_rollback_13_files_without_restoring_business_db(self):
        self.prepare()
        result=self.c.activate()
        self.assertEqual(result['state'],'applied_pending_acceptance')
        self.assertTrue(self.c.preflight('after')['no_release_write'])
        self.assertEqual(len(self.c.stage_info()['steps']),13)
        self.assertTrue(self.running)
        self.assertTrue(self.override.exists())
        before_data=(self.data/'isolated-governance.sqlite').read_bytes()
        result=self.c.rollback()
        self.assertEqual(result['state'],'rolled_back_preserving_data')
        self.assertFalse(result['database_restored'])
        self.assertFalse(self.override.exists())
        self.assertTrue(self.c.preflight('before')['no_release_write'])
        self.assertEqual((self.data/'isolated-governance.sqlite').read_bytes(),before_data)
        self.assertTrue(self.running)

    def test_changed_preimage_holds_without_service_stop(self):
        self.prepare()
        p=self.runtime/self.staged_files[0]['path'];p.write_text('UNRELATED_HUMAN_CHANGE\n')
        with self.assertRaisesRegex(module.ReleaseDenied,'Installed source changed'):
            self.c.activate()
        self.assertTrue(self.running)
        self.assertFalse(self.override.exists())

    def test_unknown_after_release_prevents_unsafe_rollback(self):
        self.prepare();self.c.activate()
        p=self.runtime/self.staged_files[1]['path'];p.write_text('OTHER_COMMIT_AFTER_RELEASE\n')
        with self.assertRaisesRegex(module.ReleaseDenied,'Concurrent newer source'):
            self.c.rollback()
        self.assertEqual(p.read_text(),'OTHER_COMMIT_AFTER_RELEASE\n')

    def test_sealed_scoped_clearance_requires_independent_review_and_ci(self):
        self.c.operator_record=lambda:{'owner':'existing-owner','phase':'fixture','expires_at':'2099-01-01T00:00:00+00:00'}
        snapshot={'state':'sealed','epoch':100,'active':1,'unresolved':33,
                  'affected_core_admissions':[], 'leases_sha256':'f'*64}
        self.c.gate_status=lambda:snapshot
        allowed={'status':'APPROVED','manifest_sha256':self.c.expected_sha,
            'source_commit':module.SOURCE_COMMIT,'allowed_services':['abit-ai.service'],
            'independent_review_approved':True,'required_hosted_ci_passed':True,
            'production_release_authorized':True}
        owner={'status':'ACKNOWLEDGED_EXECUTION','owner':'existing-owner',
            'manifest_sha256':self.c.expected_sha,'gate_epoch':100,'leases_sha256':'f'*64,
            'no_original_replay':True,'core_only_drain_accepted':True,
            'unaffected_qa_and_historical_leases_preserved':True,
            'current_core_handlers_drained':True,'unresolved_originals_reconciled':True,
            'original_06448_replay_forbidden':True,
            'acknowledged_scoped_owner_or_execution':True}
        def lookup(path):
            if str(path).endswith('production-approval.json'):
                return allowed
            if str(path).endswith('operator-execution-clearance.json'):
                return owner
            raise AssertionError('Unexpected protected proof path')
        with patch.object(module,'protected',side_effect=lookup):
            self.assertEqual(module.Controller.approval(self.c)['operator']['owner'],'existing-owner')
            for key in ('required_hosted_ci_passed','independent_review_approved'):
                allowed[key]=False
                with self.assertRaises(module.ReleaseDenied):module.Controller.approval(self.c)
                allowed[key]=True
            owner['leases_sha256']='stale'
            with self.assertRaises(module.ReleaseDenied):module.Controller.approval(self.c)
            owner['leases_sha256']='f'*64
            snapshot['affected_core_admissions']=[{'id':'core-active'}]
            with self.assertRaises(module.ReleaseDenied):module.Controller.approval(self.c)
            snapshot['affected_core_admissions']=[]
            snapshot['state']='open'
            with self.assertRaises(module.ReleaseDenied):module.Controller.approval(self.c)

    def test_repeated_activation_is_fenced_by_preimages(self):
        self.prepare();self.c.activate()
        with self.assertRaises(module.ReleaseDenied):self.c.activate()
        self.assertEqual(self.c.stage_info()['state'],'applied_pending_acceptance')

    def test_rollback_inert_stop_intent_preserves_running_core(self):
        self.prepare()
        journal=self.c.stage_info();journal['state']='stop_intent'
        (self.stage/'journal.json').write_text(json.dumps(journal))
        self.assertEqual(self.c.rollback()['state'],'rolled_back_without_core_interruption')
        self.assertTrue(self.running)
        self.assertFalse(self.override.exists())
        self.assertTrue(self.c.preflight('before')['no_release_write'])

    def test_rollback_from_stopped_before_any_install(self):
        self.prepare()
        journal=self.c.stage_info();journal['state']='core_stopped'
        (self.stage/'journal.json').write_text(json.dumps(journal))
        self.assertEqual(self.c.rollback()['state'],'rolled_back_preserving_data')
        self.assertTrue(self.c.preflight('before')['no_release_write'])

    def test_symlink_is_denied_before_service_stop(self):
        self.prepare()
        one=self.runtime/self.staged_files[0]['path']
        original=one.read_bytes();one.unlink()
        target=self.runtime/'outside.txt';target.write_bytes(original)
        one.symlink_to(target)
        with self.assertRaises(module.ReleaseDenied):self.c.activate()
        self.assertTrue(self.running)

    def test_unauthenticated_service_is_fenced(self):
        self.c.test_mode=False
        original=self.c.command
        self.c.command=lambda *args, **kwargs: 'Not logged in'
        with self.assertRaisesRegex(module.ReleaseDenied,'authentication not independently verified'):
            self.c.service_auth()
        self.c.command=lambda *args, **kwargs: 'Logged in using ChatGPT'
        self.assertTrue(self.c.service_auth())
        self.c.command=original
        self.c.test_mode=True

    def test_not_finalized_without_real_qa_and_erp_receipts(self):
        self.prepare();self.c.activate()
        with self.assertRaises(module.ReleaseDenied):
            self.c.finalize()
        self.assertTrue(self.override.exists())

if __name__=='__main__':unittest.main(verbosity=2)
