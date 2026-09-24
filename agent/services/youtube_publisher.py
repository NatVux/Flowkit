"""Safe, adapter-based YouTube publishing workflow.

The Google API client is intentionally injected. Production OAuth/upload wiring can
implement YouTubeClient without coupling state management to credentials.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from agent.db import crud

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class YouTubeAuthError(RuntimeError):
    pass


class YouTubeUploadError(RuntimeError):
    pass


class YouTubeClient(Protocol):
    async def upload_video(self, *, source_path: str, title: str, description: str, tags: list[str], privacy: str, thumbnail_path: str | None = None, resumable_uri: str | None = None) -> dict[str, Any]: ...
    async def publish_video(self, remote_video_id: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class PublishMetadata:
    title: str
    description: str
    tags: list[str]
    privacy: str


def validate_metadata(title: str, description: str, tags: list[str], privacy: str) -> PublishMetadata:
    title = title.strip()
    description = description.strip()
    if not 1 <= len(title) <= 100:
        raise ValueError("YouTube title must contain 1-100 characters")
    if len(description) > 5000:
        raise ValueError("YouTube description must be at most 5000 characters")
    if privacy not in {"private", "unlisted", "public"}:
        raise ValueError("YouTube privacy must be private, unlisted, or public")
    if len(tags) > 500:
        raise ValueError("YouTube tags are limited to 500 values")
    if any(not isinstance(tag, str) or not tag.strip() or len(tag) > 100 for tag in tags):
        raise ValueError("YouTube tags must be non-empty strings of at most 100 characters")
    return PublishMetadata(title, description, [tag.strip() for tag in tags], privacy)


def validate_media_file(path: str, label: str = "video") -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file() or resolved.stat().st_size == 0:
        raise ValueError(f"{label} file is missing or empty")
    return resolved


def upload_key(video_id: str, source_path: Path, metadata: PublishMetadata) -> str:
    material = f"{video_id}|{source_path.resolve()}|{source_path.stat().st_size}|{metadata.title}|{metadata.privacy}"
    return hashlib.sha256(material.encode()).hexdigest()


class YouTubePublisher:
    def __init__(self, client: YouTubeClient):
        self.client = client

    async def upload(self, video_id: str, *, source_path: str, title: str, description: str, tags: list[str], privacy: str = "unlisted", thumbnail_path: str | None = None) -> dict:
        video = await crud.get_video(video_id)
        if not video:
            raise ValueError("Video not found")
        metadata = validate_metadata(title, description, tags, privacy)
        source = validate_media_file(source_path, "video")
        thumbnail = validate_media_file(thumbnail_path, "thumbnail") if thumbnail_path else None
        key = upload_key(video_id, source, metadata)

        if video.get("youtube_id") and video.get("youtube_upload_key") == key:
            return {"status": "UPLOADED", "remote_video_id": video["youtube_id"], "idempotent": True}
        await crud.update_video(video_id, youtube_upload_status="UPLOADING", youtube_upload_key=key, youtube_upload_error=None, youtube_upload_attempts=(video.get("youtube_upload_attempts") or 0) + 1)
        try:
            result = await self.client.upload_video(
                source_path=str(source), title=metadata.title, description=metadata.description,
                tags=metadata.tags, privacy=metadata.privacy,
                thumbnail_path=str(thumbnail) if thumbnail else None,
                resumable_uri=video.get("youtube_resumable_uri"),
            )
        except YouTubeAuthError:
            await crud.update_video(video_id, youtube_upload_status="AUTH_EXPIRED", youtube_upload_error="YouTube authentication expired")
            raise
        except Exception as exc:
            await crud.update_video(video_id, youtube_upload_status="FAILED", youtube_upload_error=type(exc).__name__)
            raise YouTubeUploadError("YouTube upload failed") from exc

        remote_id = result.get("video_id")
        if not remote_id:
            await crud.update_video(video_id, youtube_upload_status="FAILED", youtube_upload_error="provider_missing_video_id")
            raise YouTubeUploadError("YouTube upload returned no video id")
        await crud.update_video(video_id, youtube_id=remote_id, youtube_upload_status="UPLOADED", youtube_upload_error=None, youtube_resumable_uri=result.get("resumable_uri"), upload_url=result.get("url"), youtube_uploaded_at=_now())
        return {"status": "UPLOADED", "remote_video_id": remote_id, "idempotent": False}

    async def publish(self, video_id: str) -> dict:
        video = await crud.get_video(video_id)
        if not video or not video.get("youtube_id"):
            raise ValueError("Video must be uploaded before publishing")
        if video.get("youtube_publish_status") == "PUBLISHED":
            return {"status": "PUBLISHED", "remote_video_id": video["youtube_id"], "idempotent": True}
        await crud.update_video(video_id, youtube_publish_status="PUBLISHING", youtube_publish_error=None)
        try:
            result = await self.client.publish_video(video["youtube_id"])
        except YouTubeAuthError:
            await crud.update_video(video_id, youtube_publish_status="AUTH_EXPIRED", youtube_publish_error="YouTube authentication expired")
            raise
        except Exception as exc:
            await crud.update_video(video_id, youtube_publish_status="FAILED", youtube_publish_error=type(exc).__name__)
            raise YouTubeUploadError("YouTube publish failed") from exc
        await crud.update_video(video_id, youtube_publish_status="PUBLISHED", youtube_publish_error=None, youtube_published_at=_now())
        return {"status": "PUBLISHED", "remote_video_id": video["youtube_id"], "idempotent": False}
