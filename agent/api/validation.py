"""Shared lightweight API identifier and query validation."""

from __future__ import annotations

import re
from fastapi import HTTPException

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def validate_id(value: str, name: str = "id") -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise HTTPException(422, f"Invalid {name}")
    return value
