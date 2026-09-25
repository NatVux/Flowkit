"""Scene video downloads for the pipeline runner (existing helpers, verified with ffprobe)."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from agent import config
from agent.services.media_process import probe_duration
from agent.services.video_reviewer import _download_via_get_media, _download_video
from agent.utils.paths import scene_filename
from agent.utils.slugify import slugify

# Module attributes so tests can swap the network side.
fetch_url = _download_video
fetch_fresh = _download_via_get_media
measure = probe_duration


class DownloadError(RuntimeError):
    pass


def project_output_dir(project: dict) -> Path:
    return config.OUTPUT_DIR / slugify(project["name"])


def scene_video_file(project: dict, scene: dict) -> Path:
    """output/<slug>/scenes/scene_NNN_<id>.mp4, the layout fk-concat and the review use."""
    return project_output_dir(project) / "scenes" / scene_filename(scene["display_order"], scene["id"])


def final_video_file(project: dict, video_id: str) -> Path:
    slug = slugify(project["name"])
    return project_output_dir(project) / f"{slug}_{video_id[:8]}_final.mp4"


async def valid_video(path: Path) -> bool:
    """A real clip, not a saved 4 KB XML error page from an expired signed URL."""
    if not path.is_file() or path.stat().st_size == 0:
        return False
    try:
        return await asyncio.to_thread(measure, path) > 0
    except Exception:
        return False


async def download_scene_video(scene: dict, p: str, dest: Path) -> Path:
    """One download attempt: the stored signed URL, then a freshly signed one from the
    media record. Written to a .part file and published only once ffprobe accepts it."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    url, media_id = scene.get(f"{p}_video_url"), scene.get(f"{p}_video_media_id")
    sources = [("stored url", url and (lambda: fetch_url(url, part))),
               ("fresh url", media_id and (lambda: fetch_fresh(media_id, part)))]
    errors = []
    try:
        for label, fetch in sources:
            if not fetch:
                continue
            try:
                await fetch()
                if await valid_video(part):
                    os.replace(part, dest)
                    return dest
                errors.append(f"{label}: not a readable video")
            except Exception as exc:  # network, expired URL, get_media error
                errors.append(f"{label}: {exc}")
    finally:
        part.unlink(missing_ok=True)
    raise DownloadError("; ".join(errors) or "scene has no video url or media id")
