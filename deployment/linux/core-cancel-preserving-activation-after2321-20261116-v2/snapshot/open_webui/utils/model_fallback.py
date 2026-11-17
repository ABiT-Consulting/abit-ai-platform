"""One opt-in, pre-response fallback through the normal authorized dispatcher."""
import copy
import json
import logging

from fastapi import HTTPException
from starlette.responses import StreamingResponse

log = logging.getLogger(__name__)
RELAY_MODEL = "omniroute.abit-chatgpt-relay/project-management"
RETRYABLE_STATUS = {429, 500, 502, 503}


def _has_current_tool_work(messages):
    for message in reversed(messages or []):
        if message.get("role") == "user":
            return False
        if message.get("role") in ("tool", "function") or message.get("tool_calls"):
            return True
    return False


async def complete_with_fallback(request, form_data, user, dispatch, relay_error,
                                 bypass_filter=False, bypass_system_prompt=False):
    metadata = {**form_data.get("metadata", {}),
                **getattr(request.state, "metadata", {})}
    fallback_id = metadata.get("fallback_model_id")
    primary_id = form_data.get("model")
    if (metadata.get("fallback_attempted") and not metadata.get("task")
            and primary_id == metadata.get("fallback_from")):
        form_data = {**form_data, "model": metadata["model_id"]}
        primary_id = form_data["model"]
    eligible = (
        isinstance(fallback_id, str) and bool(fallback_id.strip())
        and fallback_id != primary_id and fallback_id != RELAY_MODEL
        and not metadata.get("fallback_attempted")
        and not metadata.get("task")
        and not metadata.get("assistant_message_id")
        and not getattr(request.state, "direct", False)
        and not _has_current_tool_work(form_data.get("messages"))
    )
    kwargs = dict(bypass_filter=bypass_filter, bypass_system_prompt=bypass_system_prompt)
    if not eligible:
        try:
            return await dispatch(request, form_data, user, **kwargs)
        except relay_error as exc:
            raise HTTPException(503, "ChatGPT browser unavailable before submission; select a secondary model to enable fallback.") from exc

    # Dispatchers mutate the payload. Keep the original request for the second attempt.
    original = copy.deepcopy(form_data)
    postsubmit_job_id = None
    try:
        response = await dispatch(request, form_data, user, **kwargs)
    except relay_error as exc:
        postsubmit_job_id = getattr(exc, "postsubmit_job_id", None)
        reason = (
            "ChatGPT rate-limited after submission; handing off without re-sending to ChatGPT"
            if postsubmit_job_id else "ChatGPT browser unavailable before submission"
        )
    except HTTPException as exc:
        # A relay timeout/error may represent a submitted turn. Only its explicit
        # pre-submit signal permits fallback. Never catch cancellation or stream errors.
        if primary_id == RELAY_MODEL or exc.status_code not in RETRYABLE_STATUS:
            raise
        reason = f"Primary model returned HTTP {exc.status_code}"
    else:
        code = getattr(response, "status_code", 200)
        if primary_id == RELAY_MODEL or code not in RETRYABLE_STATUS:
            return response
        reason = f"Primary model returned HTTP {code}"
        if getattr(response, "background", None):
            await response.background()

    models = request.app.state.MODELS
    fallback = models.get(fallback_id)
    if not fallback or fallback.get("owned_by") == "arena" or fallback.get("pipe") or fallback.get("direct"):
        if postsubmit_job_id:
            from open_webui.routers.chatgpt_relay import abort_postsubmit_handoff
            abort_postsubmit_handoff(user.id, postsubmit_job_id, "Selected secondary model is unavailable.")
        raise HTTPException(503, "The selected secondary model is unavailable. Choose another secondary model.")
    if postsubmit_job_id:
        from open_webui.routers.chatgpt_relay import claim_postsubmit_handoff
        if not claim_postsubmit_handoff(user.id, postsubmit_job_id):
            raise HTTPException(409, "Secondary model handoff already started; refresh the chat for its result.")

    metadata = {**metadata, "fallback_attempted": True,
                "fallback_from": primary_id, "fallback_reason": reason,
                "model": fallback, "model_id": fallback_id}
    # Keep tool-loop continuations on the provider that actually answered.
    state_metadata = getattr(request.state, "metadata", None)
    if isinstance(state_metadata, dict):
        state_metadata.update(metadata)
    original["model"] = fallback_id
    original["metadata"] = metadata
    original.pop("model_item", None)
    # Do not let a primary model alias override the selected secondary model.
    if hasattr(request, "base_model_id"):
        delattr(request, "base_model_id")
    # Provider-specific runtime controls cannot be forwarded between model families.
    for key in ("num_ctx", "num_batch", "num_keep", "num_predict", "mirostat",
                "mirostat_eta", "mirostat_tau", "repeat_last_n", "repeat_penalty",
                "keep_alive", "options"):
        original.pop(key, None)
    log.warning("%s; using selected secondary model %s", reason, fallback_id)
    # This is the normal dispatcher with access checks, never a raw provider call.
    try:
        response = await dispatch(request, original, user, bypass_filter=False,
                                  bypass_system_prompt=bypass_system_prompt)
    except BaseException:
        if postsubmit_job_id:
            from open_webui.routers.chatgpt_relay import finish_postsubmit_handoff
            finish_postsubmit_handoff(
                user.id, postsubmit_job_id, False, "Selected secondary model could not start.",
            )
        raise
    if isinstance(response, StreamingResponse):
        async def stream():
            completed = False
            saw_error = False
            try:
                yield "data: " + json.dumps({"selected_model_id": fallback_id,
                                            "fallback_from": primary_id}) + "\n\n"
                async for chunk in response.body_iterator:
                    if postsubmit_job_id and isinstance(chunk, (bytes, str)):
                        for line in (chunk.decode("utf-8", "replace") if isinstance(chunk, bytes) else chunk).splitlines():
                            if line.startswith("data: {"):
                                try:
                                    event = json.loads(line[6:])
                                    saw_error |= isinstance(event, dict) and bool(event.get("error"))
                                except ValueError:
                                    pass
                    yield chunk
                completed = True
            finally:
                if postsubmit_job_id:
                    from open_webui.routers.chatgpt_relay import finish_postsubmit_handoff
                    finish_postsubmit_handoff(
                        user.id, postsubmit_job_id, completed and not saw_error,
                        "Selected secondary model failed or its response was interrupted.",
                    )
        headers = dict(response.headers)
        headers.pop("content-length", None)
        headers["X-ABIT-Model-Fallback"] = fallback_id
        return StreamingResponse(stream(), status_code=response.status_code,
                                 headers=headers, media_type=response.media_type,
                                 background=response.background)
    succeeded = (
        not (isinstance(response, dict) and response.get("error"))
        and getattr(response, "status_code", 200) < 400
    )
    if postsubmit_job_id:
        from open_webui.routers.chatgpt_relay import finish_postsubmit_handoff
        finish_postsubmit_handoff(
            user.id, postsubmit_job_id, succeeded,
            "Selected secondary model failed to complete.",
        )
    if isinstance(response, dict) and not response.get("error"):
        return {**response, "selected_model_id": fallback_id, "fallback_from": primary_id}
    if hasattr(response, "headers"):
        response.headers["X-ABIT-Model-Fallback"] = fallback_id
    return response

def preserve_secondary_model(stored, patch, updated):
    """Old clients omit the routing key; only an explicit value changes it."""
    saved = stored.get("params") or {}
    incoming = patch.get("params")
    if (isinstance(saved, dict) and isinstance(incoming, dict)
            and "fallback_model_id" in saved
            and "fallback_model_id" not in incoming):
        return {**updated, "params": {
            **incoming, "fallback_model_id": saved["fallback_model_id"]
        }}
    return updated
