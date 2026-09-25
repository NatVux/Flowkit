"""/api/pipeline-runs over HTTP (no lifespan: no worker, no WS server, no Flow)."""

from unittest.mock import AsyncMock

import httpx
import pytest

from agent.db import crud, schema
from agent.services.pipeline import runner as runner_module
from agent.services.pipeline.runner import PipelineRunner


class FakeClient:
    def __init__(self):
        self.connected = True
        self.generation_guard_status = {"cooldown_active": False}


@pytest.fixture
async def env(tmp_path, monkeypatch):
    await schema.close_db()
    monkeypatch.setattr(schema, "DB_PATH", tmp_path / "api.db")
    await schema.init_db()
    monkeypatch.setattr("agent.config.OUTPUT_DIR", tmp_path / "output")
    client = FakeClient()
    runner = PipelineRunner(lambda: client)
    monkeypatch.setattr(runner_module, "_runner", runner)
    emit = AsyncMock()
    monkeypatch.setattr(runner_module.event_bus, "emit", emit)

    project = await crud.create_project(name="Thu nghiem Gemini", material="3d_pixar")
    video = await crud.create_video(project_id=project["id"], title="V", orientation="VERTICAL")
    cat = await crud.create_character(name="Mèo Con")
    await crud.link_character_to_project(project["id"], cat["id"])
    await crud.create_scene(video_id=video["id"], display_order=0, prompt="p", character_names=["Mèo Con"])

    from agent.main import app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as http:
        yield {"http": http, "video": video, "project": project, "client": client, "emit": emit}
    await schema.close_db()


async def test_create_returns_a_draft_with_a_minimum_estimate(env):
    r = await env["http"].post("/api/pipeline-runs", json={"video_id": env["video"]["id"]})
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["status"] == "DRAFT" and run["checkpoints"] == ["REFS", "IMAGES"]
    est = run["estimate_at_last_checkpoint"]
    assert est["kind"] == "minimum" and (est["refs"], est["images"], est["videos"]) == (1, 1, 1)
    assert run["generations_spent"] == 0 and await crud.list_requests() == []
    again = await env["http"].post("/api/pipeline-runs", json={"video_id": env["video"]["id"]})
    assert again.status_code == 409


@pytest.mark.parametrize("body, status", [
    ({"video_id": "../etc"}, 422),
    ({"video_id": "11111111-2222-3333-4444-555555555555"}, 404),
    ({"video_id": "VIDEO", "checkpoints": ["CONCAT"]}, 422),
])
async def test_create_rejects_bad_input(env, body, status):
    if body["video_id"] == "VIDEO":
        body["video_id"] = env["video"]["id"]
    assert (await env["http"].post("/api/pipeline-runs", json=body)).status_code == status


async def test_start_status_and_lifecycle_errors(env):
    http = env["http"]
    run = (await http.post("/api/pipeline-runs", json={"video_id": env["video"]["id"]})).json()
    assert (await http.post(f"/api/pipeline-runs/{run['id']}/approve")).status_code == 409

    env["client"].connected = False
    r = await http.post(f"/api/pipeline-runs/{run['id']}/start")
    assert r.status_code == 400 and "extension" in r.json()["error"]["message"]

    env["client"].connected = True
    r = await http.post(f"/api/pipeline-runs/{run['id']}/start")
    assert r.status_code == 200 and r.json()["status"] == "RUNNING" and r.json()["stage"] == "REFS"
    [item] = r.json()["stages"]["REFS"]["items"]
    assert item["label"] == "Mèo Con" and item["status"] == "SUBMITTED" and item["request_status"] == "PENDING"

    got = (await http.get(f"/api/pipeline-runs/{run['id']}")).json()
    assert got["id"] == run["id"] and got["needs_user_action"] == [] and got["final_path"] is None
    listed = (await http.get(f"/api/pipeline-runs?video_id={env['video']['id']}")).json()
    assert [x["id"] for x in listed] == [run["id"]]

    assert (await http.post(f"/api/pipeline-runs/{run['id']}/redo", json={
        "target_type": "planet", "target_id": item["target_id"]})).status_code == 422
    assert (await http.post(f"/api/pipeline-runs/{run['id']}/redo", json={
        "target_type": "character", "target_id": item["target_id"]})).status_code == 409  # still RUNNING

    r = await http.post(f"/api/pipeline-runs/{run['id']}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "CANCELLED"
    assert await crud.list_requests() == []
    assert (await http.post(f"/api/pipeline-runs/{run['id']}/cancel")).status_code == 409
    assert (await http.get("/api/pipeline-runs/11111111-2222-3333-4444-555555555555")).status_code == 404


async def test_status_changes_are_published_on_the_event_bus(env):
    http = env["http"]
    run = (await http.post("/api/pipeline-runs", json={"video_id": env["video"]["id"]})).json()
    await http.post(f"/api/pipeline-runs/{run['id']}/start")
    events = [(c.args[0], c.args[1].get("status")) for c in env["emit"].await_args_list]
    assert ("pipeline_update", "RUNNING") in events
    assert any(name == "pipeline_items_queued" for name, _ in events)
