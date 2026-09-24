"""Mocked end-to-end logical pipeline test.

No Google Flow, YouTube, browser, or TTS model is contacted.
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from agent.db import crud, schema
from agent.sdk.services import result_handler
from agent.worker import processor


@pytest.fixture
async def pipeline_db(tmp_path, monkeypatch):
    await schema.close_db()
    monkeypatch.setattr(schema, "DB_PATH", tmp_path / "pipeline.db")
    await schema.init_db()
    yield tmp_path
    await schema.close_db()


@pytest.mark.asyncio
async def test_project_to_final_output_with_mocked_external_services(pipeline_db):
    project = await crud.create_project(name="E2E project")
    video = await crud.create_video(project_id=project["id"], title="E2E video")
    scene = await crud.create_scene(video_id=video["id"], display_order=0, prompt="A hero walks")

    image_id = "11111111-1111-4111-8111-111111111111"
    video_id = "22222222-2222-4222-8222-222222222222"
    image_result = {"data": {"media": [{"name": image_id, "image": {"generatedImage": {
        "mediaId": image_id, "fifeUrl": "https://example.test/image.jpg",
    }}}]}}
    video_result = {"data": {"operations": [{"status": "MEDIA_GENERATION_STATUS_SUCCESSFUL",
        "operation": {"metadata": {"video": {"mediaId": video_id, "fifeUrl": "https://example.test/video.mp4"}}},
    }]}}

    image_request = await crud.create_request(
        req_type="GENERATE_IMAGE", project_id=project["id"], video_id=video["id"], scene_id=scene["id"],
    )
    claimed_image = (await crud.claim_actionable_requests(limit=1))[0]
    with patch.object(processor, "_dispatch", new=AsyncMock(return_value=image_result)):
        await processor._process_one(claimed_image, {}, {})

    current_scene = await crud.get_scene(scene["id"])
    assert current_scene["vertical_image_status"] == "COMPLETED"
    assert current_scene["vertical_image_media_id"] == image_id
    assert (await crud.get_request(image_request["id"]))["status"] == "COMPLETED"

    video_request = await crud.create_request(
        req_type="GENERATE_VIDEO", project_id=project["id"], video_id=video["id"], scene_id=scene["id"],
    )
    claimed_video = (await crud.claim_actionable_requests(limit=1))[0]
    with patch.object(processor, "_dispatch", new=AsyncMock(return_value=video_result)):
        await processor._process_one(claimed_video, {}, {})

    current_scene = await crud.get_scene(scene["id"])
    assert current_scene["vertical_video_status"] == "COMPLETED"
    assert current_scene["vertical_video_media_id"] == video_id
    assert (await crud.get_request(video_request["id"]))["status"] == "COMPLETED"

    audio = pipeline_db / "scene.wav"
    final = pipeline_db / "scene-final.mp4"
    audio.write_bytes(b"mock wav")
    final.write_bytes(b"mock final")
    await crud.update_scene(
        scene["id"],
        narration_audio_path=str(audio), narration_audio_status="COMPLETED",
        narration_mix_path=str(final), narration_mix_status="COMPLETED",
    )
    persisted = await crud.get_scene(scene["id"])
    assert persisted["narration_audio_status"] == "COMPLETED"
    assert persisted["narration_mix_status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_pipeline_timeout_failure_and_retry_are_persisted(pipeline_db):
    project = await crud.create_project(name="Failure project")
    video = await crud.create_video(project_id=project["id"], title="Failure video")
    scene = await crud.create_scene(video_id=video["id"], display_order=0, prompt="Failure scene")
    request = await crud.create_request(
        req_type="GENERATE_IMAGE", project_id=project["id"], video_id=video["id"], scene_id=scene["id"],
    )
    claimed = (await crud.claim_actionable_requests(limit=1))[0]
    with patch.object(processor, "_dispatch", new=AsyncMock(return_value={"error": "temporary timeout"})):
        await processor._process_one(claimed, {}, {})

    failed_attempt = await crud.get_request(request["id"])
    assert failed_attempt["status"] == "PENDING"
    assert failed_attempt["retry_count"] == 1
    assert failed_attempt["last_failure_reason"] == "temporary timeout"
