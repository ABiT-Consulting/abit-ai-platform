"""Shadow two-module regression: real files, zero model/provider/production calls."""

import base64
import hashlib
import importlib.util
from pathlib import Path
import shutil
import sys
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE / 'backend/tests'))
from test_agentic_controls_events import Fixture

SHADOW = SOURCE / 'backend/open_webui/utils'
DENIED = ('.env', '.env.production', '.env.local', '.env.development.local', '.env.example',
          '.envrc', '.envrc.local', '.ENV.PRODUCTION', '.ENVRC', 'production.env', 'app.env.local')
ALLOWED = ('environment.py', 'env.py', '.environment', 'env.example', 'config.py')


class CredentialVariants(Fixture):
    def setUp(self):
        super().setUp()
        self.ops = self.shadow('agentic_gateway_operations')
        self.delivery = self.shadow('agentic_gateway_delivery')
        self.root = Path(self.temp.name) / 'repository'
        (self.root / 'src').mkdir(parents=True)
        self.profile = {'allowed_paths': ['src'], 'test_paths': ['src']}
        for name in DENIED:
            (self.root / 'src' / name).write_bytes(b'SYNTHETIC_PRIVATE_BYTES_DO_NOT_RETURN')
        for name in ALLOWED:
            (self.root / 'src' / name).write_bytes(b'public fixture source\n')

    def shadow(self, name):
        full = 'open_webui.utils.' + name
        spec = importlib.util.spec_from_file_location(full, SHADOW / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[full] = module
        setattr(self.utils, name, module)
        spec.loader.exec_module(module)
        return module

    def test_read_write_and_explicit_input_paths_deny_each_environment_variant(self):
        for name in DENIED:
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, 'Credential-bearing'):
                    self.ops._path({'path': 'src/' + name}, self.profile)
                self.assertTrue(self.delivery._excluded(('src', name)))
        for name in ALLOWED:
            with self.subTest(name=name):
                self.assertEqual('src/' + name, str(self.ops._path({'path': 'src/' + name}, self.profile)))
                self.assertFalse(self.delivery._excluded(('src', name)))

    def test_actual_sanitized_test_snapshot_excludes_private_bytes_keeps_source(self):
        target, manifest = self.ops._test_snapshot(self.root, self.profile)
        try:
            expected = {'src/' + name for name in ALLOWED}
            self.assertEqual(expected, {row['path'] for row in manifest})
            for name in DENIED:
                self.assertFalse((target / 'src' / name).exists())
            for name in ALLOWED:
                self.assertEqual(b'public fixture source\n', (target / 'src' / name).read_bytes())
        finally:
            shutil.rmtree(target)

    async def test_actual_read_write_handlers_deny_variants_without_touching_bytes(self):
        with patch.object(self.ops, '_workspace', return_value=(self.root, self.profile)):
            for name in DENIED:
                with self.subTest(name=name):
                    path = self.root / 'src' / name
                    before = path.read_bytes()
                    with self.assertRaisesRegex(ValueError, 'Credential-bearing'):
                        await self.ops.repository_read({'path': 'src/' + name}, row={}, packet={}, config={})
                    with self.assertRaisesRegex(ValueError, 'Credential-bearing'):
                        await self.ops.repository_write({'path': 'src/' + name,
                            'expected_sha256': hashlib.sha256(before).hexdigest(),
                            'content_base64': base64.b64encode(b'attempted change').decode()}, row={}, packet={}, config={})
                    self.assertEqual(before, path.read_bytes())
            observed = await self.ops.repository_read({'path': 'src/env.py'}, row={}, packet={}, config={})
            self.assertEqual(b'public fixture source\n', base64.b64decode(observed['content_base64']))
            await self.ops.repository_write({'path': 'src/env.py', 'expected_sha256': observed['sha256'],
                'content_base64': base64.b64encode(b'changed public source\n').decode()}, row={}, packet={}, config={})
            self.assertEqual(b'changed public source\n', (self.root / 'src/env.py').read_bytes())

    def test_actual_delivery_snapshot_and_explicit_secret_inputs_exclude_bytes(self):
        snapshot = self.delivery._snapshot_at_root(self.root, self.profile)
        self.assertEqual({'src/' + name for name in ALLOWED}, set(snapshot['contents']))
        self.assertNotIn(b'SYNTHETIC_PRIVATE_BYTES_DO_NOT_RETURN', snapshot['contents'].values())
        for name in DENIED:
            with self.subTest(name=name):
                empty = self.delivery._snapshot_at_root(self.root, {'allowed_paths': ['src/' + name]})
                self.assertEqual([], empty['manifest'])
                self.assertEqual({}, empty['contents'])

    def test_private_environment_directory_never_traversed(self):
        private = self.root / 'src/.env.local-dir'
        private.mkdir()
        (private / 'visible.py').write_bytes(b'SYNTHETIC_PRIVATE_DIRECTORY_BYTES')
        snapshot = self.delivery._snapshot_at_root(self.root, self.profile)
        target, manifest = self.ops._test_snapshot(self.root, self.profile)
        try:
            self.assertNotIn('src/.env.local-dir/visible.py', snapshot['contents'])
            self.assertFalse((target / 'src/.env.local-dir').exists())
            self.assertNotIn('src/.env.local-dir/visible.py', {row['path'] for row in manifest})
        finally:
            shutil.rmtree(target)
