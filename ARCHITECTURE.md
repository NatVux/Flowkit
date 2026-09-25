# Flow Kit — Architecture

This document describes the system as implemented on `main` (app version
`1.3.1`, extension `0.3.2`). Operational procedures live in `docs/`:
[SETUP](docs/SETUP.md), [DEVELOPMENT](docs/DEVELOPMENT.md),
[TEST_STRATEGY](docs/TEST_STRATEGY.md), [DEPLOYMENT](docs/DEPLOYMENT.md),
[BACKUP_RECOVERY](docs/BACKUP_RECOVERY.md) and
[TROUBLESHOOTING](docs/TROUBLESHOOTING.md).

## 1. Overview

Flow Kit is a single-host, local-first system that drives Google Flow
(`flow.google.com`) to produce AI videos. It has four parts:

| Part | Location | Role |
|---|---|---|
| Python agent | `agent/` | FastAPI REST API (`:8100`), WebSocket server for the extension (`:9222`), SQLite persistence, background worker |
| Chrome MV3 extension | `extension/` | Runs Flow `batchexecute` RPCs inside a signed-in Flow tab, mints reCAPTCHA tokens, returns results to the agent |
| React dashboard | `dashboard/` | Vite dev app (`:5173`) that proxies `/api`, `/ws` and `/health` to the agent |
| AI content (optional) | `agent/services/ai/`, `agent/services/ai_content.py` | Gemini (or mock) writes stories, entities, scene prompts, narration and YouTube metadata; validated, stored, then applied to the DB. Never calls Flow. See [AI_CONTENT.md](docs/AI_CONTENT.md) |
| Skills | `skills/fk-*.md` | Workflow recipes for AI coding CLIs. `python setup.py` copies them into `.claude/commands/` and generates `AGENTS.md` |

```
┌───────────────────────────┐   ws://127.0.0.1:9222    ┌────────────────────────┐        ┌──────────────────┐
│ Python agent (agent.main) │◄────────────────────────►│ Chrome MV3 extension   │───────►│ flow.google.com  │
│  REST API   :8100         │  commands (JSON)         │  background.js         │        │ signed-in tab    │
│  Worker loop              │                          │  content.js/injected.js│        │ batchexecute RPC │
│  SQLite (flow_agent.db)   │◄─────────────────────────│                        │        │ cookie + `at`    │
│  EventBus → /ws/dashboard │  POST /api/ext/callback  └────────────────────────┘        └──────────────────┘
└───────────────────────────┘  (responses)
        ▲
        │ /api, /ws, /health (Vite proxy)
┌───────┴───────────┐
│ dashboard (:5173) │
└───────────────────┘
```

Flow signs every call with the browser session cookie plus a per-page `at`
token, and generation calls also carry a single-use reCAPTCHA token. None of
that can be produced outside the page, so the agent builds each RPC envelope
and the extension executes it inside the Flow tab. **The system cannot run
headless**: a real, signed-in Chrome with a Flow tab open is required for any
generation.

## 2. Repository structure

Tracked files only (`git ls-files`); runtime artifacts are listed at the end.

```
agent/
  main.py              FastAPI app, lifespan (DB init, worker, WS server, backup scheduler),
                       /health, /ready, /api/ext/callback, /ws/dashboard
  config.py            Environment-variable configuration; loads models.json and providers.json
  models.json          Video/upscale/image model keys per tier and aspect
  providers.json       Per-role AI CLI provider/model/effort for video review
  materials.py         Built-in image "materials" (visual styles)
  logging_utils.py     JSON event logging with secret redaction
  api/                 REST routers (see §9)
  db/schema.py         SQLite schema, in-place migrations, connection + transaction helpers
  db/crud.py           CRUD, request state machine, queue claim, stale-job reset
  models/              Pydantic API models
  sdk/                 Domain layer: models, SQLite repository, OperationService, result_handler
  services/
    flow_client.py     Agent side of the extension bridge; Flow operations
    flow_batch.py      batchexecute envelope building/parsing
    flow_protocol.py   Command/response envelope validation
    omni_flash.py      Omni 1.1 Flash video payloads
    readiness.py       /ready dependency checks
    backup.py          Backup / verify / restore
    media_process.py   Subprocess runner + output validation for ffmpeg/ffprobe
    post_process.py    ffmpeg trim / merge / narration mix / music mix helpers
    tts.py             OmniVoice TTS via a separate Python interpreter
    suno.py            Suno music API client
    video_reviewer.py  Contact-sheet video review via claude / agy / codex CLIs or Anthropic SDK
    cli_providers.py   What each review CLI accepts
    youtube_publisher.py  YouTube upload/publish state machine behind an injected client
    ai/                AIProvider interface, GeminiProvider, MockAIProvider, provider selection
    ai_content.py      AI content planning service (generate → validate → store → apply)
    event_bus.py       In-process pub/sub for the dashboard WebSocket
  worker/processor.py  Worker loop, rate limiter, dispatch, retry policy
  worker/_parsing.py   Error detection and media-id/URL extraction
extension/             manifest.json, background.js, content.js, injected.js, popup.*, side_panel.*, rules.json
dashboard/             Vite + React 19 + Tailwind app
skills/                fk-*.md workflow recipes (source of truth for slash commands)
.claude/commands/      Generated copies of skills (python setup.py --tool claude)
scripts/backup.py      Backup CLI (run as `python -m scripts.backup`)
scripts/statusline.sh  Claude Code statusline (needs curl + jq)
tools/review_server.py Standalone review-board HTTP server (default port 8200) for tools/review_board.html
tests/                 pytest unit + integration suites; one Node test for the extension
setup.sh               Dependency check + venv install (bash)
setup.py               Skill/AI-tool config generator
docs/                  Operational and protocol documentation
```

Runtime artifacts (all gitignored): `flow_agent.db` (+ `-wal`, `-shm`),
`output/`, `backups/`, `agent/active_project.json`, `.fk-setup.json`, `venv/`,
and an optional `youtube/` directory used by the YouTube skills (not part of
this repository).

## 3. Configuration model

`agent/config.py` reads `os.environ` once at import. **No `.env` file is loaded
by the application** — `.env.example` is a reference list; variables must be
exported by the shell or process supervisor. Full table:
[SETUP.md § Configuration](docs/SETUP.md#configuration).

Two JSON files in `agent/` are runtime-mutable and hot-reloaded in memory:

- `models.json` — edited through `PATCH /api/models` (or `/fk-change-model`).
- `providers.json` — edited through `PATCH /api/providers` (or the dashboard
  Settings page / `/fk-change-provider`); written atomically.

`BASE_DIR` is `FLOW_AGENT_DIR` if set, otherwise the repository root. The
database is `BASE_DIR/flow_agent.db`, media is under `BASE_DIR/output/`, and
default backups go to `BASE_DIR/backups/`.

## 4. Chrome extension and Flow connection

- The extension service worker connects to the hard-coded
  `ws://127.0.0.1:9222` and posts responses to the hard-coded
  `http://127.0.0.1:8100/api/ext/callback`. The manifest's host permissions
  also only cover `127.0.0.1:8100`. **Changing `API_PORT` or `WS_PORT` breaks
  the bridge** unless `extension/background.js` and `manifest.json` are edited
  to match.
- On connect, the agent sends `{"type":"callback_secret"}` with a random
  secret generated at process start. The extension includes it as
  `X-Callback-Secret` on every callback; mismatches get HTTP 401. Restarting
  the agent issues a new secret on the next WebSocket connect.
- Commands are `{"id", "method", "params"}` with methods `batch_rpc`,
  `api_request`, `trpc_request`, `solve_captcha`, `get_status`. See
  [EXTENSION_PROTOCOL.md](docs/EXTENSION_PROTOCOL.md).
- Each command waits up to 300 s by default (`FlowClient._send`); a timeout
  returns `{"error": "Timeout (300s) waiting for <method>"}`.
- With no extension connected, every Flow call returns
  `{"error": "Extension not connected"}` and the worker does not claim jobs.
- Flow projects **cannot be created** by Flow Kit on the current API. Every RPC
  is scoped to `FLOW_PROJECT_ID` (or a per-project `flow_project_id` passed to
  `POST /api/projects`); without one, requests fail with `NO_FLOW_PROJECT`.
- `POST /api/projects` requires a connected extension (503 otherwise) and uses
  the Flow project uuid **as the local project id**. Each local project
  therefore needs its own Flow project: creating a second one on the same
  uuid fails with a primary-key conflict (HTTP 500 `INTERNAL_ERROR`). Pass a
  different `flow_project_id` in the body for each project.
- Unported capabilities fail with `UNSUPPORTED_ON_BATCH_API`: Veo video
  upscale, Veo reference-to-video, Veo start+end-frame chaining.
  `FLOW_ALLOW_DEGRADED=1` downgrades the latter two to plain image-to-video.
  Omni 1.1 Flash (`model_family=omni_flash`) supports frame, first+last and
  reference modes. See [CAPTURE.md](docs/CAPTURE.md), [OMNI_FLASH.md](docs/OMNI_FLASH.md),
  [IMAGE_API.md](docs/IMAGE_API.md).

## 5. Database

SQLite at `BASE_DIR/flow_agent.db`, accessed via `aiosqlite` through one shared
connection (`schema.get_db`) with `journal_mode=WAL`, `foreign_keys=ON`,
`busy_timeout=5000`. Writes that must be atomic use
`schema.transaction()` under an in-process `asyncio.Lock`. Run only **one
agent process per database**.

`init_db()` runs at every startup: it executes `CREATE TABLE IF NOT EXISTS`
for all tables, then applies idempotent in-place migrations (`ALTER TABLE ADD
COLUMN`, a table rebuild for the `request.type` CHECK list), installs indexes
and integrity triggers, and records `schema_migration(version=1)`. There is no
down-migration.

### Tables

| Table | Purpose / key columns |
|---|---|
| `character` | Reference entity, **not owned by a project**. `entity_type` ∈ `character, location, creature, visual_asset, generic_troop, faction`; `slug` (unique when non-empty); `media_id` + `reference_image_url` = current reference pointer; `voice_description` |
| `character_reference_asset` | Immutable, versioned reference images per character: `version`, `media_id`, `status` ∈ `ACTIVE, RETIRED, INVALID`. `ON DELETE RESTRICT` from `character` |
| `scene_character_reference` | Append-only snapshot of which reference version each scene used |
| `project` | `status` ∈ `ACTIVE, ARCHIVED, DELETED`; `material`, `user_paygate_tier`, `narrator_voice`, `narrator_ref_audio`, `allow_music`, `allow_voice`, `story`, `language` |
| `project_character` | M:N link between projects and characters |
| `material` | Custom materials (built-ins live in `agent/materials.py`) |
| `video` | Belongs to a project. `orientation` ∈ `VERTICAL, HORIZONTAL`; `status`; `youtube_id`, `privacy`, `tags` |
| `scene` | Belongs to a video. Chain: `parent_scene_id`, `chain_type` ∈ `ROOT, CONTINUATION, INSERT`, `source` ∈ `root, user, system`. Per orientation (`vertical_*`, `horizontal_*`): `image_/video_/upscale_` × `url`, `media_id`, `status`; `*_end_scene_media_id`; trim fields; `transition_prompt`; narration fields (`narrator_text`, `narration_audio_path/duration/status`, `narration_mix_path/status`) |
| `request` | Job queue: `type`, `orientation`, `status`, `retry_count`, `next_retry_at`, `started_at`, `finished_at`, `error_message`, `last_failure_reason`, `media_id`, `output_url`, `edit_prompt`, `source_media_id` |
| `ai_generation` | Validated AI provider output (`STORY_PLAN`, `YOUTUBE_METADATA`), `GENERATED` → `APPLIED` once |
| `schema_migration` | Applied schema versions |

All asset/request statuses use `PENDING, PROCESSING, COMPLETED, FAILED`.
Timestamps are ISO-8601 UTC text.

### Integrity rules enforced in SQLite

- At most one `PENDING`/`PROCESSING` request per `(scene_id, type)` and per
  `(character_id, type)` — partial unique indexes plus triggers.
- A request's `video_id` must belong to its `project_id`, and its `scene_id` to
  its `video_id`.
- `scene.parent_scene_id` must be in the same video.
- Narration statuses are validated by trigger.

If an older database already contains duplicates, the unique-index migration
is skipped with a `Skipped unique index migration` warning; the triggers still
apply to new writes.

### Known schema gap

`agent/services/youtube_publisher.py`, `agent/db/crud.py` and the video API
models use `video` columns that `schema.py` never creates
(`youtube_upload_status`, `youtube_upload_key`, `youtube_upload_error`,
`youtube_upload_attempts`, `youtube_resumable_uri`, `youtube_publish_status`,
`youtube_publish_error`, `youtube_uploaded_at`, `youtube_published_at`,
`upload_url`). Any write to them fails with
`sqlite3.OperationalError: no such column`. See §12.

## 6. Worker

`agent/worker/processor.py` runs as an asyncio task started in the FastAPI
lifespan. There is one worker per agent process.

**Startup:** `PROCESSING` requests whose `updated_at` is older than
`STALE_PROCESSING_TIMEOUT` (rounded up to whole minutes, default 10 min) are
reset to `PENDING` with `last_failure_reason='stale processing recovery'`. This
runs once at startup only.

**Loop** (every `POLL_INTERVAL`, default 5 s):

1. If no extension is connected, sleep. Nothing is claimed.
2. Claim up to `MAX_CONCURRENT_REQUESTS - active` (default 5) `PENDING`
   requests whose `next_retry_at` is null or past, **oldest `created_at`
   first**, atomically setting them `PROCESSING` with a new `started_at`.
3. Run each claimed request in its own task behind `APIRateLimiter`: a
   semaphore of `MAX_CONCURRENT_REQUESTS` plus a minimum `API_COOLDOWN`
   (default 10 s) between operation starts.
4. Emit a `worker_tick` event to the dashboard.

**Per request** (`_process_one`):

1. Resolve orientation: the request's, else the video's, else `VERTICAL`.
2. **Skip completed:** `GENERATE_IMAGE`, `GENERATE_VIDEO`,
   `GENERATE_VIDEO_REFS` and `UPSCALE_VIDEO` are marked `COMPLETED` (message
   `skipped: already completed`) without calling Flow if the scene's matching
   asset is already `COMPLETED`. `REGENERATE_*` and `EDIT_*` always run.
3. **Prerequisites:** video types need `{orientation}_image_media_id`; upscale
   needs `{orientation}_video_media_id`; edits need a source image. If missing,
   the request goes back to `PENDING` and is skipped in-memory for 30 s. This
   does not count as a retry.
4. `REGENERATE_IMAGE` / `REGENERATE_VIDEO` clear downstream scene fields before
   dispatch (§8).
5. Dispatch to `OperationService` with a hard timeout of
   `WORKER_OPERATION_TIMEOUT` (default 900 s).
6. On success: request → `COMPLETED` with `media_id`/`output_url`, then
   scene/character fields are updated (§8).
7. On error: see the retry policy in §7.

**Shutdown:** on lifespan exit (Ctrl+C / SIGTERM on Unix), the worker stops
claiming, waits up to 30 s for active tasks, cancels the rest (cancelled
requests are returned to `PENDING`), then the WebSocket server and database
are closed.

## 7. Request lifecycle

Request types: `GENERATE_CHARACTER_IMAGE`, `REGENERATE_CHARACTER_IMAGE`,
`EDIT_CHARACTER_IMAGE`, `GENERATE_IMAGE`, `REGENERATE_IMAGE`, `EDIT_IMAGE`,
`GENERATE_VIDEO`, `REGENERATE_VIDEO`, `GENERATE_VIDEO_REFS`, `UPSCALE_VIDEO`.

```
            claim                 success
PENDING ───────────► PROCESSING ──────────► COMPLETED   (terminal)
   ▲                   │    │
   │ retry / defer /   │    │ permanent error or retries exhausted
   │ stale recovery    │    ▼
   └───────────────────┘  FAILED ──(PATCH status=PENDING)──► PENDING
```

Allowed transitions are enforced by `crud.REQUEST_TRANSITIONS`; anything else
raises `InvalidRequestTransition`. Worker updates carry the `started_at` lease
they claimed with, so a worker whose request was reclaimed cannot overwrite the
newer attempt. Details: [REQUEST_LIFECYCLE.md](docs/REQUEST_LIFECYCLE.md).

**Creation.** `POST /api/requests` returns 409 if an active request of the same
type exists for the scene. `POST /api/requests/batch` instead returns the
existing active request for duplicates (scene or character). Both set
`video.orientation` from the request orientation.

**Retry policy** (`_handle_failure`), evaluated in this order:

| Condition (substring of the error, case-insensitive) | Result |
|---|---|
| `not found` and the source image can be re-uploaded (video/upscale: scene image; `EDIT_CHARACTER_IMAGE`: reference image) | Re-upload for a fresh `media_id`, back to `PENDING`, `retry_count` unchanged |
| `unsupported_on_batch_api`, `no_flow_project`, `invalid request`, `permission denied`, `unauthorized`, `forbidden`, `not configured`, `unknown request type`, `scene not found`, `character not found` | `FAILED` immediately |
| `extension reconnected`, `extension disconnected`, `extension not connected` | `PENDING`, `next_retry_at` +5 s, `retry_count` unchanged |
| `captcha` / `recaptcha` | `PENDING` with backoff `min(2^n × 10, 300)` s; `FAILED` on the 10th attempt |
| anything else | `retry_count += 1`; `PENDING` with backoff `min(2^n × 10, 300)` s while `retry_count < MAX_RETRIES`, otherwise `FAILED` |

All backoffs add up to `RETRY_JITTER_SECONDS` (default 3) of random jitter.
With the default `MAX_RETRIES=5`, a request fails on its 5th counted failure.
A manual `FAILED → PENDING` retry does not reset `retry_count`.

When a request ends `FAILED`, the matching scene asset status
(`{orientation}_image|video|upscale_status`) is also set to `FAILED`.

## 8. Scene lifecycle

```
character refs ─► scene image ─► scene video ─► (upscale)
                              └─► narration audio ─► narration mix (needs local video file)
```

Per orientation, each asset is `PENDING → PROCESSING → COMPLETED | FAILED`.

**Writes on success** (`sdk/services/result_handler.py`):

| Result | Scene updates |
|---|---|
| `GENERATE_IMAGE`, `REGENERATE_IMAGE`, `EDIT_IMAGE` | Set image fields `COMPLETED`; reset that orientation's video and upscale to `PENDING` with null URL/media id; reset narration mix. If the scene has a parent, set the parent's `{orientation}_end_scene_media_id` to the new image |
| `GENERATE_VIDEO`, `REGENERATE_VIDEO`, `GENERATE_VIDEO_REFS` | Set video fields `COMPLETED`; reset upscale and narration mix |
| `UPSCALE_VIDEO` | Set upscale fields `COMPLETED` |
| Character reference (any `*_CHARACTER_IMAGE`) | Update `character.media_id` / `reference_image_url`; append a `character_reference_asset` version; reset **both orientations'** image/video/upscale and the narration mix on every scene that references the character |

Files on disk are never deleted by invalidation; only database pointers are
cleared.

**Scene image generation** resolves `scene.character_names` against the
project's linked characters (by name or slug). If any matched character has no
`media_id`, the operation returns `Waiting for reference images: …` (a
retryable error). Otherwise it snapshots the reference versions into
`scene_character_reference` and passes their media ids to Flow.


**Video prompts** are assembled in `OperationService`: `video_prompt` (or
`prompt`), plus `Character voices: …` from matching characters'
`voice_description` when the prompt contains dialogue, plus an `Audio: …`
no-background-music line unless the project has `allow_music` or the prompt
already has an `Audio:`/`Music:` label, plus a `Negative: …` line unless one
is present.

**Chaining.** `CONTINUATION` scenes edit/generate from the parent's image. Veo
start+end-frame chaining is unported (`UNSUPPORTED_ON_BATCH_API` unless
`FLOW_ALLOW_DEGRADED=1`); Omni Flash first+last mode works.

Details: [SCENE_PIPELINE.md](docs/SCENE_PIPELINE.md),
[CHARACTER_REFERENCES.md](docs/CHARACTER_REFERENCES.md).

## 9. REST API surface

Interactive docs: `http://127.0.0.1:8100/docs`. Errors use
`{"error": {"code", "message", "details"}}`; validation errors are 422
`VALIDATION_ERROR`; bodies over 32 MiB get 413.

| Prefix | Endpoints |
|---|---|
| `/health`, `/ready` | Liveness (always 200) and readiness (200/503) |
| `/api/projects` | CRUD; `POST/DELETE /{pid}/characters/{cid}`; `GET /{pid}/characters`; `GET /{pid}/output-dir`; `POST /{pid}/generate-thumbnail` |
| `/api/characters` | CRUD |
| `/api/videos` | CRUD (`GET ?project_id=` required); `POST /{vid}/youtube/upload`, `/{vid}/youtube/publish`; `POST /{vid}/review`, `/{vid}/scenes/{sid}/review`; `POST /{vid}/narrate` |
| `/api/scenes` | CRUD (`GET ?video_id=`); `DELETE /api/scenes?video_id=&source=system\|user` deletes scenes by `source` and re-compacts `display_order` |
| `/api/requests` | `POST`, `POST /batch`, `GET` (filters `scene_id,status,video_id,project_id`), `GET /pending`, `GET /batch-status`, `GET/PATCH /{rid}` |
| `/api/flow` | `GET /status`, `GET /credits`, `POST /generate-image`, `/generate-video`, `/generate-video-refs`, `/generate-video-omni-text`, `/generate-video-omni`, `/upscale-video`, `/check-status`, `/check-omni-status`, `/refresh-urls/{project_id}`, `GET /media/{media_id}`, `POST /edit-image`, `/export-image`, `/upload-image` |
| `/api/tts` | `POST /generate`; templates `POST`, `GET`, `GET/DELETE /{name}` |
| `/api/music` | Suno: templates, `generate`, `tasks/{id}` (+`/poll`, `/download`), `generate-lyrics`, `extend`, `vocal-removal`, `convert-to-wav`, `callback`, `credits` |
| `/api/materials` | `GET`, `GET /{id}`, `POST`, `DELETE /{id}` |
| `/api/models` | `GET`, `PATCH` |
| `/api/providers` | `GET` (`?live=true`), `GET /models?provider=`, `PATCH` |
| `/api/ai` | `GET /status`, `POST /story-plan`, `POST /youtube-metadata`, `GET /generations?project_id=`, `GET /generations/{id}`, `POST /generations/{id}/apply` |
| `/api/active-project` | `GET`, `PUT`, `DELETE` (persists `agent/active_project.json`) |
| `/api/ext/callback` | Extension response delivery (secret-authenticated) |
| `/ws/dashboard` | Dashboard event stream; origins limited to `127.0.0.1`, `localhost`, `chrome-extension://` |

## 10. Media pipeline

- **Generated media** stays in Flow; the database stores Flow media ids and
  signed URLs, which expire. `POST /api/flow/refresh-urls/{project_id}` (or
  `/fk-refresh-urls`) rewrites them.
- **Low-priority ("workflow mode") videos** return the MP4 inline. The SDK
  validates the `ftyp` header, writes `output/_workflow_videos/<media_id>.mp4`
  **relative to the process working directory** (not `OUTPUT_DIR`), and stores
  a `file://<absolute path>` URL as the scene's video URL. Start the agent from
  the repository root.
- **Project output layout:** `GET /api/projects/{pid}/output-dir` creates
  `output/<project-slug>/` with `scenes, 4k, tts, narrated, trimmed, norm,
  thumbnails, subclips, review` and a `meta.json`.
- **ffmpeg helpers** (`post_process.py`): `trim_video`, `merge_videos`,
  `add_narration`, `add_music`. Each runs through `media_process.run_media_command`
  with a 120 s timeout, an atomic temp-file output, and ffprobe validation of
  the result. Only `add_narration` is called by the API (narration mix);
  download/normalize/concat of final videos is done by the skills
  (`/fk-concat`, `/fk-concat-fit-narrator`) invoking ffmpeg directly.
- **Narration** (`POST /api/videos/{vid}/narrate`): generates one WAV per scene
  with `narrator_text` through OmniVoice (`services/tts.py`, run under
  `TTS_PYTHON_BIN`, default `python3.10`). A missing output file marks
  `narration_audio_status=FAILED`. With `mix=true`, a scene is mixed only if
  its `{orientation}_video_status` is `COMPLETED` **and**
  `{orientation}_video_url` is a plain path to an existing local file (remote
  URLs and `file://` URLs do not qualify); otherwise
  `narration_mix_status=FAILED`.
- **Music**: Suno via `sunoapi.org` (`SUNO_API_KEY`, or `api_keys.suno` in a
  `youtube/channels/*/channel_rules.json`).
- **Video review**: `services/video_reviewer.py` extracts frames with ffmpeg,
  builds contact sheets (burned-in timestamps if ffmpeg's `drawtext` works),
  and asks the CLI configured for the `video_review` role in `providers.json`
  (`claude`, `agy`, `codex`), or the Anthropic SDK when `ANTHROPIC_API_KEY` is
  set.

## 11. Readiness and logging

`/ready` checks `ffmpeg` and `ffprobe` on `PATH`, a `SELECT 1` against the
database, a write probe in `OUTPUT_DIR`, and a connected extension. The same
checks run at startup and log `Startup dependency unavailable: <name>` without
blocking startup.

Logs go to **stderr only** (no log files) in the format
`%(asctime)s [%(levelname)s] %(name)s %(message)s`, level from `LOG_LEVEL`.
Lifecycle events are single-line JSON messages with an `event` key
(`worker_started`, `job_started`, `job_completed`, `job_finished`,
`job_failed`, `job_timeout`, `job_cancelled`, `retry_scheduled`,
`extension_connected`, `extension_disconnected`, `external_request_timeout`,
`media_processing_timeout`, `backup_completed`, `backup_failed`, …). Keys
containing `token`, `cookie`, `secret`, `password`, `api_key`,
`authorization`, `credential`, etc. are replaced with `[REDACTED]`. See
[TROUBLESHOOTING.md § Logs](docs/TROUBLESHOOTING.md#logs).

## 12. YouTube pipeline

The backend contains a publishing state machine
(`services/youtube_publisher.py`) behind a `YouTubeClient` protocol, exposed at
`POST /api/videos/{vid}/youtube/upload` and `/publish`. **No concrete client is
wired in**: `set_youtube_client()` is never called, so both endpoints return
`503 YouTube publishing is not configured`. Even with a client injected, the
schema gap in §5 would make the first state write fail. Details and the
intended states: [YOUTUBE_PUBLISHING.md](docs/YOUTUBE_PUBLISHING.md).

The `/fk-youtube-upload` skill uses scripts under `youtube/` (`auth.py`,
`upload.py`, `channels/<name>/…`). That directory is gitignored and **not
included in this repository**; users must supply it.

## 13. Backup and recovery

`services/backup.py` and `python -m scripts.backup create|verify|restore`
implement an online SQLite backup plus copies of `output/` and the two config
JSON files, with a SHA-256 manifest. Optional in-process scheduling via
`BACKUP_INTERVAL_SECONDS`. Restore replaces the database file atomically and
merges media. See [BACKUP_RECOVERY.md](docs/BACKUP_RECOVERY.md).

## 14. SDK layer

`agent/sdk/` wraps the same operations in domain objects (`Project`, `Video`,
`Scene`, `Character`) with two modes: queue methods (`scene.generate_image()`
creates a `request` row for the worker) and direct methods
(`scene.execute_generate_image()` calls `OperationService` immediately). Both
paths share `result_handler.parse_result` and `apply_scene_result`. The worker
itself dispatches through `OperationService` (`worker/processor.py:_dispatch`).
