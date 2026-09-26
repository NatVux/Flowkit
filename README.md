<p align="center">
  <img src="docs/images/flowkit_banner.svg" width="720" alt="FLOW KIT" />
</p>

<p align="center">
  <a href="#license"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT"/></a>
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white" alt="Python 3.10+"/>
  <img src="https://img.shields.io/badge/Chrome-MV3-4285F4?logo=googlechrome&logoColor=white" alt="Chrome MV3"/>
  <img src="https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white" alt="FastAPI"/>
  <img src="https://img.shields.io/badge/ffmpeg-required-007808?logo=ffmpeg&logoColor=white" alt="ffmpeg"/>
  <a href="CLAUDE.md"><img src="https://img.shields.io/badge/Docs-CLAUDE.md-8A2BE2" alt="Documentation"/></a>
  <a href="https://github.com/tuannguyenhoangit-droid/google-flow-agent/stargazers"><img src="https://img.shields.io/github/stars/tuannguyenhoangit-droid/google-flow-agent?style=flat&logo=github" alt="GitHub stars"/></a>
  <a href="https://github.com/tuannguyenhoangit-droid/google-flow-agent/issues"><img src="https://img.shields.io/github/issues/tuannguyenhoangit-droid/google-flow-agent?logo=github" alt="GitHub issues"/></a>
  <a href="https://deepwiki.com/tuannguyenhoangit-droid/google-flow-agent"><img src="https://img.shields.io/badge/DeepWiki-AI%20Docs-6A3BC9" alt="DeepWiki"/></a>
</p>

---

> **Working against the new Google Flow API.** Flow moved to `flow.google.com`
> in September 2026 and stopped minting the `Bearer ya29.…` that the old
> `aisandbox-pa.googleapis.com` REST API needed. The `batchexecute` transport
> that replaces it is in place and verified end to end against the live API —
> image generation, 2K image export, and image-to-video all run green. Upgrading
> from an older Flow Kit: reload the extension (v0.3.2+) and pin
> `FLOW_PROJECT_ID`; Flow Kit can no longer create the project for you.
>
> Every Omni 1.1 Flash video mode is ported and live-verified: text-to-video,
> first frame, first+last frame, and references. Three capabilities remain
> unported on the **Veo** path because their payloads were never captured off
> the new UI — **video upscale**, **Veo reference-to-video**, and **Veo
> start+end-frame chaining**. They fail loudly with `UNSUPPORTED_ON_BATCH_API`
> instead of quietly producing the wrong thing. For the latter two, Omni covers
> the same shot with `model_family=omni_flash`, or `FLOW_ALLOW_DEGRADED=1` drops
> them to plain i2v; video upscale has no fallback. To restore one properly see
> [`docs/CAPTURE.md`](docs/CAPTURE.md).

---

# FLOW KIT

Standalone local system to generate AI videos via Google Flow. A Python FastAPI agent, SQLite database, background worker, local media helpers, React dashboard, and Chrome MV3 extension work together on one machine. The extension runs `batchexecute` requests inside a real signed-in `flow.google.com` tab; this is not a headless integration.

## Documentation

| Document | Contents |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Components, repository layout, database, worker, request and scene lifecycles, media and YouTube pipelines |
| [docs/SETUP.md](docs/SETUP.md) | Installation, configuration, Chrome extension, Flow connection |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Dev environment, dashboard, extension, conventions |
| [docs/TEST_STRATEGY.md](docs/TEST_STRATEGY.md) | Running tests, CI, current baseline |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Running as a service, health checks, upgrades |
| [docs/BACKUP_RECOVERY.md](docs/BACKUP_RECOVERY.md) | Backup, verify, restore, recovery scenarios |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Common failures, logs, diagnosis |
| [docs/REQUEST_LIFECYCLE.md](docs/REQUEST_LIFECYCLE.md) · [docs/SCENE_PIPELINE.md](docs/SCENE_PIPELINE.md) · [docs/CHARACTER_REFERENCES.md](docs/CHARACTER_REFERENCES.md) | Job and asset state machines |
| [docs/EXTENSION_PROTOCOL.md](docs/EXTENSION_PROTOCOL.md) · [docs/IMAGE_API.md](docs/IMAGE_API.md) · [docs/OMNI_FLASH.md](docs/OMNI_FLASH.md) · [docs/CAPTURE.md](docs/CAPTURE.md) | Flow bridge and payload details |
| [docs/YOUTUBE_PUBLISHING.md](docs/YOUTUBE_PUBLISHING.md) | YouTube status |
| [docs/AI_CONTENT.md](docs/AI_CONTENT.md) | Optional Gemini planning: stories, scenes, prompts, narration, YouTube metadata |
| [docs/PIPELINE_RUNNER.md](docs/PIPELINE_RUNNER.md) | Server-side refs → images → videos → concat for one video, with checkpoints, redo and restart recovery |

## Current Implementation

- Flow transport: `batchexecute` through the Chrome extension and a signed-in Flow page.
- Job states: `PENDING`, `PROCESSING`, `COMPLETED`, `FAILED`, with persisted retry backoff and startup recovery of stale jobs.
- Scene assets: image, video, optional upscale, narration audio and narration mix, with dependency invalidation.
- Character references: versioned immutable assets with per-scene snapshots.
- Backups: SQLite online backup plus media/config manifest verification.
- Deployment: local-first; the Chrome/Flow bridge must run in an interactive browser session.
- Optional AI planning: with `GEMINI_API_KEY` set, Gemini drafts stories, characters, scene prompts, narration and YouTube metadata, which are validated and then applied to the database. Flow still renders all media. See [docs/AI_CONTENT.md](docs/AI_CONTENT.md).

### Known issues on `main`

- **Backend YouTube publishing is not usable.** No YouTube client is wired in (endpoints return 503) and the `video` table lacks the publishing columns. The `/fk-youtube-upload` skill needs a `youtube/` directory that is not in this repository. See [docs/YOUTUBE_PUBLISHING.md](docs/YOUTUBE_PUBLISHING.md).
- **Narration mixing** only works when a scene's video URL column holds a plain local file path.
- 8 unit tests fail on any platform because of the YouTube schema gap and two out-of-date test fixtures. See [docs/TEST_STRATEGY.md](docs/TEST_STRATEGY.md#current-baseline).

## Showcase

All outputs below were generated end-to-end by this system — from story concept to final YouTube-ready video with thumbnails, narration, and branding.

### Generated YouTube Thumbnails

<p align="center">
  <img src="docs/images/thumbnail_hormuz.jpg" width="400" alt="Hormuz Strait naval blockade thumbnail" />
  <img src="docs/images/thumbnail_f15e_rescue.jpg" width="400" alt="F-15E pilot rescue thumbnail" />
</p>
<p align="center">
  <img src="docs/images/thumbnail_operation_resolve.jpg" width="400" alt="Operation Absolute Resolve thumbnail" />
  <img src="docs/images/thumbnail_tapalpa.jpg" width="400" alt="Tapalpa cartel operation thumbnail" />
</p>
<p align="center">
  <img src="docs/images/thumbnail_north_korea.jpg" width="400" alt="North Korea defection thumbnail" />
  <img src="docs/images/thumbnail_iran_israel.jpg" width="400" alt="Iran vs Israel conflict thumbnail" />
</p>

### Visual Consistency Across Scenes

The reference image system keeps characters consistent across an entire video. Each character is generated once as a reference, then the AI uses that reference in every scene — maintaining the same face, clothing, and features.

**Doctor character** — same face, glasses, white coat across 4 different scenes:

<p align="center">
  <img src="docs/images/scene_nk_doctor_surgery.jpg" width="200" alt="Doctor in surgery" />
  <img src="docs/images/scene_nk_doctor_operating.jpg" width="200" alt="Doctor in operating theater" />
  <img src="docs/images/scene_nk_doctor_interview1.jpg" width="200" alt="Doctor interview — gesturing" />
  <img src="docs/images/scene_nk_doctor_interview2.jpg" width="200" alt="Doctor interview — smiling" />
</p>

**Defector character** — same face across ICU, hospital, interview, and Seoul streets:

<p align="center">
  <img src="docs/images/scene_nk_defector_icu.jpg" width="200" alt="Defector in ICU" />
  <img src="docs/images/scene_nk_defector_hospital.jpg" width="200" alt="Defector in hospital with nurse" />
  <img src="docs/images/scene_nk_defector_interview.jpg" width="200" alt="Defector interview" />
  <img src="docs/images/scene_nk_defector_seoul.jpg" width="200" alt="Defector walking Seoul streets" />
</p>

<sub>All frames from a single 50-scene project. Both characters maintain consistent appearance across completely different settings and lighting conditions — powered by the reference image system.</sub>

### F-15E Rescue — Full Story Arc (25 scenes)

<p align="center">
  <img src="docs/images/scene_f15e_map.jpg" width="260" alt="Scene 1: Strategic map overview" />
  <img src="docs/images/scene_f15e_pilot.jpg" width="260" alt="Scene 3: Pilot walks from F-15E" />
  <img src="docs/images/scene_f15e_formation.jpg" width="260" alt="Scene 6: F-15E formation refueling" />
</p>
<p align="center">
  <img src="docs/images/scene_f15e_hit.jpg" width="260" alt="Scene 10: F-15E hit at night" />
  <img src="docs/images/scene_f15e_csar.jpg" width="260" alt="Scene 15: CSAR command center alert" />
  <img src="docs/images/scene_f15e_survival.jpg" width="260" alt="Scene 20: Pilot surviving in mountains" />
</p>

<sub>Strategic briefing → pilot departure → formation flight → aircraft hit → CSAR alert → pilot survival.</sub>

### Hormuz Strait — Naval Scenes

<p align="center">
  <img src="docs/images/scene_hormuz_patrol.jpg" width="400" alt="Iranian patrol boats in formation" />
  <img src="docs/images/scene_hormuz_bridge.jpg" width="400" alt="US Navy commander on bridge" />
</p>
<p align="center">
  <img src="docs/images/scene_hormuz_ciws.jpg" width="400" alt="CIWS engagement at sea" />
  <img src="docs/images/scene_hormuz_sunset.jpg" width="400" alt="Warship sailing into sunset" />
</p>

### What the Pipeline Produces

Each project can go through: **story → entities → versioned reference images → scene images → video clips → optional upscale → narration (TTS) → final mix → thumbnails → optional YouTube upload/publish through the configured adapter**.

| Output | Description |
|--------|-------------|
| Reference images | One per character/location/prop — maintains visual consistency |
| Scene images | Composed using all referenced entities |
| 8-second video clips | Generated from scene images with camera motion + sound effects |
| Upscale | Optional where the selected Flow capability is supported; unsupported Veo upscale fails explicitly |
| Narrator TTS | Voice-cloned narration per scene |
| Final video | All clips concatenated, trimmed to narrator timing |
| Thumbnails | YouTube-optimized with text overlays + branding |
| YouTube publishing | Persisted upload/publish state through the mockable YouTube adapter; OAuth client wiring is deployment-specific |

---

### Chrome Extension — Live Dashboard

<p align="center">
  <img src="docs/images/extension_screenshot.jpg" width="800" alt="Chrome extension showing request log, video generation progress, and Google Flow interface" />
</p>

<sub>The Chrome extension runs alongside Google Flow — showing real-time request log (614 total, 328 success), video generation progress, and token status. The Python agent communicates with the extension via WebSocket to automate all API calls.</sub>

---

### Web Dashboard — Ops Console

A local React dashboard (`dashboard/`) for monitoring and driving the pipeline — real-time KPIs, per-video stage progress, a scene-level pipeline view with AI review, and a setup guide, all backed by the same FastAPI agent. Supports English, Vietnamese, Hindi, Indonesian, Chinese, Korean, and Japanese.

<p align="center">
  <img src="docs/images/dashboard_overview.png" width="800" alt="Dashboard home screen with KPI cards, pipeline throughput table, needs-attention panel, and live event stream" />
</p>

<p align="center">
  <img src="docs/images/dashboard_pipeline.png" width="380" alt="Scene pipeline view with stage rail (Refs/Images/Videos/Upscale) and per-scene status cards" />
  <img src="docs/images/dashboard_project_detail.png" width="380" alt="Project detail overview tab with editable fields, narrator settings, and stage rollup" />
</p>

<p align="center">
  <img src="docs/images/dashboard_guide.png" width="380" alt="Built-in setup guide with live extension connection status" />
  <img src="docs/images/dashboard_i18n.png" width="380" alt="Dashboard rendered in Japanese, demonstrating the built-in multi-language support" />
</p>

## Architecture

```
┌──────────────────┐     WebSocket      ┌──────────────────────┐     ┌──────────────────┐
│  Python Agent    │◄──────────────────►│  Chrome Extension     │────►│  flow.google.com │
│  (FastAPI+SQLite)│    127.0.0.1:9222  │  (MV3 Service Worker) │     │  (signed-in tab) │
│                  │                    │                       │     │                  │
│  - REST API :8100│  ── commands ───►  │  - reCAPTCHA mint     │     │  batchexecute    │
│  - Queue worker  │  ◄── callback ───  │  - runs the RPC in    │     │  cookie + `at`   │
│  - Media helpers │  POST /api/ext/    │    the page's world   │     │                  │
│  - SQLite DB     │       callback     │                       │     │                  │
└──────────────────┘                    └──────────────────────┘     └──────────────────┘
```

Flow signs every call with the session cookie plus a per-page `at` token, and a
generate also carries a single-use reCAPTCHA. None of that can be replayed from
outside the browser, so the agent builds the request and the **page** issues it.
One signed-in Flow tab has to stay open; nothing here works headless.

Full design: [ARCHITECTURE.md](ARCHITECTURE.md).

## Quick Start

Full instructions, including Windows: [docs/SETUP.md](docs/SETUP.md).

### Windows, daily use (no terminal)

**Double-click `start.bat`** in the flowkit folder. It:

1. prepares the dashboard when `dashboard/dist` is missing or older than the
   dashboard sources ("Đang chuẩn bị giao diện…": `npm ci` the first time, then
   `npm run build`; needs [Node.js LTS](https://nodejs.org) installed once);
2. starts the server (`.venv\Scripts\python.exe -m agent.main`, a minimised
   window — closing it stops the server) unless it is already running;
3. opens **http://127.0.0.1:8100**, where the server itself serves the dashboard.

Keep one signed-in `flow.google.com` tab open. The server listens on 127.0.0.1
only. Logs: `output\logs\server.log`.

### Install

```bash
./setup.sh          # macOS / Linux / WSL: checks Python 3.10+, pip, ffmpeg, ffprobe, Chrome; creates venv/; installs deps
```

or manually:

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt       # plus ffmpeg/ffprobe on PATH
```

### Run

```bash
# 1. Load the extension: chrome://extensions → Developer mode → Load unpacked → extension/
# 2. Open https://flow.google.com/ and sign in — leave the tab open
# 3. Create a project in the Flow UI and copy its uuid out of the URL
export FLOW_PROJECT_ID=<that uuid>    # the agent reads env vars only; .env files are not loaded

# 4. Start the agent from the repository root
python -m agent.main

# 5. Verify
curl http://127.0.0.1:8100/health
# {"status":"ok","version":"1.3.1","extension_connected":true,"ws":{…}}
curl http://127.0.0.1:8100/ready
# HTTP 200 only when SQLite, the media directory, ffmpeg, ffprobe and the extension are ready; 503 lists what is missing
curl http://127.0.0.1:8100/api/flow/status
# {"connected":true,…,"transport":"batch","flow_project_id":"…","allow_degraded":false,"flow_key_present":false}
```

`flow_key_present: false` is expected — the current transport has no bearer
token. Step 3 is not optional: Flow's project-creation endpoint went with the
migration, so without a pinned project every request fails `NO_FLOW_PROJECT`.

Flow Kit uses the Flow project uuid as its own project id, so **each Flow Kit
project needs its own Flow project**: pass `flow_project_id` on
`POST /api/projects` for every project after the first.

Optional dashboard: `cd dashboard && npm install && npm run dev` →
http://localhost:5173.

### Configuration

The most important variables (full list in [docs/SETUP.md](docs/SETUP.md#configuration)):

| Env var | Default | What it does |
|---------|---------|--------------|
| `FLOW_PROJECT_ID` | — | The Flow project RPCs are scoped to. Required unless each project passes `flow_project_id` |
| `FLOW_ALLOW_DEGRADED` | `0` | `1` lets Veo chaining and r2v fall back to plain i2v instead of failing |
| `MAX_CONCURRENT_REQUESTS` | `5` | Parallel Flow operations |
| `API_COOLDOWN` | `10` | Minimum seconds between operation starts |
| `MAX_RETRIES` | `5` | Counted failures before a request is `FAILED` |
| `BACKUP_INTERVAL_SECONDS` | `0` | In-process backup interval; `0` disables |
| `LOG_LEVEL` | `INFO` | Backend log level (logs go to stderr) |

`API_PORT` (8100) and `WS_PORT` (9222) exist but are hard-coded in the
extension; changing them breaks the bridge.

### Image API

The migrated image path supports Nano Banana Pro, Nano Banana 2 and Nano Banana
2 Lite, all five current aspect ratios, 1-4 outputs, true base-image editing and
native 2K image export. Exact future Flow image model wire ids pass through
without being silently replaced by the default model. See
[`docs/IMAGE_API.md`](docs/IMAGE_API.md).

### Upload images from API callers

External callers should upload bytes instead of passing caller-local filesystem paths. The recommended direct-file endpoint is multipart:

```bash
curl -X POST http://127.0.0.1:8100/api/flow/upload-image-file \
  -F 'file=@./source.jpg;type=image/jpeg'
```

JSON-only clients can use `POST /api/flow/upload-image` with `image_base64`. Base64 costs roughly 33% more request bytes than multipart, but avoids filesystem visibility problems.

`file_path` remains a server-local convenience mode only. The path is opened by the FlowKit service user, so it must be readable and visible inside that service's namespace. In systemd deployments with `PrivateTmp=yes`, a caller's `/tmp/...` is not the same `/tmp` seen by FlowKit. Permission failures return 403; paths invisible in the service namespace return a descriptive 404.

When `project_id` is omitted on the maintained session-project path, the upload uses/creates the current Flow session project.

### What does not work on the new API yet

Three capabilities have no captured payload, so they fail with
`UNSUPPORTED_ON_BATCH_API` rather than quietly producing the wrong thing:

| Capability | Status | Workaround |
|---|---|---|
| Veo video upscale (`UPSCALE_VIDEO`) | unported | none — keep the original render |
| Veo reference-to-video (r2v) | unported | Omni r2v (`model_family=omni_flash`), or `FLOW_ALLOW_DEGRADED=1` → i2v off the first reference |
| Veo start+end-frame chaining (`/fk-gen-chain-videos`) | unported | Omni first+last (`model_family=omni_flash`), or `FLOW_ALLOW_DEGRADED=1` → i2v off the start frame |
| Omni Flash text-to-video | ported | `POST /api/flow/generate-video-omni-text` (4/6/8/10s) |
| Omni Flash frame / first+last / reference modes | ported | `POST /api/flow/generate-video` with `model_family=omni_flash` |

Restoring one starts with a capture, not a guess: [`docs/CAPTURE.md`](docs/CAPTURE.md).

## End-to-End Example: "Pippip the Fish Merchant"

A chubby cat sells fish at a market. 3 scenes, vertical, Pixar 3D style.

### How it works (read this first)

The system uses **reference images** to keep visuals consistent across scenes:

**1. Identify every visual element** that should look the same across scenes:
- Characters → `entity_type: "character"`
- Places → `entity_type: "location"`
- Important objects → `entity_type: "visual_asset"`

**2. Describe ONLY appearance** in the entity `description` — this generates the reference image.

**3. Write scene prompts as ACTION** — reference entities by name, describe what they DO.

**4. List all entities that appear** in each scene's `character_names` array — their reference images are passed to Flow as visual input.


### Using Skills (recommended)

Skills handle the API calls, polling and verification. Use with Claude Code (`/fk-command`) or follow the recipe in `skills/*.md` for any AI agent.

```
/fk-create-project             ← interactive: asks story, creates entities + scenes
/fk-gen-refs <project_id>      ← generates all reference images, verifies UUIDs
/fk-gen-images <pid> <vid>     ← generates scene images with all refs applied
/fk-gen-videos <pid> <vid>     ← generates videos (2-5 min each, polls automatically)
/fk-concat <vid>               ← downloads + merges into final video
/fk-status <pid>               ← dashboard: what's done, what's next
```

### Manual API (step by step)

<details>
<summary>Click to expand raw curl commands</summary>

#### Step 1: Create project with reference entities

```bash
curl -X POST http://127.0.0.1:8100/api/projects \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Pippip the Fish Merchant",
    "material": "3d_pixar",
    "story": "Pippip is a chubby orange tabby cat who sells fish at a Southeast Asian open market.",
    "characters": [
      {"name": "Pippip", "entity_type": "character", "description": "Chubby orange tabby cat with big green eyes, blue apron, straw hat. Walks upright."},
      {"name": "Fish Stall", "entity_type": "location", "description": "Small rustic wooden market stall with thatched bamboo roof, crushed ice display."},
      {"name": "Open Market", "entity_type": "location", "description": "Bustling Southeast Asian open-air market with colorful awnings, hanging lanterns."},
      {"name": "Golden Fish", "entity_type": "visual_asset", "description": "Golden koi fish with shimmering iridescent scales, slight magical glow."}
    ]
  }'
# Requires the extension to be connected. Uses FLOW_PROJECT_ID unless "flow_project_id" is given.
# The response "id" is <PID>. Character ids: GET /api/projects/<PID>/characters
```

#### Step 2: Create video + scenes

```bash
curl -X POST http://127.0.0.1:8100/api/videos \
  -H "Content-Type: application/json" \
  -d '{"project_id": "<PID>", "title": "Pippip Episode 1"}'

curl -X POST http://127.0.0.1:8100/api/scenes \
  -H "Content-Type: application/json" \
  -d '{
    "video_id": "<VID>", "display_order": 0,
    "prompt": "Pippip stands behind Fish Stall, arranging fresh fish on ice. Sunrise in Open Market.",
    "character_names": ["Pippip", "Fish Stall", "Open Market"],
    "chain_type": "ROOT"
  }'

curl -X POST http://127.0.0.1:8100/api/scenes \
  -H "Content-Type: application/json" \
  -d '{
    "video_id": "<VID>", "display_order": 1,
    "prompt": "Pippip leans over Fish Stall, staring at Golden Fish on empty ice.",
    "character_names": ["Pippip", "Fish Stall", "Golden Fish", "Open Market"],
    "chain_type": "CONTINUATION", "parent_scene_id": "<scene-1-id>"
  }'
```

#### Step 3-5: Queue refs → images → videos

```bash
curl -X POST http://127.0.0.1:8100/api/requests/batch \
  -H "Content-Type: application/json" \
  -d '{"requests": [
    {"type": "GENERATE_CHARACTER_IMAGE", "character_id": "<CID1>", "project_id": "<PID>"},
    {"type": "GENERATE_CHARACTER_IMAGE", "character_id": "<CID2>", "project_id": "<PID>"}
  ]}'

curl -X POST http://127.0.0.1:8100/api/requests/batch \
  -H "Content-Type: application/json" \
  -d '{"requests": [
    {"type": "GENERATE_IMAGE", "scene_id": "<SID1>", "project_id": "<PID>", "video_id": "<VID>", "orientation": "VERTICAL"},
    {"type": "GENERATE_VIDEO", "scene_id": "<SID1>", "project_id": "<PID>", "video_id": "<VID>", "orientation": "VERTICAL"}
  ]}'
# Video requests wait (without using retries) until the scene image exists.

curl -s "http://127.0.0.1:8100/api/requests/batch-status?video_id=<VID>"
curl -s "http://127.0.0.1:8100/api/scenes?video_id=<VID>"   # image/video URLs
```

Downloading and concatenating the clips is done with ffmpeg (the `/fk-concat` skill); there is no concat endpoint.

</details>

---

## Core Concepts

### Reference Image System

Every visual element that should stay consistent gets a **reference image** — characters, locations, props. Each reference has a UUID `media_id` that is passed to scene generations. Regenerating a reference creates a new version and resets every scene that uses it.

### Scene Prompts = Action Only

```
DO:   "Pippip juggling fish at Fish Stall, crowd watching in Open Market"
DON'T: "Pippip the chubby orange tabby cat wearing a blue apron juggling..."
```

### Media ID = UUID

All `media_id` values are UUID format (`xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`). Never the base64 `CAMS...` mediaGenerationId.

### Two Prompts per Scene

- `prompt` (or `image_prompt`) — the **still image** (frame 0).
- `video_prompt` — the **motion**, typically with sub-clip timing and camera directions:

```
0-3s: Wide crane down, Luna steps out of rocket onto Candy Planet Surface. Luna gasps "It's beautiful!"
3-6s: Low angle tracking shot, Luna walks across candy ground, shallow DOF.
6-8s: Close-up Luna's face, eyes wide with wonder, golden hour backlight.
```

### Automatic prompt additions

Before a video is generated the agent appends:

- `Character voices: …` from each scene character's `voice_description` when the prompt contains dialogue;
- an `Audio: … no background music …` line, unless the project has `allow_music` or the prompt already has an `Audio:`/`Music:` label;
- a `Negative: subtitles, captions, watermark, …` line unless one is present.

## Pipeline Overview

```
1. Create project      POST /api/projects (entities + story + material)
2. Create video        POST /api/videos
3. Create scenes       POST /api/scenes (chain_type: ROOT → CONTINUATION)
4. Gen ref images      POST /api/requests/batch {type: GENERATE_CHARACTER_IMAGE}
5. Gen scene images    POST /api/requests/batch {type: GENERATE_IMAGE}
6. Gen videos          POST /api/requests/batch {type: GENERATE_VIDEO}   (2-5 min each)
7. (Upscale)           UPSCALE_VIDEO — unported on the Veo path
8. Narration           PATCH narrator_text, POST /api/videos/{vid}/narrate
9. Download + concat   ffmpeg via /fk-concat or /fk-concat-fit-narrator
```

Lifecycle details: [docs/SCENE_PIPELINE.md](docs/SCENE_PIPELINE.md), [docs/REQUEST_LIFECYCLE.md](docs/REQUEST_LIFECYCLE.md).

## Skills (AI Agent Workflows)

Ready-to-use workflow recipes in `skills/` (also available as `/slash-commands` in Claude Code):

### Basic Pipeline

| Skill | Description |
|-------|-------------|
| `/fk-create-project` | Create project + entities + video + scenes interactively |
| `/fk-research` | Fact-check story details before scripting |
| `/fk-gen-refs` | Generate reference images for all entities |
| `/fk-gen-images` | Generate scene images with character refs |
| `/fk-gen-videos` | Generate videos from scene images (Veo video upscale is currently unported — see [What does not work yet](#what-does-not-work-on-the-new-api-yet)) |
| `/fk-concat` | Download + merge all scene videos |
| `/fk-pipeline` | Smart full-pipeline orchestrator — runs the whole chain end to end |
| `/fk-monitor` | Live monitor for a running pipeline |

### Advanced Video

| Skill | Description |
|-------|-------------|
| `/fk-gen-chain-videos` | Start+end frame chaining for smooth transitions (Veo path unported; use Omni Flash or `FLOW_ALLOW_DEGRADED=1`) |
| `/fk-insert-scene` | Multi-angle shots, cutaways, close-ups within a chain |
| `/fk-creative-mix` | Analyze story + suggest all techniques (chain, insert, r2v, parallel) |

### Review & Quality

| Skill | Description |
|-------|-------------|
| `/fk-review-video` | AI vision scoring of generated scene videos (quality, consistency, usability) — see [AI Vision Providers](#ai-vision-providers-video-review) below |
| `/fk-review-board` | Visual scene-by-scene review board for feedback before locking a cut |
| `/fk-change-provider` | View/switch the AI CLI, model and effort behind `/fk-review-video` |

### Reference

| Skill | Description |
|-------|-------------|
| `/fk-camera-guide` | Camera angles, movements, lighting, DOF for cinematic video prompts |
| `/fk-thumbnail-guide` | Hook-worthy thumbnail design rules |

### TTS & Narration

| Skill | Description |
|-------|-------------|
| `/fk-gen-tts-template` | Create a voice template for consistent narration |
| `/fk-import-voice` | Import an existing voice recording as a template |
| `/fk-gen-narrator` | Generate narrator text + TTS for all scenes |
| `/fk-gen-text-overlays` | Generate text overlays from narrator text (dates, locations, stats) |
| `/fk-concat-fit-narrator` | Trim scene videos to fit narrator duration, then concat |
| `/fk-gen-music` | Generate background music via Suno |

### YouTube

| Skill | Description |
|-------|-------------|
| `/fk-youtube-seo` | Generate SEO-optimized title, description, tags |
| `/fk-brand-logo` | Apply channel icon watermark to video/thumbnails |
| `/fk-youtube-upload` | Upload to YouTube with rule validation + scheduling |
| `/fk-thumbnail` | Generate YouTube-optimized thumbnails |

### Utilities

| Skill | Description |
|-------|-------------|
| `/fk-status` | Full project dashboard + recommended next action |
| `/fk-switch-project` | Switch the active project |
| `/fk-fix-uuids` | Repair any CAMS... media_ids to UUID format |
| `/fk-refresh-urls` | Refresh expired GCS signed URLs for images/videos |
| `/fk-upload-image` | Upload a local image to get a `media_id` |
| `/fk-add-material` | Image material system |
| `/fk-change-model` | View/switch video, image, and upscale model keys |
| `/fk-dashboard` | Live status in the Claude Code statusline |
| `/fk-doctor` | Diagnose any error (Flow API, extension, worker, YouTube) and prescribe a fix |

### AI CLI Compatibility (Skill Consumption)

Skills are `.md` recipes any AI coding-assistant CLI can read and follow — this is about **which agent reads the skill files**, not which model does the work:

| CLI | Instructions | How skills work |
|-----|-------------|-----------------|
| Claude Code | `CLAUDE.md` (auto-loaded) | Native `/fk-*` slash commands |
| Codex CLI | `AGENTS.md` → reads `CLAUDE.md` | User says `/fk-<name>`, agent reads `skills/fk-<name>.md` |

The Gemini CLI target was dropped in v1.3.1 — the CLI is retired, and its
replacement `agy` reads none of what that target generated (see the changelog).
`agy` is still supported, as one of the three CLIs that can run video review —
that is configured in `agent/providers.json`, not by `setup.py`.

### AI Vision Providers (Video Review)

Separate from the table above — this is about **which CLI backend does the vision analysis** for `/fk-review-video`. Three providers are supported and swappable at runtime, no restart required:

| Provider | Binary | Reasoning efforts | Model catalog | Setup |
|----------|--------|-------------------|---------------|-------|
| `claude` | Claude Code CLI | `low` `medium` `high` `xhigh` `max` | aliases (`sonnet`, `opus`, `haiku`, `fable`) or any full model name | Default — works out of the box |
| `agy` | Google Antigravity CLI | `low` `medium` `high` | closed — `agy models` is the whole list and agy rejects anything else | Install separately, sign in once |
| `codex` | OpenAI Codex CLI | `low` `medium` `high` `xhigh` `max` (varies per model) | codex's own on-disk cache, plus slugs newer than it | `npm install -g @openai/codex`, then `codex login` once |

Provider, model and effort are set **per role** — a role being a job an AI CLI
does for Flow Kit. There is one today, `video_review`; the config is a map so
the next one is an entry rather than a schema change. Model and effort may both
be `null`, meaning "whatever that CLI defaults to".

**For `agy`, model and effort are mutually exclusive.** Its slugs name their own
effort — `gemini-3.8-flash-low`, `gemini-3.1-pro-high` — so setting both is
rejected (`--model gpt-oss-120b-medium conflicts with --effort=low`), and a slug
with no effort in its name refuses `--effort` outright
(`--effort is not supported for model "claude-sonnet-4-6"`). Pick a model, or
pick an effort and let agy choose the model. The API answers 400 for the pair.

```bash
# View provider status + the current per-role config
#   live=true additionally runs `<binary> --version` on each (a few seconds)
curl -s "http://127.0.0.1:8100/api/providers?live=true" | python3 -m json.tool

# List a provider's models (refresh=true bypasses the 5-minute cache)
curl -s "http://127.0.0.1:8100/api/providers/models?provider=agy" | python3 -m json.tool

# Point a role at a provider/model/effort — hot-reloaded, no server restart
curl -X PATCH http://127.0.0.1:8100/api/providers \
  -H "Content-Type: application/json" \
  -d '{"roles": {"video_review": {"provider": "claude", "model": "sonnet", "effort": "high"}}}'

# agy takes a model OR an effort, never both — its slugs name their own effort
curl -X PATCH http://127.0.0.1:8100/api/providers \
  -H "Content-Type: application/json" \
  -d '{"roles": {"video_review": {"provider": "agy", "model": "gemini-3.8-flash-low"}}}'

# The older whole-agent switch still works. It also clears each role's model
# (a slug means nothing to a different CLI) and drops an effort the new
# provider does not have.
curl -X PATCH http://127.0.0.1:8100/api/providers \
  -H "Content-Type: application/json" -d '{"active": "agy"}'
```

Or edit it in the dashboard under **Settings**, or run `/fk-change-provider` for
an interactive picker. Full details in `skills/fk-change-provider.md`.

**`codex` needs credits on its OpenAI workspace.** `installed: true` only means
the binary is on PATH; a workspace with no balance fails every review with
`ERROR: Your workspace is out of credits`.

**Contact sheets lose their timestamps on an ffmpeg without `libfreetype`.**
Homebrew's ffmpeg 8.x is one such build: `drawtext` is simply absent, and
naming a filter that does not exist aborts the whole chain. Flow Kit probes for
it once and falls back to untimestamped frames, telling the model the frame
interval instead so it can still answer in time ranges. `ffmpeg -filters | grep
drawtext` shows whether yours has it.

## Video Generation Techniques

| Technique | API Type | Status |
|-----------|----------|--------|
| **i2v** | `GENERATE_VIDEO` | Works |
| **i2v_fl** (start+end frame) | `GENERATE_VIDEO` on a chained scene | Veo unported; Omni first+last works |
| **r2v** | `GENERATE_VIDEO_REFS` | Veo unported; Omni reference mode works |
| **Omni text-to-video** | `POST /api/flow/generate-video-omni-text` | Works |
| **Upscale** | `UPSCALE_VIDEO` | Veo unported, no fallback |

## API Reference

Interactive OpenAPI docs: http://127.0.0.1:8100/docs. Errors are returned as
`{"error": {"code", "message", "details"}}`. Full route list:
[ARCHITECTURE.md §9](ARCHITECTURE.md#9-rest-api-surface).

### CRUD Endpoints

| Resource | Create | List | Get | Update | Delete |
|----------|--------|------|-----|--------|--------|
| Project | `POST /api/projects` | `GET /api/projects` | `GET /api/projects/{id}` | `PATCH /api/projects/{id}` | `DELETE /api/projects/{id}` |
| Character | `POST /api/characters` | `GET /api/characters` | `GET /api/characters/{id}` | `PATCH /api/characters/{id}` | `DELETE /api/characters/{id}` |
| Video | `POST /api/videos` | `GET /api/videos?project_id=` | `GET /api/videos/{id}` | `PATCH /api/videos/{id}` | `DELETE /api/videos/{id}` |
| Scene | `POST /api/scenes` | `GET /api/scenes?video_id=` | `GET /api/scenes/{id}` | `PATCH /api/scenes/{id}` | `DELETE /api/scenes/{id}` |
| Request | `POST /api/requests`, `POST /api/requests/batch` | `GET /api/requests` | `GET /api/requests/{id}` | `PATCH /api/requests/{id}` | — |

### Special Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Liveness + extension connection stats |
| `GET /ready` | Readiness (200/503) for ffmpeg, ffprobe, database, media directory, extension |
| `GET /api/flow/status` | Bridge details, pinned Flow project |
| `GET /api/flow/credits` | Flow credits + tier |
| `POST /api/flow/upload-image` | Upload a local image to get a `media_id` |
| `POST /api/flow/refresh-urls/{project_id}` | Refresh expired signed URLs |
| `GET /api/requests/pending` | Pending queue |
| `GET /api/requests/batch-status` | Aggregate counts for `video_id` / `project_id` / `type` / `orientation` |
| `GET /api/projects/{id}/characters` | Entities linked to a project |
| `GET /api/projects/{id}/output-dir` | Create/return `output/<slug>/` layout |
| `POST /api/videos/{id}/narrate` | TTS narration (+ optional mix) |
| `POST /api/videos/{id}/review` | AI video review |
| `GET/PATCH /api/models` | Model keys |
| `GET/PATCH /api/providers` | Review CLI provider config |
| `GET/PUT/DELETE /api/active-project` | Active project for skills/statusline |

### Request Types

| Type | Required Fields |
|------|----------------|
| `GENERATE_CHARACTER_IMAGE`, `REGENERATE_CHARACTER_IMAGE`, `EDIT_CHARACTER_IMAGE` | `character_id`, `project_id` |
| `GENERATE_IMAGE`, `REGENERATE_IMAGE`, `EDIT_IMAGE` | `scene_id`, `project_id`, `video_id` (`orientation` optional: falls back to the video's, then `VERTICAL`) |
| `GENERATE_VIDEO`, `REGENERATE_VIDEO`, `GENERATE_VIDEO_REFS` | `scene_id`, `project_id`, `video_id` (`orientation` optional: falls back to the video's, then `VERTICAL`) |
| `UPSCALE_VIDEO` | `scene_id`, `project_id`, `video_id` (`orientation` optional: falls back to the video's, then `VERTICAL`) |

## Worker Behavior

- **Server-side throttling** — at most `MAX_CONCURRENT_REQUESTS` (5) in flight and `API_COOLDOWN` (10 s) between starts. Submit everything with `POST /api/requests/batch`; do not script your own loops.
- **FIFO claims** — pending requests are claimed oldest first; nothing is claimed while the extension is disconnected.
- **Prerequisite deferral** — video waits for the scene image, upscale for the video; deferral does not use up retries.
- **Reference check** — a scene image fails with `Waiting for reference images: …` while any named entity lacks a `media_id`.
- **Skip completed** — `GENERATE_*` / `UPSCALE_VIDEO` for an already-completed asset finish without calling Flow. Use `REGENERATE_*` to force.
- **Cascade clear** — new image resets video + upscale; new video resets upscale; new character reference resets every scene that uses it.
- **Retry** — `min(2^n × 10 s, 300 s)` backoff plus jitter; `FAILED` after `MAX_RETRIES` (5) counted failures. Configuration errors (`NO_FLOW_PROJECT`, `UNSUPPORTED_ON_BATCH_API`, …) fail immediately; extension disconnects are retried without counting; reCAPTCHA failures get up to 10 attempts.
- **Expired media** — `not found` errors on video/upscale trigger a re-upload of the scene image and a retry.
- **Stale recovery** — at startup, `PROCESSING` requests older than 10 min return to `PENDING`.

Full rules: [ARCHITECTURE.md §6–7](ARCHITECTURE.md#6-worker).

### Default Model & Tier Compatibility

The default for `PAYGATE_TIER_TWO` `frame_2_video` and `start_end_frame_2_video` in `agent/models.json` is `veo_3_1_i2v_lite_low_priority`. The `*_ultra_relaxed` family silently returns empty operations on `SERVICE_TIER_ADVANCED` accounts because Google requires `SERVICE_TIER_ULTRA` for that path. Switch models with `/fk-change-model` or `PATCH /api/models`; see `skills/fk-change-model.md`.

## Material System

A project's `material` (default `realistic`) controls the visual style of generated images.

```bash
curl -s http://127.0.0.1:8100/api/materials          # list
```

Built-in ids include `realistic`, `3d_pixar`, `anime`, `stop_motion`, `minecraft`, `oil_painting`, `ghibli`, `watercolor`, `comic_book`, `cyberpunk`, `claymation`, `lego`. Custom materials: `POST /api/materials`.

## TTS Narration (OmniVoice)

Optional narrator voice for scenes via [OmniVoice](https://github.com/tuannguyenhoangit-droid/OmniVoice), run in a separate Python interpreter.

```bash
pip install torch==2.8.0 torchaudio==2.8.0   # or +cu128 for NVIDIA
pip install omnivoice
python3 -c "from omnivoice import OmniVoice; print('OK')"
export TTS_PYTHON_BIN=/path/to/that/python   # default: python3.10
```

1. **Voice template** — `/fk-gen-tts-template` (or `/fk-import-voice`)
2. **Narrator text** — `PATCH /api/scenes/{id}` with `narrator_text`
3. **Generate** — `/fk-gen-narrator` or `POST /api/videos/{vid}/narrate`
4. **Concat** — `/fk-concat-fit-narrator` trims scene videos to the narration

CPU is the default (`TTS_DEVICE=cpu`; MPS produces artifacts). The API's own
mix step (`mix=true`) only works when the scene video URL is a local file path
— see [docs/SCENE_PIPELINE.md](docs/SCENE_PIPELINE.md#narration).

## YouTube

- `/fk-youtube-seo`, `/fk-thumbnail`, `/fk-brand-logo` generate metadata, thumbnails and watermarks.
- `/fk-youtube-upload` expects a user-supplied `youtube/` directory (`auth.py`, `upload.py`, `channels/<name>/…`), which is **not included** in this repository.
- The backend endpoints `POST /api/videos/{id}/youtube/upload|publish` exist but return 503 until a YouTube client is wired in, and the required database columns are missing.

Details: [docs/YOUTUBE_PUBLISHING.md](docs/YOUTUBE_PUBLISHING.md).

## Error Handling

The worker routes failures by the **text** of `error_message`, not by HTTP
status. Flow's structured reasons are appended as `"<message> [<reason>]"`.

| Error contains | Handling |
|---|---|
| `NO_FLOW_PROJECT`, `UNSUPPORTED_ON_BATCH_API`, `permission denied`, `unauthorized`, `forbidden`, `invalid request` | `FAILED` immediately |
| `Extension not connected` / `extension disconnected` / `extension reconnected` | Retry in 5 s, not counted |
| `captcha` / `recaptcha` (e.g. `CAPTCHA_FAILED`) | Backoff retry, up to 10 attempts |
| `not found` (expired `media_id`) | Re-upload scene image, retry |
| `NO_FLOW_TAB`, `NO_AT_TOKEN`, `FLOW_TAB_DISCARDED`, timeouts, `[PUBLIC_ERROR_*]` reasons | Default backoff retry up to `MAX_RETRIES` |

On any pipeline error, `/fk-doctor` diagnoses and prescribes a fix. Symptom →
fix tables, log format and events: [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

## Changelog

Dates are merge dates. Older releases are tagged; `git log` is the full record.

### v1.3.1 — 2026-09-20 — the dead Gemini target

| Date | Change |
|---|---|
| 2026-09-20 | **`setup.py --tool gemini` removed.** It generated `.gemini/commands/fk/*.toml` and `GEMINI.md` for a CLI that is retired, and its replacement `agy` reads neither — verified against agy 1.2.7: a project's `.gemini/commands/*.toml` is not expanded, `.claude/commands/*.md` is not either, and `GEMINI.md`, `AGENTS.md` and `CLAUDE.md` are all absent from a print-mode run's context even inside a trusted folder. `agy plugin import` imports extensions, not command files. `GEMINI.md` is deleted; `setup.py clean` still removes what the target left on disk, because nothing else ever will. `agy` remains fully supported — as one of the three CLIs that run video review, configured in `agent/providers.json` rather than by `setup.py` |
| 2026-09-20 | **`AGENTS.md` is genuinely generated again.** It says "do not edit" and had been edited anyway: rules 14-16 (fact-check, real-people bypass, review-before-upscale) and pipeline steps 0 and 7.5 lived only in the committed artifact, so `setup.py sync` would have deleted three operational rules. They are in `setup.py` now. The same drift had left the skill table listing 25 skills — missing 11 that exist and naming one that does not; it is rebuilt from `skills/` at generation time |
| 2026-09-20 | `setup.py` gains its first tests (13), including that a `.fk-setup.json` written before this release — which records `"gemini"` — is skipped with a pointer to `clean` instead of crashing the sync |

### v1.3.0 — 2026-09-20 — video review works again

Video review had been failing on every path at once, which is why nothing about
it looked fixable from the symptoms. The unit suite was red on a normal dev
machine for the whole period — 13 of these tests fail on v1.2.0 — while CI
stayed green, because the workflow installs ffmpeg *and* a font and asserts
`drawtext` renders. The one environment that ran the suite was the one
environment where it worked.

All three providers are verified end-to-end against the real CLIs: a synthetic
clip with a planted mid-clip defect, through frame extraction, contact sheets
and a live vision call, with no mocks. claude, agy and codex each find the
defect and score it, on their default model and on an explicitly selected
model + effort.

| Date | Change |
|---|---|
| 2026-09-20 | **ffmpeg without `drawtext`**: Homebrew's ffmpeg 8.x is built without `libfreetype`, so the timestamp filter does not exist and naming it aborted the whole chain — frame extraction died before any provider was reached. Probed once, with a fallback to untimestamped frames and a prompt that hands the model the frame interval instead |
| 2026-09-20 | **`agy` was auto-denied**: handed a bare file path, agy reaches for a shell command to look at the file, headless mode cannot prompt for that permission, and the run returns an empty response on a **zero** exit code. Fixed by steering it at its own file-reading tool and naming the sheet directory with `--add-dir`, which gets the read done unprivileged — so `--dangerously-skip-permissions`, which auto-approves every tool including arbitrary shell commands, is gone. Output is parsed from `--output-format json`, and a denied tool is an error whether or not agy still answered — the prompt carries the rubric and both scene prompts, so a denied run can write a plausible review from the text alone |
| 2026-09-20 | **`codex` no longer bypasses its sandbox**: `-i` hands codex the image bytes directly, so the run needs neither a shell nor a writable filesystem. `--dangerously-bypass-approvals-and-sandbox` bought nothing and cost the sandbox; `--sandbox read-only` already implies `approval: never`. An empty output file is now an error instead of a JSON decode failure three frames away |
| 2026-09-20 | **A malformed error entry no longer vanishes.** The parser required the exact keys `severity`/`time_range`/`description` and silently dropped anything else — and what it dropped was usually CRITICAL, the one severity that caps `character_consistency` at 3.0 and forces the verdict below acceptable, so `timeRange` instead of `time_range` turned an unusable video into a clean pass. The three fields are now handled by what they can cost: near-miss names are normalised, a missing time range or description is repaired and logged, and only a severity outside `{CRITICAL, HIGH, MINOR}` fails the scene — that is the one field with no safe default, because without it we do not know whether the video passed. `VideoError.severity` is a `Literal` now, so the three code paths that branch on it cannot be handed anything else |
| 2026-09-20 | **A review with no scores in it is now a failure, not a score.** Every dimension defaults to 5.0, so a CLI answer carrying no `dimensions` became a complete, plausible review — 5.0 across the board, verdict "poor", zero errors — of a video nothing had actually looked at |
| 2026-09-20 | **stdin closed for all three CLIs.** Each appends piped stdin to the prompt when stdin is not a terminal — codex documents it as a `<stdin>` block. Under uvicorn that is whatever the launching shell handed down |
| 2026-09-20 | **Per-role provider, model and effort**, editable in the dashboard under Settings or via `PATCH /api/providers`. Efforts are validated against each CLI's real ladder (agy stops at `high`); models are validated only for agy, whose catalog is closed, so a slug newer than claude's or codex's cache still goes through. A whole-agent `{"active": …}` switch clears each role's model and clamps its effort, because neither survives a change of CLI |
| 2026-09-20 | Review hardening from an adversarial pass: one review is pinned to one provider (the role was resolved per scene, so a hand edit or a dashboard poll mid-run could split a video's score across two backends); an unknown agy model is a 400 naming the known slugs instead of a failed review; CLI failures carry stdout as well as stderr (claude puts its readable sentence there); `providers.json` is written atomically (a truncated file hard-fails `config.py` at import, so the server would not boot); and the drawtext probe is an optimisation now — extraction retries untimestamped if the filter is listed but cannot render, which is what an ffmpeg with libfreetype and no font does |
| 2026-09-20 | **agy's model and effort are mutually exclusive** and the config now says so. Its slugs name their own effort (`gemini-3.8-flash-low`), so the pair is rejected — a mismatch conflicts, and a slug with no effort in its name refuses `--effort` at all |

### v1.2.0 — 2026-09-18 — the Flow migration

Flow moved to `flow.google.com` in September 2026 and stopped minting the bearer
token the old REST API needed. Everything below is that migration.

| Date | Change |
|---|---|
| 2026-09-18 | `/health` reports the app's real version again — it had been pinned at `0.2.0` since v0.2.0 while the app said `1.1.0`, because only one of the two literals was ever bumped ([#52](../../pull/52)) |
| 2026-09-18 | Omni 1.1 Flash first-frame, first+last and reference modes ported to `batchexecute` ([#48](../../pull/48), [#50](../../pull/50)). Unported capabilities drop from four to three, all on the Veo path |
| 2026-09-17 | REST transport removed — the ten `_legacy_*` methods, the `USE_BATCH_RPC` branches, the fingerprint pools and `agent/services/headers.py`; net −1043 lines ([#49](../../pull/49)) |
| 2026-09-17 | Migrated image API: variant submit, settled-wave retry, and 2K/4K image export via `SPrCad` ([#42](../../pull/42)) |
| 2026-09-15 | Video submit unified across the frame and reference paths ([#46](../../pull/46)) |
| 2026-09-15 | Omni Flash text-to-video on the batch path ([#41](../../pull/41)) |
| 2026-09-15 | Extension: idle-tab leak fixed ([#44](../../pull/44)) |
| 2026-09-07 | `batchexecute` transport added — the agent builds the envelope, the extension signs it inside a signed-in Flow tab ([#39](../../pull/39)) |

### Earlier

| Date | Change |
|---|---|
| 2026-08-18 | Omni Flash generation ([#30](../../pull/30)); MV3 flow-key bootstrap ([#24](../../pull/24)) |
| 2026-08-04 | Web dashboard rebuilt with the real pipeline UI, a guide page and i18n |
| 2026-08-04 | Video review: contact sheets split by duration rather than one giant tile; `agy` and `codex` added as review CLI providers |
| 2026-08-04 | Skill files standardised on `fk-<name>` |
| 2026-05-09 | `v1.1.0` |
| 2026-04-27 | `v1.0.2` |
| 2026-04-22 | `v1.0.1` |

## License

MIT

---

## Community & Support

<p align="center">
  <a href="https://www.facebook.com/groups/vibecodeera">
    <img src="https://img.shields.io/badge/Join%20the%20Community-Vibe%20Code%20Era%20on%20Facebook-1877F2?style=for-the-badge&logo=facebook&logoColor=white" alt="Join the Vibe Code Era Facebook Group" />
  </a>
</p>

**Share anything crazy and useful created with Vibe Code.** Drop in to:

- Post the story-video runs and thumbnails you've generated
- Share scene templates, prompt recipes, and reference-image setups
- Ask for help when an output isn't matching what you imagined
- Request features and report bugs you've hit in the wild
- Trade tips on Google Flow plan limits, Veo i2v behaviour, and Chrome extension setup
- Facebook Post via Extension MCP
- Right way to build Mobile Application + System

→ **[facebook.com/groups/vibecodeera](https://www.facebook.com/groups/vibecodeera)**
