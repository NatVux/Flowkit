"""GeminiProvider + AIContentService end to end, with a fake google-genai client (no network).

The fake records each config it is sent and plays a script of responses/exceptions.
"""

import json
import logging

import pytest

genai_types = pytest.importorskip("google.genai.types")
genai_errors = pytest.importorskip("google.genai.errors")

from agent import config
from agent.db import crud, schema
from agent.models.ai_content import StoryPlanRequest
from agent.services.ai.base import AIRequest, AIRequestError, AIServerError
from agent.services.ai.gemini import GeminiProvider
from agent.services.ai.mock import default_story_plan
from agent.services.ai_content import AIContentService
from tests.unit.test_gemini_schema import _load as load_storyplan_schema, _unsupported

SECRET = "AIzaTEST-secret-key-value-123"
GEMINI_400_TEXT = "The specified schema produces a constraint that has too many states for serving."


class FakeModels:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        outcome = self.script.pop(0) if len(self.script) > 1 else self.script[0]  # last one repeats
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeClient:
    def __init__(self, script):
        self.models = FakeModels(script)
        self.aio = self  # client.aio.models.generate_content


def _make_provider(*script) -> tuple[GeminiProvider, FakeClient]:
    client = FakeClient(script)
    return GeminiProvider(api_key=SECRET, model="gemini-test", client=client), client


def _make_request(**kw) -> AIRequest:
    base = dict(operation="story_plan", system_instruction="sys", prompt="brief",
                response_schema=load_storyplan_schema(), request_id="rid-42", timeout_seconds=5)
    base.update(kw)
    return AIRequest(**base)


def _response(text: str):
    return genai_types.GenerateContentResponse(
        candidates=[genai_types.Candidate(content=genai_types.Content(role="model", parts=[genai_types.Part(text=text)]),
                                          finish_reason="STOP")],
        model_version="gemini-test-001",
    )


def _error(cls, code, status, message):
    return cls(code, {"error": {"code": code, "status": status, "message": message}})


def _400():
    return _error(genai_errors.ClientError, 400, "INVALID_ARGUMENT", GEMINI_400_TEXT)


def _503():
    return _error(genai_errors.ServerError, 503, "UNAVAILABLE", "The model is overloaded. Please try again later.")


PLAN_JSON = json.dumps(default_story_plan())


# ─── provider level ─────────────────────────────────────────

class TestProvider:
    async def test_sent_schema_is_gemini_safe(self):
        provider, client = _make_provider(_response(PLAN_JSON))
        await provider.generate_json(_make_request())
        sent = client.models.calls[0]["config"].response_json_schema
        assert _unsupported(sent) == []
        assert sent["properties"]["scenes"]["items"]["properties"]["narration"]["type"] == ["string", "null"]
        assert sent["required"] == ["title", "logline", "story", "scenes"]

    async def test_request_schema_is_left_untouched(self):
        request = _make_request()
        provider, _ = _make_provider(_response(PLAN_JSON))
        await provider.generate_json(request)
        assert request.response_schema == load_storyplan_schema()

    async def test_400_message_is_logged_but_not_raised(self, caplog):
        provider, _ = _make_provider(_400())
        with caplog.at_level(logging.WARNING, logger="agent.services.ai.gemini"):
            with pytest.raises(AIRequestError) as info:
                await provider.generate_json(_make_request())
        assert info.value.code == "AI_REQUEST_REJECTED" and info.value.retryable is False
        assert GEMINI_400_TEXT not in str(info.value)
        assert "400" in str(info.value) and "INVALID_ARGUMENT" in str(info.value)
        logged = [r.getMessage() for r in caplog.records if r.name == "agent.services.ai.gemini"]
        assert any(GEMINI_400_TEXT in m and "rid-42" in m and "code=400" in m for m in logged), logged

    async def test_logged_message_is_truncated(self, caplog):
        provider, _ = _make_provider(_error(genai_errors.ClientError, 400, "INVALID_ARGUMENT", "x" * 1000))
        with caplog.at_level(logging.WARNING, logger="agent.services.ai.gemini"):
            with pytest.raises(AIRequestError):
                await provider.generate_json(_make_request())
        logged = [r.getMessage() for r in caplog.records if r.name == "agent.services.ai.gemini"]
        assert any("x" * 300 in m and "x" * 301 not in m for m in logged)

    async def test_api_keys_in_the_message_are_redacted_from_the_log(self, caplog):
        other_key = "AIzaSyOTHERkeyOTHERkeyOTHERkey123456"
        provider, _ = _make_provider(_error(genai_errors.ClientError, 400, "INVALID_ARGUMENT",
                                            f"API key not valid: {SECRET} / {other_key}"))
        with caplog.at_level(logging.WARNING, logger="agent.services.ai.gemini"):
            with pytest.raises(AIRequestError):
                await provider.generate_json(_make_request())
        assert "API key not valid" in caplog.text
        assert SECRET not in caplog.text and other_key not in caplog.text

    async def test_key_straddling_the_truncation_point_is_redacted(self, caplog):
        # Redaction runs before the 300-char cut, so a key crossing position 300 leaves no "AIza" prefix.
        key = "AIzaSyCROSSINGtheTRUNCATIONpoint_12345"
        provider, _ = _make_provider(_error(genai_errors.ClientError, 400, "INVALID_ARGUMENT",
                                            "m" * 290 + key + " tail " * 50))
        with caplog.at_level(logging.WARNING, logger="agent.services.ai.gemini"):
            with pytest.raises(AIRequestError):
                await provider.generate_json(_make_request())
        logged = [r.getMessage() for r in caplog.records if r.name == "agent.services.ai.gemini"]
        assert logged and all("AIza" not in m for m in logged)
        assert any("m" * 290 + "[REDACTED]" in m for m in logged)

    async def test_503_is_retryable_server_error(self):
        provider, _ = _make_provider(_503())
        with pytest.raises(AIServerError) as info:
            await provider.generate_json(_make_request())
        assert info.value.retryable is True


# ─── through AIContentService ───────────────────────────────

@pytest.fixture
async def project(tmp_path, monkeypatch):
    await schema.close_db()
    monkeypatch.setattr(schema, "DB_PATH", tmp_path / "gemini.db")
    await schema.init_db()
    yield await crud.create_project(name="Gemini project", material="realistic")
    await schema.close_db()


class Sleeps:
    def __init__(self):
        self.delays = []

    async def __call__(self, delay):
        self.delays.append(delay)


def _service(provider):
    sleeps = Sleeps()
    # max_retries left unset: the service must use config.GEMINI_MAX_RETRIES.
    return AIContentService(lambda: provider, timeout_seconds=5, sleep=sleeps), sleeps


def _story_req(project):
    return StoryPlanRequest(project_id=project["id"], brief="A cat sells fish", scene_count=2)


class TestService:
    async def test_valid_json_is_validated_into_a_story_plan(self, project):
        provider, client = _make_provider(_response(PLAN_JSON))
        service, sleeps = _service(provider)
        gen = await service.generate_story_plan(_story_req(project))
        assert gen["status"] == "GENERATED" and gen["attempts"] == 1 and gen["provider"] == "gemini"
        assert gen["output"]["title"] == "The Fish Merchant" and len(gen["output"]["scenes"]) == 2
        assert sleeps.delays == []
        assert _unsupported(client.models.calls[0]["config"].response_json_schema) == []

    async def test_503_then_success_is_retried(self, project):
        if config.GEMINI_MAX_RETRIES < 1:
            pytest.skip("GEMINI_MAX_RETRIES=0 disables retries")
        provider, client = _make_provider(_503(), _response(PLAN_JSON))
        service, sleeps = _service(provider)
        gen = await service.generate_story_plan(_story_req(project))
        assert gen["attempts"] == 2 and len(client.models.calls) == 2
        assert len(sleeps.delays) == 1

    async def test_persistent_503_stops_after_configured_attempts(self, project):
        provider, client = _make_provider(_503())
        service, sleeps = _service(provider)
        with pytest.raises(AIServerError):
            await service.generate_story_plan(_story_req(project))
        attempts = 1 + config.GEMINI_MAX_RETRIES
        assert len(client.models.calls) == attempts
        assert len(sleeps.delays) == attempts - 1

    async def test_400_is_not_retried(self, project):
        provider, client = _make_provider(_400())
        service, sleeps = _service(provider)
        with pytest.raises(AIRequestError) as info:
            await service.generate_story_plan(_story_req(project))
        assert len(client.models.calls) == 1 and sleeps.delays == []
        assert GEMINI_400_TEXT not in str(info.value)
