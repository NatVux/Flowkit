"""Character reference versioning and historical scene snapshots."""

import pytest

from agent.db import crud, schema


@pytest.fixture
async def isolated_db(tmp_path, monkeypatch):
    await schema.close_db()
    monkeypatch.setattr(schema, "DB_PATH", tmp_path / "references.db")
    await schema.init_db()
    yield
    await schema.close_db()


async def _scene_graph():
    character = await crud.create_character(name="Hero", slug="hero")
    project = await crud.create_project(name="Reference project")
    await crud.link_character_to_project(project["id"], character["id"])
    video = await crud.create_video(project_id=project["id"], title="Reference video")
    scene = await crud.create_scene(
        video_id=video["id"], display_order=0, prompt="Hero enters", character_names=["Hero"],
    )
    return character, project, scene


@pytest.mark.asyncio
async def test_character_creation_and_reference_version(isolated_db):
    character, _, _ = await _scene_graph()
    assert character["name"] == "Hero"
    asset = await crud.save_character_reference(
        character["id"], "11111111-1111-4111-8111-111111111111",
        reference_image_url="https://example.com/hero-v1.jpg",
        metadata={"prompt": "stable hero", "model": "test"},
    )
    assert asset["version"] == 1
    assert asset["status"] == "ACTIVE"
    assert '"model": "test"' in asset["metadata_json"]


@pytest.mark.asyncio
async def test_reference_replacement_preserves_version_history(isolated_db):
    character, _, _ = await _scene_graph()
    first = await crud.save_character_reference(character["id"], "11111111-1111-4111-8111-111111111111")
    second = await crud.save_character_reference(character["id"], "22222222-2222-4222-8222-222222222222")
    assert first["version"] == 1
    assert second["version"] == 2
    assert (await crud.get_current_character_reference(character["id"]))["id"] == second["id"]
    assert (await crud.validate_character_reference(first)) is True


@pytest.mark.asyncio
async def test_scene_snapshot_retains_historical_reference_after_regeneration(isolated_db):
    character, _, scene = await _scene_graph()
    first = await crud.save_character_reference(character["id"], "11111111-1111-4111-8111-111111111111")
    await crud.capture_scene_character_references(scene["id"], [character["id"]])
    second = await crud.save_character_reference(character["id"], "22222222-2222-4222-8222-222222222222")
    await crud.capture_scene_character_references(scene["id"], [character["id"]])

    snapshots = await crud.list_scene_character_references(scene["id"])
    assert {row["version"] for row in snapshots} == {1, 2}
    assert {row["reference_id"] for row in snapshots} == {first["id"], second["id"]}


@pytest.mark.asyncio
async def test_missing_reference_is_rejected(isolated_db):
    character, _, scene = await _scene_graph()
    with pytest.raises(ValueError, match="Missing or invalid"):
        await crud.capture_scene_character_references(scene["id"], [character["id"]])


@pytest.mark.asyncio
async def test_invalid_or_deleted_asset_is_not_usable(isolated_db):
    character, _, scene = await _scene_graph()
    asset = await crud.save_character_reference(character["id"], "11111111-1111-4111-8111-111111111111")
    assert await crud.invalidate_character_reference(asset["id"], "file missing") is True
    assert await crud.validate_character_reference({**asset, "status": "INVALID"}) is False
    with pytest.raises(ValueError, match="Missing or invalid"):
        await crud.capture_scene_character_references(scene["id"], [character["id"]])

    # Historical asset rows prevent destructive character deletion.
    with pytest.raises(Exception):
        await crud.delete_character(character["id"])


@pytest.mark.asyncio
async def test_legacy_character_pointer_is_promoted_to_version_one(isolated_db):
    character, _, scene = await _scene_graph()
    await crud.update_character(
        character["id"],
        media_id="33333333-3333-4333-8333-333333333333",
        reference_image_url="https://example.com/legacy.jpg",
    )
    snapshots = await crud.capture_scene_character_references(scene["id"], [character["id"]])
    assert snapshots[0]["version"] == 1
    assert snapshots[0]["media_id"].startswith("3333")
