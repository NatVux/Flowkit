"""AI content planning endpoints. Thin: all logic lives in AIContentService."""

from fastapi import APIRouter, HTTPException

from agent.api.validation import validate_id
from agent.db import crud
from agent.models.ai_content import (
    AIGeneration, AIStatus, ApplyGenerationRequest, ApplyResult, StoryPlanRequest, YouTubeMetadataRequest,
)
from agent.services.ai import (
    AIContentBlockedError, AINotConfiguredError, AIPartialResponseError, AIProviderError,
    AIRateLimitError, AITimeoutError, provider_status,
)
from agent.services.ai_content import AIContentUnavailable, get_ai_content_service

router = APIRouter(prefix="/ai", tags=["ai"])


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (AIContentUnavailable, AINotConfiguredError)):
        return HTTPException(503, str(exc))
    if isinstance(exc, LookupError) and not isinstance(exc, (KeyError, IndexError)):
        return HTTPException(404, str(exc))
    if isinstance(exc, crud.AIGenerationConflict):
        return HTTPException(409, str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(400, str(exc))
    if isinstance(exc, AIProviderError):
        detail = {"code": exc.code, "message": str(exc), "request_id": getattr(exc, "request_id", None)}
        if isinstance(exc, AIRateLimitError):
            return HTTPException(429, detail, headers={"Retry-After": "30"})
        if isinstance(exc, AITimeoutError):
            return HTTPException(504, detail)
        if isinstance(exc, (AIContentBlockedError, AIPartialResponseError)):
            return HTTPException(422, detail)
        return HTTPException(502, detail)
    raise exc


@router.get("/status", response_model=AIStatus)
async def status():
    return provider_status()


@router.post("/story-plan", response_model=AIGeneration)
async def story_plan(body: StoryPlanRequest):
    validate_id(body.project_id, "project_id")
    try:
        return await get_ai_content_service().generate_story_plan(body)
    except Exception as exc:  # noqa: BLE001 — mapped or re-raised
        raise _http_error(exc) from exc


@router.post("/youtube-metadata", response_model=AIGeneration)
async def youtube_metadata(body: YouTubeMetadataRequest):
    validate_id(body.video_id, "video_id")
    try:
        return await get_ai_content_service().generate_youtube_metadata(body)
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc


@router.get("/generations", response_model=list[AIGeneration])
async def list_generations(project_id: str):
    validate_id(project_id, "project_id")
    return await crud.list_ai_generations(project_id)


@router.get("/generations/{generation_id}", response_model=AIGeneration)
async def get_generation(generation_id: str):
    validate_id(generation_id, "generation_id")
    gen = await crud.get_ai_generation(generation_id)
    if not gen:
        raise HTTPException(404, "AI generation not found")
    return gen


@router.post("/generations/{generation_id}/apply", response_model=ApplyResult)
async def apply_generation(generation_id: str, body: ApplyGenerationRequest):
    validate_id(generation_id, "generation_id")
    if body.video_id:
        validate_id(body.video_id, "video_id")
    try:
        return await get_ai_content_service().apply_generation(generation_id, body.video_id,
                                                               chain_scenes=body.chain_scenes)
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from exc
