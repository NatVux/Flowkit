"""Pipeline runs: refs -> images -> videos -> concat for one video, run by the server.

Thin: all logic lives in agent/services/pipeline/runner.py. See docs/PIPELINE_RUNNER.md.
"""

from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agent.api.validation import validate_id
from agent.db import pipeline_crud as pc
from agent.services.pipeline.runner import RunError, get_pipeline_runner

router = APIRouter(prefix="/pipeline-runs", tags=["pipeline"])


class CreateRun(BaseModel):
    video_id: str
    checkpoints: Optional[list[Literal["REFS", "IMAGES", "VIDEOS"]]] = Field(
        None, description="Stages after which the run waits for approve. Default: REFS and IMAGES.")
    concat: bool = Field(True, description="Join the clips into one file after VIDEOS.")


class RedoTarget(BaseModel):
    target_type: Literal["character", "scene"]
    target_id: str
    include_descendants: bool = Field(
        False, description="Scene image redo: also redo every CONTINUATION scene below it.")
    confirm_invalidates: bool = Field(
        False, description="Reference redo: accept that it wipes the image and video of scenes using it.")


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, LookupError):
        return HTTPException(404, str(exc))
    if isinstance(exc, pc.PipelineConflict):
        return HTTPException(409, str(exc))
    if isinstance(exc, RunError):
        return HTTPException(400, str(exc))
    raise exc


async def _call(coro):
    try:
        return await coro
    except (LookupError, pc.PipelineConflict, RunError) as exc:
        raise _http(exc) from exc


@router.post("", status_code=201)
async def create_run(body: CreateRun):
    """Create a DRAFT run and return its minimum generation estimate. Nothing is queued yet."""
    validate_id(body.video_id, "video_id")
    runner = get_pipeline_runner()
    run = await _call(runner.create(body.video_id, checkpoints=body.checkpoints, concat=body.concat))
    return await runner.status(run["id"])


@router.get("")
async def list_runs(video_id: Optional[str] = None, project_id: Optional[str] = None):
    for key, value in (("video_id", video_id), ("project_id", project_id)):
        if value:
            validate_id(value, key)
    return [{k: r[k] for k in ("id", "project_id", "video_id", "orientation", "status", "stage", "pause_reason",
                                "status_detail", "final_path", "created_at", "updated_at", "finished_at")}
            for r in await pc.list_runs(video_id=video_id, project_id=project_id)]


@router.get("/{run_id}")
async def get_run(run_id: str):
    validate_id(run_id, "run_id")
    return await _call(get_pipeline_runner().status(run_id))


@router.post("/{run_id}/start")
async def start_run(run_id: str):
    """DRAFT -> RUNNING: queue the REFS stage."""
    validate_id(run_id, "run_id")
    return await _call(get_pipeline_runner().start(run_id))


@router.post("/{run_id}/approve")
async def approve_run(run_id: str):
    """AWAITING_APPROVAL -> RUNNING the next stage."""
    validate_id(run_id, "run_id")
    return await _call(get_pipeline_runner().approve(run_id))


@router.post("/{run_id}/resume")
async def resume_run(run_id: str):
    """PAUSED / NEEDS_USER_ACTION -> RUNNING, releasing held requests."""
    validate_id(run_id, "run_id")
    return await _call(get_pipeline_runner().resume(run_id))


@router.post("/{run_id}/redo")
async def redo_target(run_id: str, body: RedoTarget):
    """Redo one entity (REFS) or scene (IMAGES / VIDEOS) while the run waits for you."""
    validate_id(run_id, "run_id")
    validate_id(body.target_id, "target_id")
    return await _call(get_pipeline_runner().redo(
        run_id, body.target_type, body.target_id, include_descendants=body.include_descendants,
        confirm_invalidates=body.confirm_invalidates))


@router.post("/{run_id}/cancel")
async def cancel_run(run_id: str):
    """Remove queued requests that never reached Flow; let the rest finish."""
    validate_id(run_id, "run_id")
    return await _call(get_pipeline_runner().cancel(run_id))
