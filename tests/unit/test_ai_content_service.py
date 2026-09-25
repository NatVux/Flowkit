"""AIContentService + /api/ai routes, on a temporary SQLite database with MockAIProvider."""

import json
import logging

import httpx
import pytest

from agent.db import crud, schema
from agent.models.ai_content import StoryPlan, StoryPlanRequest, YouTubeMetadataRequest
from agent.services.ai import registry
from agent.services.ai.base import (
    AIEmptyResponseError, AIMalformedResponseError, AINotConfiguredError, AIPartialResponseError,
    AIRateLimitError, AIRequestError, AIServerError, AITimeoutError,
)
from agent.services.ai.gemini import GeminiProvider
from agent.services.ai.mock import MockAIProvider, default_story_plan, default_youtube_metadata
from agent.services.ai_content import (
    DEFAULT_NEGATIVE_LINE, AIContentService, AIContentUnavailable, check_english_fields, with_negative_line,
)

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
        # default: chain where the plan says continues_previous, like /fk-create-project
        assert [s["chain_type"] for s in scenes] == ["ROOT", "CONTINUATION"]
        assert scenes[0]["parent_scene_id"] is None and scenes[1]["parent_scene_id"] == scenes[0]["id"]
        assert scenes[0]["vertical_image_status"] == "PENDING"  # ready for the normal worker
        assert (await crud.get_project(project["id"]))["story"].startswith("Pippip runs")
        stored = await crud.get_ai_generation(gen["id"])
        assert stored["status"] == "APPLIED" and stored["video_id"] == video["id"] and stored["applied_at"]

    async def test_chain_scenes_opt_out_keeps_every_scene_root(self, project_video):
        project, video = project_video
        service, _ = _service(MockAIProvider())
        gen = await service.generate_story_plan(_story_req(project))
        await service.apply_generation(gen["id"], video["id"], chain_scenes=False)
        scenes = await crud.list_scenes(video["id"])
        assert [s["chain_type"] for s in scenes] == ["ROOT", "ROOT"]
        assert all(s["parent_scene_id"] is None for s in scenes)

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

    async def test_video_with_scenes_needs_explicit_append(self, project_video):
        project, video = project_video
        await crud.create_scene(video_id=video["id"], display_order=0, prompt="hand-written")
        service, _ = _service(MockAIProvider())
        gen = await service.generate_story_plan(_story_req(project))
        chars_before = await _row_count("character")

        with pytest.raises(crud.AIGenerationConflict, match="append"):
            await service.apply_generation(gen["id"], video["id"])
        assert (await crud.get_ai_generation(gen["id"]))["status"] == "GENERATED"
        assert await _row_count("character") == chars_before and await _row_count("scene") == 1

        await service.apply_generation(gen["id"], video["id"], append=True)
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


def _vi_plan(continues=(False, True, True)) -> dict:
    """A Vietnamese 3-scene plan shaped like what /fk-create-project builds by hand."""
    names = ["Mèo Đốm", "Bà Cụ Đèn Lồng", "Chợ Đêm"]
    return {
        "title": "Mèo con lạc ở chợ đêm",
        "logline": "Một chú mèo con lạc đường được bà cụ bán đèn lồng giúp về nhà.",
        "story": "Đốm lạc vào chợ đêm đông đúc. Bà cụ bán đèn lồng thắp sáng đường về cho Đốm.",
        "characters": [
            {"name": names[0], "entity_type": "character", "description": "Tiny white kitten with a brown spot over one eye",
             "voice_description": "Small squeaky meow"},
            {"name": names[1], "entity_type": "character", "description": "Kind elderly woman in a long blue silk tunic"},
            {"name": "Paper Lantern", "entity_type": "visual_asset", "description": "Round red paper lantern"},
        ],
        "locations": [{"name": names[2], "description": "Busy night market lit by hanging lanterns"}],
        "scenes": [
            {"summary": f"Cảnh {i + 1}", "image_prompt": f"{names[0]} in {names[2]}, shot {i + 1}. Medium shot.",
             "video_prompt": f"Medium shot {i + 1}. The camera holds steady.\n\nAudio: market hum.\nSFX: bells.\n"
                             "Negative: subtitles, watermark, text overlay.",
             "narration": f"Đốm nhìn quanh, lòng đầy lo lắng ({i + 1}).",
             "character_names": [names[0], names[1], names[2]] if i else [names[0], names[2]],
             "continues_previous": c}
            for i, c in enumerate(continues)
        ],
    }


class TestApplyMatchesCreateProject:
    """What apply writes must have the shape /fk-create-project writes, so gen-refs/images/videos just work."""

    async def _apply(self, project, video, plan, **kw):
        service, _ = _service(MockAIProvider([plan]))
        gen = await service.generate_story_plan(StoryPlanRequest(
            project_id=project["id"], brief="Mèo con lạc ở chợ đêm", language="vi", scene_count=len(plan["scenes"])))
        return gen, await service.apply_generation(gen["id"], video["id"], **kw)

    async def test_vietnamese_plan_round_trips_with_diacritics(self, project_video):
        project, video = project_video
        _, result = await self._apply(project, video, _vi_plan())

        chars = {c["name"]: c for c in await crud.get_project_characters(project["id"])}
        assert set(chars) == {"Mèo Đốm", "Bà Cụ Đèn Lồng", "Paper Lantern", "Chợ Đêm"}
        assert len(result["characters_created"]) == 4
        assert chars["Mèo Đốm"]["slug"] == "meo_dom" and chars["Chợ Đêm"]["slug"] == "cho_dem"
        assert chars["Chợ Đêm"]["entity_type"] == "location"
        assert chars["Paper Lantern"]["entity_type"] == "visual_asset"
        # same builder as POST /api/projects: "Name: desc. Story context: story"
        assert chars["Bà Cụ Đèn Lồng"]["description"].startswith("Bà Cụ Đèn Lồng: Kind elderly woman in a long blue silk tunic")
        assert "Story context: Đốm lạc vào chợ đêm" in chars["Bà Cụ Đèn Lồng"]["description"]

        scenes = await crud.list_scenes(video["id"])
        assert json.loads(scenes[1]["character_names"]) == ["Mèo Đốm", "Bà Cụ Đèn Lồng", "Chợ Đêm"]
        assert scenes[0]["narrator_text"] == "Đốm nhìn quanh, lòng đầy lo lắng (1)."
        assert (await crud.get_project(project["id"]))["story"].startswith("Đốm lạc vào chợ đêm")
        # every scene name resolves to a linked entity, which is what the worker's resolver needs
        for s in scenes:
            assert set(json.loads(s["character_names"])) <= set(chars)

    async def test_three_scene_chain_root_then_continuations(self, project_video):
        project, video = project_video
        await self._apply(project, video, _vi_plan((False, True, True)))
        s = await crud.list_scenes(video["id"])
        assert [x["display_order"] for x in s] == [0, 1, 2]
        assert [x["chain_type"] for x in s] == ["ROOT", "CONTINUATION", "CONTINUATION"]
        assert [x["parent_scene_id"] for x in s] == [None, s[0]["id"], s[1]["id"]]

    async def test_chain_only_where_the_plan_continues(self, project_video):
        project, video = project_video
        await self._apply(project, video, _vi_plan((False, True, False)))
        s = await crud.list_scenes(video["id"])
        assert [x["chain_type"] for x in s] == ["ROOT", "CONTINUATION", "ROOT"]
        assert [x["parent_scene_id"] for x in s] == [None, s[0]["id"], None]

    async def test_existing_project_entity_may_be_referenced_without_redeclaring(self, project_video):
        project, video = project_video
        old = await crud.create_character(name="Old Hero", entity_type="character")
        await crud.link_character_to_project(project["id"], old["id"])
        plan = default_story_plan()
        plan["scenes"][0]["character_names"].append("old hero")  # model's own casing
        _, result = await self._apply(project, video, plan)
        assert result["characters_reused"] == []  # not redeclared, so nothing to reuse or create
        scenes = await crud.list_scenes(video["id"])
        assert json.loads(scenes[0]["character_names"]) == ["Pippip", "Fish Stall", "Old Hero"]

    async def test_unknown_entity_in_stored_plan_fails_clearly_and_writes_nothing(self, project_video):
        project, video = project_video
        service, _ = _service(MockAIProvider())
        gen = await service.generate_story_plan(_story_req(project))
        tampered = dict(gen["output"])
        tampered["scenes"] = [dict(tampered["scenes"][0], character_names=["Nobody"])]
        db = await schema.get_db()
        await db.execute("UPDATE ai_generation SET output_json=? WHERE id=?", (json.dumps(tampered), gen["id"]))
        await db.commit()

        with pytest.raises(ValueError, match="Nobody"):
            await service.apply_generation(gen["id"], video["id"])
        assert await _row_count("scene") == 0
        assert (await crud.get_ai_generation(gen["id"]))["status"] == "GENERATED"

    async def test_project_without_material_is_rejected_not_restyled(self, ai_db):
        project = await crud.create_project(name="No material")
        video = await crud.create_video(project_id=project["id"], title="t")
        service, _ = _service(MockAIProvider())
        gen = await service.generate_story_plan(_story_req(project))
        with pytest.raises(ValueError, match="material"):
            await service.apply_generation(gen["id"], video["id"])
        assert await _row_count("scene") == 0 and await _row_count("character") == 0
        assert (await crud.get_ai_generation(gen["id"]))["status"] == "GENERATED"

    async def test_empty_video_title_takes_the_plan_title(self, project_video):
        project, _ = project_video
        untitled = await crud.create_video(project_id=project["id"], title="")
        _, result = await self._apply(project, untitled, _vi_plan())
        assert result["video_updated"] is True
        assert (await crud.get_video(untitled["id"]))["title"] == "Mèo con lạc ở chợ đêm"

    async def test_existing_video_title_is_kept(self, project_video):
        project, video = project_video
        _, result = await self._apply(project, video, _vi_plan())
        assert result["video_updated"] is False
        assert (await crud.get_video(video["id"]))["title"] == "Working title"

    async def test_set_active_is_opt_in(self, project_video, tmp_path, monkeypatch):
        from agent.api import active_project
        state = tmp_path / "active_project.json"
        monkeypatch.setattr(active_project, "_STATE_FILE", state)
        project, video = project_video

        _, result = await self._apply(project, video, default_story_plan())
        assert result["active_project_set"] is False and not state.exists()

        second = await crud.create_video(project_id=project["id"], title="second")
        _, result = await self._apply(project, second, default_story_plan(), set_active=True)
        assert result["active_project_set"] is True
        assert json.loads(state.read_text(encoding="utf-8")) == {"project_id": project["id"]}

    async def test_story_plan_video_id_sets_framing_and_default_apply_target(self, project_video):
        project, _ = project_video
        vertical = await crud.create_video(project_id=project["id"], title="v", orientation="VERTICAL")
        mock = MockAIProvider()
        service, _ = _service(mock)
        gen = await service.generate_story_plan(_story_req(project, video_id=vertical["id"]))
        assert "vertical 9:16" in mock.calls[0].prompt
        result = await service.apply_generation(gen["id"], None)
        assert result["video_id"] == vertical["id"] and len(result["scenes_created"]) == 2

    async def test_story_plan_without_video_names_no_frame(self, project_video):
        project, _ = project_video
        mock = MockAIProvider()
        await _service(mock)[0].generate_story_plan(_story_req(project))
        assert "Frame:" not in mock.calls[0].prompt

    async def test_story_plan_video_from_another_project_is_rejected(self, project_video):
        project, _ = project_video
        other = await crud.create_project(name="Other", id="other-project", material="realistic")
        foreign = await crud.create_video(project_id=other["id"], title="x")
        mock = MockAIProvider()
        with pytest.raises(ValueError):
            await _service(mock)[0].generate_story_plan(_story_req(project, video_id=foreign["id"]))
        assert mock.calls == []


class TestStoryPlanContentQuality:
    """Story-language fields vs. always-English fields, plus the server-side Negative line."""

    VI_PROMPT = "Mèo Đốm đứng lẻ loi giữa lối đi của Chợ Đêm, xung quanh là những đôi chân người qua lại."

    def test_vietnamese_names_inside_english_prompts_pass(self):
        check_english_fields(StoryPlan.model_validate(_vi_plan()))  # must not raise

    def test_prompt_written_in_vietnamese_fails_naming_the_field(self):
        plan = _vi_plan()
        plan["scenes"][1]["image_prompt"] = self.VI_PROMPT
        with pytest.raises(AIMalformedResponseError, match=r"scenes\[1\]\.image_prompt") as info:
            check_english_fields(StoryPlan.model_validate(plan))
        assert "scenes[0]" not in str(info.value)

    def test_vietnamese_entity_description_fails(self):
        plan = _vi_plan()
        plan["characters"][0]["description"] = "Một chú mèo con lông trắng, mắt to tròn, trông ngây thơ và sợ sệt"
        with pytest.raises(AIMalformedResponseError, match=r"characters\[0\]\.description"):
            check_english_fields(StoryPlan.model_validate(plan))

    def test_existing_project_entity_names_are_ignored(self):
        plan = _vi_plan()
        plan["scenes"][0]["image_prompt"] = "Ông Già Nô-en Đỏ waves at Mèo Đốm in Chợ Đêm. Wide shot."
        plan["scenes"][0]["character_names"].append("Ông Già Nô-en Đỏ")
        parsed = StoryPlan.model_validate(plan, context={"existing_entity_names": ["Ông Già Nô-en Đỏ"]})
        check_english_fields(parsed, ["Ông Già Nô-en Đỏ"])

    async def test_non_english_output_is_retried_then_stored(self, project_video):
        project, _ = project_video
        bad = _vi_plan()
        bad["scenes"][0]["video_prompt"] = self.VI_PROMPT
        mock = MockAIProvider([bad, _vi_plan()])
        service, sleeps = _service(mock)
        gen = await service.generate_story_plan(StoryPlanRequest(
            project_id=project["id"], brief="Mèo con lạc", language="vi", scene_count=3))
        assert gen["attempts"] == 2 and sleeps.delays == [2.0]
        assert gen["output"]["scenes"][0]["video_prompt"].startswith("Medium shot 1")

    async def test_non_english_output_without_retries_is_not_stored(self, project_video):
        project, _ = project_video
        bad = _vi_plan()
        bad["scenes"][2]["image_prompt"] = self.VI_PROMPT
        service, _ = _service(MockAIProvider([bad]), retries=0)
        with pytest.raises(AIMalformedResponseError, match=r"scenes\[2\]\.image_prompt"):
            await service.generate_story_plan(StoryPlanRequest(
                project_id=project["id"], brief="x", language="vi", scene_count=3))
        assert await _row_count("ai_generation") == 0

    async def test_prompt_separates_story_language_from_english_fields(self, project_video):
        project, _ = project_video
        mock = MockAIProvider([_vi_plan()])
        await _service(mock)[0].generate_story_plan(StoryPlanRequest(
            project_id=project["id"], brief="x", language="vi", scene_count=3))
        request = mock.calls[0]
        assert "Story language: vi" in request.prompt
        assert "ALWAYS English" in request.system_instruction and "voice_description" in request.system_instruction
        props = request.response_schema["properties"]
        assert props["story"]["description"].startswith("In the story language")
        assert props["scenes"]["items"]["properties"]["image_prompt"]["description"].startswith("ENGLISH")

    def test_negative_line_is_added_only_when_missing(self):
        timed = "0-4s: Mèo Đốm looks around. 4-8s: slow push-in."
        assert with_negative_line(timed) == f"{timed}\n\n{DEFAULT_NEGATIVE_LINE}"
        assert DEFAULT_NEGATIVE_LINE == "Negative: subtitles, text overlays, watermark, distorted faces."
        prose = "Medium shot. The camera holds.\n\nAudio: hum.\nNegative: subtitles, watermark."
        assert with_negative_line(prose) == prose

    async def test_apply_writes_timed_and_prose_video_prompts_with_a_negative_line(self, project_video):
        project, video = project_video
        plan = _vi_plan()
        plan["scenes"][0]["video_prompt"] = "0-4s: Mèo Đốm looks around. 4-8s: slow push-in on Mèo Đốm."
        service, _ = _service(MockAIProvider([plan]))
        gen = await service.generate_story_plan(StoryPlanRequest(
            project_id=project["id"], brief="x", language="vi", scene_count=3))
        await service.apply_generation(gen["id"], video["id"])
        scenes = await crud.list_scenes(video["id"])
        assert scenes[0]["video_prompt"].endswith("\n\n" + DEFAULT_NEGATIVE_LINE)
        assert scenes[1]["video_prompt"] == plan["scenes"][1]["video_prompt"]  # already had one: untouched
        assert scenes[1]["video_prompt"].count("Negative:") == 1

    def test_voice_description_stays_optional(self):
        plan = _vi_plan()
        for c in plan["characters"]:
            c.pop("voice_description", None)
        parsed = StoryPlan.model_validate(plan)
        assert all(c.voice_description is None for c in parsed.characters)
        check_english_fields(parsed)


# scenes[0].video_prompt exactly as gemini-3.1-flash-lite returned it on 2026-09-25 (half Vietnamese).
REAL_MIXED_VIDEO_PROMPT = (
    "0-4s: Mèo Con nhìn xung quanh với vẻ bối rối, tai khẽ cụp xuống khi những người qua đường vội vã lướt qua. "
    "Then cut to 4-8s: Mèo Con bước đi rụt rè giữa không gian ồn ào. The camera pans down to match the kitten's "
    "eye level, capturing the dizzying scale of the market. High contrast, warm lighting from the lanterns "
    "creates glowing rim light on the fur."
)


class TestReusedEntitiesAndMusic:
    async def _linked(self, project, name, **kw):
        char = await crud.create_character(name=name, description="Mô tả cũ tiếng Việt",
                                           image_prompt="Single reference image of Một chú mèo xám", **kw)
        await crud.link_character_to_project(project["id"], char["id"])
        return char

    async def _apply_vi(self, project, video):
        service, _ = _service(MockAIProvider([_vi_plan()]))
        gen = await service.generate_story_plan(StoryPlanRequest(
            project_id=project["id"], brief="Mèo con lạc", language="vi", scene_count=3))
        return await service.apply_generation(gen["id"], video["id"])

    async def test_reused_entity_without_reference_image_takes_the_new_plan(self, project_video):
        project, video = project_video
        old = await self._linked(project, "Mèo Đốm", voice_description="Old voice")

        result = await self._apply_vi(project, video)

        assert result["characters_updated"] == [old["id"]] and result["characters_reused_unchanged"] == []
        assert old["id"] in result["characters_reused"]
        cat = await crud.get_character(old["id"])
        # rebuilt with _build_character_profile, like a freshly created entity
        assert cat["description"].startswith("Mèo Đốm: Tiny white kitten with a brown spot over one eye. Story context: ")
        assert "Story context: Đốm lạc vào chợ đêm đông đúc" in cat["description"]  # story language keeps diacritics
        assert cat["image_prompt"].startswith("Single reference image of Tiny white kitten")
        assert cat["voice_description"] == "Small squeaky meow"
        assert cat["name"] == "Mèo Đốm" and cat["media_id"] is None

    async def test_reused_entity_keeps_old_voice_when_the_plan_has_none(self, project_video):
        project, video = project_video
        old = await self._linked(project, "Bà Cụ Đèn Lồng", voice_description="Old gentle voice")
        await self._apply_vi(project, video)  # _vi_plan gives her no voice_description
        assert (await crud.get_character(old["id"]))["voice_description"] == "Old gentle voice"

    async def test_reused_entity_with_reference_image_is_left_unchanged(self, project_video):
        project, video = project_video
        media = "fa9eb5ee-2c8f-4a7f-88ac-140580abde5b"
        old = await self._linked(project, "Mèo Đốm", media_id=media)
        before = await crud.get_character(old["id"])

        result = await self._apply_vi(project, video)

        assert result["characters_reused_unchanged"] == [old["id"]] and result["characters_updated"] == []
        after = await crud.get_character(old["id"])
        assert (after["description"], after["image_prompt"], after["media_id"]) == \
            (before["description"], before["image_prompt"], media)

    async def test_update_and_new_entities_in_one_apply(self, project_video):
        project, video = project_video
        fresh = await self._linked(project, "Mèo Đốm")
        pinned = await self._linked(project, "Chợ Đêm", media_id="11111111-2222-3333-4444-555555555555")
        result = await self._apply_vi(project, video)
        assert result["characters_updated"] == [fresh["id"]]
        assert result["characters_reused_unchanged"] == [pinned["id"]]
        assert len(result["characters_created"]) == 2  # Bà Cụ Đèn Lồng, Paper Lantern
        assert sorted(result["characters_reused"]) == sorted([fresh["id"], pinned["id"]])

    def test_real_mixed_vietnamese_english_video_prompt_fails(self):
        plan = _vi_plan()
        plan["scenes"][0]["video_prompt"] = REAL_MIXED_VIDEO_PROMPT
        with pytest.raises(AIMalformedResponseError, match=r"scenes\[0\]\.video_prompt"):
            check_english_fields(StoryPlan.model_validate(plan), ["Mèo Con"])

    def test_english_prompt_with_vietnamese_names_passes(self):
        plan = _vi_plan()
        plan["scenes"][1]["image_prompt"] = (
            "Portrait vertical shot of Bà Cụ Đèn Lồng kneeling in Chợ Đêm, extending her hand towards Mèo Đốm, "
            "soft lantern light, eye-level shot.")
        check_english_fields(StoryPlan.model_validate(plan))

    async def test_prompt_forbids_music_unless_the_project_allows_it(self, project_video):
        project, _ = project_video
        mock = MockAIProvider()
        await _service(mock)[0].generate_story_plan(_story_req(project))
        assert "Background music: NOT allowed" in mock.calls[0].prompt

        await crud.update_project(project["id"], allow_music=1)
        mock = MockAIProvider()
        await _service(mock)[0].generate_story_plan(_story_req(project))
        assert "Background music: allowed" in mock.calls[0].prompt
        assert "NOT allowed" not in mock.calls[0].prompt


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
