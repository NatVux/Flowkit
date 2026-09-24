"""Transport-neutral interface for the Flow integration boundary."""

from __future__ import annotations

from typing import Any, Protocol


class FlowAdapter(Protocol):
    """Application-facing contract implemented by the Flow bridge."""

    @property
    def connected(self) -> bool: ...

    async def request(self, method: str, params: dict[str, Any], timeout: float = 300) -> dict[str, Any]: ...

    async def handle_message(self, data: dict[str, Any], websocket: object | None = None) -> bool: ...

    def set_extension(self, websocket: object) -> None: ...

    def clear_extension(self, websocket: object | None = None) -> None: ...
