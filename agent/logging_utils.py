"""Structured, secret-redacting backend logging helpers."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

_SENSITIVE_PARTS = (
    "password", "cookie", "token", "secret", "api_key", "apikey",
    "authorization", "bearer", "refresh", "credential", "private_key",
)


def _is_sensitive(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in _SENSITIVE_PARTS)


def redact(value: Any, key: str = "") -> Any:
    if _is_sensitive(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str) and len(value) > 4096:
        return f"{value[:4096]}...[truncated]"
    return value


def log_event(logger: logging.Logger, level: int, event: str, *, exc_info: bool = False, **fields: Any) -> None:
    """Emit one JSON event with stable event and correlation fields."""
    payload = {"event": event, **redact(fields)}
    logger.log(level, json.dumps(payload, ensure_ascii=True, sort_keys=True), exc_info=exc_info)


def configure_logging() -> None:
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s %(message)s",
        force=True,
    )
