"""Offline archive validation/retention tests; no live files or Root actions."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('prepare_delivery_history_fixture', ROOT/'scripts/prepare_delivery_history.py')
preparer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preparer)


class PrepareDeliveryHistory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        # The process remains unprivileged. Only ownership metadata and chown
        # are simulated; all no-follow, size, link, exclusive-create, chmod,
        # fsync, byte-preservation and interruption checks use real temp files.
        real_fstat = os.fstat
        def metadata(fd):
            value = real_fstat(fd)
            fields = {name:getattr(value,name) for name in dir(value) if name.startswith('st_')}
            fields.update(st_uid=0,st_gid=988)
            return SimpleNamespace(**fields)
        for mock in (patch.object(preparer.os,'fstat',side_effect=metadata),
                     patch.object(preparer.os,'geteuid',return_value=0),
                     patch.object(preparer.os,'fchown',return_value=None)):
            mock.start();self.addCleanup(mock.stop)
        self.policy={'LEGACY_SOURCE_COMMIT':'a'*40,
                     'LEGACY_PRODUCERS':{'agentic_gateway_delivery.py':'1'*64,'agentic_orchestrator.py':'2'*64}}

    def record(self, name, value):
        path=self.root/name
        path.write_text(json.dumps(value))
        return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                'mtime_ns':path.stat().st_mtime_ns}

    def deployment_fixture(self):
        pins=self.policy['LEGACY_PRODUCERS']
        files=[{'runtime_path':'/runtime/'+name,'sha256':sha} for name,sha in pins.items()]
        files += [{'runtime_path':'/runtime/dependency'+str(i)+'.py','sha256':'3'*64} for i in range(69)]
        after={'purpose':'actual71-after-original-commit-readback-recovery',
               'source_commit':self.policy['LEGACY_SOURCE_COMMIT'],'runtime_count':71,'files':files}
        completed={'ok':True,'stage':'MAIN_AND_IDLE_QA_STARTED_DRAINING20_BLOCKED_ORIGINAL_RETAINED',
                   'runtime_manifest':after,
                   'installed':[{'path':'/runtime/'+name,'sha256':sha,'uid':999,'gid':988,'mode':'0644'} for name,sha in pins.items()],
                   'new_Main':{'private_process_detail':'must stay Root-only'},
                   'private_backup_path':'must stay Root-only',
                   'Main_Start_calls':1,'Main_Stop_calls':1,'QA_Start_calls':1,'QA_Stop_calls':1}
        intent={'source':self.policy['LEGACY_SOURCE_COMMIT'],'original_invocation':'original-fixture','run_id':'run-fixture'}
        values={'complete71-after.json':after,'completed-receipt.json':completed,'intent.json':intent}
        records={name:self.record(name,value) for name,value in values.items()}
        proof={'source_commit':self.policy['LEGACY_SOURCE_COMMIT'],
               'selected':files[:2],'records':records}
        self.record('projection-proof.json',proof)
        return proof,values

    def test_read_file_rejects_links_and_excessive_bytes(self):
        good=self.root/'good';good.write_bytes(b'exact original')
        self.assertEqual(preparer.read_file(good)[0],b'exact original')
        link=self.root/'link';link.symlink_to(good)
        with self.assertRaises(OSError):preparer.read_file(link)
        hard=self.root/'hard';os.link(good,hard)
        with self.assertRaises(ValueError):preparer.read_file(hard)
        big=self.root/'big';big.write_bytes(b'x'*5)
        with self.assertRaises(ValueError):preparer.read_file(big,4)

    def test_raw_deployment_records_are_validated_and_private_fields_are_not_projected(self):
        self.deployment_fixture()
        with patch.object(preparer,'ROLLOUTS',self.root):
            projection,intent=preparer.deployment_projection(self.root/'projection-proof.json',self.policy)
        self.assertEqual(set(projection),{'schema_version','purpose','source_commit','producer_sha256',
                                         'records','observed_at','provenance'})
        text=json.dumps(projection)
        self.assertNotIn('private_process_detail',text)
        self.assertNotIn('private_backup_path',text)
        self.assertNotIn('/runtime/',text)
        self.assertEqual(intent['original_invocation'],'original-fixture')

    def test_rehashed_but_invalid_raw_deployment_records_are_denied(self):
        mutations=[('complete71-after.json',lambda x:x.update(source_commit='b'*40)),
                   ('completed-receipt.json',lambda x:x['installed'][0].update(sha256='9'*64)),
                   ('completed-receipt.json',lambda x:x.update(Main_Start_calls=2)),
                   ('completed-receipt.json',lambda x:x.update(runtime_manifest={})),
                   ('intent.json',lambda x:x.update(source='b'*40))]
        for name,mutate in mutations:
            with self.subTest(record=name):
                proof,values=self.deployment_fixture()
                mutate(values[name]);proof['records'][name]=self.record(name,values[name])
                self.record('projection-proof.json',proof)
                with patch.object(preparer,'ROLLOUTS',self.root),self.assertRaises(ValueError):
                    preparer.deployment_projection(self.root/'projection-proof.json',self.policy)

    def test_create_only_archive_is_idempotent_and_index_is_last(self):
        archive=self.root/'archive';objects={'a'*64+'.bin':b'operator','b'*64+'.bin':b'ack'}
        index='c'*64+'.history.json';raw=b'index'
        real_open=os.open;created=[]
        def observe(path,flags,*args,**kwargs):
            if flags & os.O_CREAT:created.append(str(path))
            return real_open(path,flags,*args,**kwargs)
        with patch.object(preparer.os,'open',side_effect=observe):
            preparer.install_create_only(archive,index,raw,objects)
        self.assertEqual(created,list(objects)+[index])
        before={x.name:(x.read_bytes(),x.stat().st_ino,x.stat().st_mtime_ns) for x in archive.iterdir()}
        preparer.install_create_only(archive,index,raw,objects)
        self.assertEqual(before,{x.name:(x.read_bytes(),x.stat().st_ino,x.stat().st_mtime_ns) for x in archive.iterdir()})
        self.assertEqual(archive.stat().st_mode & 0o777,0o750)
        self.assertTrue(all(x.stat().st_mode & 0o777 == 0o640 for x in archive.iterdir()))

    def test_conflicting_archive_is_preserved_before_any_new_file_is_created(self):
        archive=self.root/'archive';archive.mkdir(mode=0o750)
        existing=archive/('b'*64+'.bin');existing.write_bytes(b'old bytes');existing.chmod(0o640)
        before=(existing.read_bytes(),existing.stat().st_ino,existing.stat().st_mtime_ns)
        with self.assertRaisesRegex(ValueError,'conflicts'):
            preparer.install_create_only(archive,'c'*64+'.history.json',b'index',
                                        {'a'*64+'.bin':b'new','b'*64+'.bin':b'different'})
        self.assertEqual(list(archive.iterdir()),[existing])
        self.assertEqual(before,(existing.read_bytes(),existing.stat().st_ino,existing.stat().st_mtime_ns))

    def test_interrupted_retention_has_no_index_and_can_reuse_exact_partial_objects(self):
        archive=self.root/'archive';objects={'a'*64+'.bin':b'operator','b'*64+'.bin':b'ack'}
        index='c'*64+'.history.json';real_open=os.open
        def interrupted(path,flags,*args,**kwargs):
            if str(path)==index and flags & os.O_CREAT:raise OSError('simulated interruption before index')
            return real_open(path,flags,*args,**kwargs)
        with patch.object(preparer.os,'open',side_effect=interrupted),self.assertRaises(OSError):
            preparer.install_create_only(archive,index,b'index',objects)
        self.assertEqual({x.name for x in archive.iterdir()},set(objects))
        before={x.name:x.stat().st_ino for x in archive.iterdir()}
        preparer.install_create_only(archive,index,b'index',objects)
        self.assertEqual(before,{name:(archive/name).stat().st_ino for name in objects})
        self.assertEqual((archive/index).read_bytes(),b'index')

    def test_archive_parent_symlink_is_rejected(self):
        actual=self.root/'actual';actual.mkdir()
        alias=self.root/'alias';alias.symlink_to(actual,target_is_directory=True)
        with self.assertRaises(OSError):
            preparer.install_create_only(alias/'archive','c'*64+'.history.json',b'index',{})
        self.assertEqual(list(actual.iterdir()),[])


if __name__=='__main__':unittest.main()
