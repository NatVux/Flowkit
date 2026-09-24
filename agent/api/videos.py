from fastapi import APIRouter, HTTPException
from agent.models.video import Video, VideoCreate, VideoUpdate, YouTubeUploadRequest, YouTubeActionResponse
from agent.sdk.persistence.sqlite_repository import SQLiteRepository
from dataclasses import asdict
from agent.services.youtube_publisher import YouTubePublisher, YouTubeClient, YouTubeAuthError, YouTubeUploadError

router = APIRouter(prefix="/videos", tags=["videos"])

_repo = SQLiteRepository()


def _video_to_flat(sdk_video) -> dict:
    """Convert SDK Video domain model to flat dict matching API response shape."""
    return {
        "id": sdk_video.id,
        "project_id": sdk_video.project_id,
        "title": sdk_video.title,
        "description": sdk_video.description,
        "display_order": sdk_video.display_order,
        "status": sdk_video.status,
        "orientation": sdk_video.orientation,
        "vertical_url": sdk_video.vertical_url,
        "horizontal_url": sdk_video.horizontal_url,
        "thumbnail_url": sdk_video.thumbnail_url,
        "duration": sdk_video.duration,
        "resolution": sdk_video.resolution,
        "youtube_id": sdk_video.youtube_id,
        "upload_url": sdk_video.upload_url,
        "youtube_upload_status": sdk_video.youtube_upload_status,
        "youtube_upload_error": sdk_video.youtube_upload_error,
        "youtube_upload_attempts": sdk_video.youtube_upload_attempts,
        "youtube_uploaded_at": sdk_video.youtube_uploaded_at,
        "youtube_publish_status": sdk_video.youtube_publish_status,
        "youtube_publish_error": sdk_video.youtube_publish_error,
        "youtube_published_at": sdk_video.youtube_published_at,
        "privacy": sdk_video.privacy,
        "tags": sdk_video.tags,
        "created_at": sdk_video.created_at,
        "updated_at": sdk_video.updated_at,
    }


@router.post("", response_model=Video)
async def create(body: VideoCreate):
    sdk_video = await _repo.create_video(**body.model_dump(exclude_none=True))
    return _video_to_flat(sdk_video)


@router.get("", response_model=list[Video])
async def list_by_project(project_id: str):
    videos = await _repo.list_videos(project_id)
    return [_video_to_flat(v) for v in videos]


@router.get("/{vid}", response_model=Video)
async def get(vid: str):
    sdk_video = await _repo.get_video(vid)
    if not sdk_video:
        raise HTTPException(404, "Video not found")
    return _video_to_flat(sdk_video)


@router.patch("/{vid}", response_model=Video)
async def update(vid: str, body: VideoUpdate):
    row = await _repo.update("video", vid, **body.model_dump(exclude_unset=True))
    if not row:
        raise HTTPException(404, "Video not found")
    sdk_video = _repo._row_to_video(row)
    return _video_to_flat(sdk_video)


@router.delete("/{vid}")
async def delete(vid: str):
    if not await _repo.delete("video", vid):
        raise HTTPException(404, "Video not found")
    return {"ok": True}


_youtube_client: YouTubeClient | None = None


def set_youtube_client(client: YouTubeClient | None) -> None:
    global _youtube_client
    _youtube_client = client


def _youtube_publisher() -> YouTubePublisher:
    if _youtube_client is None:
        raise HTTPException(503, "YouTube publishing is not configured")
    return YouTubePublisher(_youtube_client)


@router.post("/{vid}/youtube/upload", response_model=YouTubeActionResponse)
async def youtube_upload(vid: str, body: YouTubeUploadRequest):
    try:
        return await _youtube_publisher().upload(vid, **body.model_dump())
    except YouTubeAuthError as exc:
        raise HTTPException(401, str(exc)) from exc
    except YouTubeUploadError as exc:
        raise HTTPException(502, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/{vid}/youtube/publish", response_model=YouTubeActionResponse)
async def youtube_publish(vid: str):
    try:
        return await _youtube_publisher().publish(vid)
    except YouTubeAuthError as exc:
        raise HTTPException(401, str(exc)) from exc
    except YouTubeUploadError as exc:
        raise HTTPException(502, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
