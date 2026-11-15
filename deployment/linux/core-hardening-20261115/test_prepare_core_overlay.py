"""Pure bounded preparation guards; no application import, network or live writes."""
from pathlib import Path
import hashlib
import importlib.util
import socket
import tempfile
import unittest
from unittest.mock import patch


class DependencyClosure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source=Path(__file__).with_name('prepare_core_overlay.py')
        spec=importlib.util.spec_from_file_location('prepared_core_overlay',source)
        cls.builder=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.builder)

    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        (self.root/'utils').mkdir()
        for name in ('connect','connect_ex'):
            guard=patch.object(socket.socket,name,side_effect=AssertionError('Network denied'))
            guard.start();self.addCleanup(guard.stop)
        guard=patch.object(socket,'getaddrinfo',side_effect=AssertionError('DNS denied'))
        guard.start();self.addCleanup(guard.stop)

    def test_namespace_package_and_exact_supplied_module_are_available(self):
        original={'utils/existing.py':b'VALUE = 1\n'}
        values={'utils/new.py':b'from open_webui.utils import existing\n'}
        receipt=self.builder.dependency_closure(values,original,self.root)
        self.assertTrue(receipt['new_application_imports_all_available'])
        self.assertEqual(set(receipt['supplied_reachable_modules']),{'utils/new.py','utils/existing.py'})
        self.assertFalse(receipt['unchanged_missing_dependencies'])
        self.assertTrue(all(v['supplied_by_fixture']for v in receipt['new_import_edges']))

    def test_missing_direct_module_cannot_enter_the_candidate(self):
        with self.assertRaisesRegex(ValueError,'Unavailable new application import'):
            self.builder.dependency_closure(
                {'utils/new.py':b'from open_webui.utils.absent import value\n'}, {},self.root)

    def test_missing_namespace_child_cannot_be_misread_as_a_package_symbol(self):
        with self.assertRaisesRegex(ValueError,'Unavailable new application import'):
            self.builder.dependency_closure(
                {'utils/new.py':b'from open_webui.utils import absent\n'}, {},self.root)

    def test_excluded_template_resolver_is_denied_even_if_a_source_is_supplied(self):
        with self.assertRaisesRegex(ValueError,'Excluded profile-template'):
            self.builder.dependency_closure(
                {'utils/new.py':b'from open_webui.utils.agentic_project_profiles import resolve\n'},
                {'utils/agentic_project_profiles.py':b'def resolve(): pass\n'},self.root)

    def test_preserved_external_boundary_is_hashed_without_executing_it(self):
        raw=b'raise AssertionError("application source must never execute")\n'
        (self.root/'external.py').write_bytes(raw)
        original={'utils/existing.py':b'import open_webui.external\nVALUE = 1\n'}
        values={'utils/existing.py':b'import open_webui.external\nVALUE = 2\n'}
        receipt=self.builder.dependency_closure(values,original,self.root)
        self.assertEqual(receipt['preserved_external_boundaries']['external.py']['sha256'],
                         hashlib.sha256(raw).hexdigest())
        self.assertFalse(receipt['new_import_edges'])

    def test_new_external_dependency_requires_a_reviewed_cohort_expansion(self):
        (self.root/'external.py').write_bytes(b'VALUE = 1\n')
        with self.assertRaisesRegex(ValueError,'outside the reviewed fixture cohort'):
            self.builder.dependency_closure(
                {'utils/new.py':b'import open_webui.external\n'}, {},self.root)

    def test_symlink_dependency_cannot_escape_the_fixed_runtime(self):
        with tempfile.TemporaryDirectory() as other:
            outside=Path(other)/'outside.py';outside.write_bytes(b'VALUE = 1\n')
            (self.root/'utils/escape.py').symlink_to(outside)
            with self.assertRaisesRegex(ValueError,'outside fixed runtime'):
                self.builder.dependency_closure(
                    {'utils/new.py':b'import open_webui.utils.escape\n'}, {},self.root)


if __name__=='__main__':
    unittest.main(verbosity=2)
