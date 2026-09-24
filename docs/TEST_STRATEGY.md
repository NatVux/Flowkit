# Test Strategy

## Test layers

### Unit

Unit tests isolate one module and use mocks for network, subprocesses, and external AI providers. Current coverage includes domain models, request state transitions, SQLite CRUD/integrity, configuration defaults, retry behavior, rate limiting, Flow protocol/client behavior, media helpers, parsing, operation routing, result handling, CLI providers, and review parsing.

### Integration

Integration tests use a temporary SQLite database and exercise real repository/database constraints. External providers remain mocked. The integration layer covers API validation, project/video/scene/request relationships, worker database claims, retries, and pipeline state updates.

### End-to-end logical pipeline

`tests/integration/test_pipeline_e2e.py` exercises:

`Project -> Video -> Scene -> Request -> Image -> Video -> Audio -> Final output`

Google Flow, TTS/OmniVoice, YouTube, Suno, Anthropic, Chrome, and FFmpeg are not contacted. Flow responses and generated media are deterministic mocked payloads/files.

## Failure matrix

| Failure | Covered by |
|---|---|
| Timeout | `test_worker_hardening.py`, Flow transport tests, media helper tests |
| Extension disconnect/reconnect | `test_flow_client_transport.py` |
| Malformed Flow response | `test_flow_client_transport.py`, Flow batch tests |
| Failed generation | processor/result handler tests |
| Retry/backoff | request lifecycle and processor tests |
| Worker restart/stale job | request lifecycle tests |
| Database constraint/rollback error | database integrity tests |
| Missing/corrupt media | media helper and post-process tests |
| Duplicate request | database integrity and lifecycle tests |
| Concurrent request claims | database integrity, lifecycle, worker hardening tests |

## Commands

From the repository root:

```powershell
uv run --with pytest --with pytest-asyncio --with pytest-mock --with pytest-cov `
  --with aiosqlite --with fastapi --with pydantic --with websockets `
  --with aiohttp --with httpx --with anthropic `
  python -m pytest --cov=agent --cov-report=term-missing -q
```

The normal suite must not use real accounts, browser sessions, Google Flow, YouTube, Suno, Anthropic, or external network calls. Integration and end-to-end tests should use temporary databases and mocked external boundaries.

## Remaining critical-path gaps

- Full FastAPI route matrix is not yet exhaustive for every provider/music/review endpoint.
- Dashboard TypeScript/browser tests are not configured in the current repository.
- Real FFmpeg behavior is covered by mocked helper tests; a separate opt-in environment test can validate installed codecs.
- YouTube OAuth/upload behavior is not present in the normal backend test surface and must remain mocked in future tests.
