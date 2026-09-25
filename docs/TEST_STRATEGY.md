# Testing

## Layout

| Path | What it covers |
|---|---|
| `tests/unit/` | One module at a time. Network, subprocesses, Flow, AI CLIs and YouTube are mocked. Some tests use a temporary SQLite database; the contact-sheet tests call the real `ffmpeg` |
| `tests/integration/test_api_database.py` | API validation and project/video/scene/request relationships against a temporary database |
| `tests/integration/test_pipeline_e2e.py` | `Project → Video → Scene → Request → Image → Video → Audio → Final output` with mocked Flow responses and media files |
| `tests/extension_mv3_bootstrap.test.cjs` | Node test of the extension's MV3 cold-start bootstrap (`background.js` in a `vm` sandbox with stubbed `chrome.*`) |
| `tests/conftest.py` | Shared fixtures |

`pytest.ini` sets `asyncio_mode = auto` and `testpaths = tests`.

No test contacts Google Flow, YouTube, Suno, Anthropic, or a real browser. The
dashboard has no tests.

## Running

Install test dependencies once:

```bash
pip install -r requirements.txt -r requirements-dev.txt
```

| Command | Scope |
|---|---|
| `python -m pytest tests/unit -q` | Unit suite — **what CI runs** |
| `python -m pytest tests/integration -q` | Integration suite |
| `python -m pytest -q` | Everything under `tests/` |
| `python -m pytest --cov=agent --cov-report=term-missing -q` | With coverage |
| `python -m pytest tests/unit/test_processor.py -q` | One file |
| `node tests/extension_mv3_bootstrap.test.cjs` | Extension bootstrap test (not run in CI) |

Without a local Python, `uv` works:

```bash
uv run --no-project --with-requirements requirements.txt --with-requirements requirements-dev.txt python -m pytest -q
```

Install `ffmpeg` (with a font available for `drawtext`) to run the
contact-sheet tests; without it they error out rather than skip.

## CI

`.github/workflows/tests.yml` runs on pull requests and pushes to `main`:
Ubuntu, Python 3.10 and 3.13, `ffmpeg` + `fonts-dejavu-core` installed, then
`python -m pytest tests/unit -q`. It also reports (without failing) whether
ffmpeg can render `drawtext`. Integration tests and the Node test are not run
in CI.

## Current baseline

Measured on 2026-09-25 against commit `2ae9008` (Windows 11, Python 3.12, no
ffmpeg on `PATH`):

| Run | Result |
|---|---|
| `python -m pytest -q` | 384 passed, 22 failed, 22 errors |
| `python -m pytest tests/unit -q` | 380 passed, 22 failed, 22 errors |
| `python -m pytest tests/integration -q` | 4 passed |
| Coverage (`agent/`) | 56% |

The node test was not run (Node.js was not installed on the measuring machine).

### Failures on any platform (8)

These come from defects on `main`, not from the environment:

| Tests | Cause |
|---|---|
| `test_youtube_publisher.py` (4) | `sqlite3.OperationalError: no such column: youtube_upload_status` — the `video` table lacks the YouTube state columns (ARCHITECTURE §5) |
| `test_flow_client_batch.py::TestRefreshProjectUrls` (3) | `sqlite3.OperationalError: no such table: scene` — `refresh_project_urls` now calls `crud.list_scenes_by_project`, which these tests do not provide a database for |
| `test_operations.py::TestGenerateSceneImage::test_resolves_character_media_ids_from_project` (1) | `KeyError: 'id'` — `generate_scene_image` now reads `scene["id"]`; the test's scene fixture has no `id` |

### Environment-dependent failures (measured on Windows without ffmpeg)

| Tests | Cause |
|---|---|
| `test_video_reviewer.py` (15 errors), `test_cli_providers.py::TestHasDrawtext` (4) | `RuntimeError: ffmpeg is unavailable` — install ffmpeg |
| `test_cli_providers.py::TestAnalyzeCliPromptBranching` (3) | Assert POSIX paths (`/tmp/...`), which render as `\tmp\...` on Windows |
| `test_setup.py` (3 failures, 7 errors) | `UnicodeDecodeError` on Windows: test fixtures write skill files in the platform encoding, `setup.py` reads UTF-8 |

On Linux with ffmpeg (the CI environment) only the 12 platform-independent
failures are expected.

## Failure-mode coverage

| Failure | Tests |
|---|---|
| Worker timeout, cancellation, concurrent claims | `test_worker_hardening.py` |
| Request state machine, retry/backoff, stale recovery, lease conflicts | `test_request_lifecycle.py`, `test_processor.py` |
| Extension disconnect/reconnect, malformed responses | `test_flow_client_transport.py` |
| batchexecute envelopes, golden payloads | `test_flow_batch.py`, `test_flow_batch_golden.py`, `test_flow_client_batch.py` |
| Constraint violations, duplicate active requests, rollback | `test_database_integrity.py` |
| Missing/corrupt media, ffmpeg timeouts | `test_media_process.py`, `test_post_process.py` |
| Backup create/verify/restore | `test_backup.py` |
| Readiness checks | `test_readiness.py` |
| Log redaction | `test_logging_utils.py` |
| API validation and error shape | `test_api_hardening.py`, `tests/integration/test_api_database.py` |
| Review CLI flags, provider config, review parsing | `test_cli_providers.py`, `test_video_reviewer.py` |

## Gaps

- The integration suite is small (4 tests) and is not run in CI.
- No test covers the dashboard or runs the extension against a real Chrome.
- The YouTube publisher is only tested against a fake client, and those tests
  currently fail (see above).
- Real Flow behavior is only covered by recorded/golden payloads; there is no
  opt-in live test.
