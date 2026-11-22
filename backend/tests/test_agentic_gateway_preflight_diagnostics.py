"""Offline real proof/controller/preclaim coverage; no external service or model."""
from copy import deepcopy
import ast
import asyncio
import json
from pathlib import Path
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

import test_agentic_relay_scope as existing

ROOT = Path(__file__).resolve().parents[1] / 'open_webui'
PRIVATE = 'sentinel-private-header-or-response-DO-NOT-PERSIST'


class PreflightDiagnostics(existing.RelayScope):
    async def failure(self, row=None):
        with self.assertRaises(self.module.CapabilityUnavailable) as caught:
            await self.check(row)
        error = caught.exception
        self.assertLess(len(str(error)), 500)
        self.assertNotIn(PRIVATE, str(error))
        self.assertNotIn('fixture-private-auth-token', str(error))
        return error

    def packet_scope(self, row):
        packet = self.packet()
        return self.module.scope_for_packet(
            row, packet, 'owner-fixture', 'a' * 64, 'dot-fixture-000001',
            self.bridge_module.stable_run_id(row['invocation_id'])
        )

    def controller(self):
        text = (ROOT / 'routers/agentic_governance.py').read_text()
        tree = ast.parse(text)
        node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                    and n.name == '_gateway_controller')
        # Execute the actual current candidate controller without importing app lifespan.
        module = ast.Module(body=[node], type_ignores=[])
        from fastapi import HTTPException
        namespace = {
            '_config': lambda: {},
            '_gate': self.gate,
            '_erp_failure': lambda error, context: context + ' (' + type(error).__name__ + ')',
            'HTTPException': HTTPException,
        }
        exec(compile(module, str(ROOT / 'routers/agentic_governance.py'), 'exec'), namespace)
        return namespace['_gateway_controller']

    async def controller_error(self, row, *, changed_packet=False, injected=None, operation='proof'):
        from fastapi import HTTPException
        gateway = self.load('agentic_gateway_sessions')
        original_proof = gateway.proof
        bridge = types.SimpleNamespace(packet=AsyncMock(), close=AsyncMock())
        packet = self.packet()
        if changed_packet:
            packet['objective'] = 'Changed authorized business context'
            packet['content_sha256'] = self.bridge_module.packet_digest(packet)
        bridge.packet.return_value = packet
        body_value = {'scope': self.packet_scope(row)}
        body_value.update(protocol_version=1, scope_digest=self.bridge_module.digest(body_value['scope']))
        body = types.SimpleNamespace(model_dump=lambda: deepcopy(body_value))
        operations = types.ModuleType('open_webui.utils.agentic_gateway_operations')
        operations.registry = lambda role: {}
        async def actual_proof(scope, **kwargs):
            if injected is not None:
                raise injected
            return await original_proof(scope, db_path=self.path, **kwargs)
        with patch.dict(__import__('sys').modules, {'open_webui.utils.agentic_gateway_operations': operations}), \
             patch.object(self.bridge_module, 'ERPBridge', lambda config: bridge), \
             patch.object(gateway, 'proof', actual_proof), \
             patch.object(gateway, 'admit', AsyncMock(side_effect=injected or ValueError('Existing denial'))):
            with self.assertRaises(HTTPException) as caught:
                await self.controller()(operation, body)
        bridge.close.assert_awaited_once()
        return caught.exception

    async def test_actual_core_owner_profile_failure_roundtrips_as_fixed_code(self):
        row = self.enqueue()
        error = await self.controller_error(row)
        self.assertEqual(error.status_code, 424)
        self.assertEqual(error.detail, 'Gateway preflight denied: OWNER_PROFILE_UNAVAILABLE')
        # Exact deployed Node wire/relay error envelope; no browser or remote transport.
        self.status = 424
        self.raw = json.dumps({'ok': False, 'success': False,
                              'message': 'Core HTTP 424: ' + error.detail}).encode()
        failure = await self.failure(row)
        self.assertEqual(failure.gateway_preflight_diagnostic, {
            'stage': 'core_profile', 'http_status': 424, 'core_http_status': 424,
            'error_code': 'OWNER_PROFILE_UNAVAILABLE',
        })
        self.assertEqual(self.actual_sends, 0)
        self.assertIsNone(self.g.find(row['invocation_id'], self.path)['automation_run_id'])

    async def test_actual_core_changed_work_packet_remains_409_with_specific_code(self):
        row = self.enqueue()
        error = await self.controller_error(row, changed_packet=True)
        self.assertEqual(error.status_code, 409)
        self.assertEqual(error.detail, 'Gateway preflight denied: WORK_PACKET_CHANGED')
        self.status = 424
        self.raw = json.dumps({'message': 'Core HTTP 409: ' + error.detail}).encode()
        failure = await self.failure(row)
        self.assertEqual(failure.gateway_preflight_diagnostic['error_code'], 'WORK_PACKET_CHANGED')
        self.assertEqual(failure.gateway_preflight_diagnostic['core_http_status'], 409)
        self.assertEqual(self.g.find(row['invocation_id'], self.path)['state'], 'queued')

    async def test_core_blocked_state_stays_ineligible_and_does_not_issue_proof(self):
        row = self.enqueue()
        unsubmitted = self.load('agentic_unsubmitted')
        unsubmitted.block(row['invocation_id'], 'Original retained blocker', self.path)
        error = await self.controller_error(row)
        self.assertEqual(error.status_code, 409)
        self.assertEqual(error.detail, 'Gateway preflight denied: INVOCATION_INELIGIBLE')
        current = self.g.find(row['invocation_id'], self.path)
        self.assertEqual(current['state'], 'blocked')
        self.assertEqual(current['reason'], 'Original retained blocker')
        self.assertEqual(current['dispatch_attempts'], 0)
        self.assertEqual(self.actual_sends, 0)

    async def test_unknown_core_exception_never_echoes_private_text(self):
        row = self.enqueue()
        error = await self.controller_error(row, injected=RuntimeError(PRIVATE))
        self.assertEqual(error.status_code, 409)
        self.assertEqual(error.detail, 'Gateway preflight denied: CORE_AUTHORITY_UNVERIFIED')
        self.assertNotIn(PRIVATE, str(error.detail))
        class UnsafeString(ValueError):
            def __str__(self):
                raise AssertionError('Unknown exceptions must not be formatted')
        self.assertEqual(self.module.gateway_proof_failure_detail(UnsafeString(PRIVATE)),
                         'Gateway preflight denied: CORE_AUTHORITY_UNVERIFIED')

    async def test_admission_error_contract_is_unchanged(self):
        row = self.enqueue()
        error = await self.controller_error(row, operation='admit', injected=ValueError(PRIVATE))
        self.assertEqual(error.status_code, 409)
        self.assertEqual(error.detail, 'Gateway authority validation failed (ValueError)')
        self.assertNotIn('Gateway preflight denied', error.detail)
        self.assertNotIn(PRIVATE, error.detail)

    async def test_native_boundary_and_profile_codes_cannot_turn_failure_into_authority(self):
        row = self.enqueue()
        cases = {
            'ERP has not verified its actual loaded managed-resource guards': ('NATIVE_BOUNDARY_UNVERIFIED', 'core_native_boundary'),
            'The existing MCP connection has not been verified for this owner': ('MCP_CONNECTION_UNVERIFIED', 'core_profile'),
            'The action gateway is not linked to the registered abitdot': ('DOT_BINDING_MISMATCH', 'core_profile'),
            'The packet requires an unsupported gateway capability': ('TOOL_SCOPE_UNSUPPORTED', 'core_registry'),
        }
        for message, (code, stage) in cases.items():
            with self.subTest(code=code):
                detail = self.module.gateway_proof_failure_detail(self.module.CapabilityUnavailable(message))
                self.status = 424
                self.raw = json.dumps({'message': 'Core HTTP 424: ' + detail}).encode()
                error = await self.failure(row)
                self.assertEqual(error.gateway_preflight_diagnostic['error_code'], code)
                self.assertEqual(error.gateway_preflight_diagnostic['stage'], stage)
        self.assertEqual(self.actual_sends, 0)
        self.assertEqual(self.g.find(row['invocation_id'], self.path)['dispatch_attempts'], 0)

    async def test_local_owner_and_account_failures_are_observable_before_http(self):
        row = self.enqueue()
        with self.assertRaises(self.module.CapabilityUnavailable) as caught:
            await self.check(row, owner='different-owner')
        self.assertEqual(caught.exception.gateway_preflight_diagnostic['stage'], 'registered_owner')
        self.assertEqual(caught.exception.gateway_preflight_diagnostic['error_code'], 'REGISTERED_OWNER_UNVERIFIED')
        self.account.return_value = 'invalid-private-account-' + PRIVATE
        error = await self.failure(row)
        self.assertEqual(error.gateway_preflight_diagnostic['stage'], 'relay_account')
        self.assertEqual(error.gateway_preflight_diagnostic['error_code'], 'RELAY_ACCOUNT_UNVERIFIED')
        self.assertEqual(self.calls, [])

    async def test_real_relay_fixed_authentication_failure_is_preserved(self):
        self.status = 403
        self.raw = json.dumps({'ok': False, 'code': 'DOT_AUTH_REQUIRED', 'message': PRIVATE}).encode()
        error = await self.failure()
        self.assertEqual(error.gateway_preflight_diagnostic['http_status'], 403)
        self.assertEqual(error.gateway_preflight_diagnostic['error_code'], 'RELAY_AUTH_REQUIRED')

    async def test_untrusted_response_and_forged_codes_are_bounded_and_redacted(self):
        row = self.enqueue()
        self.status = 424
        read_limits = []
        old_response = existing.Response
        class BoundedResponse(old_response):
            async def read(self, limit):
                read_limits.append(limit)
                return await super().read(limit)
        cases = [
            json.dumps({'message': PRIVATE, 'token': PRIVATE}).encode(),
            json.dumps({'message': 'Core HTTP 409: Gateway preflight denied: ' + PRIVATE}).encode(),
            json.dumps({'message': 'Core HTTP 409: Gateway preflight denied: WORK_PACKET_CHANGED\n' + PRIVATE}).encode(),
            json.dumps({'message': PRIVATE * 200}).encode(),
            (PRIVATE + '<html>private</html>').encode(),
            b'\xff\xfe',
            b'[' * 2000,
        ]
        with patch.object(existing, 'Response', BoundedResponse):
            for raw in cases:
                with self.subTest(size=len(raw)):
                    self.raw = raw
                    error = await self.failure(row)
                    self.assertNotIn(PRIVATE, json.dumps(error.gateway_preflight_diagnostic))
                    self.assertIn(error.gateway_preflight_diagnostic['error_code'],
                                  {'RELAY_HTTP_REJECTED', 'CORE_HTTP_REJECTED'})
        self.assertEqual(read_limits, [self.module.MAX_ERROR_BYTES + 1] * len(cases))
        self.assertEqual(self.actual_sends, 0)

    async def test_invalid_success_proof_still_denied_without_exposing_contents(self):
        row = self.enqueue()
        self.mutate = lambda proof: proof.update(secret=PRIVATE)
        error = await self.failure(row)
        self.assertEqual(error.gateway_preflight_diagnostic['stage'], 'proof_validation')
        self.assertEqual(error.gateway_preflight_diagnostic['http_status'], 200)
        self.assertEqual(error.gateway_preflight_diagnostic['error_code'], 'PROOF_INVALID')
        self.assertEqual(self.actual_sends, 0)

    async def test_oversized_success_body_and_invalid_json_fail_closed(self):
        row = self.enqueue()
        for raw, code in [
            ((PRIVATE * 1000).encode(), 'PROOF_BODY_OVERSIZED'),
            ((PRIVATE + '{malformed').encode(), 'PROOF_BODY_INVALID'),
        ]:
            with self.subTest(code=code):
                self.raw = raw
                error = await self.failure(row)
                self.assertEqual(error.gateway_preflight_diagnostic['error_code'], code)
        self.assertEqual(self.actual_sends, 0)

    async def test_transport_failure_and_cancellation_remain_distinct(self):
        row = self.enqueue()
        import aiohttp
        with patch.object(aiohttp, 'ClientSession', side_effect=TimeoutError(PRIVATE)):
            error = await self.failure(row)
        self.assertEqual(error.gateway_preflight_diagnostic['error_code'], 'RELAY_TRANSPORT_UNAVAILABLE')
        with patch.object(aiohttp, 'ClientSession', side_effect=asyncio.CancelledError()):
            with self.assertRaises(asyncio.CancelledError):
                await self.check(row)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.g.find(row['invocation_id'], self.path)['state'], 'queued')

    async def test_actual_executor_persists_fixed_reason_without_claim_or_retry_authority(self):
        row = self.enqueue()
        self.status = 424
        self.raw = json.dumps({'message': 'Core HTTP 424: Gateway preflight denied: NATIVE_BOUNDARY_UNVERIFIED',
                               'private': PRIVATE}).encode()
        preclaim = self.load('agentic_preclaim_recovery')
        executor = self.load('agentic_executor')
        async def preflight(actual, packet):
            return await self.check(actual, packet)
        adapter = types.SimpleNamespace(preflight=preflight, prepare=AsyncMock(), submit=AsyncMock())
        await executor.execute(row['invocation_id'], project_gate=self.gate, config={},
                               adapter=adapter, bridge=self.bridge, db_path=self.path)
        current = self.g.find(row['invocation_id'], self.path, include_private=True)
        self.assertEqual(current['state'], 'blocked')
        self.assertEqual(current['dispatch_attempts'], 0)
        self.assertIsNone(current['automation_run_id'])
        self.assertIsNone(current['submitted_at'])
        self.assertEqual(current['packet_hash'], row['packet_hash'])
        self.assertEqual(current['work_packet'], row['work_packet'])
        self.assertIn('"error_code":"NATIVE_BOUNDARY_UNVERIFIED"', current['reason'])
        self.assertIn('"stage":"core_native_boundary"', current['reason'])
        self.assertNotIn(PRIVATE, current['reason'])
        self.assertLess(len(current['reason']), 500)
        adapter.prepare.assert_not_awaited()
        adapter.submit.assert_not_awaited()
        self.assertEqual(self.actual_sends, 0)
        with self.g.connect(self.path) as db:
            snapshot = preclaim.snapshot(db, current)
            audit = db.execute("SELECT payload FROM governance_event WHERE invocation_id=? "
                               "AND kind='unsubmitted_preflight_blocked'", (row['invocation_id'],)).fetchall()
        self.assertEqual(snapshot['last_recovery_outcome'], 'blocked')
        self.assertIsNone(snapshot['error_code'])  # Existing retry classifier is unchanged.
        self.assertIsNone(snapshot['next_eligible_recovery_at'])
        self.assertEqual(len(audit), 1)
        self.assertEqual(json.loads(audit[0]['payload'])['reason'], current['reason'])

    async def test_valid_proof_payload_and_no_submission_are_unchanged(self):
        row = self.enqueue()
        fixed = int(__import__('time').time())
        with patch.object(self.module.time, 'time', return_value=fixed):
            result = await self.check(row)
            expected = self.proof(self.calls[0][1]['json'])
        self.assertEqual(result, expected)
        self.assertNotIn('gateway_preflight', result)
        self.assertEqual(self.actual_sends, 0)
        self.assertEqual(self.g.find(row['invocation_id'], self.path)['state'], 'queued')


def load_tests(loader, tests, pattern):
    # Run only these new cases here; the original affected scope suite is selected separately.
    return unittest.TestSuite(PreflightDiagnostics(name) for name in sorted(PreflightDiagnostics.__dict__)
                              if name.startswith('test_'))
