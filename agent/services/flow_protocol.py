"""Validation and serialization for the extension wire protocol."""

from __future__ import annotations

import json
import re
from typing import Any


_REQUEST_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_CONTROL_TYPES = {"token_captured", "extension_ready", "extension_status", "media_urls_refresh", "pong", "ping"}


class FlowProtocolError(ValueError):
    """Raised when an extension message violates the bridge contract."""


def validate_request_id(request_id: Any) -> str:
    if not isinstance(request_id, str) or not _REQUEST_ID_RE.fullmatch(request_id):
        raise FlowProtocolError("invalid request id")
    return request_id


def serialize_request(request_id: str, method: str, params: dict[str, Any]) -> str:
    validate_request_id(request_id)
    if not isinstance(method, str) or not method or len(method) > 100:
        raise FlowProtocolError("invalid request method")
    if not isinstance(params, dict):
        raise FlowProtocolError("request params must be an object")
    return json.dumps({"id": request_id, "method": method, "params": params}, separators=(",", ":"))


def parse_message(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise FlowProtocolError("extension message must be an object")
    message_type = data.get("type")
    if message_type is not None:
        if not isinstance(message_type, str) or message_type not in _CONTROL_TYPES:
            raise FlowProtocolError("unknown extension message type")
    if "id" in data:
        validate_request_id(data["id"])
    return data
