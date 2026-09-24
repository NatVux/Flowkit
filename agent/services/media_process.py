"""Safe subprocess and media-file helpers shared by media services."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

logger = logging.getLogger(__name__)
from agent.logging_utils import log_event
MAX_DIAGNOSTIC_BYTES = 4000
_OUTPUT_LOCK = threading.Lock()


@dataclass(frozen=True)
class MediaCommandResult:
    ok: bool
    returncode: int | None
    stdout: str
    stderr: str
    error: str | None = None


def validate_input_file(path: str | Path, label: str = "input") -> Path:
    resolved = Path(path).expanduser()
    if not resolved.is_file():
        raise FileNotFoundError(f"{label} file not found: {resolved}")
    if resolved.stat().st_size == 0:
        raise ValueError(f"{label} file is empty: {resolved}")
    return resolved


def validate_output_file(path: str | Path, label: str = "output") -> Path:
    resolved = Path(path)
    if not resolved.is_file():
        raise RuntimeError(f"{label} was not created: {resolved}")
    if resolved.stat().st_size == 0:
        raise RuntimeError(f"{label} is empty: {resolved}")
    return resolved


def validate_media_output(path: str | Path, label: str = "output", timeout: float = 30) -> Path:
    """Validate that a non-empty output is parseable by FFprobe."""
    resolved = validate_output_file(path, label)
    result = run_media_command(
        ["ffprobe", "-v", "error", "-show_entries", "format=format_name", "-of", "default=nw=1:nk=1", str(resolved)],
        timeout=timeout,
    )
    if not result.ok or not result.stdout.strip():
        raise RuntimeError(f"{label} is not a readable media file: {result.error or result.stderr}")
    return resolved


def _diagnostic(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return value[-MAX_DIAGNOSTIC_BYTES:]


def run_media_command(
    command: Sequence[str | Path],
    *,
    timeout: float,
    output_path: str | Path | None = None,
) -> MediaCommandResult:
    """Run an argument-list command safely, optionally atomically publishing output."""
    args = [str(part) for part in command]
    if not args or not args[0]:
        return MediaCommandResult(False, None, "", "", "empty media command")
    if shutil.which(args[0]) is None and not Path(args[0]).is_file():
        return MediaCommandResult(False, None, "", "", f"{args[0]} is unavailable; install it and ensure it is on PATH")

    temporary_path = None
    output_lock = None
    if output_path is not None:
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        output_lock = _OUTPUT_LOCK
        output_lock.acquire()
        destination.unlink(missing_ok=True)
        temporary_path = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        args[-1] = str(temporary_path)

    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            shell=False,
        )
        stdout = _diagnostic(completed.stdout)
        stderr = _diagnostic(completed.stderr)
        if completed.returncode != 0:
            log_event(logger, logging.ERROR, "media_processing_failure", operation=args[0], returncode=completed.returncode)
            return MediaCommandResult(False, completed.returncode, stdout, stderr, stderr or f"exit code {completed.returncode}")
        if temporary_path is not None:
            validate_output_file(temporary_path)
            os.replace(temporary_path, destination)
            validate_output_file(destination)
        return MediaCommandResult(True, completed.returncode, stdout, stderr)
    except FileNotFoundError as exc:
        return MediaCommandResult(False, None, "", "", f"{args[0]} is unavailable; install it and ensure it is on PATH: {exc}")
    except subprocess.TimeoutExpired as exc:
        log_event(logger, logging.WARNING, "media_processing_timeout", operation=args[0], timeout_seconds=timeout)
        return MediaCommandResult(False, None, _diagnostic(exc.stdout), _diagnostic(exc.stderr), f"media command timed out after {timeout}s")
    except (OSError, ValueError) as exc:
        return MediaCommandResult(False, None, "", "", str(exc))
    finally:
        if temporary_path is not None:
            Path(temporary_path).unlink(missing_ok=True)
        if output_lock is not None:
            output_lock.release()


def probe_duration(path: str | Path, timeout: float = 30) -> float:
    validate_input_file(path, "media")
    result = run_media_command(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        timeout=timeout,
    )
    if not result.ok:
        raise RuntimeError(f"ffprobe failed for {path}: {result.error}")
    try:
        duration = float(result.stdout.strip())
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"ffprobe returned invalid duration for {path}: {result.stdout!r}") from exc
    if duration <= 0:
        raise RuntimeError(f"ffprobe returned non-positive duration for {path}")
    return duration
