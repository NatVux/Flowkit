"""pipeline_run / pipeline_run_item persistence: run state machine, atomic submit with
request history, holding and cancelling the run's queued requests."""

import pytest

from agent.db import crud, pipeline_crud as pc, schema


@pytest.fixture
async def db(tmp_path, monkeypatch):
    await schema.close_db()
    monkeypatch.setattr(schema, "DB_PATH", tmp_path / "pipeline.db")
    await schema.init_db()
    yield
    await schema.close_db()


@pytest.fixture
async def video(db):
    project = await crud.create_project(name="Thu nghiem", material="3d_pixar")
    video = await crud.create_video(project_id=project["id"], title="V", orientation="VERTICAL")
    scenes = [await crud.create_scene(video_id=video["id"], display_order=i, prompt=f"s{i}") for i in range(2)]
    return project, video, scenes


async def _run(project, video, **kw):
    return await pc.create_run(project_id=project["id"], video_id=video["id"], orientation="VERTICAL",
                               checkpoints=kw.get("checkpoints", ["REFS", "IMAGES"]), options={"concat": True},
                               estimate={"refs": 3}, warnings=kw.get("warnings", []))


def _build(project, video):
    return lambda item: {"req_type": "GENERATE_IMAGE", "scene_id": item["target_id"], "project_id": project["id"],
                         "video_id": video["id"], "orientation": "VERTICAL"}


async def _running_with_items(project, video, scenes):
    run = await _run(project, video)
    await pc.transition_run(run["id"], "RUNNING", stage="IMAGES")
    await pc.add_items(run["id"], [{"stage": "IMAGES", "target_type": "scene", "target_id": s["id"]} for s in scenes])
    return run, await pc.list_items(run["id"])


class TestRuns:
    async def test_create_is_a_draft_with_decoded_json(self, video):
        project, v, _ = video
        run = await _run(project, v, warnings=["meta.json nói HORIZONTAL"])
        assert run["status"] == "DRAFT" and run["checkpoints"] == ["REFS", "IMAGES"]
        assert run["estimate"] == {"refs": 3} and run["warnings"] == ["meta.json nói HORIZONTAL"]

    async def test_one_unfinished_run_per_video(self, video):
        project, v, _ = video
        first = await _run(project, v)
        with pytest.raises(pc.PipelineConflict):
            await _run(project, v)
        await pc.transition_run(first["id"], "CANCELLED")
        assert (await _run(project, v))["status"] == "DRAFT"

    async def test_transitions_are_guarded_and_timestamped(self, video):
        project, v, _ = video
        run = await _run(project, v)
        with pytest.raises(pc.InvalidRunTransition):
            await pc.transition_run(run["id"], "COMPLETED")
        with pytest.raises(pc.PipelineConflict):
            await pc.transition_run(run["id"], "RUNNING", expected=("AWAITING_APPROVAL",))
        running = await pc.transition_run(run["id"], "RUNNING", stage="REFS")
        assert running["started_at"] and running["stage"] == "REFS" and running["finished_at"] is None
        done = await pc.transition_run(run["id"], "COMPLETED", final_path="output/x/x_final.mp4")
        assert done["finished_at"] and done["final_path"] == "output/x/x_final.mp4"
        with pytest.raises(pc.InvalidRunTransition):
            await pc.transition_run(run["id"], "RUNNING")

    async def test_unknown_field_is_rejected(self, video):
        project, v, _ = video
        run = await _run(project, v)
        with pytest.raises(ValueError):
            await pc.update_run(run["id"], nonsense=1)


class TestSubmit:
    async def test_submit_queues_and_records_in_one_step(self, video):
        project, v, scenes = video
        run, items = await _running_with_items(project, v, scenes)
        requests = await pc.submit_items(run["id"], [i["id"] for i in items], _build(project, v))
        assert len(requests) == 2
        for item, request in zip(await pc.list_items(run["id"]), requests):
            assert item["status"] == "SUBMITTED" and item["request_id"] == request["id"]
            assert item["request_type"] == "GENERATE_IMAGE" and item["redo_count"] == 0
            assert item["request_history"] == [{"request_id": request["id"], "request_type": "GENERATE_IMAGE",
                                                "reason": "submit", "adopted_existing": False,
                                                "at": item["request_history"][0]["at"]}]

    async def test_an_active_request_is_adopted_not_duplicated(self, video):
        project, v, scenes = video
        existing = await crud.create_request("GENERATE_IMAGE", orientation="VERTICAL", scene_id=scenes[0]["id"],
                                             project_id=project["id"], video_id=v["id"])
        run, items = await _running_with_items(project, v, scenes)
        [request] = await pc.submit_items(run["id"], [items[0]["id"]], _build(project, v))
        assert request["id"] == existing["id"]
        assert (await pc.get_item(items[0]["id"]))["request_history"][0]["adopted_existing"] is True
        assert len(await crud.list_requests(scene_id=scenes[0]["id"])) == 1

    async def test_nothing_is_submitted_unless_the_run_is_running(self, video):
        project, v, scenes = video
        run = await _run(project, v)
        await pc.add_items(run["id"], [{"stage": "IMAGES", "target_type": "scene", "target_id": scenes[0]["id"]}])
        [item] = await pc.list_items(run["id"])
        with pytest.raises(pc.PipelineConflict):
            await pc.submit_items(run["id"], [item["id"]], _build(project, v))
        assert await crud.list_requests(video_id=v["id"]) == []

    async def test_a_failure_part_way_writes_nothing(self, video):
        project, v, scenes = video
        run, items = await _running_with_items(project, v, scenes)
        build = _build(project, v)

        def flaky(item):
            if item["id"] == items[1]["id"]:
                raise RuntimeError("boom")
            return build(item)

        with pytest.raises(RuntimeError):
            await pc.submit_items(run["id"], [i["id"] for i in items], flaky)
        assert await crud.list_requests(video_id=v["id"]) == []
        assert {i["status"] for i in await pc.list_items(run["id"])} == {"PLANNED"}

    async def test_redo_keeps_history_and_counts(self, video):
        project, v, scenes = video
        run, items = await _running_with_items(project, v, scenes)
        [first] = await pc.submit_items(run["id"], [items[0]["id"]], _build(project, v))
        await crud.update_request(first["id"], status="PROCESSING")
        await crud.update_request(first["id"], status="FAILED", error_message="x")
        [second] = await pc.submit_items(run["id"], [items[0]["id"]], _build(project, v),
                                         reason="redo: new prompt", redo=True)
        item = await pc.get_item(items[0]["id"])
        assert second["id"] != first["id"] and item["request_id"] == second["id"] and item["redo_count"] == 1
        assert [h["request_id"] for h in item["request_history"]] == [first["id"], second["id"]]
        assert item["request_history"][1]["reason"] == "redo: new prompt"

    async def test_add_items_is_idempotent(self, video):
        project, v, scenes = video
        run, _ = await _running_with_items(project, v, scenes)
        again = await pc.add_items(run["id"], [{"stage": "IMAGES", "target_type": "scene", "target_id": s["id"]}
                                               for s in scenes])
        assert again == 0 and len(await pc.list_items(run["id"])) == 2


class TestHoldAndCancel:
    async def test_held_requests_stay_pending_and_unclaimable(self, video):
        project, v, scenes = video
        run, items = await _running_with_items(project, v, scenes)
        await pc.submit_items(run["id"], [i["id"] for i in items], _build(project, v))

        assert await pc.hold_requests(run["id"], "paused by user") == 2
        assert await crud.claim_actionable_requests(limit=5) == []
        for s in scenes:
            [r] = await crud.list_requests(scene_id=s["id"])
            assert r["status"] == "PENDING" and r["next_retry_at"] == pc.HOLD_UNTIL
            assert (await crud.get_scene(s["id"]))["vertical_image_status"] == "PENDING"

        assert await pc.release_requests(run["id"]) == 2
        assert len(await crud.claim_actionable_requests(limit=5)) == 2

    async def test_release_leaves_a_worker_backoff_alone(self, video):
        project, v, scenes = video
        run, items = await _running_with_items(project, v, scenes)
        [request, _] = await pc.submit_items(run["id"], [i["id"] for i in items], _build(project, v))
        backoff = "2030-01-01T00:00:00Z"
        await crud.update_request(request["id"], next_retry_at=backoff)
        await pc.release_requests(run["id"])
        assert (await crud.get_request(request["id"]))["next_retry_at"] == backoff

    async def test_cancel_deletes_only_requests_that_never_reached_flow(self, video):
        project, v, scenes = video
        run, items = await _running_with_items(project, v, scenes)
        untouched, started = await pc.submit_items(run["id"], [i["id"] for i in items], _build(project, v))
        # the second one was claimed once and went back to PENDING for a retry
        await crud.update_request(started["id"], status="PROCESSING")
        await crud.update_request(started["id"], status="PENDING", retry_count=1)
        await pc.hold_requests(run["id"], "paused")

        result = await pc.cancel_unstarted_requests(run["id"])

        assert result == {"deleted": 1, "released_to_finish": 1}
        assert await crud.get_request(untouched["id"]) is None
        kept = await crud.get_request(started["id"])
        assert kept["status"] == "PENDING" and kept["next_retry_at"] is None
        first_item = await pc.get_item(items[0]["id"])
        assert first_item["status"] == "CANCELLED" and first_item["request_id"] is None
        assert first_item["request_history"][-1]["reason"].startswith("cancelled before reaching Flow")
        assert (await crud.get_scene(scenes[0]["id"]))["vertical_image_status"] == "PENDING"
        assert await crud.list_requests(status="FAILED") == []
