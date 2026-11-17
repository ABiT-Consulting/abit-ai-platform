"""Finite metadata confinement tests; no application imports or live access."""
from __future__ import annotations

import ast
from contextlib import contextmanager
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

RUNNER=Path(__file__).with_name('run_overlay_tests.py')
tree=ast.parse(RUNNER.read_bytes())
nodes=[node for node in tree.body if
       isinstance(node,ast.FunctionDef) and node.name=='confine_metadata'
       or isinstance(node,ast.Assign) and any(isinstance(target,ast.Name)
           and target.id=='LIVE_STATE_ROOTS' for target in node.targets)]
assert len(nodes)==2
namespace={'Path':Path,'os':os}
exec(compile(ast.Module(body=nodes,type_ignores=[]),str(RUNNER),'exec'),namespace)
confine_metadata=namespace['confine_metadata']
LIVE_STATE_ROOTS=namespace['LIVE_STATE_ROOTS']


class MetadataConfinement(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.blocked=self.root/'protected'
        self.blocked.mkdir()
        (self.blocked/'record.json').write_text('synthetic fixture')
        self.file=self.root/'fixture.txt'
        self.file.write_text('fixture')
        self.original_stat=os.stat
        self.original_lstat=os.lstat
        self.accesses=[]

    @contextmanager
    def guarded(self):
        def observed_stat(path,**kwargs):
            self.accesses.append(('stat',path))
            return self.original_stat(path,**kwargs)
        def observed_lstat(path,**kwargs):
            self.accesses.append(('lstat',path))
            return self.original_lstat(path,**kwargs)
        guarded_stat,guarded_lstat=confine_metadata(
            observed_stat,observed_lstat,(*LIVE_STATE_ROOTS,self.blocked))
        with patch.object(os,'stat',guarded_stat),patch.object(os,'lstat',guarded_lstat):
            yield
        for _,path in self.accesses:
            if isinstance(path,(str,bytes,os.PathLike)):
                candidate=Path(os.fsdecode(path))
                self.assertFalse(any(candidate.is_relative_to(root)
                                     for root in (*LIVE_STATE_ROOTS,self.blocked)))

    def test_stat_and_lstat_deny_every_declared_live_root_before_io(self):
        with self.guarded():
            for root in LIVE_STATE_ROOTS:
                for value in (str(root/'record.json'),os.fsencode(root/'record.json')):
                    for method in (os.stat,os.lstat):
                        with self.subTest(root=str(root),method=method.__name__,bytes=isinstance(value,bytes)):
                            with self.assertRaisesRegex(AssertionError,'Live state metadata'):
                                method(value)
        self.assertEqual(self.accesses,[])

    def test_path_metadata_wrappers_cannot_reach_live_state(self):
        with self.guarded():
            path=LIVE_STATE_ROOTS[-1]/'record.json'
            for method in (path.stat,path.lstat,path.exists,path.is_file,path.is_dir,path.is_symlink):
                with self.subTest(method=method.__name__):
                    with self.assertRaisesRegex(AssertionError,'Live state metadata'):
                        method()
        self.assertEqual(self.accesses,[])

    def test_relative_and_bytes_paths_are_bound_to_actual_cwd(self):
        before=Path.cwd()
        os.chdir(self.root)
        try:
            with self.guarded():
                for method in (os.stat,os.lstat):
                    for value in ('protected/record.json',b'protected/record.json'):
                        with self.assertRaisesRegex(AssertionError,'Live state metadata'):
                            method(value)
        finally:
            os.chdir(before)

    def test_relative_dir_fd_is_resolved_and_blocked_descriptor_is_denied(self):
        parent=os.open(self.root,os.O_RDONLY|os.O_DIRECTORY)
        secret=os.open(self.blocked/'record.json',os.O_RDONLY)
        try:
            with self.guarded():
                for method in (os.stat,os.lstat):
                    with self.assertRaisesRegex(AssertionError,'Live state metadata'):
                        method('protected/record.json',dir_fd=parent)
                with self.assertRaisesRegex(AssertionError,'Live state metadata'):
                    os.stat(secret)
                self.assertEqual(os.stat('fixture.txt',dir_fd=parent).st_size,7)
                self.assertEqual(os.lstat(b'fixture.txt',dir_fd=parent).st_size,7)
        finally:
            os.close(secret)
            os.close(parent)

    def test_invalid_dir_fd_is_denied_without_cwd_fallback(self):
        with self.guarded():
            for descriptor in (-1,999999999,'untrusted',True):
                for method in (os.stat,os.lstat):
                    with self.subTest(descriptor=descriptor,method=method.__name__):
                        with self.assertRaisesRegex(AssertionError,'metadata descriptor'):
                            method('fixture.txt',dir_fd=descriptor)
        self.assertEqual(self.accesses,[])

    def test_fixture_symlink_into_live_root_denies_before_destination_metadata(self):
        link=self.root/'live-link'
        link.symlink_to(LIVE_STATE_ROOTS[-1],target_is_directory=True)
        with self.guarded():
            for method in (os.stat,os.lstat):
                with self.assertRaisesRegex(AssertionError,'Live state metadata'):
                    method(link/'record.json')
                with self.assertRaisesRegex(AssertionError,'Live state metadata'):
                    method(link)
            with self.assertRaisesRegex(AssertionError,'Live state metadata'):
                (link/'record.json').exists()

    def test_isolated_fixture_metadata_symlinks_and_missing_paths_still_work(self):
        link=self.root/'allowed-link'
        link.symlink_to(self.file.name)
        before=Path.cwd()
        descriptor=os.open(self.file,os.O_RDONLY)
        try:
            os.chdir(self.root)
            with self.guarded():
                self.assertTrue(self.file.is_file())
                self.assertTrue(self.root.is_dir())
                self.assertEqual(self.file.stat().st_size,7)
                self.assertEqual(os.stat(b'fixture.txt').st_size,7)
                self.assertEqual(os.stat(descriptor).st_size,7)
                self.assertEqual(link.resolve(),self.file)
                self.assertTrue(stat.S_ISLNK(os.lstat(link).st_mode))
                self.assertEqual(os.stat(link).st_size,7)
                self.assertFalse((self.root/'absent'/'file').exists())
        finally:
            os.chdir(before)
            os.close(descriptor)

    def test_symlink_resolution_is_bounded_without_recursive_wrappers(self):
        left=self.root/'left';right=self.root/'right'
        left.symlink_to(right.name);right.symlink_to(left.name)
        with self.guarded():
            for method in (os.stat,os.lstat):
                with self.assertRaisesRegex(AssertionError,'resolution limit'):
                    method(left)

    def test_lexical_parent_segments_do_not_hide_protected_traversal(self):
        (self.root/'subdirectory').mkdir()
        with self.guarded():
            for method in (os.stat,os.lstat):
                with self.assertRaisesRegex(AssertionError,'Live state metadata'):
                    method(self.blocked/'..'/'fixture.txt')
                self.assertEqual(method(self.root/'subdirectory'/'..'/'fixture.txt').st_size,7)


if __name__=='__main__':
    unittest.main()
