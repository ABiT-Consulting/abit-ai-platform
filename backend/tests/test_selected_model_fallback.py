"""Behavioral tests: opt-in failover, permissions, streaming and duplicate-work safety."""
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, main
from unittest.mock import AsyncMock
from fastapi import HTTPException
from starlette.responses import JSONResponse, StreamingResponse

path = Path(__file__).resolve().parents[1] / "open_webui/utils/model_fallback.py"
spec = importlib.util.spec_from_file_location("model_fallback", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RelayUnavailable(Exception):
    pass


class ModelFallbackTests(IsolatedAsyncioTestCase):
    def setup_call(self, primary="primary", secondary="secondary"):
        request = SimpleNamespace(
            state=SimpleNamespace(metadata={"fallback_model_id": secondary}),
            app=SimpleNamespace(state=SimpleNamespace(MODELS={
                "primary": {"id": "primary"}, "secondary": {"id": "secondary"}
            })),
        )
        data = {"model": primary, "messages": [{"role": "user", "content": "Hello"}]}
        return request, data

    async def call(self, request, data, dispatch):
        return await module.complete_with_fallback(
            request, data, SimpleNamespace(role="user"), dispatch, RelayUnavailable
        )

    async def test_success_does_not_use_secondary(self):
        request, data = self.setup_call()
        dispatch = AsyncMock(return_value={"choices": []})
        self.assertEqual(await self.call(request, data, dispatch), {"choices": []})
        self.assertEqual(dispatch.await_count, 1)

    async def test_disabled_does_not_retry(self):
        request, data = self.setup_call(secondary="")
        dispatch = AsyncMock(side_effect=HTTPException(503))
        with self.assertRaises(HTTPException):
            await self.call(request, data, dispatch)
        self.assertEqual(dispatch.await_count, 1)

    async def test_selected_model_and_original_context_are_used(self):
        request, data = self.setup_call()
        calls = []
        async def dispatch(req, body, user, **kwargs):
            calls.append((body["model"], body["messages"][0]["content"], kwargs))
            if len(calls) == 1:
                body["messages"][0]["content"] = "mutated"
                raise HTTPException(503)
            return {"choices": []}
        result = await self.call(request, data, dispatch)
        self.assertEqual([c[:2] for c in calls], [("primary", "Hello"), ("secondary", "Hello")])
        self.assertFalse(calls[1][2]["bypass_filter"])
        self.assertEqual(result["selected_model_id"], "secondary")

    async def test_permission_failure_is_not_retried(self):
        request, data = self.setup_call()
        dispatch = AsyncMock(side_effect=HTTPException(403))
        with self.assertRaises(HTTPException):
            await self.call(request, data, dispatch)
        self.assertEqual(dispatch.await_count, 1)

    async def test_explicit_relay_pre_submit_failure_uses_secondary(self):
        request, data = self.setup_call(primary=module.RELAY_MODEL)
        dispatch = AsyncMock(side_effect=[RelayUnavailable(), {"choices": []}])
        result = await self.call(request, data, dispatch)
        self.assertEqual(result["selected_model_id"], "secondary")

    async def test_indeterminate_relay_error_is_never_replayed(self):
        for status in (500, 502, 503, 504):
            request, data = self.setup_call(primary=module.RELAY_MODEL)
            dispatch = AsyncMock(side_effect=HTTPException(status))
            with self.assertRaises(HTTPException):
                await self.call(request, data, dispatch)
            self.assertEqual(dispatch.await_count, 1)

    async def test_no_fallback_loop_after_secondary_failure(self):
        request, data = self.setup_call()
        dispatch = AsyncMock(side_effect=[HTTPException(503), HTTPException(502)])
        with self.assertRaises(HTTPException):
            await self.call(request, data, dispatch)
        self.assertEqual(dispatch.await_count, 2)

    async def test_secondary_access_denial_is_preserved(self):
        request, data = self.setup_call()
        dispatch = AsyncMock(side_effect=[HTTPException(503), HTTPException(403)])
        with self.assertRaises(HTTPException) as result:
            await self.call(request, data, dispatch)
        self.assertEqual(result.exception.status_code, 403)

    async def test_missing_secondary_does_not_silently_choose_another(self):
        request, data = self.setup_call(secondary="missing")
        dispatch = AsyncMock(side_effect=HTTPException(503))
        with self.assertRaises(HTTPException) as result:
            await self.call(request, data, dispatch)
        self.assertIn("secondary model is unavailable", result.exception.detail)
        self.assertEqual(dispatch.await_count, 1)

    async def test_stream_marks_actual_model_before_content(self):
        request, data = self.setup_call()
        async def chunks():
            yield 'data: {"choices": []}\n\n'
        dispatch = AsyncMock(side_effect=[HTTPException(429), StreamingResponse(chunks())])
        result = await self.call(request, data, dispatch)
        chunks = [chunk async for chunk in result.body_iterator]
        self.assertIn('"selected_model_id": "secondary"', chunks[0])
        self.assertIn('"choices"', chunks[1])

    async def test_failure_after_stream_started_is_not_retried(self):
        request, data = self.setup_call()
        async def chunks():
            yield "partial"
            raise HTTPException(503)
        dispatch = AsyncMock(return_value=StreamingResponse(chunks()))
        result = await self.call(request, data, dispatch)
        with self.assertRaises(HTTPException):
            _ = [chunk async for chunk in result.body_iterator]
        self.assertEqual(dispatch.await_count, 1)

    async def test_tool_work_is_not_replayed(self):
        request, data = self.setup_call()
        data["messages"].append({"role": "tool", "content": "Order placed"})
        dispatch = AsyncMock(side_effect=HTTPException(503))
        with self.assertRaises(HTTPException):
            await self.call(request, data, dispatch)
        self.assertEqual(dispatch.await_count, 1)

    async def test_cancelled_request_is_not_retried(self):
        request, data = self.setup_call()
        dispatch = AsyncMock(side_effect=asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            await self.call(request, data, dispatch)
        self.assertEqual(dispatch.await_count, 1)

    async def test_matching_primary_is_not_retried(self):
        request, data = self.setup_call(secondary="primary")
        dispatch = AsyncMock(side_effect=HTTPException(503))
        with self.assertRaises(HTTPException):
            await self.call(request, data, dispatch)
        self.assertEqual(dispatch.await_count, 1)

    async def test_http_error_response_can_fall_back(self):
        request, data = self.setup_call()
        dispatch = AsyncMock(side_effect=[JSONResponse({"error": "busy"}, status_code=429), {"choices": []}])
        self.assertEqual((await self.call(request, data, dispatch))["selected_model_id"], "secondary")

    async def test_subsequent_tool_loop_keeps_actual_provider(self):
        request, data = self.setup_call()
        request.state.metadata.update(fallback_attempted=True, fallback_from="primary", model_id="secondary")
        dispatch = AsyncMock(return_value={"choices": []})
        await self.call(request, data, dispatch)
        self.assertEqual(dispatch.call_args.args[1]["model"], "secondary")
        self.assertEqual(dispatch.await_count, 1)

if __name__ == "__main__":
    main()
