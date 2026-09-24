"""Request state-machine and worker recovery tests."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from agent.db import crud, schema


@pytest.fixture
async def lifecycle_db(tmp_path, monkeypatch):
    await schema.close_db()
    monkeypatch.setattr(schema, "DB_PATH", tmp_path / "lifecycle.db")
    await schema.init_db()
    yield
    await schema.close_db()


async def _make_request():
    project = await crud.create_project(name="Lifecycle project")
    video = await crud.create_video(project_id=project["id"], title="Lifecycle video")
    scene = await crud.create_scene(video_id=video["id"], display_order=0, prompt="Lifecycle scene")
    request = await crud.create_request(
        req_type="GENERATE_IMAGE",
        project_id=project["id"],
        video_id=video["id"],
        scene_id=scene["id"],
    )
    return request


@pytest.mark.asyncio
async def test_valid_transitions_record_timestamps(lifecycle_db):
    request = await _make_request()
    running = await crud.transition_request(request["id"], "PROCESSING", expected_status="PENDING")
    assert running["status"] == "PROCESSING"
    assert running["started_at"]
    assert running["finished_at"] is None

    completed = await crud.transition_request(
        request["id"], "COMPLETED", expected_status="PROCESSING",
        expected_started_at=running["started_at"], media_id="media-1",
    )
    assert completed["status"] == "COMPLETED"
    assert completed["finished_at"]


@pytest.mark.asyncio
async def test_invalid_transition_is_rejected(lifecycle_db):
    request = await _make_request()
    with pytest.raises(crud.InvalidRequestTransition):
        await crud.transition_request(request["id"], "COMPLETED")

    await crud.transition_request(request["id"], "PROCESSING")
    await crud.transition_request(request["id"], "COMPLETED")
    with pytest.raises(crud.InvalidRequestTransition):
        await crud.transition_request(request["id"], "PENDING")


@pytest.mark.asyncio
async def test_retry_preserves_failure_reason_and_is_restart_safe(lifecycle_db):
    request = await _make_request()
    running = await crud.transition_request(request["id"], "PROCESSING")
    retried = await crud.transition_request(
        request["id"], "PENDING", expected_status="PROCESSING",
        expected_started_at=running["started_at"], retry_count=1,
        error_message="temporary provider failure",
        last_failure_reason="temporary provider failure",
        next_retry_at="2999-01-01T00:00:00Z",
    )
    assert retried["retry_count"] == 1
    assert retried["last_failure_reason"] == "temporary provider failure"
    assert retried["next_retry_at"] == "2999-01-01T00:00:00Z"

    assert await crud.claim_actionable_requests(limit=1) == []


@pytest.mark.asyncio
async def test_duplicate_execution_is_prevented_by_atomic_claim(lifecycle_db):
    request = await _make_request()
    first, second = await asyncio.gather(
        crud.claim_actionable_requests(limit=1),
        crud.claim_actionable_requests(limit=1),
    )
    claimed = [row for batch in (first, second) for row in batch]
    assert [row["id"] for row in claimed] == [request["id"]]


@pytest.mark.asyncio
async def test_stale_processing_recovery_reclaims_job(lifecycle_db):
    request = await _make_request()
    old_claim = await crud.claim_actionable_requests(limit=1)
    old_started_at = old_claim[0]["started_at"]
    old_time = (datetime.now(timezone.utc) - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    db = await schema.get_db()
    await db.execute("UPDATE request SET started_at=?, updated_at=? WHERE id=?", (old_time, old_time, request["id"]))
    await db.commit()

    assert await crud.reset_stale_processing(cutoff_minutes=10) == 1
    recovered = await crud.get_request(request["id"])
    assert recovered["status"] == "PENDING"
    assert recovered["last_failure_reason"] == "stale processing recovery"

    new_claim = (await crud.claim_actionable_requests(limit=1))[0]
    assert new_claim["started_at"] != old_started_at
    with pytest.raises(crud.RequestTransitionConflict):
        await crud.transition_request(
            request["id"], "COMPLETED", expected_status="PROCESSING",
            expected_started_at=old_started_at,
        )


@pytest.mark.asyncio
async def test_failure_recovery_can_be_retried(lifecycle_db):
    request = await _make_request()
    running = await crud.transition_request(request["id"], "PROCESSING")
    failed = await crud.transition_request(
        request["id"], "FAILED", expected_status="PROCESSING",
        expected_started_at=running["started_at"],
        error_message="permanent provider failure",
        last_failure_reason="permanent provider failure",
    )
    assert failed["finished_at"]
    pending = await crud.transition_request(
        request["id"], "PENDING", expected_status="FAILED",
        error_message=None, next_retry_at=None,
    )
    assert pending["status"] == "PENDING"
    assert pending["finished_at"] is None
