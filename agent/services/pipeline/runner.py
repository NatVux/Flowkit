"""Pipeline runner: drives one video through REFS -> IMAGES -> VIDEOS -> CONCAT on the
ordinary request queue. The worker does the Flow calls and its own transient retries;
the runner only decides what to queue next, verifies results, and stops for a person.

State lives in pipeline_run / pipeline_run_item. Every tick re-reads it, so a restart
simply carries on: an item with a request is watched, never submitted twice.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from datetime import datetime, timezone

from agent.config import POLL_INTERVAL
from agent.db import crud, pipeline_crud as pc
from agent.services.event_bus import event_bus
from agent.services.flow_client import get_flow_client
from agent.services.pipeline import concat as pconcat, media as pmedia, planner as pl
from agent.worker.processor import generation_cooldown_active

logger = logging.getLogger(__name__)

#: The MV3 service worker drops its socket briefly all the time; only a longer gap pauses a run.
DISCONNECT_GRACE_SECONDS = 60
IN_FLIGHT = ("PENDING", "PROCESSING")
DONE_ITEM = ("COMPLETED", "SKIPPED")


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class RunError(ValueError):
    """A run cannot be created or advanced as asked (bad input, not ready)."""


class PipelineRunner:
    def __init__(self, client_getter=get_flow_client, *, interval: float = POLL_INTERVAL, now=None):
        self._client_getter = client_getter
        self._interval = interval
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._shutdown = asyncio.Event()
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, run_id: str) -> asyncio.Lock:
        """The loop and API calls act on the same run; one at a time per run."""
        return self._locks.setdefault(run_id, asyncio.Lock())

    # ── loop ────────────────────────────────────────────────

    async def start(self):
        while not self._shutdown.is_set():
            await self.tick_all()
            try:
                await asyncio.wait_for(self._shutdown.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                pass

    def request_shutdown(self):
        self._shutdown.set()

    async def tick_all(self):
        for run in await pc.list_runs(statuses=("RUNNING",)):
            try:
                await self.tick(run["id"])
            except Exception:  # one broken run must not stop the others
                logger.exception("pipeline run %s tick failed", run["id"][:8])

    async def tick(self, run_id: str):
        async with self._lock(run_id):
            await self._tick_locked(run_id)

    async def _tick_locked(self, run_id: str):
        run = await pc.get_run(run_id)
        if not run or run["status"] != "RUNNING":
            return
        if not await self._session_ok(run):
            return
        handler = {"REFS": self._tick_refs, "IMAGES": self._tick_images,
                   "VIDEOS": self._tick_videos, "CONCAT": self._tick_concat}[run["stage"]]
        await handler(run)

    # ── session health: extension and UNUSUAL_ACTIVITY ─────

    async def _session_ok(self, run: dict) -> bool:
        client = self._client_getter()
        if not client.connected:
            now = self._now()
            if not run["disconnected_since"]:
                await pc.update_run(run["id"], disconnected_since=_iso(now))
            elif (now - _parse_iso(run["disconnected_since"])).total_seconds() > DISCONNECT_GRACE_SECONDS:
                await self._pause(run, "EXTENSION_DISCONNECTED",
                                  f"Extension disconnected for more than {DISCONNECT_GRACE_SECONDS}s. "
                                  "Reconnect it (open the Flow tab), then resume.")
            return False
        if run["disconnected_since"]:
            await pc.update_run(run["id"], disconnected_since=None)
        if generation_cooldown_active(client):
            await self._pause(run, "UNUSUAL_ACTIVITY",
                              "Google flagged the session (UNUSUAL_ACTIVITY cooldown). Check the Flow tab, then resume.")
            return False
        return True

    async def _transition(self, run_id: str, status: str, **fields) -> dict:
        """Every run status change goes through here, so the dashboard hears about it."""
        run = await pc.transition_run(run_id, status, **fields)
        await event_bus.emit("pipeline_update", {
            "run_id": run["id"], "video_id": run["video_id"], "status": run["status"], "stage": run["stage"],
            "pause_reason": run["pause_reason"], "status_detail": run["status_detail"],
            "final_path": run["final_path"]})
        return run

    async def _pause(self, run: dict, reason: str, detail: str):
        held = await pc.hold_requests(run["id"], reason)
        await self._transition(run["id"], "PAUSED", pause_reason=reason, status_detail=detail,
                                disconnected_since=None)
        logger.warning("pipeline run %s paused: %s (held %d requests)", run["id"][:8], reason, held)

    # ── item sync ───────────────────────────────────────────

    async def _sync(self, run: dict, items: list[dict]) -> list[dict] | None:
        """Bring SUBMITTED items up to date with their requests. Returns the items, or None
        if the run was paused on the way."""
        p = pl.prefix(run["orientation"])
        for item in items:
            if item["status"] != "SUBMITTED":
                continue
            request = await crud.get_request(item["request_id"]) if item["request_id"] else None
            if request is None:  # removed outside the runner: plan it again
                item.update(await pc.update_item(item["id"], status="PLANNED", request_id=None))
                continue
            if request["status"] in IN_FLIGHT:
                continue
            if request["status"] == "COMPLETED":
                problem = await self._verify(item, p)
                fields = ({"status": "COMPLETED"} if problem is None else
                          {"status": "NEEDS_USER_ACTION", "error_code": "MISSING_OUTPUT", "error_message": problem})
                item.update(await pc.update_item(item["id"], **fields))
                continue
            failure = pl.classify_failed_request(request)
            if failure.action == "PAUSE":
                # Nothing was produced; queue it again after the person resumes.
                await pc.update_item(item["id"], status="PLANNED", request_id=None,
                                     error_code=failure.code, error_message=failure.message)
                await self._pause(run, failure.code, failure.message)
                return None
            item.update(await pc.update_item(item["id"], status="NEEDS_USER_ACTION",
                                             error_code=failure.code, error_message=failure.message))
        return items

    async def _verify(self, item: dict, p: str) -> str | None:
        """The skills' post-checks: a UUID media id is really stored (CAMS ids are fixed from the URL)."""
        if item["target_type"] == "character":
            ent = await crud.get_character(item["target_id"])
            if ent and pl.is_uuid(ent.get("media_id")):
                return None
            fixed = pl.uuid_from_url((ent or {}).get("reference_image_url"))
            if fixed:
                await crud.update_character(item["target_id"], media_id=fixed)
                return None
            return "request completed but the entity has no UUID media_id"
        scene = await crud.get_scene(item["target_id"])
        if not scene:
            return "scene was deleted"
        if item["stage"] == "IMAGES":
            if pl.image_done(scene, p):
                return None
            fixed = pl.uuid_from_url(scene.get(f"{p}_image_url"))
            if scene.get(f"{p}_image_status") == "COMPLETED" and fixed:
                await crud.update_scene(scene["id"], **{f"{p}_image_media_id": fixed})
                return None
            return "request completed but the scene has no UUID image media_id"
        return None if pl.video_done(scene, p) else "request completed but the scene has no video"

    # ── stages ──────────────────────────────────────────────

    async def _tick_refs(self, run: dict):
        items = await self._sync(run, await pc.list_items(run["id"], "REFS"))
        if items is None:
            return
        await self._submit(run, [i for i in items if i["status"] == "PLANNED"], "REFS")
        await self._maybe_finish_stage(run, await pc.list_items(run["id"], "REFS"))

    async def _tick_images(self, run: dict):
        items = await self._sync(run, await pc.list_items(run["id"], "IMAGES"))
        if items is None:
            return
        scenes = {s["id"]: s for s in await crud.list_scenes(run["video_id"])}
        by_scene = {i["target_id"]: i for i in items}
        ready = []
        for item in items:
            if item["status"] != "PLANNED":
                continue
            parent = by_scene.get((scenes.get(item["target_id"]) or {}).get("parent_scene_id"))
            if parent is None or parent["status"] in DONE_ITEM:  # cha trước con
                ready.append(item)
        if ready:
            wave = min(i["wave"] for i in ready)
            await pc.update_run(run["id"], wave=wave)
        await self._submit(run, ready, "IMAGES")
        await self._maybe_finish_stage(run, await pc.list_items(run["id"], "IMAGES"))

    async def _tick_videos(self, run: dict):
        items = await self._sync(run, await pc.list_items(run["id"], "VIDEOS"))
        if items is None:
            return
        planned = [i for i in items if i["status"] == "PLANNED"]
        if planned:
            await self._clear_end_frames(run)
        await self._submit(run, planned, "VIDEOS")
        items = await pc.list_items(run["id"], "VIDEOS")
        await self._after_videos(run, items)
        await self._maybe_finish_stage(run, await pc.list_items(run["id"], "VIDEOS"))

    async def _after_videos(self, run: dict, items: list[dict]):
        """Download each clip as soon as its scene's video is COMPLETED (first attempt)."""
        project = await crud.get_project(run["project_id"])
        p = pl.prefix(run["orientation"])
        for item in items:
            if item["status"] == "COMPLETED" and item["download_status"] is None:
                await self._download(item, project, await crud.get_scene(item["target_id"]), p)

    async def _download(self, item: dict, project: dict, scene: dict, p: str) -> bool:
        dest = pmedia.scene_video_file(project, scene)
        attempts = item["download_attempts"] + 1
        try:
            await pmedia.download_scene_video(scene, p, dest)
        except pmedia.DownloadError as exc:
            await pc.update_item(item["id"], download_status="FAILED", download_attempts=attempts,
                                 error_message=f"download failed: {exc}"[:500])
            return False
        fields = {"download_status": "DOWNLOADED", "download_attempts": attempts, "local_path": str(dest)}
        if item["error_code"] == "MISSING_CLIP":  # it was waiting on this file: resolved
            fields.update(status="COMPLETED", error_code=None, error_message=None)
        elif (item["error_message"] or "").startswith("download failed"):
            fields["error_message"] = None
        await pc.update_item(item["id"], **fields)
        return True

    async def _tick_concat(self, run: dict):
        """fk-concat on local files only. A missing or unreadable clip is downloaded again
        once; if that fails too the run waits for a person."""
        project = await crud.get_project(run["project_id"])
        p = pl.prefix(run["orientation"])
        scenes = sorted(await crud.list_scenes(run["video_id"]), key=lambda s: s["display_order"])
        await pc.add_items(run["id"], [{"stage": "VIDEOS", "target_type": "scene", "target_id": s["id"],
                                        "status": "SKIPPED"} for s in scenes])
        items = {i["target_id"]: i for i in await pc.list_items(run["id"], "VIDEOS")}
        missing = []
        for scene in scenes:
            item = items[scene["id"]]
            path = pmedia.scene_video_file(project, scene)
            if await pmedia.valid_video(path):
                if item["local_path"] != str(path) or item["error_code"] == "MISSING_CLIP":
                    fields = {"download_status": "DOWNLOADED", "local_path": str(path)}
                    if item["error_code"] == "MISSING_CLIP":
                        fields.update(status="COMPLETED", error_code=None, error_message=None)
                    await pc.update_item(item["id"], **fields)
                continue
            if not pl.video_done(scene, p):
                missing.append((item, "scene has no finished video"))
                continue
            # Two attempts in total: the one made when the video finished (if any) plus one retry.
            attempts, ok = item["download_attempts"], False
            while attempts < 2 and not ok:
                ok = await self._download(item, project, scene, p)
                item = await pc.get_item(item["id"])
                attempts = item["download_attempts"]
            if not ok:
                missing.append((item, "clip could not be downloaded (tried twice)"))
        if missing:
            for item, why in missing:
                await pc.update_item(item["id"], status="NEEDS_USER_ACTION", error_code="MISSING_CLIP",
                                     error_message=why)
            await self._transition(run["id"], "NEEDS_USER_ACTION", status_detail=(
                f"{len(missing)} clip(s) missing for concat. Redo those scene videos or place the files, "
                "then resume."))
            return
        if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
            await self._transition(run["id"], "NEEDS_USER_ACTION",
                                    status_detail="ffmpeg/ffprobe not found on PATH; install them, then resume.")
            return
        clips = [pmedia.scene_video_file(project, s) for s in scenes]
        out = pmedia.final_video_file(project, run["video_id"])
        workdir = pmedia.project_output_dir(project) / f"concat_{run['video_id'][:8]}"
        try:
            result = await asyncio.to_thread(pconcat.run_concat, clips, out, workdir)
        except pconcat.ConcatError as exc:
            await self._transition(run["id"], "NEEDS_USER_ACTION", error=str(exc)[:1000],
                                    status_detail=f"Concat failed: {str(exc)[:300]}")
            return
        await self._transition(run["id"], "COMPLETED", final_path=result["path"], error=None,
                                status_detail=f"Final video: {result['clips']} clips, {result['duration']}s, "
                                              f"{result['width']}x{result['height']}.")

    async def _clear_end_frames(self, run: dict):
        """Each scene is its own i2v clip: start+end-frame chaining is unsupported on the batch
        API, and a CONTINUATION image sets its parent's end frame automatically. Checked
        right before every video submit, since a redone child image sets it again."""
        p = pl.prefix(run["orientation"])
        cleared = 0
        for scene in await crud.list_scenes(run["video_id"]):
            if scene.get(f"{p}_end_scene_media_id"):
                await crud.update_scene(scene["id"], **{f"{p}_end_scene_media_id": None})
                cleared += 1
        if cleared:
            await self._warn(run, f"Cleared the end frame of {cleared} chain scene(s): every scene is rendered "
                                  "as its own image-to-video clip.")

    async def _submit(self, run: dict, items: list[dict], stage: str):
        if not items:
            return
        p_orientation = run["orientation"]

        def build(item: dict) -> dict:
            spec = {"req_type": item["request_type"], "project_id": run["project_id"]}
            if item["target_type"] == "character":
                spec["character_id"] = item["target_id"]
            else:
                spec.update(scene_id=item["target_id"], video_id=run["video_id"], orientation=p_orientation)
            return spec

        await pc.submit_items(run["id"], [i["id"] for i in items], build)
        await event_bus.emit("pipeline_items_queued", {"run_id": run["id"], "stage": stage, "count": len(items)})
        logger.info("pipeline run %s: queued %d %s request(s)", run["id"][:8], len(items), stage)

    async def _maybe_finish_stage(self, run: dict, items: list[dict]):
        if any(i["status"] == "SUBMITTED" for i in items):
            return
        if all(i["status"] in DONE_ITEM for i in items):
            await self._stage_done(run)
            return
        if any(i["status"] == "PLANNED" for i in items) and not any(
                i["status"] in ("NEEDS_USER_ACTION", "FAILED") for i in items):
            return  # still has work it can queue next tick
        blocked = [i for i in items if i["status"] in ("NEEDS_USER_ACTION", "FAILED")]
        detail = f"{len(blocked)} item(s) in {run['stage']} need attention: " + "; ".join(
            f"{i['target_type']} {i['target_id'][:8]} {i['error_code']}" for i in blocked[:5])
        await self._transition(run["id"], "NEEDS_USER_ACTION", status_detail=detail)

    async def _stage_done(self, run: dict):
        stage = run["stage"]
        following = self._next_stage(run)
        if following is None:
            await self._transition(run["id"], "COMPLETED", status_detail=f"{stage} done; nothing left.")
            return
        if stage in run["checkpoints"]:
            est = await self._estimate(run)
            await self._transition(
                run["id"], "AWAITING_APPROVAL", estimate=est,
                status_detail=f"{stage} done. Approve to start {following} "
                              f"(minimum {pl.stage_estimate(following, est)} generations).")
            return
        await self._enter_stage(run, following)
        await self._tick_locked(run["id"])  # no checkpoint: carry straight on in the same tick

    def _next_stage(self, run: dict) -> str | None:
        order = ["REFS", "IMAGES", "VIDEOS"] + (["CONCAT"] if run["options"].get("concat", True) else [])
        index = order.index(run["stage"])
        return order[index + 1] if index + 1 < len(order) else None

    async def _enter_stage(self, run: dict, stage: str):
        await self._plan_stage(run, stage)
        await pc.update_run(run["id"], stage=stage, wave=None)

    async def _plan_stage(self, run: dict, stage: str):
        p = pl.prefix(run["orientation"])
        items = []
        if stage == "REFS":
            entities = await crud.get_project_characters(run["project_id"])
            needed = {e["id"] for e in pl.refs_needed(entities)}
            items = [{"stage": "REFS", "target_type": "character", "target_id": e["id"],
                      "status": "PLANNED" if e["id"] in needed else "SKIPPED"} for e in entities]
            types = {e["id"]: "GENERATE_CHARACTER_IMAGE" for e in entities}
        elif stage == "IMAGES":
            scenes = await crud.list_scenes(run["video_id"])
            tasks = {t.scene_id: t for t in pl.image_waves(scenes, p)}
            items = [{"stage": "IMAGES", "target_type": "scene", "target_id": s["id"],
                      "wave": tasks[s["id"]].wave if s["id"] in tasks else 0,
                      "status": "PLANNED" if s["id"] in tasks else "SKIPPED"} for s in scenes]
            types = {sid: t.request_type for sid, t in tasks.items()}
        elif stage == "VIDEOS":
            scenes = await crud.list_scenes(run["video_id"])
            needed = {s["id"] for s in pl.videos_needed(scenes, p)}
            items = [{"stage": "VIDEOS", "target_type": "scene", "target_id": s["id"],
                      "status": "PLANNED" if s["id"] in needed else "SKIPPED"} for s in scenes]
            types = {sid: "GENERATE_VIDEO" for sid in needed}
        else:
            return
        await pc.add_items(run["id"], items)
        for item in await pc.list_items(run["id"], stage):
            if item["status"] == "PLANNED" and item["target_id"] in types:
                await pc.update_item(item["id"], request_type=types[item["target_id"]])

    async def _estimate(self, run: dict) -> dict:
        entities = await crud.get_project_characters(run["project_id"])
        scenes = await crud.list_scenes(run["video_id"])
        return pl.estimate(entities, scenes, run["orientation"], concat=run["options"].get("concat", True))

    async def _warn(self, run: dict, message: str):
        fresh = await pc.get_run(run["id"])
        if message not in fresh["warnings"]:
            await pc.update_run(run["id"], warnings=[*fresh["warnings"], message])

    # ── user operations (called by the API) ─────────────────

    async def create(self, video_id: str, *, checkpoints: list[str] | None = None, concat: bool = True) -> dict:
        video = await crud.get_video(video_id)
        if not video:
            raise LookupError("video not found")
        project = await crud.get_project(video["project_id"])
        orientation = video.get("orientation")
        if orientation not in ("VERTICAL", "HORIZONTAL"):
            raise RunError("video has no orientation; set it on the video first")
        from agent.materials import get_material
        if not project.get("material") or get_material(project["material"]) is None:
            raise RunError("project has no valid material")
        checkpoints = list(pl.DEFAULT_CHECKPOINTS if checkpoints is None else checkpoints)
        unknown = [c for c in checkpoints if c not in pl.CREDIT_STAGES]
        if unknown:
            raise RunError(f"unknown checkpoint(s) {unknown}; use {list(pl.CREDIT_STAGES)}")
        if not await crud.list_scenes(video_id):
            raise RunError("video has no scenes")
        warnings = self._meta_warnings(project, orientation)
        entities = await crud.get_project_characters(project["id"])
        scenes = await crud.list_scenes(video_id)
        try:
            est = pl.estimate(entities, scenes, orientation, concat=concat)
        except pl.PlanError as exc:
            raise RunError(str(exc)) from exc
        return await pc.create_run(project_id=project["id"], video_id=video_id, orientation=orientation,
                                   checkpoints=checkpoints, options={"concat": concat}, estimate=est,
                                   warnings=warnings)

    @staticmethod
    def _meta_warnings(project: dict, orientation: str) -> list[str]:
        """video.orientation is the source of truth; a differing meta.json is only reported."""
        from agent.config import OUTPUT_DIR
        from agent.utils.slugify import slugify
        meta = OUTPUT_DIR / slugify(project["name"]) / "meta.json"
        try:
            found = json.loads(meta.read_text(encoding="utf-8")).get("orientation")
        except (OSError, ValueError):
            return []
        if found and found != orientation:
            return [f"meta.json says {found} but the video is {orientation}; using {orientation}."]
        return []

    def _preflight(self):
        client = self._client_getter()
        if not client.connected:
            raise RunError("extension not connected: open a signed-in Flow tab first")
        if generation_cooldown_active(client):
            remaining = client.generation_guard_status.get("cooldown_remaining_s")
            raise RunError(f"UNUSUAL_ACTIVITY cooldown active ({remaining}s left)")

    async def start(self, run_id: str) -> dict:
        async with self._lock(run_id):
            return await self._start(run_id)

    async def _start(self, run_id: str) -> dict:
        run = await self._get(run_id)
        if run["status"] != "DRAFT":
            raise pc.PipelineConflict(f"run is {run['status']}; only a DRAFT run can be started")
        self._preflight()
        est = await self._estimate(run)
        run = await self._transition(run_id, "RUNNING", expected=("DRAFT",), stage="REFS", estimate=est,
                                      status_detail=None)
        await self._plan_stage(run, "REFS")
        await self._tick_locked(run_id)
        return await self.status(run_id)

    async def approve(self, run_id: str) -> dict:
        async with self._lock(run_id):
            return await self._approve(run_id)

    async def _approve(self, run_id: str) -> dict:
        run = await self._get(run_id)
        if run["status"] != "AWAITING_APPROVAL":
            raise pc.PipelineConflict(f"run is {run['status']}; nothing to approve")
        self._preflight()
        following = self._next_stage(run)
        run = await self._transition(run_id, "RUNNING", expected=("AWAITING_APPROVAL",), status_detail=None)
        await self._enter_stage(run, following)
        await self._tick_locked(run_id)
        return await self.status(run_id)

    async def resume(self, run_id: str) -> dict:
        async with self._lock(run_id):
            return await self._resume(run_id)

    async def _resume(self, run_id: str) -> dict:
        run = await self._get(run_id)
        if run["status"] not in ("PAUSED", "NEEDS_USER_ACTION"):
            raise pc.PipelineConflict(f"run is {run['status']}; only a PAUSED or NEEDS_USER_ACTION run resumes")
        self._preflight()
        await self._transition(run_id, "RUNNING", expected=("PAUSED", "NEEDS_USER_ACTION"),
                                pause_reason=None, status_detail=None, disconnected_since=None)
        await pc.release_requests(run_id)
        # Resuming is the person's "try again": a clip that failed to download gets one more attempt.
        for item in await pc.list_items(run_id, "VIDEOS"):
            if item["error_code"] == "MISSING_CLIP" and item["download_attempts"] >= 2:
                await pc.update_item(item["id"], download_attempts=1)
        await self._tick_locked(run_id)
        return await self.status(run_id)

    async def cancel(self, run_id: str) -> dict:
        async with self._lock(run_id):
            return await self._cancel(run_id)

    async def _cancel(self, run_id: str) -> dict:
        run = await self._get(run_id)
        if run["status"] in pc.TERMINAL_RUN_STATUSES:
            raise pc.PipelineConflict(f"run is already {run['status']}")
        await pc.hold_requests(run_id, "cancelling")
        result = await pc.cancel_unstarted_requests(run_id)
        await self._transition(run_id, "CANCELLED",
                                status_detail=f"Cancelled: {result['deleted']} queued request(s) removed, "
                                              f"{result['released_to_finish']} already at Flow left to finish.")
        return await self.status(run_id)

    async def redo(self, run_id: str, target_type: str, target_id: str, *, include_descendants: bool = False,
                   confirm_invalidates: bool = False) -> dict:
        async with self._lock(run_id):
            return await self._redo(run_id, target_type, target_id, include_descendants=include_descendants,
                                    confirm_invalidates=confirm_invalidates)

    async def _redo(self, run_id: str, target_type: str, target_id: str, *, include_descendants: bool,
                    confirm_invalidates: bool) -> dict:
        """Redo one entity or scene of the current stage. Never automatic: a person asks for it."""
        run = await self._get(run_id)
        if run["status"] not in ("AWAITING_APPROVAL", "NEEDS_USER_ACTION", "PAUSED"):
            raise pc.PipelineConflict(f"run is {run['status']}; redo while it waits for you "
                                      "(AWAITING_APPROVAL, NEEDS_USER_ACTION or PAUSED)")
        self._preflight()
        p = pl.prefix(run["orientation"])
        if target_type == "character":
            if run["stage"] != "REFS":
                raise pc.PipelineConflict("references can only be redone before images; start a new run instead")
            ent = await crud.get_character(target_id)
            if not ent:
                raise LookupError("entity not found")
            affected = [s for s in await crud.list_scenes(run["video_id"])
                        if pl.uses_entity(s, ent) and (s.get(f"{p}_image_media_id") or s.get(f"{p}_video_media_id"))]
            if affected and not confirm_invalidates:
                raise pc.PipelineConflict(
                    f"a new reference wipes the image and video of {len(affected)} scene(s) using it "
                    f"({', '.join(str(s['display_order']) for s in affected)}); pass confirm_invalidates=true")
            targets = [(target_id, "REGENERATE_CHARACTER_IMAGE")]
            stage = "REFS"
        elif target_type == "scene":
            scene = await crud.get_scene(target_id)
            if not scene or scene["video_id"] != run["video_id"]:
                raise LookupError("scene not found in this run's video")
            if run["stage"] == "IMAGES":
                stage = "IMAGES"
                scenes = await crud.list_scenes(run["video_id"])
                wanted = {target_id} | (self._descendants(scenes, target_id) if include_descendants else set())
                continuation = {s["id"] for s in scenes if s.get("chain_type") == "CONTINUATION" and s.get("parent_scene_id")}
                targets = [(sid, "EDIT_IMAGE" if sid in continuation else "REGENERATE_IMAGE") for sid in wanted]
            elif run["stage"] in ("VIDEOS", "CONCAT"):
                stage = "VIDEOS"
                # fk-gen-videos: force a video regen by resetting its status first.
                await crud.update_scene(target_id, **{f"{p}_video_status": "PENDING"})
                # The old clip must not be picked up by concat in place of the new one.
                project = await crud.get_project(run["project_id"])
                pmedia.scene_video_file(project, scene).unlink(missing_ok=True)
                targets = [(target_id, "GENERATE_VIDEO")]
            else:
                raise pc.PipelineConflict("scenes are redone in the IMAGES or VIDEOS stage")
        else:
            raise RunError("target_type must be 'character' or 'scene'")

        by_target = {i["target_id"]: i for i in await pc.list_items(run_id, stage)}
        in_flight = [t for t, _ in targets if by_target.get(t, {}).get("status") == "SUBMITTED"]
        if in_flight:
            raise pc.PipelineConflict(f"{len(in_flight)} target(s) still have a request running")
        await pc.add_items(run_id, [{"stage": stage, "target_type": target_type, "target_id": t} for t, _ in targets])
        by_target = {i["target_id"]: i for i in await pc.list_items(run_id, stage)}
        if stage == "IMAGES":
            waves = {t.scene_id: t.wave for t in pl.image_waves(await crud.list_scenes(run["video_id"]), p,
                                                                 force={t for t, _ in targets})}
        for target, request_type in targets:
            fields = {"status": "PLANNED", "request_type": request_type, "request_id": None,
                      "error_code": None, "error_message": None, "redo_count": by_target[target]["redo_count"] + 1}
            if stage == "IMAGES":
                fields["wave"] = waves.get(target, 0)
            if stage == "VIDEOS":
                fields.update(download_status=None, local_path=None)
            await pc.update_item(by_target[target]["id"], **fields)
        stale = []
        if target_type == "scene" and stage == "IMAGES" and not include_descendants:
            stale = sorted(self._descendants(await crud.list_scenes(run["video_id"]), target_id))
        await self._transition(run_id, "RUNNING", pause_reason=None, stage=stage,
                                status_detail=f"Redo of {len(targets)} {target_type}(s) in {stage}.")
        if run["status"] == "PAUSED":
            await pc.release_requests(run_id)
        await self._tick_locked(run_id)
        result = await self.status(run_id)
        result["redo"] = {"estimated_generations": len(targets), "targets": [t for t, _ in targets],
                          "stale_descendants": stale}
        return result

    @staticmethod
    def _descendants(scenes: list[dict], root: str) -> set[str]:
        children: dict[str, list[str]] = {}
        for s in scenes:
            if s.get("parent_scene_id"):
                children.setdefault(s["parent_scene_id"], []).append(s["id"])
        out, stack = set(), list(children.get(root, []))
        while stack:
            sid = stack.pop()
            if sid not in out:
                out.add(sid)
                stack.extend(children.get(sid, []))
        return out

    # ── status ──────────────────────────────────────────────

    async def tick_and_get(self, run_id: str) -> dict:
        await self.tick(run_id)
        return await self.status(run_id)

    async def status(self, run_id: str) -> dict:
        run = await self._get(run_id)
        items = await pc.list_items(run_id)
        entities = {e["id"]: e for e in await crud.get_project_characters(run["project_id"])}
        scenes = {s["id"]: s for s in await crud.list_scenes(run["video_id"])}
        spent = 0
        stages: dict[str, dict] = {}
        for item in items:
            requests = [await crud.get_request(h["request_id"]) for h in item["request_history"]]
            item_spent = sum(pl.generations_spent(r) for r in requests)
            spent += item_spent
            current = await crud.get_request(item["request_id"]) if item["request_id"] else None
            label = (entities.get(item["target_id"]) or {}).get("name") if item["target_type"] == "character" \
                else f"scene #{(scenes.get(item['target_id']) or {}).get('display_order', '?')}"
            stage = stages.setdefault(item["stage"], {"total": 0, "done": 0, "items": []})
            stage["total"] += 1
            stage["done"] += item["status"] in DONE_ITEM
            stage["items"].append({
                "id": item["id"], "target_type": item["target_type"], "target_id": item["target_id"],
                "label": label, "wave": item["wave"], "status": item["status"],
                "request_type": item["request_type"], "request_id": item["request_id"],
                "request_status": current["status"] if current else None,
                "error_code": item["error_code"], "error_message": item["error_message"],
                "local_path": item["local_path"], "download_status": item["download_status"],
                "redo_count": item["redo_count"], "requests_made": len(item["request_history"]),
                "generations_spent": item_spent,
            })
        needs = [i for s in stages.values() for i in s["items"] if i["status"] in ("NEEDS_USER_ACTION", "FAILED")]
        try:
            remaining = await self._estimate(run) if run["status"] not in pc.TERMINAL_RUN_STATUSES else None
        except pl.PlanError:
            remaining = None
        return {
            "id": run["id"], "project_id": run["project_id"], "video_id": run["video_id"],
            "orientation": run["orientation"], "status": run["status"], "stage": run["stage"], "wave": run["wave"],
            "pause_reason": run["pause_reason"], "status_detail": run["status_detail"],
            "checkpoints": run["checkpoints"], "options": run["options"], "warnings": run["warnings"],
            "estimate_at_last_checkpoint": run["estimate"], "estimate_remaining": remaining,
            "generations_spent": spent,
            "generations_spent_note": "Approximate: image/reference requests count every attempt, "
                                      "a video counts once its Flow operation exists.",
            "needs_user_action": needs, "stages": stages, "final_path": run["final_path"],
            "error": run["error"], "created_at": run["created_at"], "started_at": run["started_at"],
            "finished_at": run["finished_at"],
        }

    async def _get(self, run_id: str) -> dict:
        run = await pc.get_run(run_id)
        if not run:
            raise LookupError("pipeline run not found")
        return run


_runner: PipelineRunner | None = None


def get_pipeline_runner() -> PipelineRunner:
    global _runner
    if _runner is None:
        _runner = PipelineRunner()
    return _runner
