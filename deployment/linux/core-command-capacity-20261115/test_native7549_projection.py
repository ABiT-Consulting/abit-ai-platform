"""Finite source-only fixtures for the exact retained7549 Core projection."""
import ast
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import types
import unittest

HERE = Path(__file__).resolve().parent
FIXTURE_SHA = '461c0dee654c3a373c10b6cc0a0c11ac049b50c8b8fb27f2dd44a411951f5a4e'
spec = importlib.util.spec_from_file_location('native7549_projection_subject', HERE / 'project_native7549.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
PURE_CALL = False


def audit(event, args):
    if event in {'subprocess.Popen', 'os.system', 'os.posix_spawn',
                 'socket.connect', 'socket.getaddrinfo'}:
        raise AssertionError('NETWORK_OR_PROCESS_DENIED')
    if event == 'open' and args and isinstance(args[0], (str, bytes, os.PathLike)):
        value = os.fsdecode(args[0])
        if value.startswith(('/var/lib/', '/run/', '/proc/', '/sys/',
                             '/srv/abit-dev-linux/abit-ai/data/',
                             '/srv/abit-dev-linux/abit-ai/current/',
                             '/srv/abit-dev-linux/abit-ai/releases/')):
            raise AssertionError('LIVE_STATE_OPEN_DENIED')
    if PURE_CALL and event in {'open', 'os.mkdir', 'os.remove', 'os.rmdir', 'os.rename',
                              'os.chmod', 'os.chown', 'os.utime', 'os.link', 'os.symlink'}:
        raise AssertionError('PURE_PROJECTION_IO_DENIED')


sys.addaudithook(audit)


@contextmanager
def pure_call():
    global PURE_CALL
    previous = PURE_CALL
    PURE_CALL = True
    try:
        yield
    finally:
        PURE_CALL = previous


class ExactProjection(unittest.TestCase):
    def setUp(self):
        fixture_raw = (HERE / 'fixtures/native7549-inputs.json').read_bytes()
        self.assertEqual(hashlib.sha256(fixture_raw).hexdigest(), FIXTURE_SHA)
        self.inputs = {key: value.encode() for key, value in json.loads(fixture_raw)['inputs'].items()}
        self.controller_source = (HERE / 'apply_command_capacity.py').read_bytes()
        self.assertEqual(m.sha(self.controller_source), m.CONTROLLER_SHA256)
        self.native_fixed, self.channel_fixed = m.fixed_contract(self.controller_source)
        wanted = {'need', 'sha', 'canonical', 'strict_json', 'native_positive'}
        nodes = [node for node in ast.parse(self.controller_source).body
                 if isinstance(node, ast.FunctionDef) and node.name in wanted]
        self.assertEqual({node.name for node in nodes}, wanted)
        self.consumer = {'Path': Path, 'json': json, 're': re, 'hashlib': hashlib,
                         'NATIVE_FIXED': self.native_fixed, 'CHANNEL_FIXED': self.channel_fixed}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'exact_capacity_native_consumer', 'exec'),
             self.consumer)

    def build(self):
        with pure_call():
            return m.project(self.controller_source, self.inputs)

    def consume(self, files, binding):
        values = {
            binding['path']: files['native-publication.core-projection.json'],
            binding['channel_path']: files[m.FILENAMES['channel']],
        }
        calls = []
        def read(path, pin, private=False):
            self.assertTrue(private)
            raw = values[str(path)]
            self.assertEqual(m.sha(raw), pin)
            calls.append(str(path))
            return raw
        with pure_call():
            result = self.consumer['native_positive'](types.SimpleNamespace(read=read),
                                                       {'native_publication': binding})
        return result, calls

    def test_actual_retained_projection_passes_exact_existing_core_consumer(self):
        files, binding, manifest = self.build()
        result, calls = self.consume(files, binding)
        self.assertEqual(result, {'publication_sha256': binding['sha256'],
                                  'channel_sha256': m.INPUT_SHA256['channel']})
        self.assertEqual(calls, [binding['path'], binding['channel_path']])
        self.assertEqual(set(binding['expected']['services']), {'backend', 'governance', 'scheduler'})
        self.assertFalse(manifest['activation_authority'])

    def test_unprojected_raw_publication_is_not_silently_accepted(self):
        files, binding, _ = self.build()
        files['native-publication.core-projection.json'] = self.inputs['publication']
        binding['sha256'] = m.sha(self.inputs['publication'])
        with self.assertRaisesRegex(RuntimeError, 'NATIVE_POSITIVE_PUBLICATION_DRIFT'):
            self.consume(files, binding)

    def test_container_alias_or_both_aliases_still_fail_exact_core_consumer(self):
        for duplicate in (False, True):
            with self.subTest(duplicate=duplicate):
                files, binding, _ = self.build()
                projected = json.loads(files['native-publication.core-projection.json'])
                value = projected['services']['governance']
                if not duplicate:
                    del projected['services']['governance']
                projected['services']['agentic-governance-worker'] = deepcopy(value)
                binding['expected']['services'] = deepcopy(projected['services'])
                files['native-publication.core-projection.json'] = m.canonical(projected)
                binding['sha256'] = m.sha(files['native-publication.core-projection.json'])
                with self.assertRaisesRegex(RuntimeError, 'EXACT_THREE_PUBLISHED_NATIVE_SERVICES'):
                    self.consume(files, binding)

    def test_original_bytes_and_all_other_publication_fields_are_preserved(self):
        files, _, _ = self.build()
        for name, raw in self.inputs.items():
            self.assertEqual(files[m.FILENAMES[name]], raw)
        original = json.loads(self.inputs['publication'])
        projected = json.loads(files['native-publication.core-projection.json'])
        excluded = {'services', 'channel_new_start_receipt_path', 'projection_provenance'}
        self.assertEqual({k: v for k, v in projected.items() if k not in excluded},
                         {k: v for k, v in original.items() if k not in excluded})
        self.assertEqual(projected['original_f33'], original['original_f33'])
        self.assertEqual(projected['channel_new_start_receipt_sha256'], m.INPUT_SHA256['channel'])
        channel = json.loads(files[m.FILENAMES['channel']])
        self.assertEqual(channel['physical_explicit_Start_calls_total'], 4)
        self.assertEqual(channel['Start_calls_in_this_observation'], 0)

    def test_unknown_history_and_loaded_worker_private_spec_provenance_remain(self):
        files, _, _ = self.build()
        projected = json.loads(files['native-publication.core-projection.json'])
        provenance = projected['projection_provenance']
        review = json.loads(self.inputs['worker_review'])
        self.assertEqual(provenance['history_qualification'], review['history_limits'])
        self.assertFalse(provenance['history_qualification']['history_unchanged_since7533'])
        self.assertEqual(provenance['history_qualification']['row_field_and_cause_attribution'], 'unknown')
        self.assertEqual(provenance['coverage_provenance']['loaded_contract'], review['7545']['loaded_contract'])
        self.assertEqual(provenance['coverage_provenance']['worker'], review['7545']['worker'])
        self.assertFalse(provenance['current_native_observation'])
        self.assertFalse(provenance['history_policy_adopted'])
        original = json.loads(self.inputs['publication'])
        for row in provenance['service_mapping']:
            self.assertEqual(row['source_fields'], original['services'][row['source_role']])
            self.assertEqual(row['projected_fields'], projected['services'][row['core_role']])
            self.assertIn('private_spec_sha256', row['source_fields'])

    def test_changed_missing_extra_nonbytes_or_oversize_input_is_denied(self):
        for name in self.inputs:
            values = dict(self.inputs)
            values[name] += b' '
            with self.subTest(changed=name), pure_call(), self.assertRaisesRegex(ValueError, 'EXACT_RETAINED_INPUT'):
                m.project(self.controller_source, values)
        for change in ('missing', 'extra', 'str', 'oversize'):
            values = dict(self.inputs)
            if change == 'missing':
                del values['channel']
            elif change == 'extra':
                values['foreign'] = b'{}'
            elif change == 'str':
                values['channel'] = values['channel'].decode()
            else:
                values['channel'] = b' ' * (m.MAX_INPUT_BYTES + 1)
            with self.subTest(change=change), pure_call(), self.assertRaises(ValueError):
                m.project(self.controller_source, values)

    def test_changed_controller_is_rejected_before_constant_execution(self):
        for raw in (self.controller_source + b'\n', b"raise AssertionError('EXECUTED')",
                    self.controller_source.decode(), b' ' * (m.MAX_INPUT_BYTES + 1)):
            with self.subTest(kind=type(raw).__name__), pure_call(), self.assertRaisesRegex(ValueError, 'EXACT_CONTROLLER_SOURCE'):
                m.project(raw, self.inputs)

    def test_local_retention_is_only_a_bound_create_only_proposal(self):
        files, binding, manifest = self.build()
        archive = Path(manifest['archive_directory'])
        self.assertEqual(archive.parent.as_posix(), str(m.ARCHIVE_PARENT))
        self.assertEqual(Path(binding['channel_path']), archive / m.FILENAMES['channel'])
        self.assertNotEqual(binding['channel_path'],
                            json.loads(self.inputs['publication'])['channel_new_start_receipt_path'])
        for name, proof in manifest['files'].items():
            self.assertEqual(proof, {'sha256': m.sha(files[name]), 'bytes': len(files[name])})
        self.assertFalse(manifest['root_archive_created'])
        self.assertFalse(manifest['release_plan_created'])
        self.assertEqual(manifest['directory_requires']['mode'], 0o700)
        self.assertEqual(manifest['files_require'], {
            'uid': 0, 'gid': 0, 'mode': 0o600, 'nlink': 1,
            'regular': True, 'canonical_no_symlink': True})
        self.assertEqual(manifest['create_only_retention']['existing_archive_or_file'],
                         'HOLD_OBSERVE_ONLY_NO_OVERWRITE_OR_MERGE')
        self.assertEqual(manifest['create_only_retention']['creation_helper_sha256'],
                         m.HISTORY_HELPER_SHA256)
        self.assertEqual(manifest['native_or_core_actions'], 0)

    def test_projection_is_deterministic_without_io_or_input_mutation(self):
        before = dict(self.inputs)
        first = self.build()
        self.assertEqual(first, self.build())
        self.assertEqual(self.inputs, before)
        files, binding, _ = first
        self.assertEqual(json.loads(files['native-publication.binding-fragment.json']), binding)
        self.assertNotIn('initial_gate', binding)
        self.assertNotIn('preserved_state', binding)
        self.assertNotIn('source_commit', binding)

    def test_duplicate_and_nonfinite_json_remain_rejected(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'[]', b' ' * (m.MAX_INPUT_BYTES + 1)):
            with self.subTest(raw=raw[:30]), self.assertRaises(ValueError):
                m.strict_json(raw)


if __name__ == '__main__':
    unittest.main()
