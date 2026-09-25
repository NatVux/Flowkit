"""Server-side port of /fk-concat for the pipeline runner.

Per scene: normalise to one size / 24 fps / H.264 + AAC, then join with the concat
demuxer (stream copy). Fixes over the skill's shell commands:
  1. every clip gets the same scale + pad (the skill's no-TTS-wav branch only scaled,
     stretching clips of another aspect);
  2. one -filter_complex per command, never combined with -vf;
  3. a clip without an audio stream gets silent stereo audio, so the concat demuxer
     sees identical streams in every file (and `[0:a]` never fails).
Command builders are pure; running them goes through media_process.run_media_command.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from agent.services.media_process import run_media_command

FPS = 24
AUDIO_RATE = 48000
NORMALIZE_TIMEOUT = 300
CONCAT_TIMEOUT = 600


class ConcatError(RuntimeError):
    pass


@dataclass(frozen=True)
class Probe:
    width: int
    height: int
    has_audio: bool
    duration: float


def probe_command(path: Path) -> list[str]:
    return ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
            "-of", "json", str(path)]


def parse_probe(output: str) -> Probe:
    data = json.loads(output or "{}")
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video:
        raise ConcatError("no video stream")
    duration = float((data.get("format") or {}).get("duration") or 0)
    return Probe(int(video["width"]), int(video["height"]),
                 any(s.get("codec_type") == "audio" for s in streams), duration)


def probe(path: Path) -> Probe:
    result = run_media_command(probe_command(path), timeout=60)
    if not result.ok:
        raise ConcatError(f"ffprobe failed for {path.name}: {result.error}")
    return parse_probe(result.stdout)


def normalize_command(src: Path, dst: Path, width: int, height: int, has_audio: bool) -> list[str]:
    """One -filter_complex holds every filter; no -vf anywhere."""
    video = (f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
             f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS},format=yuv420p[v]")
    cmd = ["ffmpeg", "-y", "-i", str(src)]
    if has_audio:
        graph, audio_map = f"{video};[0:a:0]aresample={AUDIO_RATE}[a]", "[a]"
    else:
        cmd += ["-f", "lavfi", "-i", f"anullsrc=channel_layout=stereo:sample_rate={AUDIO_RATE}"]
        graph, audio_map = video, "1:a:0"
    cmd += ["-filter_complex", graph, "-map", "[v]", "-map", audio_map,
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k", "-ar", str(AUDIO_RATE), "-ac", "2"]
    if not has_audio:
        cmd.append("-shortest")  # the silent source is endless
    # run_media_command writes to a .tmp name first, so the container is named explicitly.
    cmd += ["-movflags", "+faststart", "-f", "mp4", str(dst)]
    return cmd


def concat_list_text(paths: list[Path]) -> str:
    """ffconcat list: absolute paths, forward slashes, single quotes escaped."""
    lines = []
    for p in paths:
        escaped = str(p.resolve()).replace("\\", "/").replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    return "\n".join(lines) + "\n"


def concat_command(list_file: Path, dst: Path) -> list[str]:
    return ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-c", "copy", "-movflags", "+faststart", "-f", "mp4", str(dst)]


def run_concat(clips: list[Path], out: Path, workdir: Path) -> dict:
    """Blocking: normalise each clip into workdir/norm, join them into `out`, verify.

    The target size is the first clip's (upscale is out of scope, so nothing is 4K).
    Returns {"path", "duration", "width", "height", "clips"}.
    """
    if not clips:
        raise ConcatError("no clips to join")
    first = probe(clips[0])
    width, height = first.width - first.width % 2, first.height - first.height % 2
    norm_dir = workdir / "norm"
    norm_dir.mkdir(parents=True, exist_ok=True)
    normalized, expected = [], 0.0
    for clip in clips:
        info = probe(clip)
        dst = norm_dir / clip.name
        result = run_media_command(normalize_command(clip, dst, width, height, info.has_audio),
                                   timeout=NORMALIZE_TIMEOUT, output_path=dst)
        if not result.ok:
            raise ConcatError(f"normalising {clip.name} failed: {result.error}")
        normalized.append(dst)
        expected += probe(dst).duration
    list_file = workdir / "concat.txt"
    list_file.write_text(concat_list_text(normalized), encoding="utf-8")
    result = run_media_command(concat_command(list_file, out), timeout=CONCAT_TIMEOUT, output_path=out)
    if not result.ok:
        raise ConcatError(f"joining clips failed: {result.error}")
    final = probe(out)
    if not final.has_audio:
        raise ConcatError("final video has no audio stream")
    if abs(final.duration - expected) > max(1.0, 0.05 * expected):
        raise ConcatError(f"final duration {final.duration:.1f}s does not match the clips' {expected:.1f}s")
    return {"path": str(out), "duration": round(final.duration, 2), "width": final.width,
            "height": final.height, "clips": len(normalized)}
