"""Mocked YouTube publishing state tests; no credentials or network."""

from pathlib import Path

import pytest

from agent.db import crud, schema
from agent.services.youtube_publisher import (
    YouTubeAuthError,
    YouTubePublisher,
    YouTubeUploadError,
    validate_metadata,
)


class FakeYouTube:
    def __init__(self):
        self.upload_calls = []
        self.publish_calls = []
        self.upload_result = {"video_id": "yt-123", "url": "https://youtube.test/watch?v=yt-123"}
        self.publish_result = {"status": "published"}
        self.upload_error = None
        self.publish_error = None

    async def upload_video(self, **kwargs):
        self.upload_calls.append(kwargs)
        if self.upload_error:
            raise self.upload_error
        return self.upload_result

    async def publish_video(self, remote_video_id):
        self.publish_calls.append(remote_video_id)
        if self.publish_error:
            raise self.publish_error
        return self.publish_result


@pytest.fixture
async def youtube_db(tmp_path, monkeypatch):
    await schema.close_db()
    monkeypatch.setattr(schema, "DB_PATH", tmp_path / "youtube.db")
    await schema.init_db()
    yield tmp_path
    await schema.close_db()


@pytest.mark.asyncio
async def test_upload_persists_remote_id_and_metadata(youtube_db):
    project = await crud.create_project(name="YouTube project")
    video = await crud.create_video(project_id=project["id"], title="Local video")
    source = youtube_db / "video with spaces.mp4"
    thumbnail = youtube_db / "thumb.png"
    source.write_bytes(b"video")
    thumbnail.write_bytes(b"image")
    client = FakeYouTube()

    result = await YouTubePublisher(client).upload(
        video["id"], source_path=str(source), title="Title", description="Description",
        tags=["one", "two"], privacy="unlisted", thumbnail_path=str(thumbnail),
    )

    assert result["remote_video_id"] == "yt-123"
    assert len(client.upload_calls) == 1
    stored = await crud.get_video(video["id"])
    assert stored["youtube_id"] == "yt-123"
    assert stored["youtube_upload_status"] == "UPLOADED"
    assert stored["youtube_uploaded_at"]


@pytest.mark.asyncio
async def test_same_upload_is_idempotent(youtube_db):
    project = await crud.create_project(name="Idempotent project")
    video = await crud.create_video(project_id=project["id"], title="Video")
    source = youtube_db / "video.mp4"
    source.write_bytes(b"video")
    client = FakeYouTube()
    publisher = YouTubePublisher(client)
    kwargs = {"source_path": str(source), "title": "Title", "description": "", "tags": [], "privacy": "private"}

    await publisher.upload(video["id"], **kwargs)
    second = await publisher.upload(video["id"], **kwargs)

    assert second["idempotent"] is True
    assert len(client.upload_calls) == 1


@pytest.mark.asyncio
async def test_auth_expiration_is_persisted_separately(youtube_db):
    project = await crud.create_project(name="Auth project")
    video = await crud.create_video(project_id=project["id"], title="Video")
    source = youtube_db / "video.mp4"
    source.write_bytes(b"video")
    client = FakeYouTube()
    client.upload_error = YouTubeAuthError("expired")

    with pytest.raises(YouTubeAuthError):
        await YouTubePublisher(client).upload(
            video["id"], source_path=str(source), title="Title", description="", tags=[],
        )
    stored = await crud.get_video(video["id"])
    assert stored["youtube_upload_status"] == "AUTH_EXPIRED"
    assert stored["youtube_id"] is None


@pytest.mark.asyncio
async def test_publish_failure_does_not_erase_successful_upload(youtube_db):
    project = await crud.create_project(name="Publish project")
    video = await crud.create_video(project_id=project["id"], title="Video")
    source = youtube_db / "video.mp4"
    source.write_bytes(b"video")
    client = FakeYouTube()
    publisher = YouTubePublisher(client)
    await publisher.upload(video["id"], source_path=str(source), title="Title", description="", tags=[])
    client.publish_error = YouTubeUploadError("publish failed")

    with pytest.raises(YouTubeUploadError):
        await publisher.publish(video["id"])
    stored = await crud.get_video(video["id"])
    assert stored["youtube_id"] == "yt-123"
    assert stored["youtube_upload_status"] == "UPLOADED"
    assert stored["youtube_publish_status"] == "FAILED"


def test_youtube_metadata_validation():
    with pytest.raises(ValueError):
        validate_metadata("", "", [], "private")
    with pytest.raises(ValueError):
        validate_metadata("Title", "", [], "invalid")
    with pytest.raises(ValueError):
        validate_metadata("Title", "", [""], "private")
