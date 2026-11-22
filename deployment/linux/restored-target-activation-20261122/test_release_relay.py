"""Offline failure boundaries; only disposable files/processes and exact source functions."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parent

def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

a = load('relay_release_test_subject', 'release_relay.py')
b = load('relay_bootstrap_test_subject', 'relay_bootstrap.py')
g = load('relay_guard_test_subject', 'relay_runtime_guard.py')

def record():
    cycle = {'mode': 'apply', 'intents': {}, 'node_before': {'pid': 123, 'start_ticks': 4},
             'gate_before': {'state': 'open', 'epoch': 90}}
    return {'state': 'prepared', 'cycles': {'apply': cycle}}, cycle


class IntentTests(unittest.TestCase):
    def setUp(self):
        self.r, self.c = record()
        self.saved = []
        self.up = patch.object(a, 'update', lambda r: self.saved.append(copy.deepcopy(r)))
        self.up.start()
        self.addCleanup(self.up.stop)

    def test_command_unknown_after_timeout_never_repeats(self):
        execute = Mock(side_effect=subprocess.TimeoutExpired('fixed', 1, b'private-token-example'))
        with patch.object(a.subprocess, 'run', execute):
            with self.assertRaises(subprocess.TimeoutExpired):
                a.command_once(self.r, self.c, 'close', {}, ['fixed'], lambda: None)
            a.command_once(self.r, self.c, 'close', {}, ['fixed'], lambda: self.fail('must not reenter'))
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(self.c['intents']['close']['command_phase'], 'invocation_may_have_started')
        self.assertNotIn('private-token-example', json.dumps(self.saved))

    def test_definite_precondition_failure_may_be_rechecked_without_duplicate_action(self):
        execute = Mock(return_value=types.SimpleNamespace(returncode=0, stdout=b'ok', stderr=b''))
        with patch.object(a.subprocess, 'run', execute):
            with self.assertRaises(RuntimeError):
                a.command_once(self.r, self.c, 'close', {}, ['fixed'], lambda: a.require(False, 'hold'))
            self.assertEqual(execute.call_count, 0)
            a.command_once(self.r, self.c, 'close', {}, ['fixed'], lambda: None)
            a.command_once(self.r, self.c, 'close', {}, ['fixed'], lambda: self.fail('must not repeat'))
        self.assertEqual(execute.call_count, 1)

    def test_real_pidfd_signal_lost_ack_is_not_reissued(self):
        child = subprocess.Popen(['/usr/bin/python3', '-I', '-S', '-B', '-c', 'import time;time.sleep(20)'])
        self.addCleanup(lambda: (child.kill(), child.wait()) if child.poll() is None else None)
        expected = a.process(child.pid)
        actual_send = signal.pidfd_send_signal
        calls = []
        def send_then_lose(fd, sig, info, flags):
            calls.append(sig)
            actual_send(fd, sig, info, flags)
            raise OSError('synthetic acknowledgement loss')
        with patch.object(a.signal, 'pidfd_send_signal', send_then_lose):
            with self.assertRaises(OSError):
                a.signal_once(self.r, self.c, expected, lambda: None)
        self.assertEqual(child.wait(timeout=3), -signal.SIGTERM)
        with patch.object(a.signal, 'pidfd_send_signal', side_effect=AssertionError('duplicate signal')):
            a.signal_once(self.r, self.c, expected, lambda: self.fail('no repeated precondition'))
        self.assertEqual(calls, [signal.SIGTERM])
        self.assertTrue(a.original_absent(expected))

    def test_reused_pid_identity_fails_before_signal(self):
        with patch.object(a, 'process', return_value={'pid': 123, 'start_ticks': 999}),              patch.object(a.os, 'pidfd_open') as opened,              patch.object(a.signal, 'pidfd_send_signal') as sent:
            with self.assertRaises(RuntimeError):
                a.signal_once(self.r, self.c, self.c['node_before'], lambda: None)
        opened.assert_not_called()
        sent.assert_not_called()
        self.assertEqual(self.c['intents']['node_term']['command_phase'], 'precondition_failed')

    def test_intent_saved_before_external_command(self):
        def execute(*args, **kwargs):
            self.assertEqual(self.saved[-1]['cycles']['apply']['intents']['close']['command_phase'],
                             'invocation_may_have_started')
            return types.SimpleNamespace(returncode=0, stdout=b'private-example', stderr=b'')
        with patch.object(a.subprocess, 'run', execute):
            a.command_once(self.r, self.c, 'close', {}, ['fixed'], lambda: None)
        self.assertNotIn('private-example', json.dumps(self.saved))


class BarrierTests(unittest.TestCase):
    def setUp(self):
        self.nonce = 'a' * 64
        self.other = [{'table': {'family': 'ip', 'name': 'existing', 'handle': 1}}]
        self.owned = b.expected_readback(self.nonce)
        for index, item in enumerate(self.owned, 2):
            next(iter(item.values()))['handle'] = index

    def test_exact_owned_rule_and_handle_are_bound(self):
        proof = b.verify_owned(self.owned, self.nonce)
        self.assertEqual(proof['table_handle'], 2)
        self.assertEqual(proof['objects_sha256'], b.digest(self.owned))
        for mutate in ('port', 'uid', 'address', 'handle', 'comment', 'extra'):
            changed = copy.deepcopy(self.owned)
            if mutate == 'port': changed[2]['rule']['expr'][1]['match']['right'] = 8888
            elif mutate == 'uid': changed[2]['rule']['expr'][3]['match']['right'] = 999
            elif mutate == 'address': changed[2]['rule']['expr'][0]['match']['right'] = '0.0.0.0'
            elif mutate == 'handle': changed[0]['table']['handle'] = 0
            elif mutate == 'comment': changed[0]['table']['comment'] = 'another operator'
            else: changed.append({'chain': {'family': 'ip', 'table': b.TABLE, 'name': 'foreign', 'handle': 8}})
            with self.subTest(mutate=mutate), self.assertRaises(RuntimeError):
                b.verify_owned(changed, self.nonce)

    def fixture(self, rules):
        r, c = record()
        api = types.SimpleNamespace(MANIFEST_SHA=self.nonce,
            finished=Mock(side_effect=lambda r,c,key,proof: c['intents'][key].update(
                fulfilled_utc='now', proof=proof)), command_once=Mock())
        manifest = {'nft_before_sha256': b.digest(self.other)}
        barrier = b.Bootstrap(api, manifest, r, c)
        return barrier, c, api, patch.object(b, 'read_ruleset', return_value={'nftables': rules})

    def test_lost_seal_ack_observes_exact_table_without_second_command(self):
        fence, cycle, api, mocked = self.fixture(self.other + self.owned)
        cycle['intents']['nft_seal'] = {'command_phase': 'invocation_may_have_started'}
        with mocked:
            fence.seal(True)
        api.command_once.assert_not_called()
        self.assertTrue(cycle['intents']['nft_seal']['fulfilled_utc'])

    def test_unknown_seal_with_absent_table_holds_without_retry(self):
        fence, cycle, api, mocked = self.fixture(self.other)
        cycle['intents']['nft_seal'] = {'command_phase': 'invocation_may_have_started'}
        with mocked, self.assertRaises(RuntimeError):
            fence.seal(True)
        api.command_once.assert_not_called()

    def test_unknown_delete_observes_absence_and_never_deletes_another_table(self):
        fence, cycle, api, mocked = self.fixture(self.other)
        cycle['intents']['nft_unseal'] = {'command_phase': 'invocation_may_have_started'}
        with mocked:
            fence.unseal(True)
        api.command_once.assert_not_called()
        self.assertTrue(cycle['intents']['nft_unseal']['proof']['owned_table_absent'])

    def test_unknown_delete_with_remaining_table_holds_without_retry(self):
        fence, cycle, api, mocked = self.fixture(self.other + self.owned)
        cycle['intents']['nft_unseal'] = {'command_phase': 'invocation_may_have_started'}
        with mocked, self.assertRaises(RuntimeError):
            fence.unseal(True)
        api.command_once.assert_not_called()

    def test_preexisting_rule_drift_blocks_both_seal_and_removal(self):
        changed = copy.deepcopy(self.other)
        changed[0]['table']['name'] = 'replacement'
        fence, cycle, api, mocked = self.fixture(changed + self.owned)
        with mocked, self.assertRaises(RuntimeError):
            fence.snapshot(True)
        api.command_once.assert_not_called()

    def test_all_usable_keepalive_halfclosed_and_handshake_sockets_hold(self):
        ep = '0100007F:' + format(b.PORT, '04X')
        rows = [{'local': ep, 'remote': '0100007F:C001', 'state': state, 'uid': 0, 'inode': '1'}
                for state in ('01','02','03','04','05','08','09','0B')]
        self.assertEqual(len(b.usable_ingress(rows)), 8)
        self.assertEqual(b.usable_ingress([{**rows[0], 'state': s} for s in ('06','07','0A')]), [])
        # There is no "idle" or root socket exemption once a probe has returned.
        self.assertEqual(len(b.usable_ingress([rows[0]])), 1)

    def test_actual_runtime_binding_rejects_alternate_browser_paths_and_transport(self):
        values=dict(b.RUNTIME_PATHS)
        self.assertEqual(b.validate_runtime_values(values)['timeout_ms'],45000)
        changes=[('ABIT_ERP_CHATGPT_CDP_URL','http://127.0.0.1:9229'),
                 ('ABIT_ERP_CHATGPT_RELAY_HOST','0.0.0.0'),
                 ('ABIT_ERP_CHATGPT_PROJECT_CACHE','/unrelated/cache.json'),
                 ('CHATGPT_EXISTING_CHROME_USER_DATA_DIR','/unrelated/profile'),
                 ('ABIT_HERMES_PROXY_URL','https://unrelated.invalid'),
                 ('ABIT_HERMES_TIMEOUT_MS','999999')]
        for key,value in changes:
            with self.subTest(key=key),self.assertRaises(RuntimeError):
                b.validate_runtime_values({**values,key:value})

    def test_quiet_proof_resets_on_detached_outbound_work(self):
        r, cycle = record()
        expected = cycle['node_before']
        clock = [0.0]
        states = iter([{'usable_ingress':0,'node_network_work':0},
                       {'usable_ingress':0,'node_network_work':1},
                       {'usable_ingress':0,'node_network_work':0}])
        last = {'usable_ingress':0,'node_network_work':0}
        api = types.SimpleNamespace(owner=lambda m:None, preserve=lambda m:None, process=lambda pid:expected)
        fence = b.Bootstrap(api, {'hermes_transport':{'timeout_ms':1000,'origin_sha256':'x'}}, r, cycle)
        def state(pid):
            nonlocal last
            last = next(states, last)
            return last
        with patch.object(fence, 'snapshot'), patch.object(b, 'source_timeout',return_value={'timeout_ms':1000,'origin_sha256':'x'}),              patch.object(b, 'network_state',side_effect=state), patch.object(b.time,'monotonic',side_effect=lambda:clock[0]),              patch.object(b.time,'sleep',side_effect=lambda seconds:clock.__setitem__(0,clock[0]+seconds)):
            proof = fence.quiet(expected, lambda:None, deadline_seconds=5)
        self.assertGreaterEqual(clock[0], 3.5)
        self.assertFalse(proof['connections_forcibly_closed'])

    def test_persistent_usable_socket_hits_finite_hold(self):
        r, cycle = record()
        clock = [0.0]
        api = types.SimpleNamespace(owner=lambda m:None,preserve=lambda m:None,process=lambda pid:cycle['node_before'])
        fence = b.Bootstrap(api, {'hermes_transport':{'timeout_ms':1000,'origin_sha256':'x'}},r,cycle)
        with patch.object(fence,'snapshot'),patch.object(b,'source_timeout',return_value={'timeout_ms':1000,'origin_sha256':'x'}),              patch.object(b,'network_state',return_value={'usable_ingress':1,'node_network_work':0}),              patch.object(b.time,'monotonic',side_effect=lambda:clock[0]),              patch.object(b.time,'sleep',side_effect=lambda seconds:clock.__setitem__(0,clock[0]+seconds)):
            with self.assertRaisesRegex(RuntimeError,'remain'):
                fence.quiet(cycle['node_before'],lambda:None,deadline_seconds=1)
        self.assertLessEqual(clock[0], 1.25)


class CacheTests(unittest.TestCase):
    def test_only_exact_derived_catalog_expiry_is_accepted(self):
        value = {'updated_at':'2026-11-22T12:00:00Z','projects':[]}
        stamp = 1795348800
        self.assertEqual(g.valid_cache_expiry(json.dumps(value).encode(),stamp-1,stamp+1),value)
        variants = [{**value,'projects':[{'id':'authoritative-like'}]}, {**value,'owner':'foreign'},
                    {**value,'updated_at':'2026-11-21T12:00:00Z'}]
        for variant in variants:
            with self.subTest(variant=variant),self.assertRaises(RuntimeError):
                g.valid_cache_expiry(json.dumps(variant).encode(),stamp-1,stamp+1)


class CycleTests(unittest.TestCase):
    def setUp(self):
        self.r,self.c=record()
        self.events=[]
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fence=Mock()
        self.guard=Mock()
        self.guard.keeper_path.return_value=Path(self.temp.name)/'not-released'
        self.guard.readiness.return_value={'synthetic':'proof'}
        self.guard.release.return_value={'released_sha256':'x'}
        for key in ('seal','unseal','quiet','snapshot'):
            getattr(self.fence,key).side_effect=lambda *args,k=key,**kw:self.events.append(k)
        for key in ('acquire','readiness','before_signal','release','keeper_check'):
            getattr(self.guard,key).side_effect=lambda *args,k=key,**kw:self.events.append(k) or {'synthetic':'proof'}
        self.patches=[]
        def use(name,fn):
            p=patch.object(a,name,fn);p.start();self.patches.append(p)
        self.addCleanup(lambda:[p.stop() for p in reversed(self.patches)])
        use('barriers',lambda *args:(self.fence,self.guard))
        use('update',lambda r:None)
        use('owner',lambda m:None)
        use('preserve',lambda m:None)
        use('source',lambda *args:None)
        use('stage_check',lambda m:None)
        use('consistent',lambda *args:self.events.append('preserve'))
        self.verify=Mock(return_value={'node_after':'fixture'})
        use('verify_replacement',self.verify)
        use('control_receipt',lambda *args:{'state':'draining','epoch':91})
        use('install_source',lambda *args:self.events.append('install'))
        use('observe_replacement',lambda *args:self.events.append('observe'))
        def gate(m,r,c,key,issue):
            self.events.append(key)
            c['intents'][key]={'command_phase':'invocation_may_have_started','fulfilled_utc':'now'}
        use('gate_action',gate)
        def signal_once(r,c,node,before):
            before()
            self.events.append('signal')
            c['intents']['node_term']={'command_phase':'invocation_may_have_started'}
        use('signal_once',signal_once)

    def test_apply_orders_seal_lock_quiet_probe_before_install_and_one_signal(self):
        a.run_cycle({},self.r,self.c,True)
        relevant=[x for x in self.events if x in ('close','seal','acquire','quiet','readiness','install',
                                                 'before_signal','signal','observe','release','unseal','open')]
        self.assertEqual(relevant,['close','seal','acquire','quiet','readiness','install','before_signal',
                                   'signal','observe','release','unseal','open'])

    def test_unknown_original_signal_never_runs_probe_install_or_signal_again(self):
        self.c['intents']['node_term']={'command_phase':'invocation_may_have_started'}
        a.run_cycle({},self.r,self.c,True)
        self.assertNotIn('signal',self.events)
        self.assertNotIn('install',self.events)
        self.assertNotIn('readiness',self.events)
        self.assertIn('observe',self.events)

    def test_lost_unseal_ack_reconciles_late_phase_without_new_seal(self):
        self.c['intents']['nft_unseal']={'command_phase':'invocation_may_have_started'}
        self.c['sealed_preservation_verified']=True
        a.run_cycle({},self.r,self.c,True)
        self.assertEqual(self.events,['close','release','unseal','open'])
        self.assertEqual(self.guard.release.call_args.args,(False,))

    def test_finalize_observes_without_issuing_control_or_source_actions(self):
        self.c['intents']['close']={'command_phase':'invocation_may_have_started'}
        self.c['intents']['node_term']={'command_phase':'invocation_may_have_started','fulfilled_utc':'now'}
        a.run_cycle({},self.r,self.c,False)
        self.assertNotIn('signal',self.events)
        self.assertNotIn('install',self.events)
        self.assertNotIn('close',self.events)
        self.assertNotIn('release',self.events)
        self.assertEqual(self.fence.seal.call_args.args,(False,))

    def test_completed_apply_does_not_reseal_ordinary_work(self):
        self.c['intents']['open']={'command_phase':'invocation_may_have_started'}
        a.run_cycle({},self.r,self.c,True)
        self.assertEqual(self.events,['snapshot','open'])
        self.guard.acquire.assert_not_called()
        self.assertEqual(self.verify.call_args.kwargs,{'require_idle':False})


class RollbackAdmissionTests(unittest.TestCase):
    def test_unknown_original_signal_that_has_not_ceased_cannot_begin_new_transition(self):
        r,c=record();c['intents']['node_term']={'command_phase':'invocation_may_have_started'}
        with patch.object(a,'original_absent',return_value=False),patch.object(a,'update'):
            with self.assertRaisesRegex(RuntimeError,'unresolved'):
                a.begin_rollback({},r)
        self.assertNotIn('rollback',r['cycles'])

    def test_pre_signal_rollback_borrows_exact_barrier_and_lock_without_a_signal(self):
        r,c=record();c['intents'].update(close={'command_phase':'invocation_may_have_started'},
            nft_seal={'command_phase':'invocation_may_have_started','proof':{'table_handle':9}},
            browser_lock={'command_phase':'invocation_may_have_started'})
        with tempfile.TemporaryDirectory() as directory, patch.object(a,'STAGE',Path(directory)), \
             patch.object(a,'process',return_value=c['node_before']),patch.object(a,'control_receipt',return_value={'epoch':91}), \
             patch.object(a,'update'):
            rollback=a.begin_rollback({},r)
        self.assertTrue(rollback['no_node_transition'])
        self.assertTrue(rollback['borrowed_seal'])
        self.assertEqual(rollback['keeper_cycle'],'apply')
        self.assertNotIn('node_term',rollback['intents'])

    def test_rollback_does_not_borrow_keeper_when_spawn_is_proven_unissued(self):
        for phase in ('intent_saved','precondition_failed'):
            with self.subTest(phase=phase):
                r,c=record();c['intents'].update(close={'command_phase':'invocation_may_have_started'},
                    nft_seal={'command_phase':'invocation_may_have_started','proof':{'table_handle':9}},
                    browser_lock={'command_phase':phase})
                with tempfile.TemporaryDirectory() as directory,patch.object(a,'STAGE',Path(directory)), \
                     patch.object(a,'process',return_value=c['node_before']), \
                     patch.object(a,'control_receipt',return_value={'epoch':91}),patch.object(a,'update'):
                    rollback=a.begin_rollback({},r)
                self.assertTrue(rollback['no_node_transition'])
                self.assertTrue(rollback['borrowed_seal'])
                self.assertNotIn('keeper_cycle',rollback)
                self.assertNotIn('browser_lock',rollback['intents'])
                self.assertNotIn('node_term',rollback['intents'])

    def test_absent_replacement_can_only_restore_source_for_unchanged_watchdog(self):
        r,c=record();c['intents'].update(close={'command_phase':'invocation_may_have_started'},
            nft_seal={'command_phase':'invocation_may_have_started','proof':{'table_handle':9}},
            browser_lock={'command_phase':'invocation_may_have_started'},
            node_term={'command_phase':'invocation_may_have_started'})
        with tempfile.TemporaryDirectory() as directory,patch.object(a,'STAGE',Path(directory)), \
             patch.object(a,'original_absent',return_value=True),patch.object(a,'relay_current',side_effect=FileNotFoundError), \
             patch.object(a,'control_receipt',return_value={'epoch':91}),patch.object(a,'update'):
            rollback=a.begin_rollback({},r)
        self.assertTrue(rollback['watchdog_recovery'])
        self.assertIsNone(rollback['node_before'])
        self.assertNotIn('node_term',rollback['intents'])


class SuccessorTests(unittest.TestCase):
    def test_watchdog_recovery_persists_first_verified_successor(self):
        r,c=record();c.update(mode='rollback',watchdog_recovery=True,node_before=None)
        proof={'node_after':{'pid':456,'start_ticks':200}}
        with patch.object(a,'owner'),patch.object(a,'preserve'),patch.object(a,'update'), \
             patch.object(a,'verify_replacement',return_value=proof):
            self.assertEqual(a.observe_replacement({},r,c),proof)
        self.assertEqual(c['watchdog_after'],proof['node_after'])
        self.assertNotIn('node_term',c['intents'])

    def test_later_watchdog_process_is_not_the_recorded_replacement(self):
        r,c=record();c.update(mode='rollback',watchdog_recovery=True,node_before=None,
            source_complete_tick=100,watchdog_after={'pid':456,'start_ticks':200})
        with patch.object(a,'relay_current',return_value={'pid':999,'start_ticks':300}), \
             patch.object(a,'listener_owned') as listener,patch.object(a,'relay_transport') as transport:
            with self.assertRaisesRegex(RuntimeError,'successor changed'):
                a.verify_replacement({},c,require_idle=False)
        listener.assert_not_called();transport.assert_not_called()


class SourceRollbackTests(unittest.TestCase):
    def test_rollback_removes_only_exact_added_leaf_and_restores_known_preimage(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);live=root/'live';stage=root/'stage';live.mkdir();stage.mkdir()
            (stage/'before').mkdir();(stage/'after').mkdir()
            old=b'original';new=b'candidate';added=b'helper'
            targets={'new.cjs':(None,a.sha(added)),'main.cjs':(a.sha(old),a.sha(new))}
            (live/'new.cjs').write_bytes(added);(live/'main.cjs').write_bytes(new)
            (stage/'before/main.cjs').write_bytes(old)
            metadata=[os.getuid(),os.getgid(),'0o644']
            m={'runtime_files':{name:{'metadata':metadata,'parent_identity':[live.stat().st_dev,live.stat().st_ino]} for name in targets}}
            r={};c={'mode':'rollback','intents':{'close':{}}}
            with patch.object(a,'LIVE',live),patch.object(a,'STAGE',stage),patch.object(a,'TARGETS',targets),                  patch.object(a,'stage_check'),patch.object(a,'source'),patch.object(a,'control_receipt'),                  patch.object(a,'owner'),patch.object(a,'preserve'),patch.object(a,'update'):
                a.install_source(m,r,c)
                self.assertEqual((live/'main.cjs').read_bytes(),old)
                self.assertFalse((live/'new.cjs').exists())
                (live/'new.cjs').write_bytes(b'foreign bytes')
                with self.assertRaises(RuntimeError):
                    a.install_source(m,r,c)
                self.assertEqual((live/'new.cjs').read_bytes(),b'foreign bytes')

if __name__=='__main__':
    unittest.main(verbosity=2)
