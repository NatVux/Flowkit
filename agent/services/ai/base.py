"""Provider-neutral contract for AI text/JSON generation.

Business logic (agent/services/ai_content.py) depends only on this module, so
no caller ever imports a vendor SDK. Providers turn their SDK's failures into
the error types below; the service decides what is retryable from `retryable`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


# ─── Errors ─────────────────────────────────────────────────

class AIProviderError(Exception):
    """Base class. `retryable` tells the service whether another attempt can help."""

    retryable = False
    code = "AI_PROVIDER_ERROR"

    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


class AINotConfiguredError(AIProviderError):
    code = "AI_NOT_CONFIGURED"


class AITimeoutError(AIProviderError):
    retryable = True
    code = "AI_TIMEOUT"


class AIRateLimitError(AIProviderError):
    retryable = True
    code = "AI_RATE_LIMITED"


class AIServerError(AIProviderError):
    """Provider-side 5xx or connection failure."""
    retryable = True
    code = "AI_SERVER_ERROR"


class AIRequestError(AIProviderError):
    """Provider rejected the request (4xx other than 429): bad key, bad model, bad schema."""
    code = "AI_REQUEST_REJECTED"


class AIEmptyResponseError(AIProviderError):
    retryable = True
    code = "AI_EMPTY_RESPONSE"


class AIMalformedResponseError(AIProviderError):
    """Output was not valid JSON or failed schema validation."""
    retryable = True
    code = "AI_MALFORMED_RESPONSE"


class AIPartialResponseError(AIProviderError):
    """Output was cut off (e.g. token limit). Same input would be cut off again."""
    code = "AI_PARTIAL_RESPONSE"


class AIContentBlockedError(AIProviderError):
    """Provider refused on safety/policy grounds."""
    code = "AI_CONTENT_BLOCKED"


# ─── Request / response ─────────────────────────────────────

@dataclass(frozen=True)
class AIRequest:
    operation: str                     # e.g. "story_plan"; used for logs and mock routing
    system_instruction: str
    prompt: str
    response_schema: dict[str, Any]    # JSON Schema the output must follow
    request_id: str
    timeout_seconds: float = 90.0
    temperature: float | None = None
    max_output_tokens: int | None = None


@dataclass(frozen=True)
class AIResponse:
    text: str
    model: str
    finish_reason: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class AIProvider(Protocol):
    name: str
    model: str

    @property
    def configured(self) -> bool: ...

    async def generate_json(self, request: AIRequest) -> AIResponse:
        """Return raw model text that should be JSON. Raise AIProviderError subclasses."""
        ...
