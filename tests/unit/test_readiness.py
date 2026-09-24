"""Runtime readiness checks."""

import pytest

from agent import config
from agent.services import readiness


@pytest.mark.asyncio
async def test_readiness_reports_dependency_and_extension_state(monkeypatch, tmp_path):
    monkeypatch.setattr(readiness.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "ready.db")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(readiness, "get_flow_client", lambda: type("Client", (), {
        "connected": True,
        "ws_stats": {"flow_tab_available": True, "extension_statuses": ["flow_tab_available"]},
    })())

    result = await readiness.check_readiness()
    assert result["ready"] is True
    assert result["checks"]["database"]["ok"] is True
    assert result["checks"]["media_directory"]["ok"] is True
    assert result["checks"]["extension"]["flow_tab_available"] is True
