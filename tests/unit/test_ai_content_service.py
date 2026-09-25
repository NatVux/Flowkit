"""AIContentService + /api/ai routes, on a temporary SQLite database with MockAIProvider."""

import json
import logging

import httpx
import pytest

from agent.db import crud, schema
from agent.models.ai_content import StoryPlanRequest, YouTubeMetadataRequest
from agent.services.ai import registry
from agent.services.ai.base import (
    AIEmptyResponseError, AIMalformedResponseError, AINotConfiguredError, AIPartialResponseError,
    AIRateLimitError, AIRequestError, AIServerError, AITimeoutError,
)
from agent.services.ai.gemini import GeminiProvider
from agent.services.ai.mock import MockAIProvider, default_story_plan, default_youtube_metadata
from agent.services.ai_content import AIContentService, AIContentUnavailable

SECRET = "AIzaTEST-secret-key-value-123"


@pytest.fixture
async def ai_db(tmp_path, monkeypatch):
    await schema.close_db()
    monkeypatch.setattr(schema, "DB_PATH", tmp_path / "ai.db")
    await schema.init_db()
    yield
    await schema.close_db()


@pytest.fixture
async def project_video(ai_db):
    project = await crud.create_project(name="AI project", material="realistic")
    video = await crud.create_video(project_id=project["id"], title="Working title")
    return project, video


class Sleeps:
    def __init__(self):
        self.delays = []

    async def __call__(self, delay):
        self.delays.append(delay)


def _service(provider, retries=2):
    sleeps = Sleeps()
    return AIContentService(lambda: provider, max_retries=retries, timeout_seconds=5, sleep=sleeps), sleeps


async def _row_count(table: str) -> int:
    db = await schema.get_db()
    cur = await db.execute(f"SELECT COUNT(*) FROM {table}")
    return (await cur.fetchone())[0]


def _story_req(project, **kw):
    return StoryPlanRequest(project_id=project["id"], brief="A cat sells fish", scene_count=2, **kw)


# ─── generation ─────────────────────────────────────────────

class TestStoryGeneration:
    async def test_success_stores_validated_plan(self, project_video):
        project, _ = project_video
        mock = MockAIProvider()
        service, sleeps = _service(mock)

        gen = await service.generate_story_plan(_story_req(project, tone="warm"))

        assert gen["operation"] == "STORY_PLAN" and gen["status"] == "GENERATED"
        assert gen["provider"] == "mock" and gen["attempts"] == 1 and gen["request_id"]
        assert gen["output"]["scenes"][1]["continues_previous"] is True
        assert (await crud.get_ai_generation(gen["id"]))["output"] == gen["output"]
        request = mock.calls[0]
        assert request.operation == "story_plan"
        assert "exactly 2" in request.prompt and "Tone: warm" in request.prompt
        assert request.response_schema["type"] == "object"
        assert set(request.response_schema["required"]) >= {"title", "story", "scenes"}
        assert sleeps.delays == []

    async def test_fenced_json_is_accepted(self, project_video):
        project, _ = project_video
        fenced = "```json\n" + json.dumps(default_story_plan()) + "\n```"
        service, _ = _service(MockAIProvider([fenced]))
        gen = await service.generate_story_plan(_story_req(project))
        assert gen["output"]["title"] == "The Fish Merchant"

    async def test_existing_entities_are_offered_for_reuse(self, project_video):
        project, _ = project_video
        char = await crud.create_character(name="Old Hero")
        await crud.link_character_to_project(project["id"], char["id"])
        mock = MockAIProvider()
        await _service(mock)[0].generate_story_plan(_story_req(project))
        assert "Old Hero (character)" in mock.calls[0].prompt


class TestInvalidOutput:
    async def test_malformed_json_is_retried_then_fails_without_storing(self, project_video):
        project, _ = project_video
        mock = MockAIProvider(["{not json", "still not json", "[1, 2]"])
        service, sleeps = _service(mock, retries=2)
        with pytest.raises(AIMalformedResponseError):
            await service.generate_story_plan(_story_req(project))
        assert len(mock.calls) == 3 and sleeps.delays == [2.0, 4.0]
        assert await _row_count("ai_generation") == 0

    @pytest.mark.parametrize("mutate", [
        lambda p: p["scenes"][0]["character_names"].append("Nobody"),          # undefined entity
        lambda p: p["scenes"][0].__setitem__("continues_previous", True),       # first scene continues
        lambda p: p.__setitem__("scenes", []),                                  # no scenes
        lambda p: p["characters"].append(dict(p["characters"][0])),            # duplicate name
        lambda p: p["characters"][0].__setitem__("entity_type", "spaceship"),   # bad enum
        lambda p: p.pop("title"),                                               # missing field
    ])
    async def test_schema_violations_are_malformed(self, project_video, mutate):
        project, _ = project_video
        plan = default_story_plan()
        mutate(plan)
        service, _ = _service(MockAIProvider([plan]), retries=0)
        with pytest.raises(AIMalformedResponseError):
            await service.generate_story_plan(_story_req(project))
        assert await _row_count("ai_generation") == 0

    async def test_empty_response_is_retried(self, project_video):
        project, _ = project_video
        mock = MockAIProvider([AIEmptyResponseError("empty"), default_story_plan()])
        service, sleeps = _service(mock)
        gen = await service.generate_story_plan(_story_req(project))
        assert gen["attempts"] == 2 and sleeps.delays == [2.0]

    async def test_partial_response_is_not_retried(self, project_video):
        project, _ = project_video
        mock = MockAIProvider([AIPartialResponseError("cut off")])
        service, sleeps = _service(mock)
        with pytest.raises(AIPartialResponseError):
            await service.generate_story_plan(_story_req(project))
        assert len(mock.calls) == 1 and sleeps.delays == []


class TestRetryPolicy:
    async def test_timeout_is_retried_then_succeeds(self, project_video):
        project, _ = project_video
        mock = MockAIProvider([AITimeoutError("slow"), default_story_plan()])
        service, sleeps = _service(mock)
        gen = await service.generate_story_plan(_story_req(project))
        assert gen["attempts"] == 2 and len(mock.calls) == 2 and sleeps.delays == [2.0]
        # every attempt of one operation shares a request id
        assert mock.calls[0].request_id == mock.calls[1].request_id == gen["request_id"]

    async def test_persistent_timeout_gives_up_after_max_retries(self, project_video):
        project, _ = project_video
        mock = MockAIProvider([AITimeoutError("slow")] * 5)
        service, sleeps = _service(mock, retries=2)
        with pytest.raises(AITimeoutError) as info:
            await service.generate_story_plan(_story_req(project))
        assert len(mock.calls) == 3 and sleeps.delays == [2.0, 4.0]
        assert info.value.request_id == mock.calls[0].request_id

    async def test_rate_limit_is_retryable(self, project_video):
        project, _ = project_video
        mock = MockAIProvider([AIRateLimitError("429", status=429), AIServerError("503"), default_story_plan()])
        gen = await _service(mock)[0].generate_story_plan(_story_req(project))
        assert gen["attempts"] == 3

    async def test_api_failure_non_retryable_is_not_retried(self, project_video):
        project, _ = project_video
        mock = MockAIProvider([AIRequestError("400 INVALID_ARGUMENT", status=400), default_story_plan()])
        service, sleeps = _service(mock)
        with pytest.raises(AIRequestError):
            await service.generate_story_plan(_story_req(project))
        assert len(mock.calls) == 1 and sleeps.delays == []
        assert await _row_count("ai_generation") == 0

    async def test_zero_retries_means_one_attempt(self, project_video):
        project, _ = project_video
        mock = MockAIProvider([AIServerError("503")])
        with pytest.raises(AIServerError):
            await _service(mock, retries=0)[0].generate_story_plan(_story_req(project))
        assert len(mock.calls) == 1


class TestConfiguration:
    async def test_disabled_provider(self, project_video):
        project, _ = project_video
        with pytest.raises(AIContentUnavailable):
            await _service(None)[0].generate_story_plan(_story_req(project))

    async def test_missing_api_key(self, project_video):
        project, _ = project_video
        with pytest.raises(AINotConfiguredError):
            await _service(GeminiProvider(api_key="", model="m"))[0].generate_story_plan(_story_req(project))
        assert await _row_count("ai_generation") == 0

    async def test_unconfigured_is_reported_before_lookups(self, ai_db):
        with pytest.raises(AIContentUnavailable):
            await _service(None)[0].generate_story_plan(StoryPlanRequest(project_id="missing", brief="x"))
        with pytest.raises(AINotConfiguredError):
            await _service(GeminiProvider(api_key="", model="m"))[0].generate_youtube_metadata(
                YouTubeMetadataRequest(video_id="missing"))

    async def test_unknown_project(self, ai_db):
        with pytest.raises(LookupError):
            await _service(MockAIProvider())[0].generate_story_plan(
                StoryPlanRequest(project_id="missing", brief="x"))

    async def test_api_key_never_logged(self, project_video, caplog):
        project, _ = project_video
        from google.genai import errors as genai_errors

        class Client:
            def __init__(self):
                self.aio = self
                self.models = self

            async def generate_content(self, **kw):
                raise genai_errors.ClientError(403, {"error": {"code": 403, "status": "PERMISSION_DENIED",
                                                               "message": f"bad key {SECRET}"}})

        provider = GeminiProvider(api_key=SECRET, model="m", client=Client())
        caplog.set_level(logging.DEBUG)
        with pytest.raises(AIRequestError):
            await _service(provider)[0].generate_story_plan(_story_req(project))
        assert "ai_request_failed" in caplog.text and SECRET not in caplog.text


# ─── apply (non-idempotent) ─────────────────────────────────

class TestApplyStoryPlan:
    async def test_writes_characters_scenes_and_story_once(self, project_video):
        project, video = project_video
        mock = MockAIProvider()
        service, _ = _service(mock)
        gen = await service.generate_story_plan(_story_req(project))
        calls_before = len(mock.calls)

        result = await service.apply_generation(gen["id"], video["id"])

        assert result["status"] == "APPLIED" and len(result["scenes_created"]) == 2
        assert len(result["characters_created"]) == 2  # Pippip + Fish Stall
        assert len(mock.calls) == calls_before        # apply never calls the provider
        chars = {c["name"]: c for c in await crud.get_project_characters(project["id"])}
        assert chars["Fish Stall"]["entity_type"] == "location"
        assert chars["Pippip"]["voice_description"] and "reference image of" in chars["Pippip"]["image_prompt"].lower()
        scenes = await crud.list_scenes(video["id"])
        assert [s["display_order"] for s in scenes] == [0, 1]
        assert scenes[0]["prompt"].startswith("Real RAW photograph")  # material scene_prefix applied
        assert json.loads(scenes[0]["character_names"]) == ["Pippip", "Fish Stall"]
        assert scenes[0]["narrator_text"].startswith("Every morning")
        # default: independent scenes (Veo start+end chaining is unported)
        assert [s["chain_type"] for s in scenes] == ["ROOT", "ROOT"] and scenes[1]["parent_scene_id"] is None
        assert scenes[0]["vertical_image_status"] == "PENDING"  # ready for the normal worker
        assert (await crud.get_project(project["id"]))["story"].startswith("Pippip runs")
        stored = await crud.get_ai_generation(gen["id"])
        assert stored["status"] == "APPLIED" and stored["video_id"] == video["id"] and stored["applied_at"]

    async def test_chain_scenes_opt_in_creates_continuations(self, project_video):
        project, video = project_video
        service, _ = _service(MockAIProvider())
        gen = await service.generate_story_plan(_story_req(project))
        await service.apply_generation(gen["id"], video["id"], chain_scenes=True)
        scenes = await crud.list_scenes(video["id"])
        assert scenes[1]["chain_type"] == "CONTINUATION" and scenes[1]["parent_scene_id"] == scenes[0]["id"]

    async def test_second_apply_is_rejected_not_duplicated(self, project_video):
        project, video = project_video
        service, _ = _service(MockAIProvider())
        gen = await service.generate_story_plan(_story_req(project))
        await service.apply_generation(gen["id"], video["id"])
        with pytest.raises(crud.AIGenerationConflict):
            await service.apply_generation(gen["id"], video["id"])
        assert len(await crud.list_scenes(video["id"])) == 2

    async def test_failed_apply_rolls_back_everything(self, project_video):
        project, _ = project_video
        other = await crud.create_project(name="Other", id="other-project")
        foreign_video = await crud.create_video(project_id=other["id"], title="Not yours")
        service, _ = _service(MockAIProvider())
        gen = await service.generate_story_plan(_story_req(project))
        chars_before = await _row_count("character")

        with pytest.raises(crud.AIGenerationConflict):
            await service.apply_generation(gen["id"], foreign_video["id"])

        assert (await crud.get_ai_generation(gen["id"]))["status"] == "GENERATED"
        assert await _row_count("character") == chars_before and await _row_count("scene") == 0

    async def test_reuses_linked_entities_and_disambiguates_global_slugs(self, project_video):
        project, video = project_video
        mine = await crud.create_character(name="Pippip")
        await crud.link_character_to_project(project["id"], mine["id"])
        await crud.create_character(name="Fish Stall")  # same slug, other project, not linked
        service, _ = _service(MockAIProvider())
        gen = await service.generate_story_plan(_story_req(project))

        result = await service.apply_generation(gen["id"], video["id"])

        assert result["characters_reused"] == [mine["id"]]
        linked = {c["name"]: c["slug"] for c in await crud.get_project_characters(project["id"])}
        assert linked == {"Pippip": "pippip", "Fish Stall": "fish_stall_2"}

    async def test_scenes_append_after_existing(self, project_video):
        project, video = project_video
        await crud.create_scene(video_id=video["id"], display_order=0, prompt="hand-written")
        service, _ = _service(MockAIProvider())
        gen = await service.generate_story_plan(_story_req(project))
        await service.apply_generation(gen["id"], video["id"])
        assert [s["display_order"] for s in await crud.list_scenes(video["id"])] == [0, 1, 2]

    async def test_story_plan_requires_video(self, project_video):
        project, _ = project_video
        service, _ = _service(MockAIProvider())
        gen = await service.generate_story_plan(_story_req(project))
        with pytest.raises(ValueError):
            await service.apply_generation(gen["id"], None)

    async def test_tampered_stored_output_is_revalidated(self, project_video):
        project, video = project_video
        service, _ = _service(MockAIProvider())
        gen = await service.generate_story_plan(_story_req(project))
        db = await schema.get_db()
        await db.execute("UPDATE ai_generation SET output_json=? WHERE id=?", (json.dumps({"scenes": []}), gen["id"]))
        await db.commit()
        with pytest.raises(ValueError):
            await service.apply_generation(gen["id"], video["id"])
        assert await _row_count("scene") == 0


class TestYouTubeMetadata:
    async def test_generate_and_apply(self, project_video):
        _, video = project_video
        service, _ = _service(MockAIProvider())
        gen = await service.generate_youtube_metadata(YouTubeMetadataRequest(video_id=video["id"]))
        assert gen["operation"] == "YOUTUBE_METADATA" and gen["video_id"] == video["id"]

        result = await service.apply_generation(gen["id"], None)

        assert result["video_updated"] is True
        updated = await crud.get_video(video["id"])
        assert updated["title"].startswith("The Fish Merchant")
        assert updated["description"].endswith("#animation #cat")
        assert json.loads(updated["tags"]) == ["animation", "cat", "short story"]

    async def test_over_limit_tags_are_malformed(self, project_video):
        _, video = project_video
        bad = default_youtube_metadata()
        bad["tags"] = ["x" * 100] * 6  # > 500 characters in total
        service, _ = _service(MockAIProvider([bad]), retries=0)
        with pytest.raises(AIMalformedResponseError):
            await service.generate_youtube_metadata(YouTubeMetadataRequest(video_id=video["id"]))

    async def test_unknown_video(self, ai_db):
        with pytest.raises(LookupError):
            await _service(MockAIProvider())[0].generate_youtube_metadata(YouTubeMetadataRequest(video_id="nope"))


# ─── HTTP layer ─────────────────────────────────────────────

@pytest.fixture
async def api(project_video):
    from agent.main import app
    transport = httpx.ASGITransport(app=app)  # no lifespan: no worker/WS server started
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    registry.reset_ai_provider()


class TestRoutes:
    async def test_disabled_ai_does_not_break_the_app(self, api, project_video):
        project, _ = project_video
        registry.set_ai_provider(None)
        assert (await api.get("/api/ai/status")).json() == {
            "enabled": False, "provider": None, "model": None, "configured": False}
        r = await api.post("/api/ai/story-plan", json={"project_id": project["id"], "brief": "x"})
        assert r.status_code == 503
        assert (await api.get(f"/api/projects/{project['id']}")).status_code == 200

    async def test_story_plan_apply_flow(self, api, project_video):
        project, video = project_video
        registry.set_ai_provider(MockAIProvider())
        r = await api.post("/api/ai/story-plan", json={"project_id": project["id"], "brief": "cat", "scene_count": 2})
        assert r.status_code == 200, r.text
        gid = r.json()["id"]
        assert (await api.get(f"/api/ai/generations?project_id={project['id']}")).json()[0]["id"] == gid
        r = await api.post(f"/api/ai/generations/{gid}/apply", json={"video_id": video["id"]})
        assert r.status_code == 200 and len(r.json()["scenes_created"]) == 2
        r = await api.post(f"/api/ai/generations/{gid}/apply", json={"video_id": video["id"]})
        assert r.status_code == 409

    @pytest.mark.parametrize("error, status", [
        (AIRateLimitError("429"), 429), (AITimeoutError("slow"), 504),
        (AIRequestError("400"), 502), (AIPartialResponseError("cut"), 422),
    ])
    async def test_provider_errors_map_to_http(self, api, project_video, error, status):
        project, _ = project_video
        registry.set_ai_provider(MockAIProvider([error] * 5))
        from agent.services import ai_content
        ai_content._service = AIContentService(max_retries=0)
        try:
            r = await api.post("/api/ai/story-plan", json={"project_id": project["id"], "brief": "x"})
        finally:
            ai_content._service = None
        assert r.status_code == status
        assert r.json()["error"]["details"]["request_id"]

    async def test_unknown_generation_is_404(self, api):
        registry.set_ai_provider(MockAIProvider())
        assert (await api.get("/api/ai/generations/nope")).status_code == 404
        assert (await api.post("/api/ai/generations/nope/apply", json={})).status_code == 404
