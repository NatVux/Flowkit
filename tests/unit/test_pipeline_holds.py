"""Orphaned holds: requests held by a run that ended or vanished must not stay unclaimable."""

import httpx
import pytest

from agent.db import crud, pipeline_crud as pc, schema
from agent.services.pipeline.runner import PipelineRunner


@pytest.fixture
async def held(tmp_path, monkeypatch):
    """A RUNNING run with two submitted scene requests, both held; the second had reached Flow."""
    await schema.close_db()
    monkeypatch.setattr(schema, "DB_PATH", tmp_path / "holds.db")
    await schema.init_db()
    project = await crud.create_project(name="Thu nghiem", material="3d_pixar")
    video = await crud.create_video(project_id=project["id"], title="V", orientation="VERTICAL")
    scenes = [await crud.create_scene(video_id=video["id"], display_order=i, prompt=f"s{i}") for i in range(2)]
    run = await pc.create_run(project_id=project["id"], video_id=video["id"], orientation="VERTICAL",
                              checkpoints=[], options={}, estimate={}, warnings=[])
    await pc.transition_run(run["id"], "RUNNING", stage="IMAGES")
    await pc.add_items(run["id"], [{"stage": "IMAGES", "target_type": "scene", "target_id": s["id"]} for s in scenes])
    items = await pc.list_items(run["id"])
    unstarted, started = await pc.submit_items(
        run["id"], [i["id"] for i in items],
        lambda item: {"req_type": "GENERATE_IMAGE", "scene_id": item["target_id"], "project_id": project["id"],
                      "video_id": video["id"], "orientation": "VERTICAL"})
    await crud.update_request(started["id"], status="PROCESSING")
    await crud.update_request(started["id"], status="PENDING", retry_count=1)
    assert await pc.hold_requests(run["id"], "paused by user") == 2
    yield {"project": project, "video": video, "scenes": scenes, "run": run,
           "unstarted": unstarted, "started": started}
    await schema.close_db()


async def test_holds_of_an_active_run_are_kept(held):
    await pc.transition_run(held["run"]["id"], "PAUSED", pause_reason="UNUSUAL_ACTIVITY")
    assert await pc.sweep_orphaned_holds() == {"deleted": [], "released": []}
    assert (await crud.get_request(held["unstarted"]["id"]))["next_retry_at"] == pc.HOLD_UNTIL


async def test_run_cancelled_without_its_cleanup(held):
    # e.g. the server died between holding and cancel_unstarted_requests
    await pc.transition_run(held["run"]["id"], "CANCELLED")
    result = await pc.sweep_orphaned_holds()
    assert result == {"deleted": [held["unstarted"]["id"]], "released": [held["started"]["id"]]}
    assert await crud.get_request(held["unstarted"]["id"]) is None
    released = await crud.get_request(held["started"]["id"])
    assert released["status"] == "PENDING" and released["next_retry_at"] is None
    assert released["last_failure_reason"].startswith("hold released: owning pipeline run ended")
    history = [h["reason"] for i in await pc.list_items(held["run"]["id"]) for h in i["request_history"]]
    assert "orphaned hold removed" in history and "orphaned hold released" in history
    assert await crud.list_requests(status="FAILED") == []


async def test_run_deleted_from_the_database(held):
    db = await schema.get_db()
    await db.execute("DELETE FROM pipeline_run WHERE id=?", (held["run"]["id"],))
    await db.commit()
    result = await pc.sweep_orphaned_holds()
    assert result["deleted"] == [held["unstarted"]["id"]] and result["released"] == [held["started"]["id"]]
    assert len(await crud.claim_actionable_requests(limit=5)) == 1


async def test_a_worker_backoff_is_not_mistaken_for_a_hold(held):
    await pc.transition_run(held["run"]["id"], "COMPLETED")
    await pc.release_requests(held["run"]["id"])  # nothing held any more
    await crud.update_request(held["started"]["id"], next_retry_at="2030-01-01T00:00:00Z")
    assert await pc.sweep_orphaned_holds() == {"deleted": [], "released": []}


async def test_manual_gen_images_is_not_blocked_after_the_run_ends(held):
    from agent.main import app
    await pc.transition_run(held["run"]["id"], "CANCELLED")  # ended with its holds still in place
    body = {"requests": [{"type": "GENERATE_IMAGE", "scene_id": s["id"], "project_id": held["project"]["id"],
                          "video_id": held["video"]["id"], "orientation": "VERTICAL"} for s in held["scenes"]]}

    await PipelineRunner(lambda: None).tick_all()  # the runner loop sweeps before anything else

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as http:
        rows = (await http.post("/api/requests/batch", json=body)).json()
    assert all(r["next_retry_at"] is None for r in rows)
    assert rows[0]["id"] != held["unstarted"]["id"]        # a fresh request, not the dead held one
    assert rows[1]["id"] == held["started"]["id"]          # the released one is simply adopted
    claimed = await crud.claim_actionable_requests(limit=5)
    assert {r["id"] for r in claimed} == {r["id"] for r in rows}
