"""Database integrity and transaction tests using an isolated SQLite file."""

import asyncio
import sqlite3

import aiosqlite
import pytest

from agent.db import crud, schema


@pytest.fixture
async def isolated_db(tmp_path, monkeypatch):
    await schema.close_db()
    monkeypatch.setattr(schema, "DB_PATH", tmp_path / "test.db")
    await schema.init_db()
    yield
    await schema.close_db()


async def _project(name="Project"):
    return await crud.create_project(name=name)


async def _video(project_id, title="Video"):
    return await crud.create_video(project_id=project_id, title=title)


async def _scene(video_id, prompt="A scene"):
    return await crud.create_scene(video_id=video_id, display_order=0, prompt=prompt)


@pytest.mark.asyncio
async def test_create_update_delete_and_cascade(isolated_db):
    project = await _project()
    video = await _video(project["id"])
    scene = await _scene(video["id"])
    request = await crud.create_request(
        req_type="GENERATE_IMAGE",
        project_id=project["id"],
        video_id=video["id"],
        scene_id=scene["id"],
    )

    updated = await crud.update_scene(scene["id"], prompt="Updated")
    assert updated["prompt"] == "Updated"
    assert updated["updated_at"] >= updated["created_at"]

    assert await crud.delete_project(project["id"]) is True
    assert await crud.get_video(video["id"]) is None
    assert await crud.get_scene(scene["id"]) is None
    assert await crud.get_request(request["id"]) is None


@pytest.mark.asyncio
async def test_foreign_keys_and_relationship_triggers(isolated_db):
    project = await _project()
    other_project = await _project("Other")
    video = await _video(project["id"])
    scene = await _scene(video["id"])

    with pytest.raises((aiosqlite.IntegrityError, sqlite3.IntegrityError)):
        await crud.create_video(project_id="missing", title="Invalid")

    with pytest.raises((aiosqlite.IntegrityError, sqlite3.IntegrityError), match="project"):
        await crud.create_request(
            req_type="GENERATE_IMAGE",
            project_id=other_project["id"],
            video_id=video["id"],
            scene_id=scene["id"],
        )

    other_video = await _video(other_project["id"], "Other video")
    with pytest.raises((aiosqlite.IntegrityError, sqlite3.IntegrityError), match="parent_scene"):
        await crud.create_scene(
            video_id=other_video["id"],
            display_order=1,
            prompt="Invalid parent",
            parent_scene_id=scene["id"],
            chain_type="CONTINUATION",
        )


@pytest.mark.asyncio
async def test_active_duplicate_requests_are_rejected(isolated_db):
    project = await _project()
    video = await _video(project["id"])
    scene = await _scene(video["id"])
    args = {
        "req_type": "GENERATE_IMAGE",
        "project_id": project["id"],
        "video_id": video["id"],
        "scene_id": scene["id"],
    }
    await crud.create_request(**args)
    with pytest.raises((aiosqlite.IntegrityError, sqlite3.IntegrityError)):
        await crud.create_request(**args)

    claimed = (await crud.claim_actionable_requests(limit=1))[0]
    await crud.update_request(claimed["id"], status="COMPLETED", _expected_started_at=claimed["started_at"])
    second = await crud.create_request(**args)
    assert second["status"] == "PENDING"


@pytest.mark.asyncio
async def test_failed_mutation_rolls_back(isolated_db):
    project = await _project()
    with pytest.raises((aiosqlite.IntegrityError, sqlite3.IntegrityError)):
        await crud.create_project(name="Duplicate id", id=project["id"])
    stored = await crud.get_project(project["id"])
    assert stored["name"] == "Project"


@pytest.mark.asyncio
async def test_migration_is_idempotent_and_indexes_exist(isolated_db):
    await schema.close_db()
    await schema.init_db()
    await schema.close_db()
    await schema.init_db()
    db = await schema.get_db()
    cursor = await db.execute("SELECT version FROM schema_migration")
    assert [row[0] for row in await cursor.fetchall()] == [1]
    cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='index'")
    indexes = {row[0] for row in await cursor.fetchall()}
    assert "idx_request_queue" in indexes
    assert "idx_scene_parent" in indexes
    cursor = await db.execute("PRAGMA table_info(scene)")
    columns = {row[1] for row in await cursor.fetchall()}
    assert {"narration_audio_status", "narration_mix_status"} <= columns


@pytest.mark.asyncio
async def test_concurrent_claims_are_disjoint(isolated_db):
    project = await _project()
    video = await _video(project["id"])
    scenes = [await _scene(video["id"], f"Scene {index}") for index in range(2)]
    for scene in scenes:
        await crud.create_request(
            req_type="GENERATE_IMAGE",
            project_id=project["id"],
            video_id=video["id"],
            scene_id=scene["id"],
        )

    claimed = await asyncio.gather(
        crud.claim_actionable_requests(limit=1),
        crud.claim_actionable_requests(limit=1),
    )
    claimed_ids = [row["id"] for batch in claimed for row in batch]
    assert len(claimed_ids) == 2
    assert len(set(claimed_ids)) == 2
    assert all(row["status"] == "PROCESSING" for batch in claimed for row in batch)


@pytest.mark.asyncio
async def test_project_scene_lookup_uses_one_bulk_result(isolated_db):
    project = await _project("Bulk project")
    video_a = await _video(project["id"], "A")
    video_b = await _video(project["id"], "B")
    await _scene(video_a["id"], "Scene A")
    await _scene(video_b["id"], "Scene B")

    scenes = await crud.list_scenes_by_project(project["id"])
    assert [scene["prompt"] for scene in scenes] == ["Scene A", "Scene B"]
