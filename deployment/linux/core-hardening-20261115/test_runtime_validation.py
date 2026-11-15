"""Fourteen-leaf preparation boundaries using synthetic files only."""
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import test_apply_core_hardening as controls

m = controls.m
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('runtime_validation_subject', HERE / 'runtime_validation.py')
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)


def schema(**changes):
    value = {'$schema': v.JSON_SCHEMA_DRAFT, '$id': v.JSON_SCHEMA_ID,
             '$defs': {'sample': {'type': 'string'}}, 'type': 'object',
             'properties': {'sample': {'$ref': '#/$defs/sample'}},
             'additionalProperties': False}
    value.update(changes)
    return json.dumps(value).encode()


class RuntimeSyntaxGuards(controls.NoNetwork):
    def test_actual_pinned_schema_and_python_use_distinct_validators(self):
        actual = HERE.parents[2] / 'backend/open_webui' / v.JSON_SCHEMA_PATH
        self.assertEqual(v.validate_runtime(v.JSON_SCHEMA_PATH, actual.read_bytes()), 'RuntimeJSON')
        self.assertEqual(v.validate_runtime('utils/example.py', b'VALUE = 1\n'), 'RuntimePython')

    def test_python_compile_accepts_invalid_json_that_runtimejson_denies(self):
        # The former compile-only loop accepts trailing commas and bare names.
        invalid = b'{"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "https://abit.ai/schemas/governance_v1.schema.json", "$defs": {}, "additionalProperties": false,}'
        compile(invalid, v.JSON_SCHEMA_PATH, 'exec')
        with self.assertRaisesRegex(RuntimeError, 'INVALID_RUNTIME_JSON'):
            v.validate_runtime(v.JSON_SCHEMA_PATH, invalid)

    def test_duplicate_nonfinite_truncated_and_non_utf8_json_deny(self):
        for raw, expected in [
            (b'{"$defs":{},"$defs":{}}', 'DUPLICATE'),
            (b'{"value":NaN}', 'NONFINITE'),
            (b'{"value":Infinity}', 'NONFINITE'),
            (schema()[:-1] + b', "x-numeric":1e999}', 'NONFINITE'),
            (b'{"value":', 'INVALID_RUNTIME_JSON'),
            (b'\xff', 'INVALID_RUNTIME_JSON'),
        ]:
            with self.subTest(expected=expected), self.assertRaisesRegex(RuntimeError, expected):
                v.validate_runtime(v.JSON_SCHEMA_PATH, raw)

    def test_wrong_draft_id_and_schema_types_deny(self):
        for raw in [schema(**{'$schema': 'https://json-schema.org/draft-07/schema'}),
                    schema(**{'$id': 'https://foreign.invalid/schema'}),
                    schema(**{'$defs': []}), b'[]', schema(type=123),
                    schema(properties={'sample': {'maxLength': -1}})]:
            with self.subTest(raw_sha256=hashlib.sha256(raw).hexdigest()):
                with self.assertRaises(RuntimeError):
                    v.validate_runtime(v.JSON_SCHEMA_PATH, raw)

    def test_external_refs_deny_without_network_resolution(self):
        with patch.object(socket, 'create_connection', side_effect=AssertionError('NETWORK_DENIED')):
            for key in ('$ref', '$dynamicRef'):
                with self.subTest(key=key), self.assertRaisesRegex(RuntimeError, 'EXTERNAL_RUNTIME_JSON'):
                    v.validate_runtime(v.JSON_SCHEMA_PATH, schema(properties={'sample': {key: 'https://example.invalid/schema'}}))

    def test_local_refs_and_boolean_schema_values_remain_supported(self):
        self.assertEqual(v.validate_runtime(v.JSON_SCHEMA_PATH, schema()), 'RuntimeJSON')
        self.assertEqual(v.validate_runtime(v.JSON_SCHEMA_PATH, schema(**{'$defs': {'sample': True}})), 'RuntimeJSON')

    def test_wrong_kind_unsupported_path_and_oversized_source_deny(self):
        for name, raw, kind in [
            (v.JSON_SCHEMA_PATH, schema(), 'RuntimePython'),
            ('utils/example.py', b'VALUE = 1', 'RuntimeJSON'),
            ('utils/other.json', b'{}', None),
            ('../utils/example.py', b'VALUE = 1', None),
            ('/utils/example.py', b'VALUE = 1', None),
            ('utils//example.py', b'VALUE = 1', None),
            ('utils/example.py', b' ' * (v.MAX_RUNTIME_BYTES + 1), None),
        ]:
            with self.subTest(name=name, kind=kind), self.assertRaises(RuntimeError):
                v.validate_runtime(name, raw, kind)

    def test_invalid_python_is_rejected_without_execution(self):
        with self.assertRaisesRegex(RuntimeError, 'INVALID_RUNTIME_PYTHON'):
            v.validate_runtime('utils/example.py', b'def invalid(:')
        self.assertEqual(v.validate_runtime('utils/example.py', b'raise AssertionError("must not execute")\n'), 'RuntimePython')


class TypedCandidateGuards(controls.NoNetwork):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root / 'package'; self.package.mkdir()
        (self.package / 'runtime_validation.py').write_bytes((HERE / 'runtime_validation.py').read_bytes())
        self.manifest = controls.manifest()
        for item in self.manifest['files']:
            raw = schema() if item['runtime_kind'] == 'RuntimeJSON' else b'VALUE = 1\n'
            p = self.package / 'candidate/open_webui' / item['relative_path']
            p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(raw)
            item.update(candidate_sha256=m.sha(raw), candidate_bytes=len(raw))
        guard = patch.object(m, 'RUNTIME_VALIDATION', HERE / 'runtime_validation.py')
        guard.start(); self.addCleanup(guard.stop)

    def test_actual_controller_validates_thirteen_python_and_one_json_without_writes(self):
        before = {str(p): m.file_proof(p) for p in self.package.rglob('*') if p.is_file()}
        self.assertEqual(m.validate_runtime_candidates(self.package, self.manifest),
                         {'RuntimePython': 13, 'RuntimeJSON': 1})
        self.assertEqual(before, {str(p): m.file_proof(p) for p in self.package.rglob('*') if p.is_file()})
        self.assertEqual(len(m.TARGETS - m.NEW_FILES), 12)

    def test_controller_rejects_hash_bound_invalid_json_before_any_lifecycle_action(self):
        item = next(row for row in self.manifest['files'] if row['runtime_kind'] == 'RuntimeJSON')
        raw = b'{"bad": false,}'
        compile(raw, 'schema', 'exec')
        (self.package / 'candidate/open_webui' / item['relative_path']).write_bytes(raw)
        item.update(candidate_sha256=m.sha(raw), candidate_bytes=len(raw))
        self.denied(lambda: m.validate_runtime_candidates(self.package, self.manifest), 'INVALID_RUNTIME_JSON')

    def test_candidate_and_validator_tamper_are_rejected(self):
        item = self.manifest['files'][0]
        path = self.package / 'candidate/open_webui' / item['relative_path']
        original = path.read_bytes(); path.write_bytes(original + b' ')
        self.denied(lambda: m.validate_runtime_candidates(self.package, self.manifest), 'PINNED_INPUT')
        path.write_bytes(original)
        (self.package / 'runtime_validation.py').write_bytes(b'raise AssertionError("must never execute")')
        self.denied(lambda: m.validate_runtime_candidates(self.package, self.manifest), 'PINNED_INPUT')

    def test_manifest_kind_counts_and_validator_pin_cannot_be_relabelled(self):
        value = controls.manifest()
        row = next(x for x in value['files'] if x['runtime_kind'] == 'RuntimeJSON')
        row['runtime_kind'] = 'RuntimePython'
        self.denied(lambda: m.validate_manifest(value, controls.plan()), 'RUNTIME_KIND')
        for key, invalid in [('runtime_kind_counts', {'RuntimePython': 14, 'RuntimeJSON': 0}),
                             ('runtime_validator_sha256', '0' * 64)]:
            value = controls.manifest(); value[key] = invalid
            self.denied(lambda: m.validate_manifest(value, controls.plan()), 'RUNTIME_MANIFEST')

    def test_schema_install_preserves_exact_existing_metadata(self):
        runtime = self.root / 'runtime'
        target = runtime / m.RUNTIME_JSON_PATH
        target.parent.mkdir(parents=True); target.write_bytes(schema())
        target.chmod(0o640)
        old = 1_400_000_000_123_456_789; os.utime(target, ns=(old, old))
        raw = schema(title='Changed source schema')
        before = m.file_proof(target)
        with patch.object(m, 'RUNTIME', runtime):
            m.atomic_existing_leaf(target, raw, m.parent_proof(target.parent), before, m.sha(raw))
        self.assertEqual(m.file_proof(target), {**before, 'sha256': m.sha(raw), 'size': len(raw)})
        self.assertEqual(target.stat().st_mtime_ns, old)

    def test_schema_does_not_select_or_touch_similarly_named_python_caches(self):
        runtime = self.root / 'runtime'
        target = runtime / m.RUNTIME_JSON_PATH
        target.parent.mkdir(parents=True)
        target.write_bytes(schema())
        cache = target.parent / '__pycache__'; cache.mkdir()
        unrelated = cache / 'governance_v1.schema.cpython-312.pyc'; unrelated.write_bytes(b'unrelated cache')
        legacy = target.with_suffix('.pyc'); legacy.write_bytes(b'unrelated legacy')
        before = [m.file_proof(p) for p in (unrelated, legacy)]
        with patch.object(m, 'RUNTIME', runtime):
            self.assertEqual(m.selected_caches([target]), [])
            self.assertEqual(m.quarantine_bytecode([target], self.root / 'archive', lambda *a: None), [])
            self.denied(lambda: m.selected_caches([target.with_name('foreign.json')]), 'PYTHON_ONLY')
        self.assertEqual([m.file_proof(p) for p in (unrelated, legacy)], before)


class RetainedFormatCaptureGuards(controls.NoNetwork):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'capture'; self.root.mkdir(mode=0o750); self.root.chmod(0o750)
        self.raw = b'Unchanged synthetic v1 capture; preservation only.'
        self.path = self.root / (m.FORMAT_CAPTURE_INVOCATION + '.json')
        self.path.write_bytes(self.raw); self.path.chmod(0o640)
        for name, value in [('FORMAT_CAPTURE_ROOT', self.root),
                            ('FORMAT_CAPTURE_SHA', m.sha(self.raw)),
                            ('FORMAT_CAPTURE_BYTES', len(self.raw))]:
            guard = patch.object(m, name, value); guard.start(); self.addCleanup(guard.stop)
        file_proof, parent_proof = m.file_proof, m.parent_proof
        # Only ownership is projected; real bytes, modes, links and metadata are read.
        for name, value in [('file_proof', lambda path: {**file_proof(path), 'uid': 0, 'gid': 988}),
                            ('parent_proof', lambda path: {**parent_proof(path), 'uid': 0, 'gid': 988})]:
            guard = patch.object(m, name, value); guard.start(); self.addCleanup(guard.stop)
        capture = controls.format_capture()
        capture.update(parent_metadata=m.parent_proof(self.root), file_metadata=m.file_proof(self.path))
        self.baseline = {'returned_reply_format_capture': capture,
                         'protected_configuration': {str(self.path): deepcopy(capture['file_metadata'])}}

    def test_capture_is_retained_and_backed_up_without_becoming_authority(self):
        before = deepcopy(self.baseline)
        output = Path(self.temp.name) / 'operation'; output.mkdir(mode=0o700)
        def private_new(path, raw):
            with path.open('xb') as stream:
                stream.write(raw)
            path.chmod(0o600)
        record = Mock()
        result = m.backup_format_capture(self.baseline, types.SimpleNamespace(private_new=private_new), output, record)
        self.assertEqual(result['purpose'], 'preservation_only')
        self.assertIs(result['grants_recovery_authority'], False)
        self.assertEqual((output / 'returned-reply-format-capture.private').read_bytes(), self.raw)
        self.assertEqual(self.path.read_bytes(), self.raw)
        self.assertEqual(self.baseline, before)
        record.assert_called_once_with('returned-reply-format-capture.json', result)
        with self.assertRaises(FileExistsError):
            m.backup_format_capture(self.baseline, types.SimpleNamespace(private_new=private_new), output, record)
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_absent_or_unreviewed_capture_denies(self):
        self.denied(lambda: m.validate_format_capture({}), 'EXACT_RETAINED')
        self.path.unlink()
        with self.assertRaises(FileNotFoundError):
            m.protected_format_capture(self.baseline)

    def test_changed_capture_bytes_deny_without_copying(self):
        self.path.write_bytes(b'external change')
        writer = Mock()
        self.denied(lambda: m.backup_format_capture(self.baseline, types.SimpleNamespace(private_new=writer),
                     Path(self.temp.name), Mock()), 'INPUT_CHANGED')
        writer.assert_not_called()
        self.assertEqual(self.path.read_bytes(), b'external change')

    def test_symlink_and_hardlink_capture_deny_without_removing_them(self):
        self.path.unlink()
        outside = Path(self.temp.name) / 'outside'; outside.write_bytes(self.raw)
        self.path.symlink_to(outside)
        self.denied(lambda: m.protected_format_capture(self.baseline), 'CANONICAL')
        self.assertTrue(self.path.is_symlink())
        self.path.unlink(); os.link(outside, self.path)
        self.denied(lambda: m.protected_format_capture(self.baseline), 'SINGLE_LINK')
        self.assertEqual(outside.read_bytes(), self.raw)

    def test_parent_metadata_drift_denies(self):
        self.root.chmod(0o700)
        self.denied(lambda: m.protected_format_capture(self.baseline), 'PARENT_CHANGED')

    def test_v2_upgrade_authority_claim_foreign_identity_or_bad_protection_denies(self):
        cases = []
        for key, value in [('schema_version', True), ('schema_version', 2),
                           ('purpose', 'returned-reply-format.v2'), ('grants_recovery_authority', True),
                           ('invocation_id', 'foreign'), ('path', '/tmp/foreign.json')]:
            copy = deepcopy(self.baseline); copy['returned_reply_format_capture'][key] = value; cases.append(copy)
        for key, value in [('sha256', '0' * 64), ('size', len(self.raw) + 1), ('uid', 999), ('mode', 0o644)]:
            copy = deepcopy(self.baseline); copy['returned_reply_format_capture']['file_metadata'][key] = value; cases.append(copy)
        copy = deepcopy(self.baseline); copy['protected_configuration'] = {}; cases.append(copy)
        for value in cases:
            with self.subTest(case=len(cases)):
                self.denied(lambda: m.validate_format_capture(value))

    def test_change_during_backup_holds_and_preserves_external_bytes(self):
        output = Path(self.temp.name) / 'operation'; output.mkdir()
        def writer(path, raw):
            path.write_bytes(raw)
            self.path.write_bytes(b'external concurrent bytes')
        record = Mock()
        self.denied(lambda: m.backup_format_capture(self.baseline, types.SimpleNamespace(private_new=writer),
                                                   output, record), 'INPUT_CHANGED')
        self.assertEqual((output / 'returned-reply-format-capture.private').read_bytes(), self.raw)
        self.assertEqual(self.path.read_bytes(), b'external concurrent bytes')
        record.assert_not_called()


CONTRACT_PAIR = frozenset({'utils/agentic_contracts/developer_delivery.py',
                          'utils/agentic_contracts/schemas/governance_v1.schema.json'})


def pair_manifest():
    value = controls.manifest()
    for row in value['files']:
        if row['relative_path'] in CONTRACT_PAIR:
            row['replacement_required'] = False
            row['original_sha256'] = row['candidate_sha256']
            row['live_metadata']['size'] = row['candidate_bytes']
    value.update(replacement_count=12, existing_replacement_count=10, preserved_original_count=137,
                 already_current_selected_count=2,
                 already_current_selected_paths=sorted(CONTRACT_PAIR))
    return value


class VerifiedContractPairGuards(controls.NoNetwork):
    def test_after_cutover_exact_both_contract_inputs_are_accepted_readonly(self):
        value = pair_manifest(); before = deepcopy(value)
        self.assertIs(m.validate_manifest(value, controls.plan()), value)
        self.assertEqual(value, before)

    def test_no_prior_capacity_cutover_retains_all_fourteen_replacements(self):
        value = controls.manifest()
        self.assertIs(m.validate_manifest(value, controls.plan()), value)
        self.assertEqual(value['replacement_count'], 14)
        self.assertEqual(value['already_current_selected_paths'], [])

    def test_one_contract_or_any_other_already_current_leaf_denies(self):
        for paths in [{next(iter(CONTRACT_PAIR))}, {'utils/agentic_executor.py'},
                      set(CONTRACT_PAIR) | {'utils/agentic_executor.py'}]:
            value = controls.manifest()
            for row in value['files']:
                if row['relative_path'] in paths:
                    row.update(replacement_required=False, original_sha256=row['candidate_sha256'])
                    row['live_metadata']['size'] = row['candidate_bytes']
            value.update(replacement_count=14-len(paths), existing_replacement_count=12-len(paths),
                         preserved_original_count=135+len(paths), already_current_selected_count=len(paths),
                         already_current_selected_paths=sorted(paths))
            self.denied(lambda: m.validate_manifest(value, controls.plan()), 'ALREADY_CURRENT')

    def test_same_hash_pair_with_wrong_count_metadata_or_missing_hash_denies(self):
        for mutation in ('count', 'paths', 'original', 'size', 'flag'):
            value = pair_manifest()
            row = next(row for row in value['files'] if row['relative_path'] in CONTRACT_PAIR)
            if mutation == 'count':
                value['replacement_count'] = 14
            elif mutation == 'paths':
                value['already_current_selected_paths'].pop()
            elif mutation == 'original':
                row['original_sha256'] = '0' * 64
            elif mutation == 'size':
                row['live_metadata']['size'] += 1
            else:
                row['replacement_required'] = 0
            with self.subTest(mutation=mutation):
                self.denied(lambda: m.validate_manifest(value, controls.plan()))

    def test_verified_pair_is_not_written_and_its_python_cache_is_not_quarantined(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); runtime = root / 'runtime'; package = root / 'package'
            value = pair_manifest(); baseline = {'core_files': {}}
            before = {}
            for item in value['files']:
                if item['relative_path'] not in CONTRACT_PAIR:
                    continue
                name = item['relative_path']; raw = schema() if name.endswith('.json') else b'VALUE = 90091\n'
                target = runtime / name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw)
                os.utime(target, ns=(1_400_000_000_123_456_789,) * 2)
                candidate = package / 'candidate/open_webui' / name
                candidate.parent.mkdir(parents=True, exist_ok=True); candidate.write_bytes(raw)
                pin = m.file_proof(target); baseline['core_files'][str(target)] = pin; before[name] = pin
                item.update(original_sha256=m.sha(raw), candidate_sha256=m.sha(raw), candidate_bytes=len(raw),
                            live_metadata={key: val for key, val in pin.items() if key != 'sha256'},
                            parent_metadata=m.parent_proof(target.parent))
            python_target = runtime / 'utils/agentic_contracts/developer_delivery.py'
            cache = python_target.parent / '__pycache__'; cache.mkdir()
            cached = cache / 'developer_delivery.cpython-312.pyc'; cached.write_bytes(b'existing valid cache')
            cached_before = m.file_proof(cached)
            records = []
            with patch.object(m, 'RUNTIME', runtime), \
                 patch.object(m, 'atomic_existing_leaf', side_effect=AssertionError('NO REPLACEMENT')), \
                 patch.object(m, 'atomic_new_leaf', side_effect=AssertionError('NO CREATION')), \
                 patch.object(m, 'prepare_temp', side_effect=AssertionError('NO TEMP WRITE')):
                targets = m.replacement_targets(value)
                self.assertEqual(len(targets), 12)
                self.assertFalse(set(targets) & {runtime / name for name in CONTRACT_PAIR})
                self.assertEqual(m.quarantine_bytecode(targets, root / 'cache-archive', lambda *a: None), [])
                for index, item in enumerate(value['files']):
                    if item['relative_path'] in CONTRACT_PAIR:
                        self.assertIs(m.install_runtime_leaf(package, item, index, baseline,
                                     lambda name, receipt: records.append((name, receipt))), False)
            self.assertEqual(before, {name: m.file_proof(runtime / name) for name in CONTRACT_PAIR})
            self.assertEqual(cached_before, m.file_proof(cached))
            self.assertEqual(len(records), 2)
            self.assertTrue(all(name.endswith('-verified-existing.json') for name, _ in records))

    def test_verified_pair_metadata_drift_denies_without_overwriting_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / 'runtime'
            name = 'utils/agentic_contracts/developer_delivery.py'
            target = runtime / name; target.parent.mkdir(parents=True); target.write_bytes(b'VALUE = 90091\n')
            pin = m.file_proof(target)
            item = {'relative_path': name, 'replacement_required': False, 'new_file': False,
                    'original_sha256': pin['sha256'], 'candidate_sha256': pin['sha256'],
                    'candidate_bytes': pin['size'],
                    'live_metadata': {key: val for key, val in pin.items() if key != 'sha256'},
                    'parent_metadata': m.parent_proof(target.parent)}
            baseline = {'core_files': {str(target): pin}}
            target.chmod(0o600)
            with patch.object(m, 'RUNTIME', runtime):
                self.denied(lambda: m.verify_already_current(item, baseline), 'ALREADY_CURRENT')
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)


    def test_pair_retains_original_metadata_through_full_149_file_readback(self):
        fixture = controls.CohortAndEnvironmentGuards('test_complete_original147_cohort_passes')
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        value = fixture.manifest; before = {}
        for item in value['files']:
            target = fixture.runtime / item['relative_path']
            if item['relative_path'] in CONTRACT_PAIR:
                pin = m.file_proof(target); before[item['relative_path']] = pin
                item.update(replacement_required=False, new_file=False,
                            original_sha256=pin['sha256'], candidate_sha256=pin['sha256'],
                            candidate_bytes=pin['size'],
                            live_metadata={key: val for key, val in pin.items() if key != 'sha256'})
        self.assertTrue(m.source_cohort(fixture.baseline, value))
        for item in value['files']:
            if not item['replacement_required']:
                continue
            target = fixture.runtime / item['relative_path']
            metadata = item['new_metadata'] if item['new_file'] else item['live_metadata']
            target.write_bytes(b'{"fixture":2}\n' if target.suffix == '.json' else b'VALUE = 2\n')
            target.chmod(metadata['mode'])
            os.utime(target, ns=(metadata['mtime_ns'], metadata['mtime_ns']))
        self.assertTrue(m.source_cohort(fixture.baseline, value, installed=True))
        self.assertEqual(before, {name: m.file_proof(fixture.runtime / name) for name in CONTRACT_PAIR})
        target = fixture.runtime / next(iter(CONTRACT_PAIR))
        os.utime(target, ns=(target.stat().st_mtime_ns + 1,) * 2)
        self.denied(lambda: m.source_cohort(fixture.baseline, value, installed=True), 'METADATA')


if __name__ == '__main__':
    unittest.main(verbosity=2)
