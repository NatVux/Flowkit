"""Post-processing: trim, merge, add music via ffmpeg."""
import logging
from pathlib import Path

from agent.services.media_process import (
    probe_duration, run_media_command, validate_input_file, validate_media_output,
)

logger = logging.getLogger(__name__)

_FLOAT_MIN = 0.0
_FLOAT_MAX = 2.0


def _clamp_float(value: float, name: str, lo: float = _FLOAT_MIN, hi: float = _FLOAT_MAX) -> float:
    """Clamp float to [lo, hi] and warn if out of bounds."""
    if value < lo or value > hi:
        logger.warning("Parameter %s=%s out of bounds [%s, %s], clamping", name, value, lo, hi)
        return max(lo, min(hi, value))
    return value


def trim_video(input_path: str, output_path: str, start: float, end: float) -> bool:
    """Trim video to [start, end] seconds."""
    try:
        validate_input_file(input_path, "trim input")
    except (FileNotFoundError, ValueError) as exc:
        logger.error("trim_video: %s", exc)
        return False
    duration = end - start
    if duration <= 0:
        logger.error("trim_video: end must be greater than start")
        return False
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-ss", str(start), "-t", str(duration),
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-force_key_frames", "expr:gte(t,0)",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]
    result = run_media_command(cmd, timeout=120, output_path=output_path)
    if not result.ok:
        logger.error("Trim failed: %s", result.error)
        return False
    try:
        validate_media_output(output_path, "trim output")
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        logger.error("Trim output invalid: %s", exc)
        return False
    return True


def merge_videos(video_paths: list[str], output_path: str) -> bool:
    """Concatenate videos using ffmpeg concat demuxer."""
    if not video_paths:
        logger.error("merge_videos: no input videos")
        return False
    try:
        for path in video_paths:
            validate_input_file(path, "merge input")
    except (FileNotFoundError, ValueError) as exc:
        logger.error("merge_videos: %s", exc)
        return False
    concat_file = str(Path(output_path).with_name(f".{Path(output_path).name}.{__import__('uuid').uuid4().hex}.concat.txt"))
    try:
        with open(concat_file, "w") as f:
            for p in video_paths:
                # Escape single quotes to prevent path injection in concat file
                escaped = str(Path(p).resolve()).replace("\\", "/").replace("'", "'\\''").replace("\n", "")
                f.write(f"file '{escaped}'\n")

        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", concat_file,
            "-c:v", "copy", "-c:a", "copy",
            "-movflags", "+faststart",
            output_path,
        ]
        result = run_media_command(cmd, timeout=120, output_path=output_path)
    finally:
        Path(concat_file).unlink(missing_ok=True)
    if not result.ok:
        logger.error("Merge failed: %s", result.error)
        return False
    try:
        validate_media_output(output_path, "merge output")
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        logger.error("Merge output invalid: %s", exc)
        return False
    return True


def add_narration(video_path: str, narration_path: str, output_path: str,
                  narration_volume: float = 1.0, sfx_volume: float = 0.4,
                  fade_in: float = 0.5, fade_out: float = 0.5) -> bool:
    """Overlay narration audio on video, ducking the existing SFX track."""
    try:
        validate_input_file(video_path, "video input")
        validate_input_file(narration_path, "narration input")
    except (FileNotFoundError, ValueError) as exc:
        logger.error("add_narration: %s", exc)
        return False

    # Clamp float params to prevent filter injection
    narration_volume = _clamp_float(narration_volume, "narration_volume")
    sfx_volume = _clamp_float(sfx_volume, "sfx_volume")
    fade_in = _clamp_float(fade_in, "fade_in")
    fade_out = _clamp_float(fade_out, "fade_out")

    try:
        duration = probe_duration(video_path)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        logger.error("ffprobe failed for %s: %s", video_path, exc)
        return False
    fade_start = max(0, duration - fade_out)

    cmd = [
        "ffmpeg", "-y", "-i", video_path, "-i", narration_path,
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-filter_complex",
        f"[0:a]volume={sfx_volume}[sfx];[1:a]volume={narration_volume},afade=t=in:st=0:d={fade_in},afade=t=out:st={fade_start}:d={fade_out}[narr];[sfx][narr]amerge=inputs=2,pan=stereo|c0=c0+c2|c1=c1+c3[aout]",
        "-map", "0:v", "-map", "[aout]",
        "-shortest",
        "-movflags", "+faststart",
        output_path,
    ]
    result = run_media_command(cmd, timeout=120, output_path=output_path)
    if not result.ok:
        logger.error("Add narration failed: %s", result.error)
        return False
    try:
        validate_media_output(output_path, "narration output")
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        logger.error("Narration output invalid: %s", exc)
        return False
    return True


def add_music(video_path: str, music_path: str, output_path: str,
              music_volume: float = 0.3, fade_in: float = 2.0, fade_out: float = 3.0) -> bool:
    """Overlay background music on video."""
    try:
        validate_input_file(video_path, "video input")
        validate_input_file(music_path, "music input")
    except (FileNotFoundError, ValueError) as exc:
        logger.error("add_music: %s", exc)
        return False

    # Clamp float params to prevent filter injection
    music_volume = _clamp_float(music_volume, "music_volume")
    fade_in = _clamp_float(fade_in, "fade_in")
    fade_out = _clamp_float(fade_out, "fade_out")

    try:
        duration = probe_duration(video_path)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        logger.error("ffprobe failed for %s: %s", video_path, exc)
        return False
    fade_start = max(0, duration - fade_out)

    cmd = [
        "ffmpeg", "-y", "-i", video_path, "-i", music_path,
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-filter_complex",
        f"[0:a]volume=1.0[orig];[1:a]volume={music_volume},afade=t=in:st=0:d={fade_in},afade=t=out:st={fade_start}:d={fade_out}[music];[orig][music]amerge=inputs=2,pan=stereo|c0=c0+c2|c1=c1+c3[aout]",
        "-map", "0:v", "-map", "[aout]",
        "-shortest",
        "-movflags", "+faststart",
        output_path,
    ]
    result = run_media_command(cmd, timeout=120, output_path=output_path)
    if not result.ok:
        logger.error("Add music failed: %s", result.error)
        return False
    try:
        validate_media_output(output_path, "music output")
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        logger.error("Music output invalid: %s", exc)
        return False
    return True
