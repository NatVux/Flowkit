"""Safe media subprocess helper tests."""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from agent.services.media_process import (
    probe_duration,
    run_media_command,
    validate_input_file,
    validate_output_file,
)


def test_missing_input_is_actionable(tmp_path):
    with pytest.raises(FileNotFoundError, match="input file not found"):
        validate_input_file(tmp_path / "missing.mp4")


def test_zero_byte_input_is_rejected(tmp_path):
    path = tmp_path / "empty.mp4"
    path.touch()
    with pytest.raises(ValueError, match="empty"):
        validate_input_file(path)


def test_command_failure_captures_stderr_without_shell(tmp_path):
    output = tmp_path / "out.mp4"
    completed = Mock(returncode=1, stdout="normal", stderr="bad media path")
    with patch("agent.services.media_process.shutil.which", return_value="ffmpeg"), \
         patch("agent.services.media_process.subprocess.run", return_value=completed) as run:
        result = run_media_command(["ffmpeg", "-i", "input with spaces.mp4", str(output)], timeout=4)

    assert result.ok is False
    assert "bad media path" in result.error
    assert run.call_args.kwargs["shell"] is False
    assert run.call_args.kwargs["timeout"] == 4


def test_successful_output_is_published_atomically(tmp_path):
    output = tmp_path / "nested" / "out unicode.mp4"
    completed = Mock(returncode=0, stdout="", stderr="")

    def fake_run(args, **kwargs):
        Path(args[-1]).write_bytes(b"valid media")
        return completed

    with patch("agent.services.media_process.shutil.which", return_value="ffmpeg"), \
         patch("agent.services.media_process.subprocess.run", side_effect=fake_run):
        result = run_media_command(["ffmpeg", "-i", "input.mp4", str(output)], timeout=10, output_path=output)

    assert result.ok is True
    assert output.read_bytes() == b"valid media"
    assert not list(output.parent.glob("*.tmp"))
    validate_output_file(output)


def test_timeout_is_reported():
    import subprocess
    with patch("agent.services.media_process.shutil.which", return_value="ffmpeg"), \
         patch("agent.services.media_process.subprocess.run", side_effect=subprocess.TimeoutExpired("ffmpeg", 1)):
        result = run_media_command(["ffmpeg", "-i", "in.mp4", "out.mp4"], timeout=1)
    assert result.ok is False
    assert "timed out" in result.error


def test_ffprobe_duration_uses_shared_runner(tmp_path):
    source = tmp_path / "voice.wav"
    source.write_bytes(b"audio")
    with patch("agent.services.media_process.run_media_command") as run:
        run.return_value = Mock(ok=True, stdout="2.5\n", stderr="", error=None)
        assert probe_duration(source) == 2.5
