"""Provider selection. The only place that decides which AIProvider is active."""

from __future__ import annotations

import logging

from agent import config
from agent.services.ai.base import AIProvider

logger = logging.getLogger(__name__)

_provider: AIProvider | None = None
_resolved = False


def build_provider(name: str, *, api_key: str = "", model: str = "") -> AIProvider | None:
    """Return the provider for an AI_PROVIDER value, or None when AI is disabled.

    "" (auto) → Gemini when a key is present, otherwise disabled.
    "gemini"  → Gemini even without a key (reported as not configured).
    "mock"    → MockAIProvider.
    "none"/"off"/"disabled" → disabled.
    """
    name = (name or "").strip().lower()
    if name in ("none", "off", "disabled"):
        return None
    if name == "mock":
        from agent.services.ai.mock import MockAIProvider
        return MockAIProvider()
    if name in ("", "auto"):
        if not api_key:
            return None
        name = "gemini"
    if name == "gemini":
        from agent.services.ai.gemini import GeminiProvider
        return GeminiProvider(api_key=api_key, model=model)
    raise ValueError(f"Unknown AI_PROVIDER: {name!r} (expected gemini, mock or none)")


def get_ai_provider() -> AIProvider | None:
    global _provider, _resolved
    if not _resolved:
        try:
            _provider = build_provider(config.AI_PROVIDER, api_key=config.GEMINI_API_KEY, model=config.GEMINI_MODEL)
        except ValueError as exc:
            logger.error("AI provider disabled: %s", exc)
            _provider = None
        _resolved = True
    return _provider


def set_ai_provider(provider: AIProvider | None) -> None:
    """Override the active provider (tests, or wiring a custom provider at startup)."""
    global _provider, _resolved
    _provider = provider
    _resolved = True


def reset_ai_provider() -> None:
    global _provider, _resolved
    _provider = None
    _resolved = False


def provider_status() -> dict:
    provider = get_ai_provider()
    return {
        "enabled": provider is not None,
        "provider": provider.name if provider else None,
        "model": provider.model if provider else None,
        "configured": bool(provider and provider.configured),
    }
