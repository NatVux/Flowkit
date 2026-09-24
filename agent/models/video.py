from pydantic import BaseModel
from typing import Optional
from agent.models.enums import VideoStatus


class VideoCreate(BaseModel):
    project_id: str
    title: str
    description: Optional[str] = None
    display_order: int = 0
    orientation: Optional[str] = None


class VideoUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    display_order: Optional[int] = None
    status: Optional[VideoStatus] = None
    orientation: Optional[str] = None
    vertical_url: Optional[str] = None
    horizontal_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    duration: Optional[float] = None
    resolution: Optional[str] = None
    youtube_id: Optional[str] = None
    privacy: Optional[str] = None
    tags: Optional[str] = None


class YouTubeUploadRequest(BaseModel):
    source_path: str
    title: str
    description: str = ""
    tags: list[str] = []
    privacy: str = "unlisted"
    thumbnail_path: Optional[str] = None


class YouTubeActionResponse(BaseModel):
    status: str
    remote_video_id: Optional[str] = None
    idempotent: bool = False
    error: Optional[str] = None


class Video(BaseModel):
    id: str
    project_id: str
    title: str
    description: Optional[str] = None
    display_order: int = 0
    status: str = "DRAFT"
    orientation: Optional[str] = None
    vertical_url: Optional[str] = None
    horizontal_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    duration: Optional[float] = None
    resolution: Optional[str] = None
    youtube_id: Optional[str] = None
    upload_url: Optional[str] = None
    youtube_upload_status: str = "NOT_STARTED"
    youtube_upload_error: Optional[str] = None
    youtube_upload_attempts: int = 0
    youtube_uploaded_at: Optional[str] = None
    youtube_publish_status: str = "NOT_PUBLISHED"
    youtube_publish_error: Optional[str] = None
    youtube_published_at: Optional[str] = None
    privacy: str = "unlisted"
    tags: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
