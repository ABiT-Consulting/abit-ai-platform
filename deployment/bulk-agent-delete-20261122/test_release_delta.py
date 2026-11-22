"""Offline checks of the exact frontend delta and rollback; no production I/O."""
from pathlib import Path
import importlib.util
import tempfile
import unittest
p=Path(__file__).with_name("deploy_additive.py")
spec=importlib.util.spec_from_file_location("bulk_release",p)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
class ReleaseDelta(unittest.TestCase):
    def test_exact_delta_and_existing_customizations_preserved(self):
        before=b'<html>\r\n<head><script src="/existing.js"></script></head><body>Existing</body></html>'
        after=module.add_tag(before)
        self.assertEqual(after.replace(module.TAG,b"",1),before)
        self.assertEqual(module.remove_tag(after),before)
    def test_rollback_preserves_a_later_unrelated_customization(self):
        before=b'<html><head></head><body></body></html>'
        after=module.add_tag(before).replace(b'</body>',b'<div>Later change</div></body>')
        self.assertEqual(module.remove_tag(after),before.replace(b'</body>',b'<div>Later change</div></body>'))
    def test_duplicate_or_ambiguous_shell_rejected(self):
        with self.assertRaises(ValueError):module.add_tag(module.add_tag(b'<head></head>'))
        with self.assertRaises(ValueError):module.add_tag(b'<head></head></head>')
        with self.assertRaises(ValueError):module.remove_tag(b'<head></head>')
    def test_source_drift_is_rejected_before_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"index.html"
            path.write_bytes(b'Changed by another operator')
            with self.assertRaises(RuntimeError):module.atomic_replace(path,b'new',b'old')
            self.assertEqual(path.read_bytes(),b'Changed by another operator')
    def test_atomic_update_changes_only_expected_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"index.html"
            before=b'<head></head><body>Original</body>'
            path.write_bytes(before)
            after=module.add_tag(before)
            module.atomic_replace(path,after,before)
            self.assertEqual(path.read_bytes(),after)
if __name__=="__main__":unittest.main(verbosity=2)
