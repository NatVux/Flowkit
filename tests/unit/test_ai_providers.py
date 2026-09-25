"""AI provider layer: selection, configuration, Gemini error/response mapping.

No network: GeminiProvider gets a fake client whose generate_content returns real
google-genai response objects or raises real google-genai error types.
"""

import asyncio
import json

import httpx
import pytest

genai_types = pytest.importorskip("google.genai.types")
genai_errors = pytest.importorskip("google.genai.errors")

from agent.services.ai import registry
from agent.services.ai.base import (
    AIContentBlockedError, AIEmptyResponseError, AINotConfiguredError, AIPartialResponseError,
    AIRateLimitError, AIRequest, AIRequestError, AIServerError, AITimeoutError,
)
from agent.services.ai.gemini import GeminiProvider
from agent.services.ai.mock import MockAIProvider

SECRET = "AIzaTEST-secret-key-value-123"


def _request(**kw) -> AIRequest:
    base = dict(operation="story_plan", system_instruction="sys", prompt="brief",
                response_schema={"type": "object"}, request_id="rid-1", timeout_seconds=5)
    base.update(kw)
    return AIRequest(**base)


def _response(text="{}", finish="STOP", block=None, parts=None, candidates=True):
    if not candidates:
        return genai_types.GenerateContentResponse(
            candidates=[],
            prompt_feedback=genai_types.GenerateContentResponsePromptFeedback(block_reason=block) if block else None,
        )
    parts = parts if parts is not None else [genai_types.Part(text=text)]
    return genai_types.GenerateContentResponse(
        candidates=[genai_types.Candidate(content=genai_types.Content(role="model", parts=parts),
                                          finish_reason=finish)],
        model_version="gemini-test-001",
    )


class FakeModels:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    async def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class FakeClient:
    def __init__(self, outcome):
        self.models = FakeModels(outcome)
        self.aio = self  # client.aio.models.generate_content


def _gemini(outcome) -> tuple[GeminiProvider, FakeClient]:
    client = FakeClient(outcome)
    return GeminiProvider(api_key=SECRET, model="gemini-test", client=client), client


def _api_error(cls, code, status):
    return cls(code, {"error": {"code": code, "status": status, "message": f"boom key={SECRET}"}})


# ─── provider selection ─────────────────────────────────────

class TestProviderSelection:
    def test_auto_without_key_is_disabled(self):
        assert registry.build_provider("", api_key="") is None

    def test_auto_with_key_selects_gemini(self):
        provider = registry.build_provider("", api_key=SECRET, model="m")
        assert isinstance(provider, GeminiProvider) and provider.configured and provider.model == "m"

    def test_explicit_mock(self):
        assert isinstance(registry.build_provider("mock"), MockAIProvider)

    @pytest.mark.parametrize("name", ["none", "off", "disabled", "NONE"])
    def test_explicitly_disabled_even_with_key(self, name):
        assert registry.build_provider(name, api_key=SECRET) is None

    def test_unknown_provider_is_rejected(self):
        with pytest.raises(ValueError):
            registry.build_provider("openai", api_key=SECRET)

    def test_unknown_provider_in_config_disables_ai_instead_of_crashing(self, monkeypatch):
        registry.reset_ai_provider()
        monkeypatch.setattr(registry.config, "AI_PROVIDER", "bogus")
        try:
            assert registry.get_ai_provider() is None
            assert registry.provider_status()["enabled"] is False
        finally:
            registry.reset_ai_provider()

    def test_status_reports_provider_without_secrets(self, monkeypatch):
        registry.reset_ai_provider()
        monkeypatch.setattr(registry.config, "AI_PROVIDER", "")
        monkeypatch.setattr(registry.config, "GEMINI_API_KEY", SECRET)
        monkeypatch.setattr(registry.config, "GEMINI_MODEL", "gemini-x")
        try:
            status = registry.provider_status()
            assert status == {"enabled": True, "provider": "gemini", "model": "gemini-x", "configured": True}
            assert SECRET not in json.dumps(status)
        finally:
            registry.reset_ai_provider()


# ─── missing key ────────────────────────────────────────────

class TestMissingKey:
    def test_gemini_without_key_is_not_configured(self):
        assert GeminiProvider(api_key="", model="m").configured is False

    async def test_gemini_without_key_refuses_to_call(self):
        with pytest.raises(AINotConfiguredError):
            await GeminiProvider(api_key="", model="m").generate_json(_request())

    def test_explicit_gemini_without_key_is_reported_unconfigured(self):
        provider = registry.build_provider("gemini", api_key="", model="m")
        assert provider is not None and provider.configured is False

    async def test_sdk_not_installed_is_not_configured(self, monkeypatch):
        import sys
        monkeypatch.setitem(sys.modules, "google.genai", None)  # makes `from google import genai` fail
        with pytest.raises(AINotConfiguredError, match="google-genai is not installed"):
            await GeminiProvider(api_key=SECRET, model="m").generate_json(_request())

    def test_repr_never_contains_the_key(self):
        assert SECRET not in repr(GeminiProvider(api_key=SECRET, model="m"))


# ─── Gemini request building ────────────────────────────────

class TestGeminiRequest:
    async def test_sends_structured_json_config(self):
        provider, client = _gemini(_response('{"ok": true}'))
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
        result = await provider.generate_json(_request(response_schema=schema, timeout_seconds=12))
        call = client.models.calls[0]
        assert call["model"] == "gemini-test" and call["contents"] == "brief"
        assert call["config"].response_mime_type == "application/json"
        assert call["config"].response_json_schema == schema
        assert call["config"].system_instruction == "sys"
        assert call["config"].http_options.timeout == 12000
        assert result.text == '{"ok": true}' and result.model == "gemini-test-001"


# ─── Gemini error mapping ───────────────────────────────────

class TestGeminiErrors:
    async def test_reason_codes_are_surfaced_but_messages_are_not(self):
        body = {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": f"API key not valid {SECRET}",
                          "details": [{"reason": "API_KEY_INVALID"}, {"reason": "free text " + SECRET}]}}
        provider, _ = _gemini(genai_errors.ClientError(400, body))
        with pytest.raises(AIRequestError) as info:
            await provider.generate_json(_request())
        assert "API_KEY_INVALID" in str(info.value) and SECRET not in str(info.value)

    async def test_automatic_function_calling_is_disabled(self):
        provider, client = _gemini(_response("{}"))
        await provider.generate_json(_request())
        assert client.models.calls[0]["config"].automatic_function_calling.disable is True

    @pytest.mark.parametrize("exc, expected, retryable", [
        (_api_error(genai_errors.ClientError, 429, "RESOURCE_EXHAUSTED"), AIRateLimitError, True),
        (_api_error(genai_errors.ServerError, 503, "UNAVAILABLE"), AIServerError, True),
        (_api_error(genai_errors.ServerError, 500, "INTERNAL"), AIServerError, True),
        (_api_error(genai_errors.ServerError, 504, "DEADLINE_EXCEEDED"), AITimeoutError, True),
        (_api_error(genai_errors.ClientError, 400, "INVALID_ARGUMENT"), AIRequestError, False),
        (_api_error(genai_errors.ClientError, 401, "UNAUTHENTICATED"), AIRequestError, False),
        (_api_error(genai_errors.ClientError, 403, "PERMISSION_DENIED"), AIRequestError, False),
        (_api_error(genai_errors.ClientError, 404, "NOT_FOUND"), AIRequestError, False),
        (httpx.ReadTimeout("read timed out"), AITimeoutError, True),
        (httpx.ConnectError("refused"), AIServerError, True),
        (asyncio.TimeoutError(), AITimeoutError, True),
        (RuntimeError("unexpected"), AIServerError, True),
    ])
    async def test_sdk_failures_become_typed_errors(self, exc, expected, retryable):
        provider, _ = _gemini(exc)
        with pytest.raises(expected) as info:
            await provider.generate_json(_request())
        assert info.value.retryable is retryable
        assert SECRET not in str(info.value)  # provider message bodies are never echoed


# ─── Gemini response handling ───────────────────────────────

class TestGeminiResponses:
    async def test_prompt_block_is_content_blocked(self):
        provider, _ = _gemini(_response(candidates=False, block="SAFETY"))
        with pytest.raises(AIContentBlockedError):
            await provider.generate_json(_request())

    async def test_no_candidates_is_empty(self):
        provider, _ = _gemini(_response(candidates=False))
        with pytest.raises(AIEmptyResponseError) as info:
            await provider.generate_json(_request())
        assert info.value.retryable

    async def test_blank_text_is_empty(self):
        provider, _ = _gemini(_response("   "))
        with pytest.raises(AIEmptyResponseError):
            await provider.generate_json(_request())

    async def test_safety_finish_is_content_blocked(self):
        provider, _ = _gemini(_response('{"a": 1}', finish="SAFETY"))
        with pytest.raises(AIContentBlockedError) as info:
            await provider.generate_json(_request())
        assert not info.value.retryable

    async def test_max_tokens_is_partial_and_not_retryable(self):
        provider, _ = _gemini(_response('{"title": "cut of', finish="MAX_TOKENS"))
        with pytest.raises(AIPartialResponseError) as info:
            await provider.generate_json(_request())
        assert not info.value.retryable

    async def test_thought_parts_are_excluded(self):
        parts = [genai_types.Part(text="thinking...", thought=True), genai_types.Part(text='{"a": 1}')]
        provider, _ = _gemini(_response(parts=parts))
        assert (await provider.generate_json(_request())).text == '{"a": 1}'


# ─── mock provider ──────────────────────────────────────────

class TestMockProvider:
    async def test_scripted_outcomes_then_defaults(self):
        mock = MockAIProvider([{"x": 1}, "raw", AITimeoutError("slow")])
        assert json.loads((await mock.generate_json(_request())).text) == {"x": 1}
        assert (await mock.generate_json(_request())).text == "raw"
        with pytest.raises(AITimeoutError):
            await mock.generate_json(_request())
        default = json.loads((await mock.generate_json(_request())).text)
        assert default["scenes"] and len(mock.calls) == 4
