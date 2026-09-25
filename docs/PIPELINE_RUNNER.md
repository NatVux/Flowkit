# Pipeline Runner

The server can take one video from references to a finished MP4 by itself:

```
REFS ──► IMAGES (waves: parent before child) ──► VIDEOS ──► CONCAT
```

It replaces an agent running `/fk-gen-refs`, `/fk-gen-images`, `/fk-gen-videos`
and `/fk-concat` in turn and polling in between. It does **not** call Flow: it
queues ordinary requests (the same ones those skills queue) and the worker
does the Flow calls, with its own retries and the upstream generation guard.

A run never moves on its own: it waits for `start`, stops at checkpoints for
`approve`, and a pause is only lifted by `resume`. No Gemini or Claude call is
made anywhere in the pipeline.

Code: `agent/services/pipeline/` (`planner.py` pure logic, `runner.py` loop and
operations, `media.py` downloads, `concat.py` ffmpeg), `agent/db/pipeline_crud.py`,
`agent/api/pipeline.py`.

## Quick start

```bash
# 1. Create a run: nothing is queued yet, you get the minimum cost
curl -s -X POST http://127.0.0.1:8100/api/pipeline-runs \
  -H "Content-Type: application/json" -d '{"video_id":"<VID>"}'
# → {"id":"<RUN>","status":"DRAFT","estimate_at_last_checkpoint":{"refs":3,"images":3,"videos":3,...}}

# 2. Start: queues the missing references
curl -s -X POST http://127.0.0.1:8100/api/pipeline-runs/<RUN>/start

# 3. Watch
curl -s http://127.0.0.1:8100/api/pipeline-runs/<RUN>

# 4. At each checkpoint (default: after REFS and after IMAGES), look at the
#    results, redo what you do not like, then approve the next stage
curl -s -X POST http://127.0.0.1:8100/api/pipeline-runs/<RUN>/approve
```

## Stages

| Stage | Work (ported from) | Request | Done when |
|---|---|---|---|
| **REFS** | Entities of the project without a **UUID** `media_id` — a `CAMS…` id is not done (`/fk-gen-refs`). An entity that has a reference is never regenerated: a new reference wipes the image and video of every scene using it. | `GENERATE_CHARACTER_IMAGE`, one batch | every entity has a UUID `media_id` (a CAMS id is fixed from `reference_image_url`) |
| **IMAGES** | Scenes whose `<orientation>_image_status` is not `COMPLETED` or whose image id is not a UUID. ROOT scenes first; a CONTINUATION scene only after its parent's image is done (`/fk-gen-images` waves). Independent chains run side by side. | ROOT → `GENERATE_IMAGE`; CONTINUATION → `EDIT_IMAGE` (the worker takes the parent image as the source) | every scene has a COMPLETED UUID image (a CAMS id is fixed from `/image/<uuid>` in the URL) |
| **VIDEOS** | Scenes without a COMPLETED video. **Each scene is its own image-to-video clip**: before submitting, the runner clears every `<orientation>_end_scene_media_id` of the video, because start+end-frame chaining is not supported on the batch API and a CONTINUATION image sets its parent's end frame automatically. | `GENERATE_VIDEO`, one batch | every scene has a COMPLETED video; each clip is **downloaded as soon as it is COMPLETED** |
| **CONCAT** | `/fk-concat` on local files only (no upscale). | none (no credit) | `output/<slug>/<slug>_<video8>_final.mp4` exists and is verified |

Orientation comes from `video.orientation`. If `output/<slug>/meta.json` says
something else, the run only records a warning.

Each stage is queued as one batch; the worker and the generation guard pace it.

## Run states

```
DRAFT ──start──► RUNNING(stage) ──stage done & checkpoint──► AWAITING_APPROVAL ──approve──► RUNNING(next)
                   │  ▲                                          │ ▲
                   │  └──resume── PAUSED(reason)                 │ └── redo (then the stage finishes again)
                   │  └──resume── NEEDS_USER_ACTION ◄────────────┘
                   └── last stage done ──► COMPLETED        any unfinished state ──cancel──► CANCELLED
FAILED: reserved for configuration errors the runner cannot recover from.
```

| State | Meaning | What you do |
|---|---|---|
| `DRAFT` | Created, estimate computed, nothing queued | `start` |
| `RUNNING` | Queuing and watching the current stage | wait |
| `AWAITING_APPROVAL` | A checkpoint stage is done; `status_detail` gives the next stage's minimum generations | check results, `redo` items, `approve` |
| `PAUSED` | Session problem: `pause_reason` is `UNUSUAL_ACTIVITY` or `EXTENSION_DISCONNECTED`. The run's queued requests are **held** | fix the session, `resume` |
| `NEEDS_USER_ACTION` | Some entity/scene needs a person (see `needs_user_action`) | fix the prompt / file, `redo` it (or `resume` after fixing outside) |
| `COMPLETED` | Done; `final_path` is set (if concat is on) | — |
| `CANCELLED` | Stopped by `cancel` | start a new run if needed |

Checkpoints are configurable per run: `"checkpoints": ["REFS","IMAGES"]` is
the default, `[]` runs straight through after `start`, `["VIDEOS"]` stops
before concat.

### What pauses and what needs a person

| Event | Run | Item |
|---|---|---|
| A request fails with `PUBLIC_ERROR_UNUSUAL_ACTIVITY`, or the Flow client's cooldown is active | `PAUSED` (`UNUSUAL_ACTIVITY`), queued requests held. No retry. On `resume` the flagged item is queued again. | back to `PLANNED` |
| Extension disconnected for more than **60 s** (shorter gaps are normal for the MV3 service worker and ignored) | `PAUSED` (`EXTENSION_DISCONNECTED`), queued requests held. `resume` only once it is connected. | unchanged |
| `PUBLIC_ERROR_UNSAFE_GENERATION` / `PUBLIC_ERROR_MINOR_INPUT_IMAGE` (the worker fails these at once) | `NEEDS_USER_ACTION` once nothing else is in flight | `NEEDS_USER_ACTION`, `error_code` `UNSAFE_GENERATION` / `MINOR_INPUT_IMAGE`; its CONTINUATION children wait |
| `UNSUPPORTED_ON_BATCH_API`, not-found after 2 recoveries, retries exhausted | `NEEDS_USER_ACTION` | `NEEDS_USER_ACTION` with the reason |
| Request COMPLETED but no UUID media id stored | `NEEDS_USER_ACTION` | `MISSING_OUTPUT` |
| A clip cannot be downloaded (2 attempts: when the video finished + once at concat; `resume` allows one more) | `NEEDS_USER_ACTION` | `MISSING_CLIP` |
| ffmpeg / ffprobe missing, or the concat fails its checks | `NEEDS_USER_ACTION`, message in `status_detail` / `error` | — |

Transient errors (network, captcha, Flow 5xx…) stay with the worker's own
retry policy ([REQUEST_LIFECYCLE.md](REQUEST_LIFECYCLE.md)); the runner never
resubmits content failures by itself.

## API

| Endpoint | Body | Effect |
|---|---|---|
| `POST /api/pipeline-runs` | `{"video_id", "checkpoints"?: [..], "concat"?: true}` | 201, a `DRAFT` run with its estimate. 409 if the video already has an unfinished run |
| `GET /api/pipeline-runs?video_id=&project_id=` | | runs, newest first |
| `GET /api/pipeline-runs/{id}` | | full status (below) |
| `POST /{id}/start` | | `DRAFT → RUNNING` (REFS) |
| `POST /{id}/approve` | | `AWAITING_APPROVAL → RUNNING` (next stage) |
| `POST /{id}/resume` | | `PAUSED` / `NEEDS_USER_ACTION → RUNNING`, releases held requests |
| `POST /{id}/redo` | `{"target_type": "character"\|"scene", "target_id", "include_descendants"?: false, "confirm_invalidates"?: false}` | redo one target (below) |
| `POST /{id}/cancel` | | `CANCELLED` (below) |

`start`, `approve`, `resume` and `redo` first check that the extension is
connected and no UNUSUAL_ACTIVITY cooldown is active (400 otherwise). Wrong
state → 409, unknown run/video → 404.

Status response (abridged):

```json
{
  "status": "AWAITING_APPROVAL", "stage": "IMAGES", "wave": 1,
  "pause_reason": null, "status_detail": "IMAGES done. Approve to start VIDEOS (minimum 3 generations).",
  "warnings": ["Cleared the end frame of 1 chain scene(s): ..."],
  "estimate_remaining": {"kind": "minimum", "refs": 0, "images": 0, "videos": 3, "total_generations": 3, ...},
  "generations_spent": 5,
  "needs_user_action": [],
  "stages": {"IMAGES": {"total": 3, "done": 3, "items": [
     {"label": "scene #1", "status": "COMPLETED", "wave": 1, "request_type": "EDIT_IMAGE",
      "request_status": "COMPLETED", "redo_count": 0, "requests_made": 1, "generations_spent": 1, ...}]}},
  "final_path": null
}
```

- **Estimates are minimums**: one generation per entity/scene still to do,
  including scenes a new reference will reset. Worker retries of transient
  errors are not included.
- **`generations_spent`** is counted from each item's request history:
  image/reference requests count every attempt (`1 + retry_count`), a video
  counts once its Flow operation exists (retries re-poll it), a request that
  never reached the worker or was skipped as already done counts 0. It is an
  approximation of what Flow billed.

### Redo

Only while the run waits for you (`AWAITING_APPROVAL`, `NEEDS_USER_ACTION`,
`PAUSED`), and only for the current stage. A redo is always explicit; the
runner never cascades by itself.

| Target | Stage | Request | Notes |
|---|---|---|---|
| entity | REFS | `REGENERATE_CHARACTER_IMAGE` | A new reference wipes the image and video of every scene using it: if any has one, `confirm_invalidates: true` is required. After REFS, start a new run instead |
| scene | IMAGES | ROOT `REGENERATE_IMAGE`, CONTINUATION `EDIT_IMAGE` | Children were edited from the old image: the response lists them in `stale_descendants`; pass `include_descendants: true` to redo them too (in waves) |
| scene | VIDEOS or CONCAT | `GENERATE_VIDEO` after resetting the scene's video status (as `/fk-gen-videos` says) | The old local clip is deleted; the run returns to VIDEOS |

Each item keeps `redo_count` and `request_history` (every request id with its
reason), so you can see what every entity/scene cost.

### Pause and cancel without failing requests

No request status was added.

- **Pause** holds the run's `PENDING` requests: they stay `PENDING` (scene
  statuses untouched, `/fk-status` still shows them as pending) with
  `next_retry_at = 9999-12-31T23:59:59Z` and `last_failure_reason = "held by
  pipeline run <id>: <reason>"`, which the worker's claim query never reaches.
  `resume` clears it. A request already `PROCESSING` finishes normally.
- **Cancel** deletes the run's requests that never reached Flow (`PENDING`,
  never claimed, no retries, no Flow operation) — no `FAILED` rows, no scene
  status change — and records each in its item's history. Requests that did
  reach Flow are released to finish, because a running Flow job cannot be
  recalled; their results are kept.

## Restarts and duplicates

- All run state is in `pipeline_run` / `pipeline_run_item`; every tick
  re-reads it. There is nothing to rebuild in memory.
- Queueing a request and recording it on its item happen in **one
  transaction** (`pipeline_crud.submit_items`), through the same deduplicating
  enqueue as `/api/requests/batch`: an active request for the same
  scene/entity + type is adopted, never duplicated. An item that has a request
  is only ever watched.
- On startup the worker resets every orphaned `PROCESSING` request to
  `PENDING` before anything runs. A video request re-polls its stored Flow
  operation (no second render); an image request is submitted again (may cost
  one more generation) — see [REQUEST_LIFECYCLE.md](REQUEST_LIFECYCLE.md).

## Concat

For every scene, in `display_order`, from `output/<slug>/scenes/scene_NNN_<id>.mp4`:

1. **Normalise** into `output/<slug>/concat_<video8>/norm/` at the first
   clip's size, 24 fps, H.264 CRF 18, AAC 192k 48 kHz stereo:
   `scale=W:H:force_original_aspect_ratio=decrease,pad=W:H:(ow-iw)/2:(oh-ih)/2,setsar=1`.
2. **Join** with the concat demuxer (`-c copy`) using a UTF-8 list file.
3. **Verify** the final file: a video and an audio stream, and a duration
   within 5 % (at least 1 s) of the clips' total.

Fixes over the `/fk-concat` shell commands:

- the same scale **and pad** for every clip (the skill's no-narration-wav
  branch only scaled, stretching clips of another aspect);
- one `-filter_complex` per command, never together with `-vf`;
- a clip without audio gets silent stereo (`anullsrc`), so every normalised
  file has identical streams.

## Configuration

| Variable | Default | |
|---|---|---|
| `PIPELINE_RUNNER_ENABLED` | `1` | Start the runner loop with the server. Runs only move when started/approved |
| `POLL_INTERVAL` | `5` | Seconds between runner ticks (shared with the worker) |

The extension-disconnect grace period is 60 s (`DISCONNECT_GRACE_SECONDS`).

## Out of scope

Video review (needs `ANTHROPIC_API_KEY`; planned on Gemini later), narration
/ TTS, upscale (unsupported on the batch API), start+end-frame chain videos,
prompt rewriting after a content-filter rejection, and trimming.

Known and left as is: the worker prunes its in-memory `_deferred` map on the
next tick, so a "not ready yet" request is retried after one poll interval
rather than 30 s (harmless, costs a cooldown slot).
