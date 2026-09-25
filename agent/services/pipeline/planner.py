"""Pure planning logic for pipeline runs: no database, no Flow, no files.

What needs work in each stage, in which order, how many generations that costs at
minimum, and how a failed request should be treated. Ported from the step skills:
fk-gen-refs (UUID media ids, CAMS is not done), fk-gen-images (ROOT -> CONTINUATION
waves, EDIT_IMAGE after the parent), fk-gen-videos (one i2v per scene).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from agent.worker.processor import content_policy_code

STAGES = ("REFS", "IMAGES", "VIDEOS", "CONCAT")
CREDIT_STAGES = ("REFS", "IMAGES", "VIDEOS")
DEFAULT_CHECKPOINTS = ("REFS", "IMAGES")

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
_UUID_IN_URL = re.compile(r"/(?:image|video)/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
                          re.IGNORECASE)

IMAGE_TYPES = ("GENERATE_IMAGE", "REGENERATE_IMAGE", "EDIT_IMAGE")
VIDEO_TYPES = ("GENERATE_VIDEO", "REGENERATE_VIDEO", "GENERATE_VIDEO_REFS")
REF_TYPES = ("GENERATE_CHARACTER_IMAGE", "REGENERATE_CHARACTER_IMAGE", "EDIT_CHARACTER_IMAGE")


def is_uuid(value) -> bool:
    """A Flow media id. `CAMS...` strings are mediaGenerationIds, not media ids."""
    return bool(value) and bool(_UUID.match(str(value)))


def uuid_from_url(url: str | None) -> str | None:
    """fk-gen-images CAMS fix: the media id is the path segment after /image/ (or /video/)."""
    match = _UUID_IN_URL.search(url or "")
    return match.group(1).lower() if match else None


def prefix(orientation: str) -> str:
    return "vertical" if orientation == "VERTICAL" else "horizontal"


def scene_names(scene: dict) -> list[str]:
    raw = scene.get("character_names")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    return [n for n in raw or [] if isinstance(n, str)]


def uses_entity(scene: dict, entity: dict) -> bool:
    """Same rule as crud.list_scenes_for_character: a name in character_names equals name or slug."""
    names = set(scene_names(scene))
    return entity.get("name") in names or (bool(entity.get("slug")) and entity["slug"] in names)


# ─── stage selection ────────────────────────────────────────

def refs_needed(entities: list[dict]) -> list[dict]:
    """fk-gen-refs: entities without a UUID media_id. An entity that has one is never
    regenerated here: a new reference wipes every scene that uses it."""
    return [e for e in entities if not is_uuid(e.get("media_id"))]


def image_done(scene: dict, p: str) -> bool:
    return scene.get(f"{p}_image_status") == "COMPLETED" and is_uuid(scene.get(f"{p}_image_media_id"))


def video_done(scene: dict, p: str) -> bool:
    return scene.get(f"{p}_video_status") == "COMPLETED" and bool(scene.get(f"{p}_video_media_id"))


@dataclass(frozen=True)
class ImageTask:
    scene_id: str
    wave: int
    request_type: str  # GENERATE_IMAGE (ROOT) or EDIT_IMAGE (CONTINUATION, from its parent)


class PlanError(ValueError):
    """The scene graph cannot be planned (missing parent, cycle)."""


def image_waves(scenes: list[dict], p: str, *, force: set[str] = frozenset()) -> list[ImageTask]:
    """fk-gen-images waves. A scene needs an image when it is not COMPLETED with a UUID
    (or is in `force`). ROOT / parentless scenes use GENERATE_IMAGE; CONTINUATION
    scenes use EDIT_IMAGE from their parent's image. wave = 0 unless the parent also
    needs work, then parent's wave + 1: a child is only submitted once its parent is done.
    """
    by_id = {s["id"]: s for s in scenes}
    needs = {s["id"] for s in scenes if s["id"] in force or not image_done(s, p)}
    waves: dict[str, int] = {}

    def wave_of(sid: str, seen: tuple[str, ...] = ()) -> int:
        if sid in waves:
            return waves[sid]
        if sid in seen:
            raise PlanError(f"scene chain has a cycle through {sid}")
        parent_id = by_id[sid].get("parent_scene_id")
        if parent_id and parent_id not in by_id:
            raise PlanError(f"scene {sid} continues from {parent_id}, which is not in this video")
        waves[sid] = wave_of(parent_id, (*seen, sid)) + 1 if parent_id and parent_id in needs else 0
        return waves[sid]

    tasks = []
    for s in sorted(scenes, key=lambda s: s.get("display_order", 0)):
        if s["id"] not in needs:
            continue
        continuation = s.get("chain_type") == "CONTINUATION" and s.get("parent_scene_id")
        tasks.append(ImageTask(s["id"], wave_of(s["id"]), "EDIT_IMAGE" if continuation else "GENERATE_IMAGE"))
    return sorted(tasks, key=lambda t: (t.wave, by_id[t.scene_id].get("display_order", 0)))


def videos_needed(scenes: list[dict], p: str) -> list[dict]:
    return [s for s in sorted(scenes, key=lambda s: s.get("display_order", 0)) if not video_done(s, p)]


def scenes_reset_by_refs(scenes: list[dict], entities: list[dict]) -> set[str]:
    """Scenes whose image and video a new reference will wipe (apply_character_result)."""
    return {s["id"] for s in scenes for e in entities if uses_entity(s, e)}


# ─── estimate ───────────────────────────────────────────────

def estimate(entities: list[dict], scenes: list[dict], orientation: str, *, concat: bool = True) -> dict:
    """Minimum generations per stage for a run started now. Worker retries of transient
    errors are not included, so the real count can be higher."""
    p = prefix(orientation)
    refs = refs_needed(entities)
    reset = scenes_reset_by_refs(scenes, refs)
    tasks = image_waves(scenes, p, force=reset)
    image_ids = {t.scene_id for t in tasks}
    videos = [s for s in scenes if s["id"] in image_ids or not video_done(s, p)]
    per_wave: dict[int, int] = {}
    for t in tasks:
        per_wave[t.wave] = per_wave.get(t.wave, 0) + 1
    out = {
        "kind": "minimum",
        "note": "Minimum generations; worker retries of transient errors are not included.",
        "refs": len(refs),
        "images": len(tasks),
        "image_waves": [per_wave[w] for w in sorted(per_wave)],
        "videos": len(videos),
        "concat": 0,
        "scenes_reset_by_new_refs": len(reset),
    }
    out["total_generations"] = out["refs"] + out["images"] + out["videos"]
    out["concat_included"] = concat
    return out


def stage_estimate(stage: str, full: dict) -> int:
    return {"REFS": full.get("refs", 0), "IMAGES": full.get("images", 0),
            "VIDEOS": full.get("videos", 0)}.get(stage, 0)


# ─── failures and spend ─────────────────────────────────────

@dataclass(frozen=True)
class FailureClass:
    action: str   # "PAUSE" (session problem) or "USER" (needs a person)
    code: str
    message: str


def classify_failed_request(request: dict) -> FailureClass:
    """How the runner treats a FAILED request (the worker already did its retries)."""
    error = request.get("error_message") or request.get("last_failure_reason") or ""
    lower = error.lower()
    policy = content_policy_code(error)
    if policy:
        return FailureClass("USER", policy.removeprefix("PUBLIC_ERROR_"),
                            "Rejected by the content filter: change the prompt (or the references), then redo.")
    if "public_error_unusual_activity" in lower or "unusual activity" in lower:
        return FailureClass("PAUSE", "UNUSUAL_ACTIVITY",
                            "Google flagged the session. Check the Flow tab, then resume.")
    if "unsupported_on_batch_api" in lower:
        return FailureClass("USER", "UNSUPPORTED_ON_BATCH_API", error[:300])
    if "not found after" in lower and "recoveries" in lower:
        return FailureClass("USER", "MEDIA_NOT_FOUND", error[:300])
    return FailureClass("USER", "FAILED_AFTER_RETRIES", error[:300] or "request failed")


def generations_spent(request: dict | None) -> int:
    """Approximate paid generation calls one request made.

    Never claimed -> 0. Skipped as already completed -> 0. Image/reference requests
    submit on every attempt -> 1 + retry_count. A video request resumes its stored
    operation on retries -> 1 once an operation id exists, else 0.
    """
    if not request or not request.get("started_at"):
        return 0
    if (request.get("error_message") or "").startswith("skipped: already completed"):
        return 0
    if request.get("type") in VIDEO_TYPES:
        return 1 if request.get("request_id") else 0
    return 1 + int(request.get("retry_count") or 0)
