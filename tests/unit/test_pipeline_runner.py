"""Pipeline runner on a temporary DB. Requests go through the real worker logic
(processor._process_one) with only _dispatch faked, so results land on scenes and
entities exactly as in production. No Flow, no files."""

import itertools
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from agent.db import crud, pipeline_crud as pc, schema
from agent.services.pipeline import runner as runner_module
from agent.services.pipeline.runner import PipelineRunner, RunError
from agent.worker import processor

_ids = (f"{n:08d}-aaaa-4aaa-8aaa-aaaaaaaaaaaa" for n in itertools.count(1))


def media_ok(req):
    mid = next(_ids)
    if req["type"] in ("GENERATE_VIDEO", "REGENERATE_VIDEO"):
        return {"data": {"operations": [{"status": "MEDIA_GENERATION_STATUS_SUCCESSFUL", "operation": {
            "metadata": {"video": {"mediaId": mid, "fifeUrl": f"https://example.test/video/{mid}.mp4"}}}}]}}
    return {"data": {"media": [{"name": mid, "image": {"generatedImage": {
        "mediaId": mid, "fifeUrl": f"https://flow-content.google/image/{mid}?x=1"}}}]}}


class FakeClient:
    def __init__(self):
        self.connected = True
        self.generation_guard_status = {"cooldown_active": False, "cooldown_remaining_s": 0.0}


class Clock:
    def __init__(self):
        self.t = datetime(2026, 9, 26, 10, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.t


@pytest.fixture
async def db(tmp_path, monkeypatch):
    await schema.close_db()
    monkeypatch.setattr(schema, "DB_PATH", tmp_path / "runner.db")
    await schema.init_db()
    monkeypatch.setattr("agent.config.OUTPUT_DIR", tmp_path / "output")

    async def offline(*_args):  # no network in these tests; downloads are covered in test_pipeline_concat
        raise OSError("offline")

    monkeypatch.setattr(runner_module.pmedia, "fetch_url", offline)
    monkeypatch.setattr(runner_module.pmedia, "fetch_fresh", offline)
    yield
    await schema.close_db()


@pytest.fixture
async def world(db):
    """3 entities (Chợ Đêm already has a reference) and scenes s0 -> s1 (continuation), s2."""
    project = await crud.create_project(name="Thu nghiem Gemini", material="3d_pixar")
    video = await crud.create_video(project_id=project["id"], title="Mèo", orientation="VERTICAL")
    ents = {}
    for name, media in (("Mèo Con", None), ("Bà Cụ", None), ("Chợ Đêm", "99999999-bbbb-4bbb-8bbb-bbbbbbbbbbbb")):
        ents[name] = await crud.create_character(name=name, media_id=media)
        await crud.link_character_to_project(project["id"], ents[name]["id"])
    s0 = await crud.create_scene(video_id=video["id"], display_order=0, prompt="p0", character_names=["Mèo Con", "Chợ Đêm"])
    s1 = await crud.create_scene(video_id=video["id"], display_order=1, prompt="p1", character_names=["Mèo Con"],
                                 chain_type="CONTINUATION", parent_scene_id=s0["id"])
    s2 = await crud.create_scene(video_id=video["id"], display_order=2, prompt="p2", character_names=["Bà Cụ"])
    client, clock = FakeClient(), Clock()
    runner = PipelineRunner(lambda: client, now=clock)
    return {"project": project, "video": video, "ents": ents, "scenes": [s0, s1, s2],
            "client": client, "clock": clock, "runner": runner}


async def work(outcome=media_ok):
    """One worker pass over every claimable request (held ones are not claimable)."""
    claimed = await crud.claim_actionable_requests(limit=50)
    for req in claimed:
        with patch.object(processor, "_dispatch", AsyncMock(return_value=outcome(req))):
            await processor._process_one(req, {}, {})
    return claimed


async def active_requests(video_id=None):
    return [r for r in await crud.list_requests() if r["status"] in ("PENDING", "PROCESSING")]


def by_label(status, stage):
    return {i["label"]: i for i in status["stages"][stage]["items"]}


# ─── happy path ─────────────────────────────────────────────

async def test_full_run_with_default_checkpoints(world):
    r, video = world["runner"], world["video"]
    run = await r.create(video["id"], concat=False)
    assert run["status"] == "DRAFT"
    assert (run["estimate"]["refs"], run["estimate"]["images"], run["estimate"]["videos"]) == (2, 3, 3)
    assert run["estimate"]["kind"] == "minimum"

    st = await r.start(run["id"])
    assert st["status"] == "RUNNING" and st["stage"] == "REFS"
    refs = by_label(st, "REFS")
    assert refs["Chợ Đêm"]["status"] == "SKIPPED" and refs["Mèo Con"]["status"] == "SUBMITTED"
    assert len(await active_requests()) == 2  # one batch, only the missing references

    await work()
    await r.tick(run["id"])
    st = await r.status(run["id"])
    assert st["status"] == "AWAITING_APPROVAL" and "Approve to start IMAGES (minimum 3" in st["status_detail"]

    st = await r.approve(run["id"])
    imgs = by_label(st, "IMAGES")
    # cha trước con: s1 waits for s0
    assert imgs["scene #0"]["status"] == "SUBMITTED" and imgs["scene #0"]["request_type"] == "GENERATE_IMAGE"
    assert imgs["scene #2"]["status"] == "SUBMITTED"
    assert imgs["scene #1"]["status"] == "PLANNED" and imgs["scene #1"]["wave"] == 1

    await work()
    await r.tick(run["id"])
    st = await r.status(run["id"])
    assert by_label(st, "IMAGES")["scene #1"]["request_type"] == "EDIT_IMAGE"
    assert by_label(st, "IMAGES")["scene #1"]["status"] == "SUBMITTED"
    await work()
    await r.tick(run["id"])
    st = await r.status(run["id"])
    assert st["status"] == "AWAITING_APPROVAL" and st["stage"] == "IMAGES"
    # the continuation image set its parent's end frame, as result_handler does
    assert (await crud.get_scene(world["scenes"][0]["id"]))["vertical_end_scene_media_id"]

    st = await r.approve(run["id"])
    assert all(i["status"] == "SUBMITTED" for i in st["stages"]["VIDEOS"]["items"])
    assert (await crud.get_scene(world["scenes"][0]["id"]))["vertical_end_scene_media_id"] is None
    assert any("end frame" in w for w in st["warnings"])

    await work()
    await r.tick(run["id"])
    st = await r.status(run["id"])
    assert st["status"] == "COMPLETED"
    assert st["generations_spent"] == 2 + 3 + 0  # videos count once their Flow op id is stored (none here)


async def test_no_checkpoints_runs_straight_through_after_start(world):
    r = world["runner"]
    run = await r.create(world["video"]["id"], checkpoints=[], concat=False)
    await r.start(run["id"])
    for _ in range(6):
        await work()
        await r.tick(run["id"])
    assert (await r.status(run["id"]))["status"] == "COMPLETED"


# ─── restart / duplicates ───────────────────────────────────

async def test_restart_mid_stage_never_submits_twice(world):
    r = world["runner"]
    run = await r.create(world["video"]["id"], concat=False)
    await r.start(run["id"])
    before = sorted(x["id"] for x in await crud.list_requests())
    await crud.claim_actionable_requests(limit=1)  # one was being processed when the server died

    await crud.reset_orphaned_processing()          # what lifespan does first
    fresh = PipelineRunner(lambda: world["client"], now=world["clock"])
    for _ in range(3):
        await fresh.tick_all()
    assert sorted(x["id"] for x in await crud.list_requests()) == before
    assert {x["status"] for x in await crud.list_requests()} == {"PENDING"}

    await work()
    await fresh.tick(run["id"])
    assert (await fresh.status(run["id"]))["status"] == "AWAITING_APPROVAL"


# ─── content policy ─────────────────────────────────────────

async def test_unsafe_marks_the_scene_blocks_its_child_and_redo_recovers(world):
    r, scenes = world["runner"], world["scenes"]
    run = await r.create(world["video"]["id"], concat=False)
    await r.start(run["id"]); await work(); await r.tick(run["id"])
    await r.approve(run["id"])

    def unsafe_for_s0(req):
        if req["scene_id"] == scenes[0]["id"]:
            return {"error": "RpcError: x [PUBLIC_ERROR_UNSAFE_GENERATION]"}
        return media_ok(req)

    await work(unsafe_for_s0)
    await r.tick(run["id"])
    st = await r.status(run["id"])
    assert st["status"] == "NEEDS_USER_ACTION"
    imgs = by_label(st, "IMAGES")
    assert imgs["scene #0"]["status"] == "NEEDS_USER_ACTION" and imgs["scene #0"]["error_code"] == "UNSAFE_GENERATION"
    assert imgs["scene #1"]["status"] == "PLANNED"      # blocked by its parent, nothing queued
    assert imgs["scene #2"]["status"] == "COMPLETED"
    assert [n["label"] for n in st["needs_user_action"]] == ["scene #0"]

    await crud.update_scene(scenes[0]["id"], prompt="gentler prompt")
    st = await r.redo(run["id"], "scene", scenes[0]["id"])
    assert st["status"] == "RUNNING" and st["redo"]["estimated_generations"] == 1
    assert st["redo"]["stale_descendants"] == [scenes[1]["id"]]
    item = by_label(st, "IMAGES")["scene #0"]
    assert item["status"] == "SUBMITTED" and item["request_type"] == "REGENERATE_IMAGE"
    assert item["redo_count"] == 1 and item["requests_made"] == 2

    await work(); await r.tick(run["id"])
    await work(); await r.tick(run["id"])
    assert (await r.status(run["id"]))["status"] == "AWAITING_APPROVAL"


# ─── UNUSUAL_ACTIVITY and the extension ─────────────────────

async def test_unusual_activity_pauses_holds_the_rest_and_resume_requeues(world):
    r = world["runner"]
    run = await r.create(world["video"]["id"], concat=False)
    await r.start(run["id"])
    [first, second] = await crud.claim_actionable_requests(limit=2)
    with patch.object(processor, "_dispatch", AsyncMock(return_value={
            "error": "FlowBatchError: x: PUBLIC_ERROR_UNUSUAL_ACTIVITY"})):
        await processor._process_one(first, {}, {})
    await crud.update_request(second["id"], status="PENDING")  # the other one is still waiting

    await r.tick(run["id"])
    st = await r.status(run["id"])
    assert st["status"] == "PAUSED" and st["pause_reason"] == "UNUSUAL_ACTIVITY"
    held = await crud.get_request(second["id"])
    assert held["status"] == "PENDING" and held["next_retry_at"] == pc.HOLD_UNTIL
    assert await crud.claim_actionable_requests(limit=5) == []
    for name in ("Mèo Con", "Bà Cụ"):
        assert (await crud.get_character(world["ents"][name]["id"]))["media_id"] is None

    st = await r.resume(run["id"])
    assert st["status"] == "RUNNING"
    assert (await crud.get_request(second["id"]))["next_retry_at"] is None
    items = st["stages"]["REFS"]["items"]
    assert all(i["status"] in ("SUBMITTED", "SKIPPED") for i in items)
    assert max(i["requests_made"] for i in items) == 2  # the flagged one was queued again


async def test_active_cooldown_pauses_a_running_run(world):
    r = world["runner"]
    run = await r.create(world["video"]["id"], concat=False)
    await r.start(run["id"])
    world["client"].generation_guard_status = {"cooldown_active": True, "cooldown_remaining_s": 80}
    await r.tick(run["id"])
    assert (await r.status(run["id"]))["pause_reason"] == "UNUSUAL_ACTIVITY"
    with pytest.raises(RunError, match="cooldown"):
        await r.resume(run["id"])


async def test_short_disconnects_are_tolerated_long_ones_pause(world):
    r, client, clock = world["runner"], world["client"], world["clock"]
    run = await r.create(world["video"]["id"], concat=False)
    await r.start(run["id"])

    client.connected = False
    await r.tick(run["id"])
    clock.t += timedelta(seconds=45)
    await r.tick(run["id"])
    assert (await r.status(run["id"]))["status"] == "RUNNING"
    client.connected = True
    await r.tick(run["id"])
    assert (await pc.get_run(run["id"]))["disconnected_since"] is None

    client.connected = False
    await r.tick(run["id"])
    clock.t += timedelta(seconds=61)
    await r.tick(run["id"])
    st = await r.status(run["id"])
    assert st["status"] == "PAUSED" and st["pause_reason"] == "EXTENSION_DISCONNECTED"
    assert {x["next_retry_at"] for x in await active_requests()} == {pc.HOLD_UNTIL}
    with pytest.raises(RunError, match="extension"):
        await r.resume(run["id"])
    client.connected = True
    assert (await r.resume(run["id"]))["status"] == "RUNNING"


# ─── cancel, redo guards, create checks ─────────────────────

async def test_cancel_removes_unstarted_requests_without_failing_anything(world):
    r = world["runner"]
    run = await r.create(world["video"]["id"], concat=False)
    await r.start(run["id"])
    st = await r.cancel(run["id"])
    assert st["status"] == "CANCELLED"
    assert await crud.list_requests() == []
    assert await crud.list_requests(status="FAILED") == []
    with pytest.raises(pc.PipelineConflict):
        await r.cancel(run["id"])


async def test_reference_redo_needs_confirmation_when_it_wipes_scenes(world):
    r, scenes = world["runner"], world["scenes"]
    run = await r.create(world["video"]["id"], concat=False)
    await r.start(run["id"]); await work(); await r.tick(run["id"])
    # (a finished reference had already wiped any earlier image of the scenes using it)
    await crud.update_scene(scenes[0]["id"], vertical_image_media_id="12345678-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                            vertical_image_status="COMPLETED")
    cat = world["ents"]["Mèo Con"]["id"]
    with pytest.raises(pc.PipelineConflict, match="confirm_invalidates"):
        await r.redo(run["id"], "character", cat)
    st = await r.redo(run["id"], "character", cat, confirm_invalidates=True)
    assert by_label(st, "REFS")["Mèo Con"]["request_type"] == "REGENERATE_CHARACTER_IMAGE"


async def test_video_redo_resets_the_status_and_regenerates(world):
    r, scenes = world["runner"], world["scenes"]
    run = await r.create(world["video"]["id"], checkpoints=["VIDEOS"], concat=True)  # stops before CONCAT
    await r.start(run["id"])
    for _ in range(4):
        await work(); await r.tick(run["id"])
    assert (await r.status(run["id"]))["status"] == "AWAITING_APPROVAL"
    st = await r.redo(run["id"], "scene", scenes[2]["id"])
    item = by_label(st, "VIDEOS")["scene #2"]
    assert item["status"] == "SUBMITTED" and item["request_type"] == "GENERATE_VIDEO"
    assert (await crud.get_scene(scenes[2]["id"]))["vertical_video_status"] == "PENDING"


async def test_redo_is_refused_while_running(world):
    r = world["runner"]
    run = await r.create(world["video"]["id"], concat=False)
    await r.start(run["id"])
    with pytest.raises(pc.PipelineConflict, match="RUNNING"):
        await r.redo(run["id"], "scene", world["scenes"][0]["id"])


async def test_create_checks(world, tmp_path):
    r, video = world["runner"], world["video"]
    with pytest.raises(RunError, match="checkpoint"):
        await r.create(video["id"], checkpoints=["CONCAT"])
    bare = await crud.create_video(project_id=world["project"]["id"], title="no orientation")
    with pytest.raises(RunError, match="orientation"):
        await r.create(bare["id"])
    meta = tmp_path / "output" / "thu_nghiem_gemini" / "meta.json"
    meta.parent.mkdir(parents=True)
    meta.write_text(json.dumps({"orientation": "HORIZONTAL"}), encoding="utf-8")
    run = await r.create(video["id"])
    assert run["warnings"] == ["meta.json says HORIZONTAL but the video is VERTICAL; using VERTICAL."]
    with pytest.raises(pc.PipelineConflict):
        await r.create(video["id"])


async def test_start_needs_the_extension(world):
    r = world["runner"]
    run = await r.create(world["video"]["id"])
    world["client"].connected = False
    with pytest.raises(RunError, match="extension"):
        await r.start(run["id"])
    assert (await pc.get_run(run["id"]))["status"] == "DRAFT"
    assert await crud.list_requests() == []
