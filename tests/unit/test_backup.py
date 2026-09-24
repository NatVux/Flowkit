"""Backup and restore tests using temporary SQLite/output trees."""

import json

import pytest

from agent import config
from agent.db import crud, schema
from agent.services.backup import create_backup, restore_backup, verify_backup


@pytest.fixture
async def backup_env(tmp_path, monkeypatch):
    await schema.close_db()
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "flow.db")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(schema, "DB_PATH", tmp_path / "flow.db")
    await schema.init_db()
    yield tmp_path
    await schema.close_db()


@pytest.mark.asyncio
async def test_backup_uses_verified_database_and_media(backup_env):
    project = await crud.create_project(name="Backup project")
    media = config.OUTPUT_DIR / "project" / "scene.mp4"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"media")
    destination = backup_env / "backups" / "one"

    result = await create_backup(destination)

    assert result["path"] == str(destination)
    assert (destination / "database.sqlite").is_file()
    assert (destination / "media" / "output" / "project" / "scene.mp4").read_bytes() == b"media"
    assert verify_backup(destination)["valid"] is True
    manifest = json.loads((destination / "manifest.json").read_text())
    assert "database.sqlite" in manifest["files"]
    assert project["id"]


@pytest.mark.asyncio
async def test_restore_restores_database_and_merges_media_without_deleting_current(backup_env):
    project = await crud.create_project(name="Original")
    media = config.OUTPUT_DIR / "original.txt"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_text("original")
    destination = backup_env / "backups" / "restore"
    await create_backup(destination)

    await crud.update_project(project["id"], name="Changed")
    extra = config.OUTPUT_DIR / "extra.txt"
    extra.write_text("keep")
    await schema.close_db()

    result = await restore_backup(destination)
    assert result["previous_database"]
    await schema.init_db()
    restored = await crud.get_project(project["id"])
    assert restored["name"] == "Original"
    assert extra.read_text() == "keep"


@pytest.mark.asyncio
async def test_tampered_backup_is_rejected(backup_env):
    await crud.create_project(name="Tamper project")
    destination = backup_env / "backups" / "tamper"
    await create_backup(destination)
    (destination / "database.sqlite").write_bytes(b"not sqlite")

    with pytest.raises(Exception):
        verify_backup(destination)
