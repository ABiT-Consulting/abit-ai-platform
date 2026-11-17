"""Private channel selection never changes legacy client construction."""

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


class Runtime(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            'controlled_erp_runtime', ROOT / 'backend/open_webui/utils/agentic_erp_runtime.py'
        )
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Path(self.temp.name) / 'server.json'
        self.environment = patch.dict(os.environ, {'ABIT_ERP_TASK_RESOLUTION_CONFIG': str(self.config)})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_disabled_is_explicit_and_malformed_flags_never_select_public_fallback(self):
        for value in ({}, {'enabled': False}):
            self.config.write_text(json.dumps({'agentic_erp_channel': value}))
            self.assertIsNone(self.module.configured_channel())
        for value in (None, [], {'enabled': 1}, {'enabled': 'true'}):
            self.config.write_text(json.dumps({'agentic_erp_channel': value}))
            with self.assertRaises(ValueError):
                self.module.configured_channel()

    def test_config_removed_or_disabled_after_connection_cannot_fallback(self):
        self.module._CHANNEL = object()
        self.config.write_text('{}')
        with self.assertRaises(ValueError):
            self.module.configured_channel()
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                self.module.configured_channel()

    def test_governed_selection_keeps_legacy_constructor_and_does_not_read_missing_token(self):
        import httpx

        constructed = []

        class NativeClient:
            def __init__(self, token_file):
                constructed.append(token_file)

        fake = types.SimpleNamespace(ERP='https://erp.abit.ai', ERPClient=NativeClient)
        with patch.dict(sys.modules, {'open_webui.utils.erp_task_resolution': fake}):
            with patch.object(self.module, 'configured_transport', return_value=None):
                self.assertIsInstance(self.module.governed_client('existing-private-path'), NativeClient)
            with patch.object(
                self.module,
                'configured_transport',
                return_value=httpx.MockTransport(lambda request: httpx.Response(200)),
            ):
                selected = self.module.governed_client('/does/not/exist')
                self.assertIsInstance(selected.client, httpx.AsyncClient)
                self.assertEqual(constructed, ['existing-private-path'])
                import asyncio

                asyncio.run(selected.client.aclose())


if __name__ == '__main__':
    unittest.main()
