"""Async CRUD operations with column whitelisting."""
import json
import logging
import uuid
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional
from agent.db import schema
from agent.db.schema import get_db, transaction

logger = logging.getLogger(__name__)

_VALID_TABLES = frozenset({"character", "project", "video", "scene", "request", "material"})


def _validate_table(table: str) -> None:
    if table not in _VALID_TABLES:
        raise ValueError(f"Invalid table name: {table!r}")

# Column whitelists per table — prevents SQL injection via kwargs keys
_COLUMNS = {
    "character": {"name", "slug", "entity_type", "description", "image_prompt", "voice_description", "reference_image_url", "media_id", "updated_at"},
    "project": {"name", "description", "story", "thumbnail_url", "language", "status", "user_paygate_tier", "narrator_voice", "narrator_ref_audio", "material", "allow_music", "allow_voice", "updated_at"},
    "video": {"title", "description", "display_order", "status", "orientation", "vertical_url", "horizontal_url",
              "thumbnail_url", "duration", "resolution", "youtube_id", "upload_url", "privacy", "tags",
              "youtube_upload_status", "youtube_upload_key", "youtube_upload_error", "youtube_upload_attempts",
              "youtube_resumable_uri", "youtube_publish_status", "youtube_publish_error", "youtube_uploaded_at", "youtube_published_at", "updated_at"},
    "scene": {"prompt", "image_prompt", "video_prompt", "character_names", "parent_scene_id", "chain_type",
              "vertical_image_url", "vertical_image_media_id", "vertical_image_status",
              "vertical_video_url", "vertical_video_media_id", "vertical_video_status",
              "vertical_upscale_url", "vertical_upscale_media_id", "vertical_upscale_status",
              "horizontal_image_url", "horizontal_image_media_id", "horizontal_image_status",
              "horizontal_video_url", "horizontal_video_media_id", "horizontal_video_status",
              "horizontal_upscale_url", "horizontal_upscale_media_id", "horizontal_upscale_status",
              "vertical_end_scene_media_id", "horizontal_end_scene_media_id",
              "trim_start", "trim_end", "duration", "display_order", "source", "transition_prompt", "narrator_text",
              "narration_audio_path", "narration_audio_duration", "narration_audio_status", "narration_mix_path", "narration_mix_status", "updated_at"},
    "request": {"status", "request_id", "media_id", "output_url", "error_message", "retry_count", "next_retry_at", "started_at", "finished_at", "last_failure_reason", "source_media_id", "updated_at"},
}

REQUEST_TRANSITIONS = {
    "PENDING": frozenset({"PROCESSING"}),
    "PROCESSING": frozenset({"PENDING", "COMPLETED", "FAILED"}),
    "FAILED": frozenset({"PENDING"}),
    "COMPLETED": frozenset(),
}


class InvalidRequestTransition(ValueError):
    """Raised when a request status change is not allowed."""


class RequestTransitionConflict(RuntimeError):
    """Raised when another worker changed the request first."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _uuid() -> str:
    return str(uuid.uuid4())


def _safe_kwargs(table: str, kwargs: dict) -> dict:
    """Filter kwargs to only allowed columns."""
    allowed = _COLUMNS.get(table, set())
    return {k: v for k, v in kwargs.items() if k in allowed}


async def _update(table: str, pk: str, pk_val: str, **kwargs) -> Optional[dict]:
    _validate_table(table)
    kwargs = _safe_kwargs(table, kwargs)
    if not kwargs:
        return await _get(table, pk, pk_val)
    kwargs["updated_at"] = _now()
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [pk_val]
    db = await get_db()
    async with schema._db_lock:
        async with transaction(db):
            await db.execute(f"UPDATE {table} SET {sets} WHERE {pk}=?", vals)
    return await _get_with_db(db, table, pk, pk_val)


async def _get(table: str, pk: str, pk_val: str) -> Optional[dict]:
    _validate_table(table)
    db = await get_db()
    return await _get_with_db(db, table, pk, pk_val)


async def _get_with_db(db, table: str, pk: str, pk_val: str) -> Optional[dict]:
    _validate_table(table)
    cur = await db.execute(f"SELECT * FROM {table} WHERE {pk}=?", (pk_val,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def _delete(table: str, pk: str, pk_val: str) -> bool:
    _validate_table(table)
    db = await get_db()
    async with schema._db_lock:
        async with transaction(db):
            cur = await db.execute(f"DELETE FROM {table} WHERE {pk}=?", (pk_val,))
    return cur.rowcount > 0


# ─── Character ──────────────────────────────────────────────

async def create_character(name: str, entity_type: str = "character", description: str = None, image_prompt: str = None, voice_description: str = None, reference_image_url: str = None, media_id: str = None, slug: str = None) -> dict:
    from agent.utils.slugify import slugify
    db = await get_db()
    cid, now = _uuid(), _now()
    _slug = slug or slugify(name)
    async with schema._db_lock:
        async with transaction(db):
            await db.execute(
                "INSERT INTO character (id,name,slug,entity_type,description,image_prompt,voice_description,reference_image_url,media_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (cid, name, _slug, entity_type, description, image_prompt, voice_description, reference_image_url, media_id, now, now))
    return await _get_with_db(db, "character", "id", cid)

async def get_character(cid: str): return await _get("character", "id", cid)
async def update_character(cid: str, **kw): return await _update("character", "id", cid, **kw)
async def delete_character(cid: str): return await _delete("character", "id", cid)

async def list_characters() -> list[dict]:
    db = await get_db()
    cur = await db.execute("SELECT * FROM character ORDER BY created_at DESC")
    return [dict(r) for r in await cur.fetchall()]


# ─── Project ────────────────────────────────────────────────

async def create_project(name: str, description: str = None, story: str = None, language: str = "en", user_paygate_tier: str = "PAYGATE_TIER_ONE", id: str = None, material: str = None, allow_music: bool = False, allow_voice: bool = False) -> dict:
    db = await get_db()
    pid, now = id or _uuid(), _now()
    async with schema._db_lock:
        async with transaction(db):
            await db.execute(
                "INSERT INTO project (id,name,description,story,language,user_paygate_tier,material,allow_music,allow_voice,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (pid, name, description, story, language, user_paygate_tier, material, int(allow_music), int(allow_voice), now, now))
    return await _get_with_db(db, "project", "id", pid)

async def get_project(pid: str): return await _get("project", "id", pid)
async def update_project(pid: str, **kw): return await _update("project", "id", pid, **kw)
async def delete_project(pid: str): return await _delete("project", "id", pid)

async def list_projects(status: str = None) -> list[dict]:
    db = await get_db()
    if status:
        cur = await db.execute("SELECT * FROM project WHERE status=? ORDER BY created_at DESC", (status,))
    else:
        cur = await db.execute("SELECT * FROM project ORDER BY created_at DESC")
    return [dict(r) for r in await cur.fetchall()]

async def link_character_to_project(project_id: str, character_id: str) -> bool:
    db = await get_db()
    try:
        async with schema._db_lock:
            async with transaction(db):
                await db.execute("INSERT OR IGNORE INTO project_character VALUES (?,?)", (project_id, character_id))
        return True
    except Exception as e:
        logger.warning("link_character_to_project failed: %s", e)
        return False

async def unlink_character_from_project(project_id: str, character_id: str) -> bool:
    db = await get_db()
    async with schema._db_lock:
        async with transaction(db):
            cur = await db.execute("DELETE FROM project_character WHERE project_id=? AND character_id=?", (project_id, character_id))
    return cur.rowcount > 0

async def get_project_characters(project_id: str) -> list[dict]:
    db = await get_db()
    cur = await db.execute(
        "SELECT c.* FROM character c JOIN project_character pc ON c.id=pc.character_id WHERE pc.project_id=?",
        (project_id,))
    return [dict(r) for r in await cur.fetchall()]


# ─── Video ──────────────────────────────────────────────────

async def create_video(project_id: str, title: str, description: str = None, display_order: int = 0, orientation: str = None) -> dict:
    db = await get_db()
    vid, now = _uuid(), _now()
    async with schema._db_lock:
        async with transaction(db):
            await db.execute(
                "INSERT INTO video (id,project_id,title,description,display_order,orientation,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (vid, project_id, title, description, display_order, orientation, now, now))
    return await _get_with_db(db, "video", "id", vid)

async def get_video(vid: str): return await _get("video", "id", vid)
async def update_video(vid: str, **kw): return await _update("video", "id", vid, **kw)
async def delete_video(vid: str): return await _delete("video", "id", vid)

async def list_videos(project_id: str) -> list[dict]:
    db = await get_db()
    cur = await db.execute("SELECT * FROM video WHERE project_id=? ORDER BY display_order", (project_id,))
    return [dict(r) for r in await cur.fetchall()]


# ─── Scene ──────────────────────────────────────────────────

async def create_scene(video_id: str, display_order: int, prompt: str,
                       image_prompt: str = None, video_prompt: str = None,
                       transition_prompt: str = None,
                       character_names: list[str] = None,
                       parent_scene_id: str = None, chain_type: str = "ROOT",
                       source: str = "root") -> dict:
    db = await get_db()
    sid, now = _uuid(), _now()
    chars_json = json.dumps(character_names) if character_names else None
    async with schema._db_lock:
        async with transaction(db):
            await db.execute(
                """INSERT INTO scene (id,video_id,display_order,prompt,image_prompt,video_prompt,transition_prompt,character_names,
                   parent_scene_id,chain_type,source,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (sid, video_id, display_order, prompt, image_prompt, video_prompt, transition_prompt, chars_json,
                 parent_scene_id, chain_type, source, now, now))
    return await _get_with_db(db, "scene", "id", sid)

async def get_scene(sid: str): return await _get("scene", "id", sid)
async def update_scene(sid: str, **kw): return await _update("scene", "id", sid, **kw)
async def delete_scene(sid: str): return await _delete("scene", "id", sid)

async def list_scenes(video_id: str) -> list[dict]:
    db = await get_db()
    cur = await db.execute("SELECT * FROM scene WHERE video_id=? ORDER BY display_order", (video_id,))
    return [dict(r) for r in await cur.fetchall()]


async def list_scenes_by_project(project_id: str) -> list[dict]:
    """Fetch all project scenes in one indexed join."""
    db = await get_db()
    cur = await db.execute(
        """SELECT s.* FROM scene s
           JOIN video v ON v.id = s.video_id
           WHERE v.project_id=?
           ORDER BY v.display_order, s.display_order""",
        (project_id,),
    )
    return [dict(row) for row in await cur.fetchall()]


async def list_scenes_by_media_id(media_id: str) -> list[dict]:
    """Find scenes where any media_id field matches the given UUID."""
    db = await get_db()
    cur = await db.execute(
        """SELECT * FROM scene WHERE
           vertical_image_media_id=? OR horizontal_image_media_id=?
           OR vertical_video_media_id=? OR horizontal_video_media_id=?
           OR vertical_upscale_media_id=? OR horizontal_upscale_media_id=?""",
        (media_id, media_id, media_id, media_id, media_id, media_id))
    return [dict(r) for r in await cur.fetchall()]


async def list_characters_by_media_id(media_id: str) -> list[dict]:
    """Find characters where media_id matches."""
    db = await get_db()
    cur = await db.execute("SELECT * FROM character WHERE media_id=?", (media_id,))
    return [dict(r) for r in await cur.fetchall()]


async def save_character_reference(
    character_id: str,
    media_id: str,
    reference_image_url: str | None = None,
    source_file: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """Persist a new immutable reference version and make it current."""
    if not media_id:
        raise ValueError("Reference asset requires media_id")
    if source_file and not Path(source_file).is_file():
        raise ValueError("Reference source file does not exist")
    db = await get_db()
    asset_id, now = _uuid(), _now()
    metadata_json = json.dumps(metadata or {}, sort_keys=True)
    async with schema._db_lock:
        async with transaction(db):
            cur = await db.execute(
                "SELECT * FROM character_reference_asset WHERE character_id=? AND media_id=?",
                (character_id, media_id),
            )
            existing = await cur.fetchone()
            if existing:
                return dict(existing)
            cur = await db.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM character_reference_asset WHERE character_id=?",
                (character_id,),
            )
            version = (await cur.fetchone())[0]
            await db.execute(
                "UPDATE character_reference_asset SET status='RETIRED', retired_at=? WHERE character_id=? AND status='ACTIVE'",
                (now, character_id),
            )
            await db.execute(
                """INSERT INTO character_reference_asset
                   (id, character_id, version, media_id, reference_image_url, source_file, metadata_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (asset_id, character_id, version, media_id, reference_image_url, source_file, metadata_json, now),
            )
            cur = await db.execute("SELECT * FROM character_reference_asset WHERE id=?", (asset_id,))
            return dict(await cur.fetchone())


async def get_current_character_reference(character_id: str) -> dict | None:
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM character_reference_asset WHERE character_id=? AND status='ACTIVE' ORDER BY version DESC LIMIT 1",
        (character_id,),
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def ensure_character_reference_version(character: dict) -> dict | None:
    """Promote a legacy character.media_id into version 1 on first use."""
    current = await get_current_character_reference(character["id"])
    if current:
        return current
    if not character.get("media_id"):
        return None
    return await save_character_reference(
        character["id"],
        character["media_id"],
        reference_image_url=character.get("reference_image_url"),
        metadata={"source": "legacy_character_pointer"},
    )


async def validate_character_reference(asset: dict) -> bool:
    """Validate persisted reference metadata without deleting the asset."""
    if not asset or asset.get("status") == "INVALID" or not asset.get("media_id"):
        return False
    source_file = asset.get("source_file")
    return not source_file or Path(source_file).is_file()


async def invalidate_character_reference(reference_id: str, reason: str = "asset invalid") -> bool:
    """Mark an asset invalid without deleting historical metadata."""
    db = await get_db()
    async with schema._db_lock:
        async with transaction(db):
            cur = await db.execute(
                "UPDATE character_reference_asset SET status='INVALID', metadata_json=? WHERE id=? AND status <> 'INVALID'",
                (json.dumps({"invalid_reason": reason}), reference_id),
            )
    return cur.rowcount == 1


async def capture_scene_character_references(scene_id: str, character_ids: list[str]) -> list[dict]:
    """Snapshot current reference versions used by a scene generation."""
    db = await get_db()
    captured = []
    assets = []
    for character_id in character_ids:
        cur = await db.execute("SELECT * FROM character WHERE id=?", (character_id,))
        character_row = await cur.fetchone()
        asset = await ensure_character_reference_version(dict(character_row)) if character_row else None
        if not await validate_character_reference(asset):
            raise ValueError(f"Missing or invalid reference asset for character {character_id}")
        assets.append(asset)
    async with schema._db_lock:
        async with transaction(db):
            for character_id, asset in zip(character_ids, assets):
                await db.execute(
                          """INSERT OR IGNORE INTO scene_character_reference
                       (scene_id, character_id, reference_id, version, media_id, reference_image_url, metadata_json)
                              VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (scene_id, character_id, asset["id"], asset["version"], asset["media_id"], asset.get("reference_image_url"), asset.get("metadata_json")),
                )
                captured.append(asset)
    return captured


async def list_scene_character_references(scene_id: str) -> list[dict]:
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM scene_character_reference WHERE scene_id=? ORDER BY character_id",
        (scene_id,),
    )
    return [dict(row) for row in await cur.fetchall()]


async def list_scenes_for_character(character_id: str) -> list[dict]:
    """Return scenes in projects linked to a reference character."""
    db = await get_db()
    cur = await db.execute(
        """SELECT s.* FROM scene s
           JOIN video v ON v.id = s.video_id
                     JOIN project_character pc ON pc.project_id = v.project_id
                     JOIN character c ON c.id = pc.character_id
                     WHERE pc.character_id=?
                         AND EXISTS (
                                 SELECT 1 FROM json_each(COALESCE(s.character_names, '[]'))
                                 WHERE value IN (c.name, c.slug)
                         )""",
        (character_id,),
    )
    return [dict(row) for row in await cur.fetchall()]


# ─── Request ────────────────────────────────────────────────

async def create_request(req_type: str, orientation: str = None,
                         scene_id: str = None, character_id: str = None,
                         project_id: str = None, video_id: str = None,
                         source_media_id: str = None, **_kw) -> dict:
    db = await get_db()
    rid, now = _uuid(), _now()
    async with schema._db_lock:
        async with transaction(db):
            await db.execute(
                """INSERT INTO request (id,project_id,video_id,scene_id,character_id,type,orientation,source_media_id,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (rid, project_id, video_id, scene_id, character_id, req_type, orientation, source_media_id, now, now))
    return await _get_with_db(db, "request", "id", rid)

async def get_request(rid: str): return await _get("request", "id", rid)


async def transition_request(rid: str, status: str, *, expected_status: str = None, expected_started_at: str = None, **updates) -> dict:
    """Atomically move a request through the persisted state machine."""
    if status not in REQUEST_TRANSITIONS:
        raise InvalidRequestTransition(f"Unknown request status: {status}")
    db = await get_db()
    now = _now()
    async with schema._db_lock:
        async with transaction(db):
            cur = await db.execute("SELECT status FROM request WHERE id=?", (rid,))
            row = await cur.fetchone()
            if row is None:
                return None
            current = row[0]
            if expected_status and current != expected_status:
                raise RequestTransitionConflict(f"Request {rid} is {current}, expected {expected_status}")
            if expected_started_at and row[0] == "PROCESSING":
                cur = await db.execute("SELECT started_at FROM request WHERE id=?", (rid,))
                started_row = await cur.fetchone()
                if started_row[0] != expected_started_at:
                    raise RequestTransitionConflict(f"Request {rid} lease is no longer current")
            if status != current and status not in REQUEST_TRANSITIONS[current]:
                raise InvalidRequestTransition(f"Cannot transition request {rid} from {current} to {status}")

            values = dict(updates)
            values["status"] = status
            values["updated_at"] = now
            if status == "PROCESSING":
                values["started_at"] = now
                values["finished_at"] = None
            elif status == "PENDING":
                values["finished_at"] = None
            elif status in ("COMPLETED", "FAILED"):
                values["finished_at"] = now
            sets = ", ".join(f"{key}=?" for key in values)
            params = list(values.values()) + [rid]
            status_clause = ""
            if expected_status:
                status_clause = " AND status=?"
                params.append(expected_status)
            if expected_started_at:
                status_clause += " AND started_at=?"
                params.append(expected_started_at)
            cur = await db.execute(f"UPDATE request SET {sets} WHERE id=?{status_clause}", params)
            if cur.rowcount != 1:
                raise RequestTransitionConflict(f"Request {rid} changed during transition")
    return await get_request(rid)


async def update_request(rid: str, **kw):
    status = kw.pop("status", None)
    if status is not None:
        expected_started_at = kw.pop("_expected_started_at", None)
        return await transition_request(rid, status, expected_started_at=expected_started_at, **kw)
    return await _update("request", "id", rid, **kw)

async def list_requests(scene_id: str = None, status: str = None,
                        video_id: str = None, project_id: str = None) -> list[dict]:
    db = await get_db()
    q, params = "SELECT * FROM request WHERE 1=1", []
    if scene_id:
        q += " AND scene_id=?"; params.append(scene_id)
    if status:
        q += " AND status=?"; params.append(status)
    if video_id:
        q += " AND video_id=?"; params.append(video_id)
    if project_id:
        q += " AND project_id=?"; params.append(project_id)
    q += " ORDER BY created_at DESC"
    cur = await db.execute(q, params)
    return [dict(r) for r in await cur.fetchall()]

async def list_pending_requests() -> list[dict]:
    db = await get_db()
    cur = await db.execute("SELECT * FROM request WHERE status='PENDING' ORDER BY created_at")
    return [dict(r) for r in await cur.fetchall()]


async def list_actionable_requests(exclude_ids: set[str] = None, limit: int = 5) -> list[dict]:
    """Priority-ordered fetch of PENDING requests ready to process."""
    db = await get_db()
    now = _now()
    exclude = exclude_ids or set()

    # Fetch all pending, filter in Python (SQLite doesn't support parameterized IN with variable length)
    cur = await db.execute("""
        SELECT * FROM request
        WHERE status = 'PENDING'
          AND (next_retry_at IS NULL OR next_retry_at <= ?)
        ORDER BY
          CASE type
            WHEN 'GENERATE_CHARACTER_IMAGE' THEN 0
            WHEN 'REGENERATE_CHARACTER_IMAGE' THEN 0
            WHEN 'EDIT_CHARACTER_IMAGE' THEN 0
            WHEN 'GENERATE_IMAGE' THEN 1
            WHEN 'REGENERATE_IMAGE' THEN 1
            WHEN 'EDIT_IMAGE' THEN 1
            WHEN 'GENERATE_VIDEO' THEN 2
            WHEN 'GENERATE_VIDEO_REFS' THEN 2
            WHEN 'UPSCALE_VIDEO' THEN 3
            ELSE 2
          END,
          created_at ASC
    """, (now,))
    rows = [dict(r) for r in await cur.fetchall()]
    # Exclude in-flight IDs
    filtered = [r for r in rows if r["id"] not in exclude]
    return filtered[:limit]


async def claim_actionable_requests(exclude_ids: set[str] = None, limit: int = 5) -> list[dict]:
    """Claim pending requests atomically for safe multi-worker processing."""
    db = await get_db()
    exclude = exclude_ids or set()
    now = _now()
    async with schema._db_lock:
        async with transaction(db):
            cur = await db.execute("""
                SELECT id FROM request
                WHERE status='PENDING' AND (next_retry_at IS NULL OR next_retry_at <= ?)
                ORDER BY created_at ASC LIMIT ?
            """, (now, limit + len(exclude)))
            ids = [row[0] for row in await cur.fetchall() if row[0] not in exclude][:limit]
            if not ids:
                return []
            placeholders = ",".join("?" for _ in ids)
            await db.execute(
                f"UPDATE request SET status='PROCESSING', started_at=?, finished_at=NULL, updated_at=? WHERE status='PENDING' AND id IN ({placeholders})",
                [now, now, *ids],
            )
            cur = await db.execute(f"SELECT * FROM request WHERE id IN ({placeholders})", ids)
            rows = [dict(row) for row in await cur.fetchall()]
            return sorted(rows, key=lambda row: ids.index(row["id"]))


async def reset_stale_processing(cutoff_minutes: int = 10) -> int:
    """Reset PROCESSING requests older than cutoff back to PENDING."""
    db = await get_db()
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=cutoff_minutes)).strftime('%Y-%m-%dT%H:%M:%SZ')
    async with schema._db_lock:
        async with transaction(db):
            cursor = await db.execute(
                "UPDATE request SET status='PENDING', finished_at=NULL, error_message='reset: stale processing', last_failure_reason='stale processing recovery', updated_at=? WHERE status='PROCESSING' AND updated_at < ?",
                (_now(), cutoff))
        return cursor.rowcount


# ─── Material ────────────────────────────────────────────────

async def create_material(id: str, name: str, style_instruction: str,
                          negative_prompt: str = None, scene_prefix: str = None,
                          lighting: str = None) -> dict:
    db = await get_db()
    now = _now()
    async with schema._db_lock:
        async with transaction(db):
            await db.execute(
                """INSERT INTO material (id,name,style_instruction,negative_prompt,scene_prefix,lighting,created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (id, name, style_instruction, negative_prompt, scene_prefix,
                 lighting or "Studio lighting, highly detailed", now))
    return await _get_with_db(db, "material", "id", id)

async def get_material(mid: str): return await _get("material", "id", mid)
async def delete_material(mid: str): return await _delete("material", "id", mid)
async def list_materials() -> list[dict]:
    db = await get_db()
    cur = await db.execute("SELECT * FROM material ORDER BY created_at")
    return [dict(r) for r in await cur.fetchall()]


# ─── AI generation (validated provider output) ──────────────

class AIGenerationConflict(RuntimeError):
    """The generation was already applied, or does not match the target."""


def _ai_generation_row(row) -> dict:
    data = dict(row)
    data["output"] = json.loads(data.pop("output_json"))
    data["input"] = json.loads(data.pop("input_json"))
    return data


async def create_ai_generation(*, operation: str, provider: str, model: str, project_id: str,
                               request_id: str, attempts: int, input_data: dict, output_data: dict,
                               video_id: str = None) -> dict:
    db = await get_db()
    gid, now = _uuid(), _now()
    async with schema._db_lock:
        async with transaction(db):
            await db.execute(
                """INSERT INTO ai_generation (id,operation,status,provider,model,project_id,video_id,request_id,
                   attempts,input_json,output_json,created_at,updated_at) VALUES (?,?,'GENERATED',?,?,?,?,?,?,?,?,?,?)""",
                (gid, operation, provider, model, project_id, video_id, request_id, attempts,
                 json.dumps(input_data), json.dumps(output_data), now, now))
    return await get_ai_generation(gid)


async def get_ai_generation(gid: str) -> Optional[dict]:
    db = await get_db()
    cur = await db.execute("SELECT * FROM ai_generation WHERE id=?", (gid,))
    row = await cur.fetchone()
    return _ai_generation_row(row) if row else None


async def list_ai_generations(project_id: str) -> list[dict]:
    db = await get_db()
    cur = await db.execute("SELECT * FROM ai_generation WHERE project_id=? ORDER BY created_at DESC", (project_id,))
    return [_ai_generation_row(r) for r in await cur.fetchall()]


async def _claim_generation(db, gid: str, operation: str, video_id: str, now: str) -> dict:
    cur = await db.execute("SELECT operation, status, project_id FROM ai_generation WHERE id=?", (gid,))
    gen = await cur.fetchone()
    if gen is None:
        raise LookupError("AI generation not found")
    if gen[0] != operation:
        raise AIGenerationConflict(f"generation is {gen[0]}, not {operation}")
    if gen[1] != "GENERATED":
        raise AIGenerationConflict("generation was already applied")
    cur = await db.execute("SELECT 1 FROM video WHERE id=? AND project_id=?", (video_id, gen[2]))
    if await cur.fetchone() is None:
        raise AIGenerationConflict("video does not belong to the generation's project")
    cur = await db.execute(
        "UPDATE ai_generation SET status='APPLIED', video_id=?, applied_at=?, updated_at=? WHERE id=? AND status='GENERATED'",
        (video_id, now, now, gid))
    if cur.rowcount != 1:
        raise AIGenerationConflict("generation was applied concurrently")
    return {"project_id": gen[2]}


async def apply_story_plan(gid: str, video_id: str, *, entities: list[dict], scenes: list[dict],
                           story: str | None = None, chain_scenes: bool = False) -> dict:
    """Write a validated story plan into the project/video in ONE transaction.

    entities: {name, slug, entity_type, description, image_prompt, voice_description}
    scenes:   {prompt, video_prompt, narrator_text, character_names, continues_previous}
    chain_scenes: honour continues_previous (CONTINUATION + parent). Otherwise every
              scene is ROOT, which renders on every video model.
    Entities already linked to the project (same slug or case-insensitive name)
    are reused. New ones get a globally unique slug. Scenes are appended after the
    video's last scene. Nothing is written unless everything succeeds.
    """
    from agent.utils.slugify import slugify
    db = await get_db()
    now = _now()
    created, reused, scene_ids = [], [], []
    async with schema._db_lock:
        async with transaction(db):
            claim = await _claim_generation(db, gid, "STORY_PLAN", video_id, now)
            project_id = claim["project_id"]

            cur = await db.execute(
                "SELECT c.id, c.name, c.slug FROM character c JOIN project_character pc ON pc.character_id=c.id WHERE pc.project_id=?",
                (project_id,))
            linked = [dict(r) for r in await cur.fetchall()]
            by_key = {}
            for row in linked:
                if row["slug"]:
                    by_key[row["slug"]] = row
                by_key[row["name"].lower()] = row

            name_map: dict[str, str] = {}
            for ent in entities:
                slug = ent.get("slug") or slugify(ent["name"])
                existing = by_key.get(slug) or by_key.get(ent["name"].lower())
                if existing:
                    reused.append(existing["id"])
                    name_map[ent["name"]] = existing["name"]
                    continue
                candidate, n = slug, 2
                while True:
                    cur = await db.execute("SELECT 1 FROM character WHERE slug=?", (candidate,))
                    if await cur.fetchone() is None:
                        break
                    candidate, n = f"{slug}_{n}", n + 1
                cid = _uuid()
                await db.execute(
                    """INSERT INTO character (id,name,slug,entity_type,description,image_prompt,voice_description,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (cid, ent["name"], candidate, ent["entity_type"], ent.get("description"),
                     ent.get("image_prompt"), ent.get("voice_description"), now, now))
                await db.execute("INSERT INTO project_character (project_id, character_id) VALUES (?,?)", (project_id, cid))
                created.append(cid)
                name_map[ent["name"]] = ent["name"]

            cur = await db.execute("SELECT COALESCE(MAX(display_order), -1) FROM scene WHERE video_id=?", (video_id,))
            order = (await cur.fetchone())[0] + 1
            previous = None
            for sc in scenes:
                sid = _uuid()
                parent = previous if chain_scenes and sc.get("continues_previous") and previous else None
                names = [name_map.get(n, n) for n in sc.get("character_names") or []]
                await db.execute(
                    """INSERT INTO scene (id,video_id,display_order,prompt,video_prompt,narrator_text,character_names,
                       parent_scene_id,chain_type,source,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,'root',?,?)""",
                    (sid, video_id, order, sc["prompt"], sc.get("video_prompt"), sc.get("narrator_text"),
                     json.dumps(names) if names else None, parent,
                     "CONTINUATION" if parent else "ROOT", now, now))
                scene_ids.append(sid)
                previous, order = sid, order + 1

            if story:
                await db.execute(
                    "UPDATE project SET story=?, updated_at=? WHERE id=? AND (story IS NULL OR story='')",
                    (story, now, project_id))
    return {"characters_created": created, "characters_reused": reused, "scenes_created": scene_ids}


async def apply_youtube_metadata(gid: str, video_id: str, *, title: str, description: str, tags: list[str]) -> dict:
    """Write validated YouTube metadata onto the video in ONE transaction."""
    db = await get_db()
    now = _now()
    async with schema._db_lock:
        async with transaction(db):
            await _claim_generation(db, gid, "YOUTUBE_METADATA", video_id, now)
            await db.execute(
                "UPDATE video SET title=?, description=?, tags=?, updated_at=? WHERE id=?",
                (title, description, json.dumps(tags), now, video_id))
    return {"video_updated": True}
