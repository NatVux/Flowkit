# Setup

Installing Flow Kit on one machine and connecting it to Google Flow. All
commands run from the repository root.

## Requirements

| Requirement | Why | Checked by |
|---|---|---|
| Python 3.10+ (CI runs 3.10 and 3.13) | Agent | `setup.sh` |
| `ffmpeg` and `ffprobe` on `PATH` | `/ready`, video review, narration mix | `setup.sh`, `/ready` |
| Google Chrome | Hosts the extension and the Flow tab | `setup.sh` (warning only) |
| A Google account with Flow access | Every generation runs in your signed-in Flow tab | — |
| `jq` (optional) | `scripts/statusline.sh` | `setup.sh` (warning only) |
| Node.js + npm (optional) | Dashboard | — |

Chrome and the agent must run on the **same host**: the extension connects to
`127.0.0.1` only.

## Installation

### Option A: `setup.sh` (macOS, Linux, WSL)

```bash
./setup.sh
```

The script checks `python3`, pip, ffmpeg and ffprobe (and aborts if any is
missing), warns if Chrome or jq is missing, creates `venv/`, installs
`requirements.txt`, checks that `agent.main` imports, and adds a Claude Code
`statusLine` entry to `.claude/settings.local.json` when jq is available.

It uses `python3` and `venv/bin/activate`, so it does **not** work with a
native Windows Python from Git Bash. On Windows, use WSL or Option B.

### Option B: manual

macOS / Linux / WSL:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Windows PowerShell:

```powershell
py -3.12 -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Install ffmpeg (which provides ffprobe) separately, e.g. `brew install ffmpeg`,
`sudo apt install ffmpeg`, or a Windows build from https://ffmpeg.org/download.html
added to `PATH`.

### AI CLI integration (optional)

Skills in `skills/fk-*.md` become slash commands through:

```bash
python setup.py                 # interactive picker
python setup.py --tool claude   # .claude/commands/fk-*.md
python setup.py --tool codex    # AGENTS.md
python setup.py --tool all
python setup.py sync            # regenerate for previously selected tools
python setup.py clean           # remove generated files
```

## Configuration

**The agent does not read `.env` files.** Every setting is an environment
variable read once at startup by `agent/config.py`. `.env.example` lists the
common ones; export them in your shell or process supervisor. Restart the agent
after changing any of them.

bash:

```bash
export FLOW_PROJECT_ID=<uuid>
```

PowerShell:

```powershell
$env:FLOW_PROJECT_ID = "<uuid>"
```

### Core

| Variable | Default | Effect |
|---|---|---|
| `FLOW_PROJECT_ID` | empty | Flow project uuid all RPCs are scoped to. Required unless every project is created with its own `flow_project_id` |
| `FLOW_ALLOW_DEGRADED` | `0` | `1` downgrades Veo r2v and Veo start+end-frame chaining to plain image-to-video instead of failing |
| `DEFAULT_PAYGATE_TIER` | `PAYGATE_TIER_TWO` | Stored for the DB and dashboard; does not select a model |
| `FLOW_AGENT_DIR` | repository root | Data directory: `flow_agent.db`, `output/`, `backups/`. Must already exist. Leave unset unless you need to move data (see note) |
| `API_HOST` / `API_PORT` | `127.0.0.1` / `8100` | REST API bind address |
| `WS_HOST` / `WS_PORT` | `127.0.0.1` / `9222` | WebSocket server for the extension |
| `LOG_LEVEL` | `INFO` | Python log level |
| `GLA_RELOAD` | `0` | `1` enables uvicorn auto-reload (development) |

> **Ports are hard-coded in the extension.** `extension/background.js` connects
> to `ws://127.0.0.1:9222` and posts results to
> `http://127.0.0.1:8100/api/ext/callback`, and `manifest.json` only grants
> `http://127.0.0.1:8100/*`. Changing `API_PORT`/`WS_PORT` without editing
> those files breaks the bridge.

> **`FLOW_AGENT_DIR` note.** `.env.example` sets `FLOW_AGENT_DIR=./agent`. If
> exported, that places the database at `agent/flow_agent.db` and media in
> `agent/output/`, and backups then skip `models.json`/`providers.json` (they
> are looked up at `$FLOW_AGENT_DIR/agent/`). A relative value also depends on
> the working directory.

### Worker

| Variable | Default | Effect |
|---|---|---|
| `POLL_INTERVAL` | `5` | Seconds between queue polls |
| `MAX_CONCURRENT_REQUESTS` | `5` | Parallel Flow operations |
| `API_COOLDOWN` | `10` | Minimum seconds between operation starts |
| `MAX_RETRIES` | `5` | Counted failures before `FAILED` |
| `RETRY_JITTER_SECONDS` | `3` | Random jitter added to backoff |
| `VIDEO_POLL_INTERVAL` | `10` | Seconds between video/upscale status polls |
| `VIDEO_POLL_TIMEOUT` | `420` | Seconds to wait for a video operation |
| `WORKER_OPERATION_TIMEOUT` | `900` | Hard timeout per request |
| `STALE_PROCESSING_TIMEOUT` | `600` | Age at which a `PROCESSING` request is reset at startup |
| `BACKUP_INTERVAL_SECONDS` | `0` | In-process backup interval; `0` disables |

### Media, review, music

| Variable | Default | Effect |
|---|---|---|
| `TTS_PYTHON_BIN` | `python3.10` | Interpreter with OmniVoice installed |
| `TTS_MODEL` / `TTS_DEVICE` / `TTS_SAMPLE_RATE` | `k2-fsa/OmniVoice` / `cpu` / `24000` | OmniVoice settings |
| `REVIEW_FPS_LIGHT` / `REVIEW_FPS_DEEP` | `4` / `8` | Frame extraction rate for review |
| `REVIEW_MAX_FRAMES` | `64` | Frame cap per review |
| `REVIEW_SHEET_COLS` / `REVIEW_SHEET_ROWS` | `3` / `3` | Contact-sheet grid |
| `REVIEW_CLI_TIMEOUT_S` | `120` | Timeout per review CLI call |
| `ANTHROPIC_API_KEY` | empty | If set, video review uses the Anthropic SDK |
| `REVIEW_MODEL` | `claude-haiku-4-5-20251001` | Model for SDK review |
| `SUNO_API_KEY` | empty | Suno key; falls back to `api_keys.suno` in `youtube/channels/*/channel_rules.json` |
| `SUNO_BASE_URL` / `SUNO_MODEL` | `https://api.sunoapi.org` / `V4` | Suno API |
| `SUNO_CALLBACK_URL` | `http://{API_HOST}:{API_PORT}/api/music/callback` | Suno callback |
| `SUNO_POLL_INTERVAL` / `SUNO_POLL_TIMEOUT` | `5` / `600` | Suno polling |

### AI content planning (optional)

| Variable | Default | Effect |
|---|---|---|
| `GEMINI_API_KEY` | empty | Enables Gemini story/scene/metadata planning. Secret; never logged |
| `AI_PROVIDER` | empty (auto) | `gemini`, `mock` or `none` |
| `GEMINI_MODEL` | `gemini-flash-latest` | Gemini model id |
| `GEMINI_TIMEOUT_SECONDS` / `GEMINI_MAX_RETRIES` | `90` / `2` | Per-attempt timeout; retries for retryable errors |

See [AI_CONTENT.md](AI_CONTENT.md).

### Runtime-editable JSON

- `agent/models.json` — video/image/upscale model keys. View with
  `GET /api/models`, change with `PATCH /api/models` or `/fk-change-model`.
- `agent/providers.json` — AI CLI, model and effort for the `video_review`
  role. View with `GET /api/providers`, change with `PATCH /api/providers`,
  the dashboard Settings page, or `/fk-change-provider`.

Both are applied without a restart and are tracked in git, so local changes
show up in `git status`.

## Chrome extension setup

1. Open `chrome://extensions`.
2. Enable **Developer mode**.
3. **Load unpacked** → select the repository's `extension/` directory.
4. Confirm the card shows **Flow Kit 0.3.2**.

After pulling changes to `extension/`, click the reload icon on the card.
The toolbar popup and the side panel show connection state and a request log.

## Flow connection

1. Open https://flow.google.com/ in the same Chrome profile and sign in.
   **Leave the tab open**; every generation runs inside it.
2. Create a project in the Flow UI and copy its uuid from the URL.
3. Export it: `export FLOW_PROJECT_ID=<uuid>` (or `$env:FLOW_PROJECT_ID="<uuid>"`).
4. Start the agent from the repository root:

   ```bash
   python -m agent.main
   ```

5. Verify:

   ```bash
   curl -s http://127.0.0.1:8100/health
   curl -s http://127.0.0.1:8100/ready
   curl -s http://127.0.0.1:8100/api/flow/status
   ```

Expected when everything is connected:

- `/health` → `{"status":"ok","version":"1.3.1","extension_connected":true,"ws":{…}}`
- `/ready` → HTTP 200, `"ready": true`, with `ffmpeg`, `ffprobe`, `database`,
  `media_directory`, `extension` all `"ok": true`. Any failing check gives HTTP 503.
- `/api/flow/status` →
  `{"connected":true,…,"transport":"batch","flow_project_id":"<uuid>","allow_degraded":false,"flow_key_present":false}`

`flow_key_present: false` is normal: the batchexecute transport has no bearer
token.

### One Flow project per Flow Kit project

`POST /api/projects` stores the Flow project uuid as the local project id. The
first project can use the pinned `FLOW_PROJECT_ID`; each further project needs
its own Flow project uuid in the request body:

```json
{"name": "Second project", "flow_project_id": "<another-flow-project-uuid>", "material": "realistic"}
```

Reusing a uuid that already has a local project fails with HTTP 500.
`POST /api/projects` also requires the extension to be connected (503 otherwise).

## Dashboard (optional)

```bash
cd dashboard
npm install
npm run dev
```

Open http://localhost:5173. Vite proxies `/api`, `/ws` and `/health` to
`127.0.0.1:8100` (see `dashboard/vite.config.ts`), so the agent must be
running.

## TTS (optional)

Narration uses OmniVoice in a separate interpreter. See
`skills/fk-gen-tts-template.md` for installation. Point the agent at it with
`TTS_PYTHON_BIN=/path/to/python`.
