"""Configuration contract tests."""

import importlib

import agent.config as config


def test_safe_development_defaults_are_present():
    assert config.API_HOST == "127.0.0.1"
    assert config.WS_HOST == "127.0.0.1"
    assert config.API_PORT > 0
    assert config.WS_PORT > 0
    assert config.MAX_RETRIES > 0
    assert config.MAX_CONCURRENT_REQUESTS > 0
    assert config.WORKER_OPERATION_TIMEOUT > 0


def test_secrets_are_not_required_at_import(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SUNO_API_KEY", raising=False)
    reloaded = importlib.reload(config)
    assert reloaded.ANTHROPIC_API_KEY == ""
    assert reloaded.SUNO_API_KEY == ""
