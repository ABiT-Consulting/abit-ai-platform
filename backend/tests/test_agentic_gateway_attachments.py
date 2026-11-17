"""Declared file byte identity and revocation during presentation; no providers."""

import base64
from copy import deepcopy
import hashlib
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from test_agentic_controls_events import Fixture, P, T


class Attachments(Fixture):
    def setUp(self):
        super().setUp()
        self.m = self.load('agentic_gateway_attachments')
        self.worker = self.load('agentic_attachment_worker')
        self.data = 'SRS \u4efb\u52a1\nContext only'.encode()
        self.file = {
            'id': 'FILE-fixture',
            'name': 'SRS \u4efb\u52a1.txt',
            'is_private': 1,
            'content_hash': hashlib.md5(self.data, usedforsecurity=False).hexdigest(),
        }
        self.packet_value = self.packet()
        self.packet_value['attachments'] = [self.file]
        self.row_value = {'erp_project': P, 'erp_task': T}
        self.value = {
            'schema_version': 1,
            'project_id': P,
            'task_id': T,
            'file_id': self.file['id'],
            'file_name': self.file['name'],
            'is_private': 1,
            'content_hash': self.file['content_hash'],
            'attached_to_doctype': 'Task',
            'attached_to_name': T,
            'sha256': hashlib.sha256(self.data).hexdigest(),
            'file_size': len(self.data),
            'content_base64': base64.b64encode(self.data).decode(),
        }
        self.reader = SimpleNamespace(context_attachment=AsyncMock(side_effect=lambda *args: deepcopy(self.value)))
        self.authority = AsyncMock()

    def arguments(self):
        return dict(row=self.row_value, packet=self.packet_value, bridge=self.reader, check_authority=self.authority)

    async def test_exact_unicode_context_read_chunk_identity(self):
        result = await self.m.read({'file_id': self.file['id']}, **self.arguments())
        self.assertEqual(base64.b64decode(result['content_base64']), self.data)
        self.assertEqual(result['sha256'], self.value['sha256'])
        self.assertTrue(result['context_only'])
        self.assertEqual(self.authority.await_count, 3)

    async def test_undeclared_duplicate_and_public_file_never_call_native(self):
        for attachments in ([], [self.file, self.file], [{**self.file, 'is_private': 0}]):
            self.packet_value['attachments'] = attachments
            with self.assertRaises(ValueError):
                await self.m.read({'file_id': self.file['id']}, **self.arguments())
        self.reader.context_attachment.assert_not_awaited()

    async def test_native_wrong_scope_hash_size_or_base64_are_denied(self):
        for change in (
            {'task_id': 'TASK-other'},
            {'attached_to_name': 'TASK-other'},
            {'sha256': 'a' * 64},
            {'file_size': 0},
            {'content_base64': self.value['content_base64'] + '\n'},
        ):
            prior = deepcopy(self.value)
            self.value.update(change)
            with self.assertRaises(ValueError):
                await self.m.read({'file_id': self.file['id']}, **self.arguments())
            self.value = prior

    async def test_missing_server_authority_is_not_a_fallback(self):
        with self.assertRaises(ValueError):
            await self.m.read({'file_id': self.file['id']}, **{**self.arguments(), 'check_authority': None})
        self.reader.context_attachment.assert_not_awaited()

    async def test_hold_assignment_lease_or_mro_change_during_native_read_discards_bytes(self):
        for message in ('Hold', 'TaskStop', 'Assignment changed', 'Lease expired', 'File MRO changed'):
            self.authority = AsyncMock(side_effect=[None, ValueError(message)])
            with self.assertRaisesRegex(ValueError, message):
                await self.m.read({'file_id': self.file['id']}, **self.arguments())

    async def test_stop_during_sandbox_presentation_discards_output(self):
        self.authority = AsyncMock(side_effect=[None, None, None, ValueError('TaskStop after parser')])
        with patch.object(
            self.m, '_sandbox', AsyncMock(return_value={'source_sha256': self.value['sha256'], 'text': 'SRS'})
        ):
            with self.assertRaisesRegex(ValueError, 'TaskStop'):
                await self.m.present({'file_id': self.file['id']}, **self.arguments())

    def test_fixed_parser_text_pagination_and_invalid_binary(self):
        rendered = self.worker.render(b'A' * 13000, 1, 0)
        self.assertEqual(len(rendered['text']), 12000)
        self.assertEqual(rendered['next_text_offset'], 12000)
        self.assertTrue(rendered['truncated'])
        with self.assertRaises(UnicodeError):
            self.worker.render(b'\xff', 1, 0)
        with self.assertRaises(ValueError):
            self.worker.render(b'text', 2, 0)

    def test_fixed_image_parser_derives_bounded_preview_and_actual_hash(self):
        from PIL import Image

        image = io.BytesIO()
        Image.new('RGB', (1600, 800), 'blue').save(image, format='PNG')
        raw = image.getvalue()
        result = self.worker.render(raw, 1, 0)
        preview = base64.b64decode(result['preview_base64'])
        self.assertEqual(result['source_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(result['preview_sha256'], hashlib.sha256(preview).hexdigest())
        self.assertLessEqual(len(preview), self.m.MAX_PREVIEW)
        self.assertEqual(result['preview_dimensions'], [1280, 640])
        self.assertTrue(result['derived_preview'])

    def test_fixed_pdf_parser_actual_document_and_encryption_deny(self):
        from pypdf import PdfWriter

        document = PdfWriter()
        document.add_blank_page(width=200, height=200)
        buffer = io.BytesIO()
        document.write(buffer)
        result = self.worker.render(buffer.getvalue(), 1, 0)
        self.assertEqual(result['pages'], 1)
        self.assertEqual(result['kind'], 'pdf_text')
        with self.assertRaises(ValueError):
            self.worker.render(buffer.getvalue(), 2, 0)
        document.encrypt('synthetic-password')
        buffer = io.BytesIO()
        document.write(buffer)
        with self.assertRaises(ValueError):
            self.worker.render(buffer.getvalue(), 1, 0)

    async def test_actual_namespace_parser_text_pdf_image(self):
        # Run this case in the same delegated production service context as the
        # existing repository namespace test. No model/native ERP calls occur.
        from PIL import Image
        from pypdf import PdfWriter

        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        pdf = io.BytesIO()
        writer.write(pdf)
        image = io.BytesIO()
        Image.new('RGB', (50, 30), 'green').save(image, format='PNG')
        for data, kind in (
            (b'actual isolated context', 'text'),
            (pdf.getvalue(), 'pdf_text'),
            (image.getvalue(), 'image_preview'),
        ):
            result = await self.m._sandbox(data, page=1, text_offset=0)
            self.assertEqual(result['kind'], kind)
            self.assertEqual(result['source_sha256'], hashlib.sha256(data).hexdigest())
