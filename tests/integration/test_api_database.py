"""API/database integration checks using the real SQLite repository."""

import pytest
from fastapi.testclient import TestClient

from agent.db import schema
from agent.main import app


@pytest.fixture
async def integration_db(tmp_path, monkeypatch):
    await schema.close_db()
    monkeypatch.setattr(schema, "DB_PATH", tmp_path / "integration.db")
    await schema.init_db()
    yield
    await schema.close_db()


@pytest.mark.asyncio
async def test_project_video_scene_request_relationships(integration_db):
    from agent.db import crud

    project = await crud.create_project(name="Integration project")
    video = await crud.create_video(project_id=project["id"], title="Integration video")
    scene = await crud.create_scene(video_id=video["id"], display_order=0, prompt="A test scene")
    request = await crud.create_request(
        req_type="GENERATE_IMAGE", project_id=project["id"],
        video_id=video["id"], scene_id=scene["id"],
    )

    assert (await crud.get_request(request["id"]))["scene_id"] == scene["id"]
    assert (await crud.list_requests(project_id=project["id"]))[0]["id"] == request["id"]


@pytest.mark.asyncio
async def test_api_validation_does_not_write_invalid_request(integration_db):
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post("/api/requests", json={"type": "INVALID"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
