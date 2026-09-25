# Troubleshooting

Start with the three status endpoints, then the logs. For AI-assisted
diagnosis, the `/fk-doctor` skill walks through the same checks.

## First checks

```bash
curl -s http://127.0.0.1:8100/health          # is the agent up? extension_connected?
curl -s http://127.0.0.1:8100/ready           # which dependency is missing? (503 lists it)
curl -s http://127.0.0.1:8100/api/flow/status # tab available? project pinned?
curl -s "http://127.0.0.1:8100/api/requests?status=FAILED"
curl -s "http://127.0.0.1:8100/api/requests/batch-status?video_id=<VID>"
```

| Symptom | Likely cause |
|---|---|
| `curl` cannot connect | Agent not running, or started with a different `API_PORT` |
| `extension_connected: false` | Extension not loaded/enabled, Chrome closed, or agent on a non-default `WS_PORT` |
| `flow_tab_available: false` | No open, non-discarded `flow.google.com` tab |
| `flow_project_id: null` | `FLOW_PROJECT_ID` not exported in the agent's environment |
| `/ready` 503 with `ffmpeg`/`ffprobe` false | ffmpeg not on the agent's `PATH` |
| Requests stay `PENDING` | Extension disconnected (worker does not claim), `next_retry_at` in the future, or prerequisites missing (deferred) |

## Logs

The agent logs to **stderr only**; there are no log files. Capture them from
your terminal or process supervisor (`journalctl -u <unit>`, NSSM
`AppStderr`, or `python -m agent.main 2> agent.log`).

Format: `2026-09-25 15:09:17,896 [LEVEL] logger.name message`.
Set verbosity with `LOG_LEVEL=DEBUG|INFO|WARNING|ERROR` (default `INFO`).
`DEBUG` adds per-poll worker detail (`Worker: N actionable, …`) and review/poll
internals.

Lifecycle events are JSON in the message part:

```text
[INFO] agent.worker.processor {"attempt": 1, "event": "job_started", "operation": "GENERATE_IMAGE", "request_id": "…", "scene_id": "…", …}
```

| Event | Meaning |
|---|---|
| `worker_started` / `worker_stopped` | Agent lifespan start / end |
| `extension_connected` / `extension_disconnected` | Extension WebSocket state |
| `job_started`, `job_completed`, `job_finished` | Request processing; `job_finished` carries `final_status` |
| `job_failed` | Exception or permanent failure (`reason`, `error_type`) |
| `job_timeout` | `WORKER_OPERATION_TIMEOUT` exceeded |
| `job_cancelled` | Cancelled during shutdown, returned to `PENDING` |
| `retry_scheduled` | `reason`: `transient_connection`, `captcha`, `retryable_error`, `expired_media_recovered` |
| `external_request_timeout` | No extension reply within the command timeout |
| `media_processing_timeout` | ffmpeg/ffprobe subprocess timed out |
| `backup_completed` / `backup_failed` | Scheduled backup result |

Useful plain-text lines: `Startup dependency unavailable: <name>`,
`Cleaned up N stale PROCESSING requests`,
`Request <id> FAILED permanently: …`, `VIDEO prereq deferred: …`,
`ext/callback: id=… pending=… match=yes|no`.

Secret-looking keys are redacted as `[REDACTED]`; string values over 4096
characters are truncated.

Other log locations:

- Extension: `chrome://extensions` → Flow Kit → **Inspect views: service
  worker** → Console (`[FlowAgent] …` lines). The popup and side panel show a
  request log (last 100 entries).
- Flow page: DevTools console of the Flow tab.
- Request history: the `error_message` and `last_failure_reason` columns
  (`GET /api/requests/{id}`).

## Common failures

### Setup and startup

| Symptom | Fix |
|---|---|
| `python: command not found` / Windows Store prompt | Install Python 3.10+; on Windows use the `py` launcher or a venv |
| `setup.sh`: `venv/bin/activate: No such file` on Git Bash | Native Windows Python creates `venv\Scripts`. Use WSL or the manual steps in [SETUP.md](SETUP.md) |
| `ModuleNotFoundError: No module named 'agent'` | Run from the repository root; use `python -m agent.main` / `python -m scripts.backup` |
| Settings in `.env` are ignored | Nothing loads `.env`. Export the variables in the shell or supervisor |
| Database / media appear in `agent/` | `FLOW_AGENT_DIR=./agent` was exported from `.env.example`. Unset it or use an absolute path |
| `sqlite3.OperationalError: unable to open database file` at startup | `FLOW_AGENT_DIR` points to a directory that does not exist or is not writable. Create it |
| `Address already in use` on 8100 or 9222 | Another agent instance (or another program) holds the port. Run one agent per database |
| `Skipped unique index migration` warning | Old duplicate active requests exist. Resolve them (mark extras `FAILED`) and restart |

### Bridge and Flow

| Error / symptom | Cause | Fix |
|---|---|---|
| `Extension not connected` | No WebSocket from the extension | Reload the extension; check Chrome is running on the same host and the agent uses port 9222 |
| `NO_FLOW_TAB` | No Flow tab | Open https://flow.google.com/ |
| `FLOW_TAB_DISCARDED` | Chrome discarded the idle tab | Click the tab to reload it; keep it open |
| `NO_AT_TOKEN` | Tab signed out, on an interstitial, or still loading | Sign in and let the Flow app finish loading |
| `CAPTCHA_FAILED: …` | reCAPTCHA could not be minted | Retried automatically (up to 10). Persistent: reload the Flow tab |
| `… [PUBLIC_ERROR_UNUSUAL_ACTIVITY]` / `reCAPTCHA evaluation failed` | Google flagged the session | Pause submissions, sign out/in at flow.google.com, reduce `MAX_CONCURRENT_REQUESTS` / raise `API_COOLDOWN` |
| `NO_FLOW_PROJECT: …` | No Flow project uuid | Export `FLOW_PROJECT_ID` or pass `flow_project_id`. Terminal, not retried |
| `UNSUPPORTED_ON_BATCH_API: …` | Veo upscale, Veo r2v or Veo start+end chaining | Use `model_family=omni_flash`, or `FLOW_ALLOW_DEGRADED=1` for r2v/chaining. See [CAPTURE.md](CAPTURE.md) |
| `Timeout (300s) waiting for batch_rpc` | Extension or page stopped responding | Reload the Flow tab and the extension |
| `"POST /api/ext/callback HTTP/1.1" 401` in the agent log | Extension is still using the secret from a previous agent process | Reload the extension so it reconnects and receives the new secret |
| `POST /api/projects` → 503 | Extension not connected | Connect the extension first |
| `POST /api/projects` → 500 | Flow project uuid already used by a local project | Pass a different `flow_project_id` ([SETUP.md](SETUP.md#one-flow-project-per-flow-kit-project)) |
| Poll reports `Media not found.` | Normal for finished jobs on the new API | Not an error |

### Generation

| Error / symptom | Cause | Fix |
|---|---|---|
| `Waiting for reference images: …` | A named character has no `media_id` | Generate its reference first (`GENERATE_CHARACTER_IMAGE`) |
| `… [PUBLIC_ERROR_UNSAFE_GENERATION]` | Safety filter | Rewrite the prompt; retries will keep failing until it changes |
| `… [PUBLIC_ERROR_USER_QUOTA_REACHED]` | Flow credits exhausted | Wait for reset; check `GET /api/flow/credits` |
| `… [PUBLIC_ERROR_MODEL_ACCESS_DENIED]` | Model not available on the account tier | Change the model (`/fk-change-model`, `PATCH /api/models`) |
| `Requested entity was not found` | Uploaded `media_id` expired | Auto re-upload for video/upscale requests; otherwise `POST /api/flow/upload-image` and patch the id |
| Image/video URLs return 403 | Signed URLs expired | `POST /api/flow/refresh-urls/{project_id}` |
| `media_id` starts with `CAMS…` | Old-style id | `/fk-fix-uuids` |
| Request `FAILED` after retries | `retry_count` reached `MAX_RETRIES` | Fix the cause, then `PATCH /api/requests/{id}` `{"status":"PENDING"}` |
| `POST /api/requests` → 409 | Same scene + type already active | Wait for it, or use `/api/requests/batch` (idempotent) |
| Request stays `PROCESSING` after a crash | Stale lease | Restart the agent after `STALE_PROCESSING_TIMEOUT` (10 min) |
| Scene suddenly back to `PENDING` | A referenced character got a new reference, or the image was regenerated | Expected invalidation — regenerate downstream assets |

### Media, narration, review

| Error / symptom | Fix |
|---|---|
| `media command timed out after 120s` | Large input or slow disk; check the file and free space |
| `narration_mix_status = FAILED` | Mixing needs a `COMPLETED` video whose `{orientation}_video_url` is a plain local file path. Remote and `file://` URLs do not qualify; download the clip and `PATCH /api/scenes/{id}` the path first |
| `narration_audio_status = FAILED` | OmniVoice did not produce a WAV. Check `TTS_PYTHON_BIN` points at an interpreter with `omnivoice` installed |
| `ffmpeg is unavailable; install it and ensure it is on PATH` | Install ffmpeg for the agent's user |
| Contact sheets have no timestamps | ffmpeg lacks `drawtext` (no libfreetype or no font). Supported fallback; check `ffmpeg -filters` for `drawtext` |
| `codex`: `Your workspace is out of credits` | Top up the OpenAI workspace or switch provider (`PATCH /api/providers`) |
| `agy`: model/effort rejected with 400 | agy takes a model **or** an effort, not both |

### YouTube

| Symptom | Cause |
|---|---|
| `POST /api/videos/{vid}/youtube/upload` → 503 `YouTube publishing is not configured` | No YouTube client is wired into the backend |
| `no such column: youtube_upload_status` | Schema lacks the YouTube state columns |
| `/fk-youtube-upload` cannot find `youtube/auth.py` | The `youtube/` scripts are not part of this repository |

Details: [YOUTUBE_PUBLISHING.md](YOUTUBE_PUBLISHING.md).

### Backup / restore

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError: No module named 'agent'` from `scripts/backup.py` | Use `python -m scripts.backup …` from the repository root |
| `Backup is missing manifest.json or database.sqlite` | Wrong path, or an incomplete copy |
| `Backup file verification failed: <file>` | File changed or corrupted after the backup; use another backup |
| Backup `config/` is empty | `FLOW_AGENT_DIR` is not the repository root |

## Inspecting the database

Use the API where possible. For read-only inspection with the `sqlite3` CLI
(stop writes first if you intend to modify anything):

```bash
sqlite3 flow_agent.db "SELECT status, COUNT(*) FROM request GROUP BY status;"
sqlite3 flow_agent.db "SELECT id, type, retry_count, next_retry_at, error_message FROM request WHERE status IN ('PENDING','FAILED') ORDER BY updated_at DESC LIMIT 20;"
```
