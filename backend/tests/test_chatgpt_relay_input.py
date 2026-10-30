"""No-network tests for multimodal ChatGPT relay user turns and staging."""
import base64
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

from fastapi import HTTPException

SOURCE = Path(__file__).resolve().parents[1] / 'open_webui/utils/chatgpt_relay_input.py'
spec = importlib.util.spec_from_file_location('isolated_relay_input', SOURCE)
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)
SAMPLE = b'\x89PNG\r\n\x1a\n' + b'synthetic test fixture'
IMAGE = 'data:image/png;base64,' + base64.b64encode(SAMPLE).decode()


class ExplicitUserTurnTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_plain_text_digest_unchanged(self):
        text, images, digest = await relay.parse_explicit_user_turn(
            {'role': 'user', 'content': '  hello  '}, object()
        )
        self.assertEqual((text, images), ('hello', []))
        self.assertEqual(digest, hashlib.sha256(b'hello').hexdigest())

    async def test_accepted_image_is_not_silently_dropped(self):
        text, images, digest = await relay.parse_explicit_user_turn({
            'role': 'user',
            'content': [
                {'type': 'text', 'text': 'Review the screenshot'},
                {'type': 'image_url', 'image_url': {'url': IMAGE}},
            ]}, object())
        self.assertEqual(text, 'Review the screenshot')
        self.assertEqual(images, [('png', SAMPLE)])
        self.assertNotEqual(digest, hashlib.sha256(text.encode()).hexdigest())

    async def test_reject_non_user_and_unsupported_parts(self):
        for last in (
            {'role': 'assistant', 'content': 'do something'},
            {'role': 'system', 'content': 'do something'},
            {'role': 'user', 'content': [{'type': 'tool_result', 'text': 'inject'}]},
            {'role': 'user', 'content': [{'type': 'image_url', 'image_url': 'https://example.com/secret'}]},
            {'role': 'user', 'content': []},
            {'role': 'user', 'content': [{'type': 'image_url', 'image_url': IMAGE}]},
        ):
            with self.subTest(role=last['role'], content_type=type(last['content']).__name__):
                with self.assertRaises(HTTPException):
                    await relay.parse_explicit_user_turn(last, object())

    async def test_different_image_gets_different_idempotency_hash(self):
        def request(image):
            return {'role': 'user', 'content': [
                {'type': 'text', 'text': 'compare'},
                {'type': 'image_url', 'image_url': {'url': image}},
            ]}
        other = 'data:image/png;base64,' + base64.b64encode(SAMPLE+b'x').decode()
        one = await relay.parse_explicit_user_turn(request(IMAGE), object())
        two = await relay.parse_explicit_user_turn(request(other), object())
        self.assertNotEqual(one[2], two[2])

    async def test_private_staging_is_scoped_and_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            original = relay.STAGING_ROOT
            relay.STAGING_ROOT = Path(directory)
            try:
                files = relay.stage_images([('png', SAMPLE)], 'aibit_' + 'a'*48)
                self.assertEqual(len(files), 1)
                self.assertEqual(Path(files[0]['path']).read_bytes(), SAMPLE)
                relay.cleanup_images(files, 'aibit_' + 'a'*48)
                self.assertFalse(Path(files[0]['path']).exists())
                self.assertEqual(list(Path(directory).iterdir()), [])
            finally:
                relay.STAGING_ROOT = original


if __name__ == '__main__':
    unittest.main()
