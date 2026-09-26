"""AI content planning: stories, characters, locations, scenes, prompts, narration,
YouTube metadata.

Flow stays the media generator. This service only produces text, in two steps:

1. generate_*: ask the provider for JSON, parse it, validate it with the Pydantic
   models in agent/models/ai_content.py, and store the *validated* result as an
   `ai_generation` row. Provider calls have no side effects, so retryable
   failures (timeout, rate limit, 5xx, empty or malformed output) are retried.
2. apply_generation: write a stored generation into characters/scenes/video in a
   single transaction. This is not idempotent, so it is never retried; the row's
   GENERATED → APPLIED guard makes a repeated call fail with a conflict instead
   of duplicating scenes.

Scenes written here are ordinary scenes: the existing worker and FlowClient
generate their images and videos from `prompt`, `video_prompt` and
`character_names` exactly as for hand-written scenes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from typing import Awaitable, Callable, TypeVar

from pydantic import BaseModel, ValidationError

from agent import config
from agent.db import crud
from agent.logging_utils import log_event
from agent.models.ai_content import (
    StoryPlan, StoryPlanRequest, YouTubeMetadata, YouTubeMetadataRequest, provider_schema,
)
from agent.services.ai.base import (
    AIMalformedResponseError, AINotConfiguredError, AIProvider, AIProviderError, AIRequest,
)
from agent.services.ai.registry import get_ai_provider

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_MAX_BACKOFF_SECONDS = 30.0

STORY_SYSTEM = (
    "You plan short AI-generated videos. You write text only; a separate image/video model renders it. "
    "Return JSON matching the schema exactly.\n"
    "Languages:\n"
    "- title, logline, story, each scene summary and narration: the story language given below.\n"
    "- Every entity description, image_prompt, video_prompt and voice_description: ALWAYS English, whatever "
    "the story language - the image and video models read them.\n"
    "- Entity names stay in the story language, and prompts refer to each entity by exactly that name "
    "(e.g. 'Wide shot of Mèo Con in Chợ Đêm').\n"
    "Rules:\n"
    "- characters/locations: every person, creature, recurring object or place that must look the same "
    "across scenes. description = visual appearance only (no actions, no story). Give each character and "
    "creature a voice_description when it could speak or make sounds (tone and pace, at most ~30 words); "
    "it is optional.\n"
    "- Each scene image_prompt describes ONE still frame: action, environment, mood, then a camera/composition "
    "cue (shot size, angle). Refer to entities by their exact name; never restate their appearance, clothing "
    "or colours - reference images handle that.\n"
    "- Faces: when a character is in frame, show the full face (front, three-quarter or profile). Never frame a "
    "character from behind, crop the face, or make it tiny - the video model would invent the missing face. "
    "If the face should not be seen, use a hands-only point-of-view shot or leave the character out.\n"
    "- video_prompt covers ~8 seconds as ONE continuous shot in plain prose, at most 350 characters. Never "
    "cut to another shot: no 'Then cut to', no second or third shot, no timed beats. Give the camera "
    "movement its own sentence and describe the lighting. At most one short line of dialogue, written as: "
    "Name says: \"line\" (no subtitles). Do not write 'Audio:', 'SFX:' or 'Negative:' lines - they are "
    "added when the video is generated.\n"
    "- character_names lists the exact names of every defined character/location visible in the scene.\n"
    "- continues_previous is true only when the scene continues directly from the previous shot: same main "
    "character, same or adjacent place, no time skip. A continuing scene's image_prompt must still state a "
    "new camera angle and composition.\n"
    "- narration is optional voice-over text for the scene, short enough to read in about 8 seconds.\n"
    "- Music: follow the 'Background music' line below. When music is not allowed, never mention music.\n"
    "- Wording: harmless stories (children's tales included) are sometimes blocked by image filters by "
    "mistake when prompts use alarming words (attack, kill, blood, explosion, weapon, scream). Describe "
    "tension and emotion through expressions, light and atmosphere in gentle, non-graphic words instead.\n"
    "- Do not include art-style words (photorealistic, anime, 3D...); the style is applied separately.\n"
    "- No on-screen text, subtitles, logos or watermarks."
)

# video_prompt carries no Audio: line, so the worker appends its own ("no background music" unless the
# project allows music); the plan must still not ask for music in the prose itself.
_MUSIC_NOT_ALLOWED = ("Background music: NOT allowed. Never mention music, songs, melodies or a soundtrack "
                      "in any video_prompt.")
_MUSIC_ALLOWED = "Background music: allowed. A video_prompt may mention music."

_FRAMING = {
    "VERTICAL": "Frame: vertical 9:16 portrait. Compose every image_prompt and video_prompt for a tall frame.",
    "HORIZONTAL": "Frame: horizontal 16:9 landscape. Compose every image_prompt and video_prompt for a wide frame.",
}

METADATA_SYSTEM = (
    "You write YouTube metadata for a finished short video. Return JSON matching the schema exactly. "
    "Title: at most 100 characters, compelling, no clickbait lies. Description: plain text, first two "
    "lines summarise the video. Tags: relevant search terms, no '#'. Hashtags: 3-5, each starting with '#'."
)


class AIContentUnavailable(RuntimeError):
    """No AI provider is enabled/configured."""


class PlanValidationError(ValueError):
    """An edited story plan failed the same checks as model output. `problems`: [{loc, msg}]."""

    def __init__(self, problems: list[dict]):
        self.problems = problems
        super().__init__("; ".join(f"{p['loc']}: {p['msg']}" for p in problems))


class AIContentService:
    def __init__(
        self,
        provider_getter: Callable[[], AIProvider | None] = get_ai_provider,
        *,
        max_retries: int | None = None,
        timeout_seconds: float | None = None,
        base_delay: float = 2.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self._provider_getter = provider_getter
        self.max_retries = config.GEMINI_MAX_RETRIES if max_retries is None else max_retries
        self.timeout_seconds = config.GEMINI_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
        self.base_delay = base_delay
        self._sleep = sleep

    # ── provider access ─────────────────────────────────────

    def _provider(self) -> AIProvider:
        provider = self._provider_getter()
        if provider is None:
            raise AIContentUnavailable("AI content generation is disabled (set GEMINI_API_KEY or AI_PROVIDER)")
        if not provider.configured:
            raise AINotConfiguredError(f"AI provider '{provider.name}' is not configured")
        return provider

    # ── core: call, parse, validate, retry ──────────────────

    async def _generate(self, operation: str, system: str, prompt: str, model_cls: type[T],
                        context: dict, validation_context: dict | None = None,
                        post_check: Callable[[T], None] | None = None) -> tuple[T, dict]:
        provider = self._provider()
        request_id = str(uuid.uuid4())
        schema = provider_schema(model_cls)
        attempts = 1 + max(0, self.max_retries)
        base = {"request_id": request_id, "operation": operation, "provider": provider.name,
                "model": provider.model, **context}

        for attempt in range(1, attempts + 1):
            started = time.monotonic()
            log_event(logger, logging.INFO, "ai_request_started", attempt=attempt, prompt_chars=len(prompt), **base)
            try:
                response = await provider.generate_json(AIRequest(
                    operation=operation, system_instruction=system, prompt=prompt,
                    response_schema=schema, request_id=request_id, timeout_seconds=self.timeout_seconds,
                ))
                result = _parse(response.text, model_cls, validation_context)
                if post_check:
                    post_check(result)  # raises AIMalformedResponseError, retried like any malformed output
            except AIProviderError as exc:
                duration = round((time.monotonic() - started) * 1000)
                will_retry = exc.retryable and attempt < attempts
                log_event(logger, logging.WARNING if will_retry else logging.ERROR, "ai_request_failed",
                          attempt=attempt, duration_ms=duration, error_code=exc.code,
                          retryable=exc.retryable, http_status=exc.status, message=str(exc)[:300], **base)
                if not will_retry:
                    exc.request_id = request_id  # surfaced to the API caller for correlation
                    raise
                delay = min(self.base_delay * (2 ** (attempt - 1)), _MAX_BACKOFF_SECONDS)
                log_event(logger, logging.INFO, "ai_retry_scheduled", attempt=attempt, delay_s=delay, **base)
                await self._sleep(delay)
                continue
            log_event(logger, logging.INFO, "ai_request_completed", attempt=attempt,
                      duration_ms=round((time.monotonic() - started) * 1000),
                      finish_reason=response.finish_reason, response_chars=len(response.text),
                      usage=response.usage, **base)
            return result, {"request_id": request_id, "attempts": attempt,
                            "provider": provider.name, "model": response.model or provider.model}
        raise AssertionError("unreachable")  # pragma: no cover

    # ── operations ──────────────────────────────────────────

    async def generate_story_plan(self, req: StoryPlanRequest) -> dict:
        self._provider()  # report "not configured" before any lookup
        project = await crud.get_project(req.project_id)
        if not project:
            raise LookupError("Project not found")
        existing = await crud.get_project_characters(req.project_id)
        language = req.language or project.get("language") or "en"
        orientation = None
        if req.video_id:
            video = await crud.get_video(req.video_id)
            if not video:
                raise LookupError("Video not found")
            if video["project_id"] != req.project_id:
                raise ValueError("video does not belong to the project")
            orientation = video.get("orientation")

        lines = [
            f"Brief: {req.brief}",
            f"Number of scenes: exactly {req.scene_count}",
            f"Story language: {language} (title, logline, story, summaries, narration, entity names). "
            "Entity descriptions, image_prompt, video_prompt and voice_description: English.",
        ]
        if orientation in _FRAMING:
            lines.append(_FRAMING[orientation])
        lines.append(_MUSIC_ALLOWED if project.get("allow_music") else _MUSIC_NOT_ALLOWED)
        if req.audience:
            lines.append(f"Audience: {req.audience}")
        if req.tone:
            lines.append(f"Tone: {req.tone}")
        if project.get("story"):
            lines.append(f"Existing project story (stay consistent): {project['story'][:2000]}")
        if existing:
            reuse = ", ".join(f"{c['name']} ({c['entity_type']})" for c in existing[:30])
            lines.append(f"Entities that already exist in this project (reuse these exact names when they appear): {reuse}")
        existing_names = [c["name"] for c in existing]
        plan, meta = await self._generate("story_plan", STORY_SYSTEM, "\n".join(lines), StoryPlan,
                                          {"project_id": req.project_id},
                                          {"existing_entity_names": existing_names},
                                          lambda p: check_story_plan(p, existing_names))
        if len(plan.scenes) != req.scene_count:
            log_event(logger, logging.WARNING, "ai_scene_count_mismatch", request_id=meta["request_id"],
                      requested=req.scene_count, returned=len(plan.scenes))
        return await self._store("STORY_PLAN", req.project_id, req.video_id, req.model_dump(), plan, meta)

    async def generate_youtube_metadata(self, req: YouTubeMetadataRequest) -> dict:
        self._provider()  # report "not configured" before any lookup
        video = await crud.get_video(req.video_id)
        if not video:
            raise LookupError("Video not found")
        project = await crud.get_project(video["project_id"]) or {}
        scenes = await crud.list_scenes(req.video_id)
        lines = [f"Project: {project.get('name', '')}", f"Working title: {video.get('title', '')}"]
        if project.get("story"):
            lines.append(f"Story: {project['story'][:3000]}")
        if video.get("description"):
            lines.append(f"Current description: {video['description'][:1000]}")
        for s in scenes[:40]:
            text = s.get("narrator_text") or s.get("prompt") or ""
            if text:
                lines.append(f"Scene {s['display_order'] + 1}: {text[:300]}")
        lines.append(f"Language: {project.get('language') or 'en'}")
        if req.instructions:
            lines.append(f"Extra instructions: {req.instructions}")
        metadata, meta = await self._generate("youtube_metadata", METADATA_SYSTEM, "\n".join(lines),
                                              YouTubeMetadata, {"video_id": req.video_id})
        return await self._store("YOUTUBE_METADATA", video["project_id"], req.video_id, req.model_dump(), metadata, meta)

    async def update_story_plan(self, generation_id: str, data: dict) -> dict:
        """Save a person's edits to a story plan before it is applied.

        The edit goes through exactly what model output goes through - StoryPlan validation
        (with the project's existing entity names) and check_story_plan (English prompts, one
        continuous shot) - so apply can trust it the same way. No provider call.
        """
        gen = await crud.get_ai_generation(generation_id)
        if not gen:
            raise LookupError("AI generation not found")
        if gen["operation"] != "STORY_PLAN":
            raise ValueError("only a story plan can be edited")
        existing = [c["name"] for c in await crud.get_project_characters(gen["project_id"])]
        try:
            plan = StoryPlan.model_validate(data, context={"existing_entity_names": existing})
        except ValidationError as exc:
            raise PlanValidationError([
                {"loc": ".".join(str(p) for p in err["loc"]) or "plan", "msg": err["msg"]}
                for err in exc.errors()[:20]]) from exc
        try:
            check_story_plan(plan, existing)
        except AIMalformedResponseError as exc:
            raise PlanValidationError(_check_problems(str(exc))) from exc
        row = await crud.update_ai_generation_output(generation_id, "STORY_PLAN", plan.model_dump(mode="json"))
        log_event(logger, logging.INFO, "ai_generation_edited", generation_id=generation_id,
                  project_id=gen["project_id"], scenes=len(plan.scenes))
        return row

    async def _store(self, operation: str, project_id: str, video_id: str | None, input_data: dict,
                     result: BaseModel, meta: dict) -> dict:
        row = await crud.create_ai_generation(
            operation=operation, provider=meta["provider"], model=meta["model"], project_id=project_id,
            video_id=video_id, request_id=meta["request_id"], attempts=meta["attempts"],
            input_data=input_data, output_data=result.model_dump(mode="json"),
        )
        log_event(logger, logging.INFO, "ai_generation_stored", generation_id=row["id"],
                  request_id=meta["request_id"], operation=operation, project_id=project_id)
        return row

    # ── apply (non-idempotent: never retried) ───────────────

    async def apply_generation(self, generation_id: str, video_id: str | None, *, chain_scenes: bool = True,
                               append: bool = False, set_active: bool = False) -> dict:
        gen = await crud.get_ai_generation(generation_id)
        if not gen:
            raise LookupError("AI generation not found")
        target_video = video_id or gen.get("video_id")
        if not target_video:
            raise ValueError("video_id is required to apply a story plan")
        started = time.monotonic()
        base = {"generation_id": generation_id, "request_id": gen["request_id"],
                "operation": gen["operation"], "video_id": target_video}

        try:
            if gen["operation"] == "STORY_PLAN":
                # Re-validate what was stored: the row is data, not trusted code.
                existing = await crud.get_project_characters(gen["project_id"])
                plan = StoryPlan.model_validate(
                    gen["output"], context={"existing_entity_names": [c["name"] for c in existing]})
                result = await crud.apply_story_plan(
                    generation_id, target_video, story=plan.story, title=plan.title,
                    chain_scenes=chain_scenes, append=append,
                    **await _story_rows(plan, gen["project_id"]),
                )
            elif gen["operation"] == "YOUTUBE_METADATA":
                md = YouTubeMetadata.model_validate(gen["output"])
                description = md.description
                if md.hashtags:
                    description = f"{description}\n\n{' '.join(md.hashtags)}"[:5000]
                result = await crud.apply_youtube_metadata(
                    generation_id, target_video, title=md.title, description=description, tags=md.tags)
            else:  # pragma: no cover - CHECK constraint prevents this
                raise ValueError(f"Unknown operation {gen['operation']}")
        except Exception as exc:
            log_event(logger, logging.ERROR, "ai_generation_apply_failed", error_type=type(exc).__name__,
                      message=str(exc)[:300], **base)
            raise
        log_event(logger, logging.INFO, "ai_generation_applied",
                  duration_ms=round((time.monotonic() - started) * 1000),
                  scenes_created=len(result.get("scenes_created", [])),
                  characters_created=len(result.get("characters_created", [])), **base)
        active_set = False
        if set_active and gen["operation"] == "STORY_PLAN":
            # After the commit: failing here must not look like a failed apply.
            from agent.api.active_project import _write_state
            try:
                _write_state({"project_id": gen["project_id"]})
                active_set = True
            except OSError as exc:
                log_event(logger, logging.WARNING, "ai_set_active_failed", message=str(exc)[:300], **base)
        return {"generation_id": generation_id, "status": "APPLIED", "video_id": target_video,
                "active_project_set": active_set, **result}


def _parse(text: str, model_cls: type[T], context: dict | None = None) -> T:
    """Model text → validated object. Anything else is AIMalformedResponseError."""
    raw = (text or "").strip()
    if raw.startswith("```"):  # tolerate a fenced block despite the JSON mime type
        raw = raw.strip("`")
        raw = raw[raw.find("{"):] if "{" in raw else raw
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AIMalformedResponseError(f"response is not valid JSON ({exc.msg} at char {exc.pos})") from exc
    if not isinstance(data, dict):
        raise AIMalformedResponseError(f"response JSON is a {type(data).__name__}, expected an object")
    try:
        return model_cls.model_validate(data, context=context)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}" for err in exc.errors()[:5]
        )
        raise AIMalformedResponseError(f"response failed validation: {problems}") from exc


# ── story-plan content checks ───────────────────────────────

#: Above this share of accented words (entity names removed) a field is not English.
#: Vietnamese prose sits around 60-90%; a half-Vietnamese prompt around 20-35%.
ENGLISH_MAX_ACCENTED_SHARE = 0.15
#: The system prompt asks for at most 350 characters; this leaves room before a retry is spent.
#: A long multi-shot prompt (828 chars, "Then cut to") is what Google failed and refunded on
#: 2026-09-26 while one continuous shot of ~335 chars rendered - see docs/PIPELINE_RUNNER.md.
VIDEO_PROMPT_MAX_CHARS = 400
_WORD = re.compile(r"[^\W\d_]+")
_CUT_TO = re.compile(r"\bcut\s+to\b", re.IGNORECASE)


def _accented_share(text: str, names: list[str]) -> float:
    for name in sorted(names, key=len, reverse=True):  # longest first: "Bà Cụ" before "Bà"
        text = re.sub(re.escape(name), " ", text, flags=re.IGNORECASE)
    words = _WORD.findall(text)
    return sum(1 for w in words if not w.isascii()) / len(words) if words else 0.0


def check_english_fields(plan: StoryPlan, extra_names: list[str] = ()) -> None:
    """Post-check (not schema validation): fields the image/video models read must be English.

    Entity names may stay in the story language, so they are removed before measuring.
    Raises AIMalformedResponseError naming each offending field, so the normal retry runs.
    """
    names = [e.name for e in (*plan.characters, *plan.locations)] + list(extra_names)
    fields = []
    for i, c in enumerate(plan.characters):
        fields += [(f"characters[{i}].description", c.description),
                   (f"characters[{i}].voice_description", c.voice_description)]
    fields += [(f"locations[{i}].description", loc.description) for i, loc in enumerate(plan.locations)]
    for i, s in enumerate(plan.scenes):
        fields += [(f"scenes[{i}].image_prompt", s.image_prompt), (f"scenes[{i}].video_prompt", s.video_prompt)]
    bad = []
    for path, text in fields:
        share = _accented_share(text, names) if text else 0.0
        if share > ENGLISH_MAX_ACCENTED_SHARE:
            bad.append(f"{path} ({share:.0%} accented words)")
    if bad:
        raise AIMalformedResponseError("response failed language check, must be English: " + "; ".join(bad))


def check_video_prompts(plan: StoryPlan) -> None:
    """Post-check: each video_prompt is one continuous shot of bounded length.

    Raises AIMalformedResponseError naming each offending scene, so the normal retry runs.
    """
    bad = []
    for i, s in enumerate(plan.scenes):
        if len(s.video_prompt) > VIDEO_PROMPT_MAX_CHARS:
            bad.append(f"scenes[{i}].video_prompt ({len(s.video_prompt)} chars, max {VIDEO_PROMPT_MAX_CHARS})")
        if _CUT_TO.search(s.video_prompt):
            bad.append(f"scenes[{i}].video_prompt (cuts to another shot)")
    if bad:
        raise AIMalformedResponseError("response failed video_prompt check, must be one continuous shot: "
                                       + "; ".join(bad))


_CHECK_PART = re.compile(r"(\w+)\[(\d+)\]\.(\w+) \(([^)]*)\)")


def _check_problems(message: str) -> list[dict]:
    """'...: scenes[0].video_prompt (cuts to another shot); ...' -> [{loc: 'scenes.0.video_prompt', msg}]."""
    problems = [{"loc": f"{m[1]}.{m[2]}.{m[3]}", "msg": m[4]} for m in _CHECK_PART.finditer(message)]
    return problems or [{"loc": "plan", "msg": message}]


def check_story_plan(plan: StoryPlan, extra_names: list[str] = ()) -> None:
    """All story-plan post-checks; each raises AIMalformedResponseError for a retry."""
    check_english_fields(plan, extra_names)
    check_video_prompts(plan)


async def _story_rows(plan: StoryPlan, project_id: str) -> dict:
    """Turn a validated plan into DB-ready rows, using the same builders as project creation."""
    from agent.api.projects import _build_character_profile
    from agent.materials import get_material
    from agent.utils.slugify import slugify

    project = await crud.get_project(project_id) or {}
    material_id = project.get("material")
    material = get_material(material_id) if material_id else None
    if material is None:
        # /fk-create-project requires a material; guessing one would restyle the whole project.
        raise ValueError(f"project has no valid material ({material_id!r}); set one with PATCH /api/projects/{project_id}")
    prefix = material.get("scene_prefix") or ""

    entities = []
    for ent, etype in [*((c, c.entity_type) for c in plan.characters), *((loc, "location") for loc in plan.locations)]:
        profile = _build_character_profile(ent.name, ent.description, plan.story, entity_type=etype, material_id=material_id)
        entities.append({
            "name": ent.name, "slug": slugify(ent.name), "entity_type": etype,
            "description": profile["description"], "image_prompt": profile["image_prompt"],
            "voice_description": getattr(ent, "voice_description", None),
        })
    scenes = []
    for sc in plan.scenes:
        prompt = sc.image_prompt if not prefix or sc.image_prompt.startswith(prefix) else f"{prefix} {sc.image_prompt}"
        scenes.append({
            "prompt": prompt, "video_prompt": sc.video_prompt, "narrator_text": sc.narration,
            "character_names": sc.character_names, "continues_previous": sc.continues_previous,
        })
    return {"entities": entities, "scenes": scenes}


_service: AIContentService | None = None


def get_ai_content_service() -> AIContentService:
    global _service
    if _service is None:
        _service = AIContentService()
    return _service
