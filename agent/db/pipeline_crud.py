"""Persistence for pipeline runs (pipeline_run, pipeline_run_item).

Everything the runner knows lives here, so a restart resumes from the database:
an item that has a request_id is only ever watched, never submitted again, and
submitting (queue insert + item update) happens in one transaction.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Callable, Optional

from agent.db import schema
from agent.db.crud import _enqueue_one_in_tx, _now, _uuid
from agent.db.schema import get_db, transaction

RUN_STATUSES = ("DRAFT", "RUNNING", "AWAITING_APPROVAL", "PAUSED", "NEEDS_USER_ACTION",
                "COMPLETED", "CANCELLED", "FAILED")
ACTIVE_RUN_STATUSES = ("DRAFT", "RUNNING", "AWAITING_APPROVAL", "PAUSED", "NEEDS_USER_ACTION")
TERMINAL_RUN_STATUSES = ("COMPLETED", "CANCELLED", "FAILED")

RUN_TRANSITIONS = {
    "DRAFT": {"RUNNING", "CANCELLED", "FAILED"},
    "RUNNING": {"AWAITING_APPROVAL", "PAUSED", "NEEDS_USER_ACTION", "COMPLETED", "CANCELLED", "FAILED"},
    "AWAITING_APPROVAL": {"RUNNING", "CANCELLED", "FAILED"},
    "PAUSED": {"RUNNING", "NEEDS_USER_ACTION", "CANCELLED", "FAILED"},
    "NEEDS_USER_ACTION": {"RUNNING", "PAUSED", "CANCELLED", "FAILED"},
    "COMPLETED": set(),
    "CANCELLED": set(),
    "FAILED": set(),
}

#: A held request stays PENDING (scene statuses untouched) but the worker's claim
#: query (`next_retry_at <= now`) never reaches it until the hold is released.
HOLD_UNTIL = "9999-12-31T23:59:59Z"

_RUN_JSON = {"checkpoints": "checkpoints_json", "options": "options_json",
             "estimate": "estimate_json", "warnings": "warnings_json"}
_RUN_COLUMNS = {"stage", "wave", "pause_reason", "status_detail", "disconnected_since", "final_path",
                "error", "orientation", *_RUN_JSON.values()}
_ITEM_COLUMNS = {"wave", "request_type", "request_id", "status", "error_code", "error_message", "local_path",
                 "download_status", "download_attempts", "redo_count", "request_history_json"}


class PipelineConflict(RuntimeError):
    """The operation does not fit the run's current state."""


class InvalidRunTransition(PipelineConflict):
    pass


def _run_row(row) -> dict:
    data = dict(row)
    for key, column in _RUN_JSON.items():
        data[key] = json.loads(data.pop(column) or ("[]" if key in ("checkpoints", "warnings") else "{}"))
    return data


def _item_row(row) -> dict:
    data = dict(row)
    data["request_history"] = json.loads(data.pop("request_history_json") or "[]")
    return data


def _encode_run_fields(fields: dict) -> dict:
    out = {}
    for key, value in fields.items():
        if key in _RUN_JSON:
            out[_RUN_JSON[key]] = json.dumps(value, ensure_ascii=False)
        elif key in _RUN_COLUMNS:
            out[key] = value
        else:
            raise ValueError(f"unknown pipeline_run field: {key}")
    return out


# ─── runs ───────────────────────────────────────────────────

async def create_run(*, project_id: str, video_id: str, orientation: str, checkpoints: list[str],
                     options: dict, estimate: dict, warnings: list[str]) -> dict:
    db = await get_db()
    rid, now = _uuid(), _now()
    try:
        async with schema._db_lock:
            async with transaction(db):
                await db.execute(
                    """INSERT INTO pipeline_run (id,project_id,video_id,orientation,status,checkpoints_json,options_json,
                       estimate_json,warnings_json,created_at,updated_at) VALUES (?,?,?,?, 'DRAFT', ?,?,?,?,?,?)""",
                    (rid, project_id, video_id, orientation, json.dumps(checkpoints), json.dumps(options, ensure_ascii=False),
                     json.dumps(estimate, ensure_ascii=False), json.dumps(warnings, ensure_ascii=False), now, now))
    except sqlite3.IntegrityError as exc:
        if "uq_pipeline_run_active_video" in str(exc) or "pipeline_run.video_id" in str(exc):
            raise PipelineConflict("this video already has an unfinished pipeline run") from exc
        raise
    return await get_run(rid)


async def get_run(run_id: str) -> Optional[dict]:
    db = await get_db()
    cur = await db.execute("SELECT * FROM pipeline_run WHERE id=?", (run_id,))
    row = await cur.fetchone()
    return _run_row(row) if row else None


async def list_runs(*, video_id: str | None = None, project_id: str | None = None,
                    statuses: tuple[str, ...] | None = None) -> list[dict]:
    db = await get_db()
    q, params = "SELECT * FROM pipeline_run WHERE 1=1", []
    if video_id:
        q += " AND video_id=?"; params.append(video_id)
    if project_id:
        q += " AND project_id=?"; params.append(project_id)
    if statuses:
        q += f" AND status IN ({','.join('?' for _ in statuses)})"; params.extend(statuses)
    cur = await db.execute(q + " ORDER BY created_at DESC", params)
    return [_run_row(r) for r in await cur.fetchall()]


async def transition_run(run_id: str, status: str, *, expected: tuple[str, ...] | None = None, **fields) -> dict:
    """Move a run through RUN_TRANSITIONS atomically; other fields are written in the same update."""
    db = await get_db()
    now = _now()
    values = _encode_run_fields(fields)
    async with schema._db_lock:
        async with transaction(db):
            cur = await db.execute("SELECT status, started_at FROM pipeline_run WHERE id=?", (run_id,))
            row = await cur.fetchone()
            if row is None:
                raise LookupError("pipeline run not found")
            current, started_at = row[0], row[1]
            if expected and current not in expected:
                raise PipelineConflict(f"run is {current}, expected {'/'.join(expected)}")
            if status != current and status not in RUN_TRANSITIONS[current]:
                raise InvalidRunTransition(f"cannot move a {current} run to {status}")
            values.update(status=status, updated_at=now)
            if status == "RUNNING" and not started_at:
                values["started_at"] = now
            if status in TERMINAL_RUN_STATUSES:
                values["finished_at"] = now
            sets = ", ".join(f"{k}=?" for k in values)
            await db.execute(f"UPDATE pipeline_run SET {sets} WHERE id=?", [*values.values(), run_id])
    return await get_run(run_id)


async def update_run(run_id: str, **fields) -> dict:
    """Write non-status fields (stage, wave, estimate, warnings, disconnected_since...)."""
    values = _encode_run_fields(fields)
    values["updated_at"] = _now()
    db = await get_db()
    async with schema._db_lock:
        async with transaction(db):
            sets = ", ".join(f"{k}=?" for k in values)
            await db.execute(f"UPDATE pipeline_run SET {sets} WHERE id=?", [*values.values(), run_id])
    return await get_run(run_id)


# ─── items ──────────────────────────────────────────────────

async def add_items(run_id: str, items: list[dict]) -> int:
    """Plan items {stage, target_type, target_id, wave, status?}; an existing (run, stage, target) is kept."""
    db = await get_db()
    now = _now()
    added = 0
    async with schema._db_lock:
        async with transaction(db):
            for it in items:
                cur = await db.execute(
                    """INSERT OR IGNORE INTO pipeline_run_item (id,run_id,stage,target_type,target_id,wave,status,
                       created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)""",
                    (_uuid(), run_id, it["stage"], it["target_type"], it["target_id"], it.get("wave", 0),
                     it.get("status", "PLANNED"), now, now))
                added += cur.rowcount
    return added


async def list_items(run_id: str, stage: str | None = None) -> list[dict]:
    db = await get_db()
    q, params = "SELECT * FROM pipeline_run_item WHERE run_id=?", [run_id]
    if stage:
        q += " AND stage=?"; params.append(stage)
    cur = await db.execute(q + " ORDER BY stage, wave, created_at", params)
    return [_item_row(r) for r in await cur.fetchall()]


async def get_item(item_id: str) -> Optional[dict]:
    db = await get_db()
    cur = await db.execute("SELECT * FROM pipeline_run_item WHERE id=?", (item_id,))
    row = await cur.fetchone()
    return _item_row(row) if row else None


async def update_item(item_id: str, **fields) -> dict:
    unknown = set(fields) - _ITEM_COLUMNS
    if unknown:
        raise ValueError(f"unknown pipeline_run_item fields: {sorted(unknown)}")
    fields["updated_at"] = _now()
    db = await get_db()
    async with schema._db_lock:
        async with transaction(db):
            sets = ", ".join(f"{k}=?" for k in fields)
            await db.execute(f"UPDATE pipeline_run_item SET {sets} WHERE id=?", [*fields.values(), item_id])
    return await get_item(item_id)


async def submit_items(run_id: str, item_ids: list[str], build: Callable[[dict], dict], *,
                       reason: str = "submit", redo: bool = False) -> list[dict]:
    """Queue a request for each item and record it, all in ONE transaction.

    build(item) returns the enqueue dict ({req_type, scene_id|character_id, project_id,
    video_id, orientation}). An active request for the same target + type is adopted
    instead of duplicated. The previous request (if any) moves into request_history;
    redo=True also increments redo_count. Items whose run is not RUNNING are refused.
    """
    db = await get_db()
    now = _now()
    out = []
    async with schema._db_lock:
        async with transaction(db):
            cur = await db.execute("SELECT status FROM pipeline_run WHERE id=?", (run_id,))
            row = await cur.fetchone()
            if row is None:
                raise LookupError("pipeline run not found")
            if row[0] != "RUNNING":
                raise PipelineConflict(f"run is {row[0]}; requests are only submitted while RUNNING")
            for item_id in item_ids:
                cur = await db.execute("SELECT * FROM pipeline_run_item WHERE id=? AND run_id=?", (item_id, run_id))
                item_row = await cur.fetchone()
                if item_row is None:
                    raise LookupError(f"pipeline item {item_id} not found in run")
                item = _item_row(item_row)
                request_spec = build(item)
                request, created = await _enqueue_one_in_tx(db, request_spec, now)
                history = item["request_history"]
                history.append({"request_id": request["id"], "request_type": request_spec["req_type"],
                                "reason": reason, "adopted_existing": not created, "at": now})
                await db.execute(
                    """UPDATE pipeline_run_item SET request_id=?, request_type=?, status='SUBMITTED', error_code=NULL,
                       error_message=NULL, redo_count=redo_count+?, request_history_json=?, updated_at=? WHERE id=?""",
                    (request["id"], request_spec["req_type"], 1 if redo else 0,
                     json.dumps(history, ensure_ascii=False), now, item_id))
                out.append(request)
    return out


# ─── holding the run's queued requests (pause / cancel) ─────

_RUN_REQUESTS = "SELECT request_id FROM pipeline_run_item WHERE run_id=? AND request_id IS NOT NULL"


async def hold_requests(run_id: str, reason: str) -> int:
    """Keep the run's PENDING requests PENDING but unclaimable. Scene statuses are untouched."""
    db = await get_db()
    now = _now()
    async with schema._db_lock:
        async with transaction(db):
            cur = await db.execute(
                f"""UPDATE request SET next_retry_at=?, last_failure_reason=?, updated_at=?
                    WHERE status='PENDING' AND id IN ({_RUN_REQUESTS})""",
                (HOLD_UNTIL, f"held by pipeline run {run_id}: {reason}", now, run_id))
            return cur.rowcount


async def release_requests(run_id: str) -> int:
    """Undo hold_requests: the worker may claim them again."""
    db = await get_db()
    now = _now()
    async with schema._db_lock:
        async with transaction(db):
            cur = await db.execute(
                f"""UPDATE request SET next_retry_at=NULL, updated_at=?
                    WHERE status='PENDING' AND next_retry_at=? AND id IN ({_RUN_REQUESTS})""",
                (now, HOLD_UNTIL, run_id))
            return cur.rowcount


#: A held request's next_retry_at; anything this far out was held by a run.
HOLD_THRESHOLD = "9000-01-01"
_HELD_BY = "held by pipeline run "


def _never_reached_flow(row) -> bool:
    return row["started_at"] is None and not row["retry_count"] and not row["flow_op"]


async def sweep_orphaned_holds() -> dict:
    """Free held requests whose run is gone or finished (CANCELLED/COMPLETED/FAILED/deleted).

    A hold is only lifted by its run (resume/cancel). If the run ended without that -
    a crash between hold and cancel, a deleted run - the request would stay PENDING and
    unclaimable forever, and the deduplicating enqueue would keep handing it back to a
    manual /fk-gen-images. Never-started ones are deleted; the rest are released.
    """
    db = await get_db()
    now = _now()
    deleted, released = [], []
    async with schema._db_lock:
        async with transaction(db):
            cur = await db.execute(
                """SELECT r.id, r.started_at, r.retry_count, r.request_id AS flow_op, r.last_failure_reason,
                          (SELECT COUNT(*) FROM pipeline_run_item i JOIN pipeline_run pr ON pr.id = i.run_id
                            WHERE i.request_id = r.id AND pr.status IN ({})) AS active_owners
                   FROM request r WHERE r.status='PENDING' AND r.next_retry_at >= ?""".format(
                    ",".join("?" for _ in ACTIVE_RUN_STATUSES)),
                (*ACTIVE_RUN_STATUSES, HOLD_THRESHOLD))
            for row in await cur.fetchall():
                if row["active_owners"]:
                    continue
                reason = row["last_failure_reason"] or ""
                if reason.startswith(_HELD_BY):  # the run named in the hold may still be active
                    owner_id = reason[len(_HELD_BY):].split(":", 1)[0]
                    owner = await db.execute("SELECT status FROM pipeline_run WHERE id=?", (owner_id,))
                    status = await owner.fetchone()
                    if status is not None and status[0] in ACTIVE_RUN_STATUSES:
                        continue
                items = await db.execute(
                    "SELECT id, request_history_json FROM pipeline_run_item WHERE request_id=?", (row["id"],))
                for item in await items.fetchall():
                    history = json.loads(item["request_history_json"] or "[]")
                    history.append({"request_id": row["id"], "at": now, "reason":
                                    "orphaned hold removed" if _never_reached_flow(row) else "orphaned hold released"})
                    await db.execute("UPDATE pipeline_run_item SET request_history_json=?, updated_at=? WHERE id=?",
                                     (json.dumps(history, ensure_ascii=False), now, item["id"]))
                if _never_reached_flow(row):
                    await db.execute("DELETE FROM request WHERE id=?", (row["id"],))
                    deleted.append(row["id"])
                else:
                    await db.execute(
                        "UPDATE request SET next_retry_at=NULL, last_failure_reason=?, updated_at=? WHERE id=?",
                        (f"hold released: owning pipeline run ended ({reason[:200]})", now, row["id"]))
                    released.append(row["id"])
    return {"deleted": deleted, "released": released}


async def cancel_unstarted_requests(run_id: str) -> dict:
    """For a cancelled run: delete queued requests that never reached Flow, release the rest.

    "Never reached Flow" = PENDING, never claimed (started_at NULL), no retries and no
    stored Flow operation. Deleting them leaves no FAILED rows and no scene status
    change; each one is recorded in its item's request_history. A request that already
    reached Flow is released to finish (its result is kept), because a running Flow job
    cannot be recalled.
    """
    db = await get_db()
    now = _now()
    deleted = released = 0
    async with schema._db_lock:
        async with transaction(db):
            cur = await db.execute(
                """SELECT i.id AS item_id, i.request_history_json, r.id AS rid, r.status, r.started_at,
                          r.retry_count, r.request_id AS flow_op
                   FROM pipeline_run_item i JOIN request r ON r.id = i.request_id
                   WHERE i.run_id=?""", (run_id,))
            for row in await cur.fetchall():
                unstarted = (row["status"] == "PENDING" and row["started_at"] is None
                             and not row["retry_count"] and not row["flow_op"])
                if unstarted:
                    history = json.loads(row["request_history_json"] or "[]")
                    history.append({"request_id": row["rid"], "reason": "cancelled before reaching Flow (deleted)",
                                    "at": now})
                    await db.execute(
                        "UPDATE pipeline_run_item SET request_id=NULL, status='CANCELLED', request_history_json=?, "
                        "updated_at=? WHERE id=?", (json.dumps(history, ensure_ascii=False), now, row["item_id"]))
                    await db.execute("DELETE FROM request WHERE id=?", (row["rid"],))
                    deleted += 1
                elif row["status"] == "PENDING":
                    await db.execute(
                        "UPDATE request SET next_retry_at=NULL, updated_at=? WHERE id=? AND next_retry_at=?",
                        (now, row["rid"], HOLD_UNTIL))
                    released += 1
            await db.execute(
                "UPDATE pipeline_run_item SET status='CANCELLED', updated_at=? "
                "WHERE run_id=? AND status IN ('PLANNED')", (now, run_id))
    return {"deleted": deleted, "released_to_finish": released}
