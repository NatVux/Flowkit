"""Runtime dependency and readiness checks."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import aiosqlite

from agent import config
from agent.services.flow_client import get_flow_client


async def check_readiness() -> dict[str, Any]:
    checks: dict[str, dict[str, Any]] = {}
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    checks["ffmpeg"] = {"ok": bool(ffmpeg), "path": ffmpeg}
    checks["ffprobe"] = {"ok": bool(ffprobe), "path": ffprobe}

    try:
        async with aiosqlite.connect(str(config.DB_PATH)) as db:
            await db.execute("SELECT 1")
        checks["database"] = {"ok": True, "path": str(config.DB_PATH)}
    except Exception as exc:
        checks["database"] = {"ok": False, "error": type(exc).__name__}

    try:
        config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        probe = config.OUTPUT_DIR / ".flowkit-write-check"
        probe.write_bytes(b"ok")
        probe.unlink(missing_ok=True)
        checks["media_directory"] = {"ok": True, "path": str(config.OUTPUT_DIR)}
    except Exception as exc:
        checks["media_directory"] = {"ok": False, "error": type(exc).__name__}

    client = get_flow_client()
    checks["extension"] = {
        "ok": client.connected,
        "connected": client.connected,
        "flow_tab_available": client.ws_stats.get("flow_tab_available", False),
        "status": client.ws_stats.get("extension_statuses", []),
    }
    required = ("ffmpeg", "ffprobe", "database", "media_directory", "extension")
    ready = all(checks[name].get("ok") for name in required)
    return {"ready": ready, "checks": checks}
