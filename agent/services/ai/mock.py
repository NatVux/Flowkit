"""Deterministic AIProvider for tests and offline development (AI_PROVIDER=mock)."""

from __future__ import annotations

import json
from collections import deque
from typing import Any, Callable, Iterable

from agent.services.ai.base import AIRequest, AIResponse


def default_story_plan() -> dict[str, Any]:
    return {
        "title": "The Fish Merchant",
        "logline": "A cat who sells fish is tempted by the last golden fish.",
        "story": "Pippip runs a small fish stall. One morning a golden fish arrives and he must choose.",
        "characters": [
            {"name": "Pippip", "entity_type": "character",
             "description": "Chubby orange tabby cat, green eyes, blue apron, straw hat",
             "voice_description": "Warm cheerful voice with a slight purr"},
        ],
        "locations": [
            {"name": "Fish Stall", "description": "Small wooden market stall with a thatched roof and an ice display"},
        ],
        "scenes": [
            {"summary": "Morning setup", "image_prompt": "Pippip arranges fish on ice at Fish Stall, sunrise.",
             "video_prompt": "Pippip lays a fish on the ice and smiles proudly. The camera slowly pushes in. Warm morning light.",
             "narration": "Every morning, Pippip opens his stall before the sun is up.",
             "character_names": ["Pippip", "Fish Stall"], "continues_previous": False},
            {"summary": "The golden fish", "image_prompt": "Pippip stares at a glowing golden fish on the ice at Fish Stall.",
             "video_prompt": "Close-up of the golden fish shimmering as Pippip leans in, wide-eyed. The camera holds steady. Soft glow.",
             "narration": "Then one day, something shimmered on the ice.",
             "character_names": ["Pippip", "Fish Stall"], "continues_previous": True},
        ],
    }


def default_youtube_metadata() -> dict[str, Any]:
    return {
        "title": "The Fish Merchant | A Cat's Golden Temptation",
        "description": "Pippip the cat runs a fish stall until a golden fish changes everything.",
        "tags": ["animation", "cat", "short story"],
        "hashtags": ["#animation", "#cat"],
    }


_DEFAULTS: dict[str, Callable[[], dict[str, Any]]] = {
    "story_plan": default_story_plan,
    "youtube_metadata": default_youtube_metadata,
}


class MockAIProvider:
    """Returns scripted outcomes in order, then per-operation defaults.

    Each scripted item is a str (raw text returned as-is), a dict (JSON-encoded),
    or an Exception instance (raised). Every call is recorded in `calls`.
    """

    name = "mock"

    def __init__(self, script: Iterable[Any] = (), *, model: str = "mock-model", configured: bool = True):
        self.model = model
        self._script = deque(script)
        self._configured = configured
        self.calls: list[AIRequest] = []

    @property
    def configured(self) -> bool:
        return self._configured

    def queue(self, *items: Any) -> None:
        self._script.extend(items)

    async def generate_json(self, request: AIRequest) -> AIResponse:
        self.calls.append(request)
        if self._script:
            item = self._script.popleft()
        else:
            factory = _DEFAULTS.get(request.operation)
            item = factory() if factory else {}
        if isinstance(item, BaseException):
            raise item
        text = item if isinstance(item, str) else json.dumps(item)
        return AIResponse(text=text, model=self.model, finish_reason="STOP")
