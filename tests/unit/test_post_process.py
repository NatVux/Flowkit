"""Post-processing safety tests with mocked media commands."""

from pathlib import Path
from unittest.mock import Mock, patch

from agent.services import post_process


def test_trim_rejects_missing_input(tmp_path):
    assert post_process.trim_video(str(tmp_path / "missing.mp4"), str(tmp_path / "out.mp4"), 0, 1) is False


def test_trim_does_not_publish_failed_output(tmp_path):
    source = tmp_path / "input with spaces.mp4"
    source.write_bytes(b"video")
    output = tmp_path / "out unicode.mp4"
    failed = Mock(ok=False, error="ffmpeg unavailable")
    with patch.object(post_process, "run_media_command", return_value=failed):
        assert post_process.trim_video(str(source), str(output), 0, 1) is False
    assert not output.exists()


def test_merge_cleans_concat_file_on_failure(tmp_path):
    source = tmp_path / "a unicode.mp4"
    source.write_bytes(b"video")
    output = tmp_path / "final.mp4"
    failed = Mock(ok=False, error="codec failure")
    with patch.object(post_process, "run_media_command", return_value=failed):
        assert post_process.merge_videos([str(source)], str(output)) is False
    assert not output.exists()
    assert not list(tmp_path.glob("*.concat.txt"))


def test_narration_rejects_missing_audio(tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    assert post_process.add_narration(str(video), str(tmp_path / "missing.wav"), str(tmp_path / "out.mp4")) is False
