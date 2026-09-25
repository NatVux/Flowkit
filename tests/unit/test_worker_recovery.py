"""Worker hardening for the pipeline runner: content-policy codes, startup orphan recovery,
deduplicated enqueueing, the UNUSUAL_ACTIVITY cooldown and capped not-found recovery."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from agent.db import crud, schema
from agent.sdk.persistence.sqlite_repository import SQLiteRepository
from agent.sdk.services import operations as ops_module
from agent.worker import processor

IMAGE_ID = "11111111-1111-4111-8111-111111111111"
VIDEO_MEDIA = "22222222-2222-4222-8222-222222222222"
OPERATION = "33333333-3333-4333-8333-333333333333"


@pytest.fixture
async def db(tmp_path, monkeypatch):
    await schema.close_db()
    monkeypatch.setattr(schema, "DB_PATH", tmp_path / "worker.db")
    await schema.init_db()
    yield
    await schema.close_db()


async def _scene(**scene_fields):
    project = await crud.create_project(name="P", material="3d_pixar")
    video = await crud.create_video(project_id=project["id"], title="V", orientation="VERTICAL")
    scene = await crud.create_scene(video_id=video["id"], display_order=0, prompt="A cat walks")
    if scene_fields:
        scene = await crud.update_scene(scene["id"], **scene_fields)
    return project, video, scene


async def _claimed(req_type="GENERATE_IMAGE", **scene_fields):
    project, video, scene = await _scene(**scene_fields)
    await crud.create_request(req_type, orientation="VERTICAL", scene_id=scene["id"],
                              project_id=project["id"], video_id=video["id"])
    [claimed] = await crud.claim_actionable_requests(limit=1)
    return claimed, scene


# ─── W1: content-policy codes are terminal ──────────────────

class TestContentPolicyCodes:
    # The first string reuses the shape of a REAL batchexecute error (seen for
    # PUBLIC_ERROR_UNUSUAL_ACTIVITY: "[7, None, [['...ErrorInfo', ['PUBLIC_ERROR_…']]]]") with the
    # code swapped. No real content-policy rejection has been captured, so whether Flow sends
    # that reason string at all is unconfirmed - see docs/PIPELINE_RUNNER.md.
    @pytest.mark.parametrize("error", [
        "RpcError: ogiZ0b failed: [3, None, [['type.googleapis.com/google.rpc.ErrorInfo', ['PUBLIC_ERROR_UNSAFE_GENERATION']]]]",
        "Invalid prompt [PUBLIC_ERROR_MINOR_INPUT_IMAGE]",
    ])
    async def test_exact_code_fails_at_once_and_marks_the_scene(self, db, error):
        req, scene = await _claimed()
        await processor._handle_failure(req["id"], req, {"error": error})
        row = await crud.get_request(req["id"])
        assert row["status"] == "FAILED" and row["retry_count"] == 0
        assert (await crud.get_scene(scene["id"]))["vertical_image_status"] == "FAILED"

    @pytest.mark.parametrize("error", ["unsafe generation", "UNSAFE_GENERATION", "public_error_unsafe_generation"])
    def test_loose_wording_is_not_a_content_policy_code(self, error):
        assert processor.content_policy_code(error) is None
        assert processor._is_retryable_error(error)

    async def test_loose_wording_takes_the_normal_counted_retry(self, db):
        req, _ = await _claimed()
        await processor._handle_failure(req["id"], req, {"error": "prompt looked unsafe generation-wise"})
        row = await crud.get_request(req["id"])
        assert row["status"] == "PENDING" and row["retry_count"] == 1


# ─── W2: orphaned PROCESSING rows at startup ────────────────

class FakeVideoClient:
    """Stands in for FlowClient: records submits, answers polls with a finished render."""

    connected = True

    def __init__(self):
        self.submits = []
        self.polls = []

    async def generate_video(self, **kw):
        self.submits.append(kw)
        return {"error": "must not submit"}

    async def check_video_status(self, operations):
        self.polls.append(operations)
        return {"data": {"operations": [{
            "status": "MEDIA_GENERATION_STATUS_SUCCESSFUL",
            "operation": {"name": OPERATION, "metadata": {"video": {
                "mediaId": VIDEO_MEDIA, "fifeUrl": "https://example.test/video.mp4"}}},
        }]}}


@pytest.fixture
def fake_ops(monkeypatch):
    client = FakeVideoClient()
    monkeypatch.setattr(ops_module, "VIDEO_POLL_INTERVAL", 0)
    previous = ops_module._ops
    ops_module.init_operations(client, SQLiteRepository())
    yield client
    ops_module._ops = previous


class TestStartupOrphanRecovery:
    async def test_recent_processing_rows_are_reset_whatever_their_age(self, db):
        req, _ = await _claimed()
        assert (await crud.get_request(req["id"]))["status"] == "PROCESSING"
        assert await crud.reset_orphaned_processing() == 1
        row = await crud.get_request(req["id"])
        assert row["status"] == "PENDING" and row["error_message"] == "reset: orphaned at startup"

    async def test_orphaned_video_request_is_polled_again_not_resubmitted(self, db, fake_ops):
        req, scene = await _claimed("GENERATE_VIDEO", vertical_image_media_id=IMAGE_ID,
                                    vertical_image_status="COMPLETED")
        # the previous process had submitted and stored the Flow operation id, then died
        await crud.update_request(req["id"], request_id=OPERATION)

        await crud.reset_orphaned_processing()
        [again] = await crud.claim_actionable_requests(limit=1)
        assert again["id"] == req["id"] and again["request_id"] == OPERATION
        await processor._process_one(again, {}, {})

        assert fake_ops.submits == []  # no second paid render
        assert fake_ops.polls and fake_ops.polls[0][0]["operation"]["name"] == OPERATION
        assert (await crud.get_request(req["id"]))["status"] == "COMPLETED"
        done = await crud.get_scene(scene["id"])
        assert done["vertical_video_status"] == "COMPLETED" and done["vertical_video_media_id"] == VIDEO_MEDIA

    async def test_recovery_runs_before_the_loop_and_only_once(self, db):
        controller = processor.WorkerController()
        order = []
        with patch.object(crud, "reset_orphaned_processing", AsyncMock(side_effect=lambda: order.append("reset") or 0)), \
             patch.object(controller, "_run_loop", AsyncMock(side_effect=lambda: order.append("loop"))):
            await controller.recover_orphaned()  # what lifespan does before creating the task
            await controller.start()
        assert order == ["reset", "loop"]


# ─── W3: deduplicated, transactional enqueue ────────────────

class TestEnqueueDeduped:
    async def test_active_duplicate_is_reused_within_and_across_calls(self, db):
        project, video, scene = await _scene()
        item = {"req_type": "GENERATE_IMAGE", "scene_id": scene["id"], "project_id": project["id"],
                "video_id": video["id"], "orientation": "VERTICAL"}
        (first, created1), (second, created2) = await crud.enqueue_deduped([item, dict(item)])
        assert created1 and not created2 and second["id"] == first["id"]
        [(third, created3)] = await crud.enqueue_deduped([item])
        assert not created3 and third["id"] == first["id"]

    async def test_character_requests_are_deduplicated_too(self, db):
        project, _, _ = await _scene()
        char = await crud.create_character(name="Mèo Con")
        item = {"req_type": "GENERATE_CHARACTER_IMAGE", "character_id": char["id"], "project_id": project["id"]}
        (a, _), (b, created) = await crud.enqueue_deduped([item, dict(item)])
        assert b["id"] == a["id"] and not created

    async def test_a_failing_item_inserts_nothing(self, db):
        project, video, scene = await _scene()
        good = {"req_type": "GENERATE_IMAGE", "scene_id": scene["id"], "project_id": project["id"],
                "video_id": video["id"]}
        bad = {"req_type": "NOT_A_TYPE", "scene_id": scene["id"], "project_id": project["id"], "video_id": video["id"]}
        with pytest.raises(Exception):
            await crud.enqueue_deduped([good, bad])
        assert await crud.list_requests(scene_id=scene["id"]) == []

    async def test_http_single_duplicate_is_still_409_and_batch_returns_existing(self, db):
        from agent.main import app
        project, video, scene = await _scene()
        body = {"type": "GENERATE_IMAGE", "scene_id": scene["id"], "project_id": project["id"],
                "video_id": video["id"], "orientation": "VERTICAL"}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as http:
            first = (await http.post("/api/requests", json=body)).json()
            assert (await http.post("/api/requests", json=body)).status_code == 409
            batch = (await http.post("/api/requests/batch", json={"requests": [body, body]})).json()
        assert [r["id"] for r in batch] == [first["id"], first["id"]]


# ─── W4: no claiming during the UNUSUAL_ACTIVITY cooldown ───

class CooldownClient:
    connected = True

    def __init__(self, active: bool):
        self.generation_guard_status = {"cooldown_active": active, "cooldown_remaining_s": 90.0}


class TestCooldownHold:
    async def _one_tick(self, client):
        controller = processor.WorkerController()
        claim = AsyncMock(return_value=[])

        async def stop_after_tick(_seconds):
            controller.request_shutdown()

        with patch.object(processor, "get_flow_client", return_value=client), \
             patch.object(processor.crud, "claim_actionable_requests", claim), \
             patch.object(controller, "_sleep_or_shutdown", side_effect=stop_after_tick):
            await controller._run_loop()
        return claim

    async def test_nothing_is_claimed_while_the_cooldown_is_active(self, db):
        claim = await self._one_tick(CooldownClient(active=True))
        claim.assert_not_called()

    async def test_claiming_resumes_when_the_cooldown_ends(self, db):
        claim = await self._one_tick(CooldownClient(active=False))
        claim.assert_awaited_once()

    async def test_waiting_requests_stay_pending(self, db):
        project, video, scene = await _scene()
        created = await crud.create_request("GENERATE_IMAGE", orientation="VERTICAL", scene_id=scene["id"],
                                            project_id=project["id"], video_id=video["id"])
        controller = processor.WorkerController()

        async def stop_after_tick(_seconds):
            controller.request_shutdown()

        with patch.object(processor, "get_flow_client", return_value=CooldownClient(active=True)), \
             patch.object(controller, "_sleep_or_shutdown", side_effect=stop_after_tick):
            await controller._run_loop()
        assert (await crud.get_request(created["id"]))["status"] == "PENDING"
        assert (await crud.get_scene(scene["id"]))["vertical_image_status"] == "PENDING"

    def test_client_without_a_guard_is_not_in_cooldown(self):
        assert processor.generation_cooldown_active(object()) is False


# ─── W6: not-found recovery ─────────────────────────────────

class TestNotFoundRecovery:
    async def test_poll_timeout_is_a_counted_retry_not_a_media_reupload(self, db):
        req, scene = await _claimed("GENERATE_VIDEO", vertical_image_media_id=IMAGE_ID,
                                    vertical_image_status="COMPLETED")
        recover = AsyncMock(return_value=True)
        with patch.object(processor, "_recover_entity_not_found", recover):
            await processor._handle_failure(req["id"], req, {"error": "Polling timeout after 420s: Media not found."})
        recover.assert_not_called()
        row = await crud.get_request(req["id"])
        assert row["status"] == "PENDING" and row["retry_count"] == 1
        assert (await crud.get_scene(scene["id"]))["vertical_image_media_id"] == IMAGE_ID

    async def test_recovery_is_capped_at_two_per_request(self, db):
        req, scene = await _claimed("GENERATE_VIDEO", vertical_image_media_id=IMAGE_ID,
                                    vertical_image_status="COMPLETED")
        recover = AsyncMock(return_value=True)
        error = {"error": "RpcError: Requested entity was not found."}
        with patch.object(processor, "_recover_entity_not_found", recover):
            for attempt in (1, 2):
                await processor._handle_failure(req["id"], req, error)
                row = await crud.get_request(req["id"])
                assert row["status"] == "PENDING" and row["retry_count"] == 0
                assert row["last_failure_reason"].startswith(f"not_found_recovery={attempt};")
                [req] = await crud.claim_actionable_requests(limit=1)
            await processor._handle_failure(req["id"], req, error)

        assert recover.await_count == 2
        row = await crud.get_request(req["id"])
        assert row["status"] == "FAILED"
        assert row["error_message"].startswith("not found after 2 media re-upload recoveries")
        assert (await crud.get_scene(scene["id"]))["vertical_video_status"] == "FAILED"
