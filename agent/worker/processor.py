"""Background worker — processes pending requests via Chrome extension.

Thin dispatcher: picks up PENDING requests, delegates to OperationService
for actual API work, handles status transitions + retry + scene updates.
"""
import asyncio
import base64
import json
import logging
import random
import re
import time
from datetime import datetime, timedelta, timezone

import aiohttp

from agent.db import crud
from agent.services.flow_client import get_flow_client
from agent.services.event_bus import event_bus
from agent.config import (
    POLL_INTERVAL, MAX_RETRIES, API_COOLDOWN, MAX_CONCURRENT_REQUESTS,
    WORKER_OPERATION_TIMEOUT, RETRY_JITTER_SECONDS,
)
from agent.worker._parsing import _is_error
from agent.sdk.services.result_handler import (
    parse_result, apply_scene_result, apply_character_result, invalidate_scene_dependents,
)
from agent.logging_utils import log_event

logger = logging.getLogger(__name__)

_API_CALL_TYPES = {"GENERATE_IMAGE", "REGENERATE_IMAGE", "EDIT_IMAGE",
                   "GENERATE_VIDEO", "REGENERATE_VIDEO", "GENERATE_VIDEO_REFS", "UPSCALE_VIDEO",
                   "GENERATE_CHARACTER_IMAGE", "REGENERATE_CHARACTER_IMAGE",
                   "EDIT_CHARACTER_IMAGE"}

_TYPE_PRIORITY = {
    "GENERATE_CHARACTER_IMAGE": 0, "REGENERATE_CHARACTER_IMAGE": 0, "EDIT_CHARACTER_IMAGE": 0,
    "GENERATE_IMAGE": 1, "REGENERATE_IMAGE": 1, "EDIT_IMAGE": 1,
    "GENERATE_VIDEO": 2, "REGENERATE_VIDEO": 2, "GENERATE_VIDEO_REFS": 2,
    "UPSCALE_VIDEO": 3,
}


class APIRateLimiter:
    """Enforces max concurrent requests AND minimum gap between API calls."""
    def __init__(self, max_concurrent: int, cooldown_seconds: float):
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._cooldown = cooldown_seconds
        self._last_call = 0.0
        self._gate = asyncio.Lock()

    async def acquire(self):
        await self._semaphore.acquire()
        try:
            async with self._gate:
                elapsed = time.monotonic() - self._last_call
                if elapsed < self._cooldown:
                    await asyncio.sleep(self._cooldown - elapsed)
                self._last_call = time.monotonic()
        except BaseException:
            self._semaphore.release()
            raise

    def release(self):
        self._semaphore.release()


class WorkerController:
    """Controls the background worker loop with rate limiting and graceful shutdown."""

    def __init__(self):
        self._shutdown = asyncio.Event()
        self._active_ids: set[str] = set()
        self._tasks: set[asyncio.Task] = set()
        self._rate_limiter = APIRateLimiter(MAX_CONCURRENT_REQUESTS, API_COOLDOWN)
        self._deferred: dict[str, float] = {}  # rid -> defer_until timestamp
        self._retry_after: dict[str, float] = {}  # rid -> retry_after timestamp
        self._recovered = False

    @property
    def active_count(self) -> int:
        """Number of currently active requests."""
        return len(self._active_ids)

    async def start(self):
        """Start the worker loop (recovering orphans first if the caller has not)."""
        if not self._recovered:
            await self.recover_orphaned()
        await self._run_loop()

    async def recover_orphaned(self):
        """Reset every PROCESSING request left by the previous process back to PENDING.

        Must run before the worker claims anything: at that point no task of this
        process can own a PROCESSING row, so all of them are orphans, however recent.
        """
        try:
            count = await crud.reset_orphaned_processing()
            if count:
                logger.info("Reset %d orphaned PROCESSING requests to PENDING", count)
        except Exception as e:
            logger.warning("Could not reset orphaned requests: %s", e)
        self._recovered = True

    def request_shutdown(self):
        """Signal the worker to stop and let active tasks be cancelled by drain."""
        self._shutdown.set()

    async def drain(self, timeout: float = 30.0):
        """Wait for active tasks, then cancel anything that exceeds the deadline."""
        tasks = tuple(self._tasks)
        if not tasks:
            return
        done, pending = await asyncio.wait(tasks, timeout=timeout)
        if pending:
            logger.warning("worker_shutdown_timeout active=%d timeout=%.1f", len(pending), timeout)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    async def _run_loop(self):
        client = get_flow_client()

        while not self._shutdown.is_set():
            try:
                if not client.connected:
                    await self._sleep_or_shutdown(POLL_INTERVAL)
                    continue

                # During the UNUSUAL_ACTIVITY cooldown every generation call is refused
                # locally; claiming now would only turn waiting requests into FAILED ones.
                # Every queue type submits a guarded generation call, so claim nothing.
                if generation_cooldown_active(client):
                    await self._sleep_or_shutdown(POLL_INTERVAL)
                    continue

                now = time.time()
                slots_available = MAX_CONCURRENT_REQUESTS - len(self._active_ids)
                if slots_available <= 0:
                    await self._sleep_or_shutdown(POLL_INTERVAL)
                    continue

                excluded_ids = set(self._active_ids)
                excluded_ids.update(rid for rid, deadline in self._deferred.items() if deadline > now)
                excluded_ids.update(rid for rid, deadline in self._retry_after.items() if deadline > now)
                pending = await crud.claim_actionable_requests(
                    exclude_ids=excluded_ids, limit=slots_available
                )

                pending_count = len(pending)
                await event_bus.emit("worker_tick", {
                    "active": len(self._active_ids),
                    "slots": slots_available,
                    "pending": pending_count,
                })

                if pending:
                    logger.debug("Worker: %d actionable, %d active, %d slots",
                                len(pending), len(self._active_ids), slots_available)

                for req in pending:
                    if slots_available <= 0:
                        break
                    rid = req["id"]

                    # Skip in-flight
                    if rid in self._active_ids:
                        continue

                    self._deferred.pop(rid, None)

                    self._active_ids.add(rid)
                    slots_available -= 1
                    task = asyncio.create_task(self._run_one(req), name=f"flowkit-request-{rid}")
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)

                # Prune stale deferred/retry entries for requests no longer pending
                pending_ids = {r["id"] for r in pending}
                self._deferred = {k: v for k, v in self._deferred.items() if k in pending_ids}
                self._retry_after = {k: v for k, v in self._retry_after.items() if k in pending_ids}

            except Exception as e:
                logger.exception("Worker loop error: %s", e)

            await self._sleep_or_shutdown(POLL_INTERVAL)

    async def _sleep_or_shutdown(self, seconds: float):
        try:
            await asyncio.wait_for(self._shutdown.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def _run_one(self, req: dict):
        rid = req["id"]
        started = time.monotonic()
        final_status = "UNKNOWN"
        try:
            await self._rate_limiter.acquire()
            try:
                await _process_one(req, self._deferred, self._retry_after)
            finally:
                self._rate_limiter.release()
        except asyncio.CancelledError:
            final_status = "CANCELLED"
            log_event(logger, logging.WARNING, "job_cancelled", request_id=rid, project_id=req.get("project_id"), video_id=req.get("video_id"), scene_id=req.get("scene_id"), operation=req.get("type"))
            try:
                await _set_request_status(req, "PENDING", error_message="worker task cancelled", last_failure_reason="worker task cancelled")
            except Exception:
                logger.exception("worker_cancel_recovery request_id=%s", rid)
            raise
        except Exception:
            log_event(logger, logging.ERROR, "job_failed", exc_info=True, request_id=rid, project_id=req.get("project_id"), video_id=req.get("video_id"), scene_id=req.get("scene_id"), operation=req.get("type"), error_type="worker_task_error")
        finally:
            self._active_ids.discard(rid)
            try:
                latest = await asyncio.shield(crud.get_request(rid))
                final_status = (latest or {}).get("status", final_status)
            except Exception:
                pass
            log_event(logger, logging.INFO, "job_completed" if final_status == "COMPLETED" else "job_finished",
                      request_id=rid, project_id=req.get("project_id"), video_id=req.get("video_id"),
                      scene_id=req.get("scene_id"), operation=req.get("type"),
                      attempt=req.get("retry_count", 0) + 1,
                      duration_ms=round((time.monotonic() - started) * 1000), final_status=final_status)


async def _prerequisites_met(req: dict, orientation: str) -> bool:
    """Check if prerequisites are ready. Returns False to defer (stay PENDING)."""
    req_type = req.get("type", "")
    prefix = "vertical" if orientation == "VERTICAL" else "horizontal"

    # Video gen needs scene image to be ready; upscale needs video to be ready
    if req_type in ("GENERATE_VIDEO", "REGENERATE_VIDEO", "GENERATE_VIDEO_REFS", "UPSCALE_VIDEO"):
        scene = await crud.get_scene(req.get("scene_id"))
        if not scene:
            return True  # let _dispatch handle "scene not found"
        if req_type in ("GENERATE_VIDEO", "REGENERATE_VIDEO", "GENERATE_VIDEO_REFS"):
            if not scene.get(f"{prefix}_image_media_id"):
                logger.info("VIDEO prereq deferred: scene=%s no %s_image_media_id", req.get("scene_id","")[:12], prefix)
                return False
        elif req_type == "UPSCALE_VIDEO":
            if not scene.get(f"{prefix}_video_media_id"):
                logger.info("UPSCALE prereq deferred: scene=%s no %s_video_media_id", req.get("scene_id","")[:12], prefix)
                return False

    # Edit requests need source media (own image or parent's for INSERT scenes)
    if req_type in ("EDIT_IMAGE", "EDIT_CHARACTER_IMAGE"):
        if not req.get("source_media_id"):
            if req_type == "EDIT_CHARACTER_IMAGE":
                char = await crud.get_character(req.get("character_id"))
                if not char or not char.get("media_id"):
                    return False
            elif req_type == "EDIT_IMAGE":
                scene = await crud.get_scene(req.get("scene_id"))
                if not scene:
                    return True  # let _dispatch handle
                # CONTINUATION scenes always use parent's image as source
                src = None
                if scene.get("parent_scene_id"):
                    parent = await crud.get_scene(scene["parent_scene_id"])
                    src = parent.get(f"{prefix}_image_media_id") if parent else None
                if not src:
                    src = scene.get(f"{prefix}_image_media_id")
                logger.info("EDIT_IMAGE prereq: scene=%s src=%s parent=%s", req.get("scene_id","")[:12], src, scene.get("parent_scene_id","")[:12] if scene.get("parent_scene_id") else "none")
                if not src:
                    return False

    return True


async def _resolve_orientation(req: dict) -> str:
    """Resolve orientation from request, falling back to video table, then VERTICAL."""
    orient = req.get("orientation")
    if orient:
        return orient
    vid = req.get("video_id")
    if vid:
        video = await crud.get_video(vid)
        if video and video.get("orientation"):
            return video["orientation"]
    return "VERTICAL"


async def _set_request_status(req: dict, status: str, **updates):
    return await crud.update_request(
        req["id"],
        status=status,
        _expected_started_at=req.get("started_at"),
        **updates,
    )


async def _process_one(req: dict, deferred: dict = None, retry_after: dict = None):
    rid, req_type = req["id"], req["type"]
    orientation = await _resolve_orientation(req)
    log_context = {
        "request_id": rid,
        "project_id": req.get("project_id"),
        "video_id": req.get("video_id"),
        "scene_id": req.get("scene_id"),
        "operation": req_type,
        "attempt": req.get("retry_count", 0) + 1,
    }

    if await _is_already_completed(req, orientation):
        logger.info("Request %s skipped — already COMPLETED", rid[:8])
        # Copy existing result data from scene/character onto the request record
        skip_kwargs = {"status": "COMPLETED", "error_message": "skipped: already completed"}
        prefix = "vertical" if orientation == "VERTICAL" else "horizontal"
        if req_type in ("GENERATE_CHARACTER_IMAGE", "REGENERATE_CHARACTER_IMAGE", "EDIT_CHARACTER_IMAGE"):
            char = await crud.get_character(req.get("character_id"))
            if char:
                skip_kwargs["media_id"] = char.get("media_id")
                skip_kwargs["output_url"] = char.get("image_url")
        else:
            scene = await crud.get_scene(req.get("scene_id"))
            if scene:
                if req_type == "GENERATE_IMAGE":
                    skip_kwargs["media_id"] = scene.get(f"{prefix}_image_media_id")
                    skip_kwargs["output_url"] = scene.get(f"{prefix}_image_url")
                elif req_type in ("GENERATE_VIDEO", "REGENERATE_VIDEO", "GENERATE_VIDEO_REFS"):
                    skip_kwargs["media_id"] = scene.get(f"{prefix}_video_media_id")
                    skip_kwargs["output_url"] = scene.get(f"{prefix}_video_url")
                elif req_type == "UPSCALE_VIDEO":
                    skip_kwargs["media_id"] = scene.get(f"{prefix}_upscale_media_id")
                    skip_kwargs["output_url"] = scene.get(f"{prefix}_upscale_url")
        await _set_request_status(req, "COMPLETED", **{k: v for k, v in skip_kwargs.items() if k != "status"})
        return

    # Check prerequisites before dispatching — don't burn retries on missing deps
    if not await _prerequisites_met(req, orientation):
        if deferred is not None:
            deferred[rid] = time.time() + 30  # defer 30s before rechecking
        await _set_request_status(req, "PENDING")
        return

    log_event(logger, logging.INFO, "job_started", **log_context)
    await event_bus.emit("request_update", {"id": rid, "status": "PROCESSING", "type": req_type})

    try:
        if req_type == "REGENERATE_IMAGE":
            await invalidate_scene_dependents(req["scene_id"], orientation, "image")
        elif req_type == "REGENERATE_VIDEO":
            await invalidate_scene_dependents(req["scene_id"], orientation, "video")
        operation_started = time.monotonic()
        result = await asyncio.wait_for(
            _dispatch(req, orientation), timeout=WORKER_OPERATION_TIMEOUT
        )
        if _is_error(result):
            await _handle_failure(rid, req, result, retry_after)
        else:
            gen_result = parse_result(result, req_type)
            await _set_request_status(req, "COMPLETED", media_id=gen_result.media_id, output_url=gen_result.url)
            if req_type in ("GENERATE_CHARACTER_IMAGE", "REGENERATE_CHARACTER_IMAGE", "EDIT_CHARACTER_IMAGE"):
                char_id = req.get("character_id")
                if char_id:
                    await apply_character_result(char_id, gen_result)
            else:
                await apply_scene_result(req.get("scene_id"), req_type, orientation, gen_result)
            await event_bus.emit("request_update", {"id": rid, "status": "COMPLETED"})
            log_event(logger, logging.INFO, "job_completed", **log_context,
                      duration_ms=round((time.monotonic() - operation_started) * 1000), final_status="COMPLETED")
    except Exception as e:
        if isinstance(e, asyncio.TimeoutError):
            log_event(logger, logging.WARNING, "job_timeout", **log_context, timeout_seconds=WORKER_OPERATION_TIMEOUT)
        log_event(logger, logging.ERROR, "job_failed", exc_info=True, **log_context,
                  duration_ms=round((time.monotonic() - operation_started) * 1000) if "operation_started" in locals() else 0,
                  final_status="FAILED", error_type=type(e).__name__)
        await event_bus.emit("request_update", {"id": rid, "status": "FAILED", "error": str(e)})
        await _handle_failure(rid, req, {"error": str(e)}, retry_after)


async def _dispatch(req: dict, orientation: str) -> dict:
    """Route request to the appropriate OperationService method."""
    from agent.sdk.services.operations import get_operations
    ops = get_operations()
    req_type, rid = req["type"], req["id"]
    pid = req.get("project_id", "0")

    # Scene-based operations
    if req_type in ("GENERATE_IMAGE", "REGENERATE_IMAGE", "EDIT_IMAGE",
                    "GENERATE_VIDEO", "REGENERATE_VIDEO", "GENERATE_VIDEO_REFS", "UPSCALE_VIDEO"):
        scene = await crud.get_scene(req.get("scene_id"))
        if not scene:
            return {"error": "Scene not found"}
        scene["_project_id"] = pid

        if req_type in ("GENERATE_IMAGE", "REGENERATE_IMAGE"):
            return await ops.generate_scene_image(scene, orientation)
        if req_type == "EDIT_IMAGE":
            return await ops.edit_scene_image(scene, orientation, source_media_id=req.get("source_media_id"))
        if req_type in ("GENERATE_VIDEO", "REGENERATE_VIDEO"):
            return await ops.generate_scene_video(scene, orientation, request_id=rid)
        if req_type == "GENERATE_VIDEO_REFS":
            return await ops.generate_scene_video_refs(scene, orientation, request_id=rid)
        if req_type == "UPSCALE_VIDEO":
            return await ops.upscale_scene_video(scene, orientation, request_id=rid)

    # Character operations
    if req_type in ("GENERATE_CHARACTER_IMAGE", "REGENERATE_CHARACTER_IMAGE", "EDIT_CHARACTER_IMAGE"):
        char = await crud.get_character(req.get("character_id"))
        if not char:
            return {"error": "Character not found"}
        if req_type == "REGENERATE_CHARACTER_IMAGE":
            # Clear existing media so generate_reference_image takes the normal (not fast) path
            await crud.update_character(char["id"], media_id=None, reference_image_url=None)
            char["media_id"] = None
            char["reference_image_url"] = None
            return await ops.generate_reference_image(char, pid)
        if req_type == "EDIT_CHARACTER_IMAGE":
            src = req.get("source_media_id") or char.get("media_id")
            if not src:
                return {"error": "No source image to edit — generate a reference image first"}
            edit_prompt = char.get("image_prompt") or char.get("description", "")
            project = await crud.get_project(pid) if pid != "0" else None
            tier = project.get("user_paygate_tier", "PAYGATE_TIER_ONE") if project else "PAYGATE_TIER_ONE"
            aspect = "IMAGE_ASPECT_RATIO_LANDSCAPE" if char.get("entity_type") in ("location",) else "IMAGE_ASPECT_RATIO_PORTRAIT"
            return await ops._client.edit_image(
                prompt=edit_prompt, source_media_id=src,
                project_id=pid, aspect_ratio=aspect,
                user_paygate_tier=tier,
            )
        return await ops.generate_reference_image(char, pid)

    return {"error": f"Unknown request type: {req_type}"}


async def _reupload_media(url: str, project_id: str) -> str | None:
    """Download image from URL and re-upload to get a fresh media_id."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    logger.warning("Re-upload: failed to download %s (status %d)", url[:60], resp.status)
                    return None
                image_bytes = await resp.read()
                content_type = resp.headers.get("Content-Type", "image/jpeg")

        if not content_type.startswith("image/"):
            logger.warning("Re-upload: unexpected content-type %s from %s", content_type, url[:60])
            return None
        image_b64 = base64.b64encode(image_bytes).decode()
        mime = content_type.split(";")[0].strip()

        client = get_flow_client()
        result = await client.upload_image(image_b64, mime_type=mime, project_id=project_id)
        new_mid = result.get("_mediaId")
        if new_mid:
            logger.info("Re-upload OK: fresh media_id=%s", new_mid[:20])
            return new_mid
        logger.warning("Re-upload: no media_id in response: %s", str(result)[:200])
    except Exception as e:
        logger.warning("Re-upload failed: %s", e)
    return None


async def _recover_entity_not_found(req: dict) -> bool:
    """When Google returns 'entity not found', re-upload the image to get a fresh media_id."""
    req_type = req.get("type", "")
    pid = req.get("project_id", "")
    orientation = await _resolve_orientation(req)
    prefix = "vertical" if orientation == "VERTICAL" else "horizontal"

    # Scene-based requests: re-upload scene image
    if req_type in ("GENERATE_VIDEO", "REGENERATE_VIDEO", "GENERATE_VIDEO_REFS", "UPSCALE_VIDEO"):
        scene = await crud.get_scene(req.get("scene_id"))
        if not scene:
            return False
        url = scene.get(f"{prefix}_image_url")
        if not url:
            return False
        new_mid = await _reupload_media(url, pid)
        if new_mid:
            await crud.update_scene(scene["id"], **{f"{prefix}_image_media_id": new_mid})
            logger.info("Recovered scene %s: new %s_image_media_id=%s", scene["id"][:12], prefix, new_mid[:12])
            return True

    # Character-based requests: re-upload ref image
    if req_type in ("EDIT_CHARACTER_IMAGE",):
        char = await crud.get_character(req.get("character_id"))
        if not char:
            return False
        url = char.get("reference_image_url")
        if not url:
            return False
        new_mid = await _reupload_media(url, pid)
        if new_mid:
            await crud.update_character(char["id"], media_id=new_mid)
            logger.info("Recovered character %s: new media_id=%s", char["id"][:12], new_mid[:12])
            return True

    return False


def _retry_at(delay_seconds: int) -> str:
    jitter = random.uniform(0, RETRY_JITTER_SECONDS)
    return (datetime.now(timezone.utc) + timedelta(seconds=delay_seconds + jitter)).isoformat(timespec="microseconds").replace("+00:00", "Z")


#: Content-policy rejections: the same prompt and refs fail the same way, and each retry
#: is another paid generation. Matched as exact codes only, never loose wording.
_CONTENT_POLICY_CODE = re.compile(r"\bPUBLIC_ERROR_(?:UNSAFE_GENERATION|MINOR_INPUT_IMAGE)\b")

#: Re-uploading media for a "not found" error is capped per request, so a job that keeps
#: coming back not-found fails clearly instead of cycling forever.
MAX_NOT_FOUND_RECOVERIES = 2
_RECOVERY_MARK = re.compile(r"^not_found_recovery=(\d+);")


def generation_cooldown_active(client) -> bool:
    """True while the Flow client's UNUSUAL_ACTIVITY cooldown refuses generation calls."""
    status = getattr(client, "generation_guard_status", None)
    return bool(isinstance(status, dict) and status.get("cooldown_active"))


def content_policy_code(error: str) -> str | None:
    """PUBLIC_ERROR_UNSAFE_GENERATION / PUBLIC_ERROR_MINOR_INPUT_IMAGE if the error carries one."""
    match = _CONTENT_POLICY_CODE.search(error or "")
    return match.group(0) if match else None


def _is_retryable_error(error: str) -> bool:
    if content_policy_code(error):
        return False
    permanent_markers = (
        "unsupported_on_batch_api", "no_flow_project", "invalid request",
        "permission denied", "unauthorized", "forbidden", "not configured",
        "unknown request type", "scene not found", "character not found",
    )
    return not any(marker in error.lower() for marker in permanent_markers)


async def _handle_failure(rid: str, req: dict, result: dict, retry_after: dict = None):
    error_msg = result.get("error")
    if not error_msg:
        data = result.get("data", {})
        if isinstance(data, dict):
            ef = data.get("error", "Unknown error")
            if isinstance(ef, dict):
                error_msg = ef.get("message", json.dumps(ef)[:200])
                # Extract detailed reason from error details (e.g. PUBLIC_ERROR_UNSAFE_GENERATION)
                details = ef.get("details", [])
                if details and isinstance(details, list):
                    for d in details:
                        reason = d.get("reason") if isinstance(d, dict) else None
                        if reason:
                            error_msg = f"{error_msg} [{reason}]"
                            break
            else:
                error_msg = str(ef)
        else:
            error_msg = "Unknown error"
    if isinstance(error_msg, dict):
        error_msg = json.dumps(error_msg)[:200]

    # Auto-recover expired media by re-uploading. A video poll timeout also quotes Flow's
    # "Media not found." - that is a still-running job, not missing media: re-uploading the
    # image would change the scene's image id under a render made from the old one, so it
    # takes the normal counted retry (which re-polls the stored operation).
    lower_msg = str(error_msg).lower()
    if "not found" in lower_msg and not lower_msg.startswith("polling timeout"):
        mark = _RECOVERY_MARK.match(req.get("last_failure_reason") or "")
        recoveries = int(mark.group(1)) if mark else 0
        if recoveries >= MAX_NOT_FOUND_RECOVERIES:
            message = f"not found after {recoveries} media re-upload recoveries: {error_msg}"
            await _set_request_status(req, "FAILED", error_message=message, last_failure_reason=message)
            await _mark_scene_failed(req)
            log_event(logger, logging.ERROR, "job_failed", request_id=rid, project_id=req.get("project_id"), video_id=req.get("video_id"), scene_id=req.get("scene_id"), operation=req.get("type"), final_status="FAILED", reason="not_found_recovery_limit")
            return
        recovered = await _recover_entity_not_found(req)
        if recovered:
            log_event(logger, logging.WARNING, "retry_scheduled", request_id=rid, project_id=req.get("project_id"), video_id=req.get("video_id"), scene_id=req.get("scene_id"), operation=req.get("type"), reason="expired_media_recovered", attempt=recoveries + 1)
            await _set_request_status(req, "PENDING", error_message=f"recovered: {error_msg}",
                                      last_failure_reason=f"not_found_recovery={recoveries + 1}; {error_msg}")
            return

    error_lower = str(error_msg).lower()

    # PUBLIC_ERROR_UNUSUAL_ACTIVITY is a Google anti-abuse/session trust block,
    # not an ordinary CAPTCHA mint failure. Retrying it in the generic CAPTCHA
    # loop only creates more generation submits while Google is asking us to
    # slow down, so stop this request and require an explicit resubmit after
    # the session/network has recovered.
    if "public_error_unusual_activity" in error_lower or "unusual activity" in error_lower:
        await _set_request_status(req, "FAILED", error_message=str(error_msg), last_failure_reason=str(error_msg))
        await _mark_scene_failed(req)
        logger.error("Request %s FAILED (Google unusual-activity block; manual recovery required): %s",
                     rid[:8], error_msg)
        return

    if not _is_retryable_error(str(error_msg)):
        await _set_request_status(req, "FAILED", error_message=str(error_msg), last_failure_reason=str(error_msg))
        await _mark_scene_failed(req)
        log_event(logger, logging.ERROR, "job_failed", request_id=rid, project_id=req.get("project_id"), video_id=req.get("video_id"), scene_id=req.get("scene_id"), operation=req.get("type"), final_status="FAILED", reason="permanent_error")
        return

    # A capability the batch path does not have, or a missing Flow project, is
    # a configuration answer — not something a retry can reach. Fail it once.
    if "unsupported_on_batch_api" in error_lower or "no_flow_project" in error_lower:
        await _set_request_status(req, "FAILED", error_message=str(error_msg), last_failure_reason=str(error_msg))
        await _mark_scene_failed(req)
        logger.error("Request %s FAILED (not retryable): %s", rid[:8], error_msg)
        return

    # WS transient errors (extension disconnect/reconnect): retry without incrementing count
    if "extension reconnected" in error_lower or "extension disconnected" in error_lower or "extension not connected" in error_lower:
        await _set_request_status(req, "PENDING", error_message=str(error_msg), last_failure_reason=str(error_msg), next_retry_at=_retry_at(5))
        log_event(logger, logging.WARNING, "retry_scheduled", request_id=rid, project_id=req.get("project_id"), video_id=req.get("video_id"), scene_id=req.get("scene_id"), operation=req.get("type"), reason="transient_connection")
        return

    # reCAPTCHA errors: retry up to 10 times — deferred dict in main loop handles delay
    if "captcha" in error_lower or "recaptcha" in error_lower:
        retry = req.get("retry_count", 0) + 1
        if retry < 10:
            await _set_request_status(req, "PENDING", retry_count=retry, error_message=str(error_msg), last_failure_reason=str(error_msg), next_retry_at=_retry_at(min(2 ** retry * 10, 300)))
            log_event(logger, logging.WARNING, "retry_scheduled", request_id=rid, project_id=req.get("project_id"), video_id=req.get("video_id"), scene_id=req.get("scene_id"), operation=req.get("type"), attempt=retry, reason="captcha")
            return
        else:
            await _set_request_status(req, "FAILED", error_message=str(error_msg), last_failure_reason=str(error_msg))
            await _mark_scene_failed(req)
            logger.error("Request %s FAILED after 10 reCAPTCHA retries: %s", rid[:8], error_msg)
            return

    retry = req.get("retry_count", 0) + 1
    if retry < MAX_RETRIES:
        now = time.time()
        if retry_after is not None:
            ra = retry_after.get(rid, 0.0)
            if ra > now:
                # Still in backoff — reset to PENDING so it's not stuck in PROCESSING
                await _set_request_status(req, "PENDING", error_message=str(error_msg), last_failure_reason=str(error_msg), next_retry_at=_retry_at(max(1, int(ra - now))))
                return
            retry_after[rid] = now + min(2 ** retry * 10, 300)
        await _set_request_status(req, "PENDING", retry_count=retry, error_message=str(error_msg), last_failure_reason=str(error_msg), next_retry_at=_retry_at(min(2 ** retry * 10, 300)))
        log_event(logger, logging.WARNING, "retry_scheduled", request_id=rid, project_id=req.get("project_id"), video_id=req.get("video_id"), scene_id=req.get("scene_id"), operation=req.get("type"), attempt=retry, reason="retryable_error")
    else:
        await _set_request_status(req, "FAILED", error_message=str(error_msg), last_failure_reason=str(error_msg))
        await _mark_scene_failed(req)
        logger.error("Request %s FAILED permanently: %s", rid[:8], error_msg)


async def _mark_scene_failed(req: dict):
    scene_id = req.get("scene_id")
    if not scene_id:
        return
    orientation = await _resolve_orientation(req)
    prefix = "vertical" if orientation == "VERTICAL" else "horizontal"
    req_type = req["type"]
    updates = {}
    if req_type in ("GENERATE_IMAGE", "REGENERATE_IMAGE", "EDIT_IMAGE"):
        updates[f"{prefix}_image_status"] = "FAILED"
    elif req_type in ("GENERATE_VIDEO", "REGENERATE_VIDEO", "GENERATE_VIDEO_REFS"):
        updates[f"{prefix}_video_status"] = "FAILED"
    elif req_type == "UPSCALE_VIDEO":
        updates[f"{prefix}_upscale_status"] = "FAILED"
    if updates:
        await crud.update_scene(scene_id, **updates)


async def _is_already_completed(req: dict, orientation: str) -> bool:
    scene_id = req.get("scene_id")
    req_type = req.get("type", "")
    if not scene_id or req_type == "GENERATE_CHARACTER_IMAGE":
        return False
    scene = await crud.get_scene(scene_id)
    if not scene:
        return False
    prefix = "vertical" if orientation == "VERTICAL" else "horizontal"
    if req_type in ("EDIT_IMAGE", "REGENERATE_IMAGE", "REGENERATE_VIDEO", "REGENERATE_CHARACTER_IMAGE", "EDIT_CHARACTER_IMAGE"):
        return False  # Always run — explicitly requesting new generation
    if req_type == "GENERATE_IMAGE":
        return scene.get(f"{prefix}_image_status") == "COMPLETED"
    if req_type in ("GENERATE_VIDEO", "GENERATE_VIDEO_REFS"):
        return scene.get(f"{prefix}_video_status") == "COMPLETED"
    if req_type == "UPSCALE_VIDEO":
        return scene.get(f"{prefix}_upscale_status") == "COMPLETED"
    return False


# ─── Module-level controller ──────────────────────────────────

_controller: WorkerController | None = None


def get_worker_controller() -> WorkerController:
    global _controller
    if _controller is None:
        _controller = WorkerController()
    return _controller
