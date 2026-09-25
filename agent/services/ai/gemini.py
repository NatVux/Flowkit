"""Gemini implementation of AIProvider, on the official `google-genai` SDK.

The SDK is imported lazily so the agent starts (and every non-AI feature works)
when the package is missing or no key is configured. The SDK's own retry is left
off (its default); retries are decided by AIContentService from the error types.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from agent.services.ai.base import (
    AIContentBlockedError, AIEmptyResponseError, AINotConfiguredError, AIPartialResponseError,
    AIProviderError, AIRateLimitError, AIRequest, AIRequestError, AIResponse, AIServerError,
    AITimeoutError,
)
from agent.services.ai.gemini_schema import to_gemini_schema

logger = logging.getLogger(__name__)

_BLOCKED_FINISH = {"SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "RECITATION"}
_PARTIAL_FINISH = {"MAX_TOKENS"}
_GOOGLE_KEY = re.compile(r"AIza[0-9A-Za-z_\-]+")


def _sdk():
    """The google.genai module. Missing SDK means 'not configured', never a crash."""
    try:
        import google.genai as genai
        import google.genai.types  # noqa: F401  (ensures the submodule is importable)
    except ImportError as exc:
        raise AINotConfiguredError("google-genai is not installed (pip install google-genai)") from exc
    return genai


def _enum_name(value: Any) -> str | None:
    if value is None:
        return None
    return getattr(value, "name", None) or str(value).rsplit(".", 1)[-1]


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, model: str, *, client: Any = None):
        self.model = model
        self._api_key = api_key
        self._client = client

    def __repr__(self) -> str:  # never include the key
        return f"GeminiProvider(model={self.model!r}, configured={self.configured})"

    @property
    def configured(self) -> bool:
        return bool(self._api_key or self._client)

    def _get_client(self):
        if self._client is None:
            if not self._api_key:
                raise AINotConfiguredError("GEMINI_API_KEY is not set")
            self._client = _sdk().Client(api_key=self._api_key)
        return self._client

    def _config(self, request: AIRequest):
        types = _sdk().types
        kwargs: dict[str, Any] = {
            "system_instruction": request.system_instruction,
            "response_mime_type": "application/json",
            "response_json_schema": to_gemini_schema(request.response_schema),
            "http_options": types.HttpOptions(timeout=int(request.timeout_seconds * 1000)),
            # No tools are declared; keep the SDK's automatic function calling off.
            "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True),
        }
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            kwargs["max_output_tokens"] = request.max_output_tokens
        return types.GenerateContentConfig(**kwargs)

    async def generate_json(self, request: AIRequest) -> AIResponse:
        client = self._get_client()
        try:
            config = self._config(request)
            # The SDK has its own timeout; this outer guard covers anything it misses.
            response = await asyncio.wait_for(
                client.aio.models.generate_content(model=self.model, contents=request.prompt, config=config),
                timeout=request.timeout_seconds + 5,
            )
        except AIProviderError:
            raise
        except asyncio.TimeoutError as exc:
            raise AITimeoutError(f"Gemini did not answer within {request.timeout_seconds:.0f}s") from exc
        except Exception as exc:  # noqa: BLE001 — translated below, never leaked raw
            raise _translate_exception(exc, request.request_id, self._api_key) from exc
        return _read_response(response, self.model)


def _translate_exception(exc: Exception, request_id: str | None = None, api_key: str = "") -> AIProviderError:
    try:
        from google.genai import errors as genai_errors
    except ImportError:  # pragma: no cover - SDK present whenever this runs
        genai_errors = None
    try:
        import httpx
        timeout_types: tuple = (httpx.TimeoutException,)
        transport_types: tuple = (httpx.TransportError,)
    except ImportError:  # pragma: no cover
        timeout_types, transport_types = (), ()

    if timeout_types and isinstance(exc, timeout_types):
        return AITimeoutError("Gemini request timed out")
    if genai_errors is not None and isinstance(exc, genai_errors.APIError):
        code = getattr(exc, "code", None)
        status = getattr(exc, "status", None) or ""
        # Gemini's own message goes to the server log only (it can explain a 400), never to the caller.
        logger.warning("gemini api error request_id=%s code=%s status=%s message=%s",
                       request_id, code, status, _redact(getattr(exc, "message", None) or "", api_key)[:300])
        reasons = _error_reasons(getattr(exc, "details", None))
        message = f"Gemini API error {code} {status}".strip() + (f" [{', '.join(reasons)}]" if reasons else "")
        if code == 429 or status == "RESOURCE_EXHAUSTED":
            return AIRateLimitError(message, status=code)
        if code in (408, 504) or status == "DEADLINE_EXCEEDED":
            return AITimeoutError(message, status=code)
        if isinstance(exc, genai_errors.ServerError) or (isinstance(code, int) and code >= 500):
            return AIServerError(message, status=code)
        return AIRequestError(message, status=code)
    if transport_types and isinstance(exc, transport_types):
        return AIServerError(f"Gemini connection failed: {type(exc).__name__}")
    return AIServerError(f"Gemini call failed: {type(exc).__name__}")


def _redact(text: str, api_key: str) -> str:
    """Strip anything that looks like a Google API key before a provider message is logged."""
    if api_key:
        text = text.replace(api_key, "[REDACTED]")
    return _GOOGLE_KEY.sub("[REDACTED]", text)


def _error_reasons(details: Any) -> list[str]:
    """Machine reason codes (e.g. API_KEY_INVALID) from a Google error body.

    Only upper-case enum tokens are kept; free-text messages are never surfaced,
    since they can echo request content.
    """
    error = details.get("error") if isinstance(details, dict) else None
    items = error.get("details") if isinstance(error, dict) else None
    reasons = []
    for item in items if isinstance(items, list) else []:
        reason = item.get("reason") if isinstance(item, dict) else None
        if isinstance(reason, str) and reason.replace("_", "").isalnum() and reason.isupper() and len(reason) <= 64:
            reasons.append(reason)
    return reasons


def _read_response(response: Any, model: str) -> AIResponse:
    feedback = getattr(response, "prompt_feedback", None)
    block_reason = _enum_name(getattr(feedback, "block_reason", None)) if feedback else None
    if block_reason and block_reason != "BLOCKED_REASON_UNSPECIFIED":
        raise AIContentBlockedError(f"Gemini blocked the prompt: {block_reason}")

    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        raise AIEmptyResponseError("Gemini returned no candidates")
    candidate = candidates[0]
    finish = _enum_name(getattr(candidate, "finish_reason", None))
    if finish in _BLOCKED_FINISH:
        raise AIContentBlockedError(f"Gemini stopped generation: {finish}")

    content = getattr(candidate, "content", None)
    parts = getattr(content, "parts", None) or []
    text = "".join(
        part.text for part in parts
        if getattr(part, "text", None) and not getattr(part, "thought", False)
    ).strip()
    if finish in _PARTIAL_FINISH:
        raise AIPartialResponseError(f"Gemini output was truncated ({finish}, {len(text)} chars)")
    if not text:
        raise AIEmptyResponseError("Gemini returned an empty response")

    usage_meta = getattr(response, "usage_metadata", None)
    usage = {}
    if usage_meta is not None:
        for key in ("prompt_token_count", "candidates_token_count", "total_token_count"):
            value = getattr(usage_meta, key, None)
            if value is not None:
                usage[key] = value
    return AIResponse(text=text, model=getattr(response, "model_version", None) or model,
                      finish_reason=finish, usage=usage)
