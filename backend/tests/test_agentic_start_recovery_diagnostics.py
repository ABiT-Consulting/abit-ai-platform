"""Actual native error formatter retained without arbitrary exception contents."""
import httpx
from test_agentic_controls_events import Fixture


class RecoveryDiagnostics(Fixture):
    def setUp(self):
        super().setUp()
        self.recovery = self.load('agentic_recovery')
        self.status = self.load('erp_status_write')
        self.channel = self.load('agentic_erp_channel')

    async def test_native_validation_type_message_and_redaction_are_retained(self):
        secret = 'synthetic-authorized-secret-123456789'
        request = httpx.Request('POST', 'https://erp.example.invalid/api/method/fixed',
                                headers={'Authorization': 'token ' + secret})
        response = httpx.Response(403, request=request, json={
            'exc_type': 'PermissionError',
            'message': 'Task write denied; token=' + secret,
        })
        value = self.recovery._recovery_failure(self.status.ERPResponseError(response))
        self.assertIn('ERP HTTP 403 PermissionError', value)
        self.assertIn('Task write denied', value)
        self.assertIn('[redacted]', value)
        self.assertNotIn(secret, value)
        self.assertLessEqual(len(value), 720)

    async def test_exact_channel_denial_is_visible_but_unknown_transport_text_is_not(self):
        error = self.channel.ERPChannelError('ERP channel native operation is not allowed')
        self.assertEqual(self.recovery._recovery_failure(error),
                         'ERPChannelError: ERP channel native operation is not allowed')
        self.assertEqual(self.recovery._recovery_failure(
            self.channel.ERPChannelError('private prompt token=do-not-copy')),
            'ERPChannelError')

    async def test_unknown_exception_never_exposes_prompt_or_credential_text(self):
        self.assertEqual(self.recovery._recovery_failure(ValueError('private work packet secret=never')),
                         'ValueError')
