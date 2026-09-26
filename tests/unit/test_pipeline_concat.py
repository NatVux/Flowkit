"""Concat port: pure command builders (always run) and real ffmpeg on lavfi-generated
clips (skipped when ffmpeg cannot be found). No Flow, no network."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from agent.db import crud, pipeline_crud as pc, schema
from agent.services.pipeline import concat as pconcat, media as pmedia
from agent.services.pipeline.runner import PipelineRunner

# ─── command builders ───────────────────────────────────────

SRC, DST = Path("in.mp4"), Path("out.mp4")


def _video_graph(cmd):
    return cmd[cmd.index("-filter_complex") + 1].split(";")[0]


class TestCommands:
    @pytest.mark.parametrize("has_audio", [True, False])
    def test_one_filter_complex_never_vf_and_explicit_container(self, has_audio):
        cmd = pconcat.normalize_command(SRC, DST, 720, 1280, has_audio)
        assert cmd.count("-filter_complex") == 1 and "-vf" not in cmd and "-af" not in cmd
        assert cmd[-3:] == ["-f", "mp4", str(DST)]
        assert cmd[cmd.index("-c:a") + 1] == "aac" and cmd[cmd.index("-ar") + 1] == "48000"

    def test_every_branch_gets_the_same_scale_and_pad(self):
        with_audio = _video_graph(pconcat.normalize_command(SRC, DST, 720, 1280, True))
        without = _video_graph(pconcat.normalize_command(SRC, DST, 720, 1280, False))
        assert with_audio == without
        assert "force_original_aspect_ratio=decrease" in with_audio and "pad=720:1280:(ow-iw)/2:(oh-ih)/2" in with_audio

    def test_clip_without_audio_gets_silent_stereo(self):
        cmd = pconcat.normalize_command(SRC, DST, 720, 1280, False)
        assert "anullsrc=channel_layout=stereo:sample_rate=48000" in cmd
        assert cmd[cmd.index("-map", cmd.index("-map") + 1) + 1] == "1:a:0" and "-shortest" in cmd
        assert "[0:a" not in cmd[cmd.index("-filter_complex") + 1]

    def test_clip_with_audio_maps_its_own_track(self):
        cmd = pconcat.normalize_command(SRC, DST, 720, 1280, True)
        assert "anullsrc" not in " ".join(cmd) and "[0:a:0]aresample=48000[a]" in cmd[cmd.index("-filter_complex") + 1]

    def test_concat_list_escapes_and_keeps_unicode(self, tmp_path):
        clip = tmp_path / "Mèo Con's" / "scene_000_x.mp4"
        text = pconcat.concat_list_text([clip])
        assert text.startswith("file '") and "Mèo Con'\\''s" in text and "\\" not in text.replace("'\\''", "")

    def test_parse_probe(self):
        out = '{"streams":[{"codec_type":"video","width":720,"height":1280},{"codec_type":"audio"}],' \
              '"format":{"duration":"8.0"}}'
        assert pconcat.parse_probe(out) == pconcat.Probe(720, 1280, True, 8.0)
        with pytest.raises(pconcat.ConcatError):
            pconcat.parse_probe('{"streams":[{"codec_type":"audio"}],"format":{}}')


# ─── real ffmpeg ────────────────────────────────────────────

def _ffmpeg_dir():
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return ""
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    for exe in base.glob("*FFmpeg*/**/bin/ffmpeg.exe") if base.is_dir() else []:
        return str(exe.parent)
    return None


@pytest.fixture
def ffmpeg(monkeypatch):
    found = _ffmpeg_dir()
    if found is None:
        pytest.skip("ffmpeg not found")
    if found:
        monkeypatch.setenv("PATH", found + os.pathsep + os.environ.get("PATH", ""))


def make_clip(path: Path, size: str, *, audio: bool, seconds: float = 1.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=24:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "aac"]
    subprocess.run(cmd + ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", str(path)], check=True)
    return path


class TestRealConcat:
    def test_mixed_sizes_and_a_silent_clip_join_into_one_file(self, ffmpeg, tmp_path):
        work = tmp_path / "Mèo Con" / "concat"  # Vietnamese path through the UTF-8 list file
        clips = [make_clip(tmp_path / "a.mp4", "720x1280", audio=True),
                 make_clip(tmp_path / "b.mp4", "640x480", audio=False),   # other aspect, no audio
                 make_clip(tmp_path / "c.mp4", "720x1280", audio=True)]
        out = tmp_path / "Mèo Con" / "final.mp4"
        result = pconcat.run_concat(clips, out, work)
        assert (result["width"], result["height"], result["clips"]) == (720, 1280, 3)
        assert abs(result["duration"] - 3.0) < 0.5
        final = pconcat.probe(out)
        assert final.has_audio and (final.width, final.height) == (720, 1280)
        silent = pconcat.probe(work / "norm" / "b.mp4")
        assert silent.has_audio and (silent.width, silent.height) == (720, 1280)  # padded, not stretched
        assert (work / "concat.txt").read_text(encoding="utf-8").count("file '") == 3

    def test_unreadable_clip_is_a_concat_error(self, ffmpeg, tmp_path):
        bad = tmp_path / "bad.mp4"
        bad.write_bytes(b"<?xml version='1.0'?><Error>ExpiredToken</Error>")
        with pytest.raises(pconcat.ConcatError):
            pconcat.run_concat([bad], tmp_path / "out.mp4", tmp_path / "w")


# ─── runner: downloads + concat stage ───────────────────────

class FakeClient:
    connected = True
    generation_guard_status = {"cooldown_active": False}


@pytest.fixture
async def video_ready(tmp_path, monkeypatch, ffmpeg):
    """Two scenes whose videos are already COMPLETED in the DB; downloads are faked."""
    await schema.close_db()
    monkeypatch.setattr(schema, "DB_PATH", tmp_path / "c.db")
    await schema.init_db()
    monkeypatch.setattr("agent.config.OUTPUT_DIR", tmp_path / "output")
    project = await crud.create_project(name="Thu nghiem Gemini", material="3d_pixar")
    video = await crud.create_video(project_id=project["id"], title="V", orientation="VERTICAL")
    scenes = []
    for i in range(2):
        s = await crud.create_scene(video_id=video["id"], display_order=i, prompt=f"p{i}")
        scenes.append(await crud.update_scene(
            s["id"], vertical_image_status="COMPLETED", vertical_image_media_id=f"0000000{i}-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            vertical_video_status="COMPLETED", vertical_video_media_id=f"1000000{i}-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            vertical_video_url=f"https://example.test/video/{i}.mp4"))
    source = make_clip(tmp_path / "source.mp4", "720x1280", audio=True)
    calls = {"url": 0, "fresh": 0, "fail": set()}

    async def fake_url(url, dest):
        calls["url"] += 1
        if url in calls["fail"]:
            dest.write_bytes(b"<Error>expired</Error>")  # what an expired signed URL saves
            return
        shutil.copy(source, dest)

    async def fake_fresh(media_id, dest):
        calls["fresh"] += 1
        raise ValueError("get_media failed")

    monkeypatch.setattr(pmedia, "fetch_url", fake_url)
    monkeypatch.setattr(pmedia, "fetch_fresh", fake_fresh)
    runner = PipelineRunner(lambda: FakeClient())
    yield {"project": project, "video": video, "scenes": scenes, "calls": calls, "runner": runner}
    await schema.close_db()


async def _to_concat(env):
    run = await env["runner"].create(env["video"]["id"], checkpoints=[], concat=True)
    return await env["runner"].start(run["id"])  # nothing to generate: straight to CONCAT


async def test_run_reaches_a_final_file_from_downloaded_clips(video_ready):
    st = await _to_concat(video_ready)
    assert st["status"] == "COMPLETED", st["status_detail"]
    final = Path(st["final_path"])
    assert final.name == f"thu_nghiem_gemini_{video_ready['video']['id'][:8]}_final.mp4" and final.is_file()
    assert abs(pconcat.probe(final).duration - 2.0) < 0.5
    items = st["stages"]["VIDEOS"]["items"]
    assert {i["download_status"] for i in items} == {"DOWNLOADED"}
    assert all(Path(i["local_path"]).name.startswith("scene_00") for i in items)


async def test_missing_clip_is_retried_once_then_waits_for_a_person(video_ready):
    env = video_ready
    env["calls"]["fail"].add("https://example.test/video/1.mp4")
    st = await _to_concat(env)  # the clip already existed before the run: first try + one retry
    assert st["status"] == "NEEDS_USER_ACTION"
    assert env["calls"]["url"] == 3  # scene 0 once, scene 1 twice
    [bad] = st["needs_user_action"]
    assert bad["label"] == "scene #1" and bad["error_code"] == "MISSING_CLIP"
    assert (await pc.get_item(bad["id"]))["download_attempts"] == 2
    assert not Path(pmedia.scene_video_file(env["project"], env["scenes"][1])).exists()  # no XML saved as .mp4

    env["calls"]["fail"].clear()           # e.g. the network is back
    st = await env["runner"].resume(st["id"])
    assert st["status"] == "COMPLETED", st["status_detail"]
    assert st["needs_user_action"] == []


async def test_video_redo_from_concat_deletes_the_old_clip(video_ready):
    env = video_ready
    env["calls"]["fail"].add("https://example.test/video/1.mp4")
    st = await _to_concat(env)
    assert st["status"] == "NEEDS_USER_ACTION" and st["stage"] == "CONCAT"
    old_clip = pmedia.scene_video_file(env["project"], env["scenes"][0])
    assert old_clip.is_file()

    st = await env["runner"].redo(st["id"], "scene", env["scenes"][0]["id"])

    assert not old_clip.exists()  # concat must not pick up the previous render
    assert st["stage"] == "VIDEOS"
    item = next(i for i in st["stages"]["VIDEOS"]["items"] if i["label"] == "scene #0")
    assert item["status"] == "SUBMITTED" and item["request_type"] == "GENERATE_VIDEO"
