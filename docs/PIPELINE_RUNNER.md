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

**Unconfirmed: how a content-filter rejection looks on the batch API.** A
batchexecute error slot is recorded as `RpcError: <rpcid> failed: <repr of the
slot>`. The only shape seen for real is `[7, None, [['type.googleapis.com/google.rpc.ErrorInfo',
['PUBLIC_ERROR_UNUSUAL_ACTIVITY']]]]`, where the reason code survives verbatim;
bare codes such as `[8]` also occur. If a rejection arrives as
`ErrorInfo` with `PUBLIC_ERROR_UNSAFE_GENERATION` / `PUBLIC_ERROR_MINOR_INPUT_IMAGE`,
it is recognised. If it arrives as a bare `[3]` (gRPC INVALID_ARGUMENT, also
used for malformed payloads), as an image payload without media ("Image
generation returned no media url"), or as a failed video operation ("Operation
failed: <name>", whose complaint text is not kept), it is **not**: the worker
retries it like any error and the item ends `FAILED_AFTER_RETRIES`. Capture a
real rejection before adding a rule for it.

### Mẫu lỗi thật

Errors seen on real runs, copied verbatim, so recognition rules can be added
later. The raw batchexecute body is not kept by Flow Kit: `RpcError` holds
`repr()` of the error slot, which is the rawest form available.

**`PUBLIC_ERROR_MODEL_ACCESS_DENIED` — video submit (2026-09-26)**

- Run `dcae218e-cd9c-43e4-af2d-15ef030794e4`, video `c2fea6d6-…`, project
  `6130c05e-…`, stage VIDEOS, `GENERATE_VIDEO` (i2v, VERTICAL), tier
  `PAYGATE_TIER_TWO`, model map `frame_2_video` portrait →
  `veo_3_1_i2v_lite_low_priority`.
- Batch call itself: HTTP 200 from the extension callback; the error is in the
  RPC slot. No Flow operation was created (rejected at submit).
- Stored `request.error_message`, identical for all 3 scenes and all 4 attempts:

  ```
  RpcError: eb1hJf failed: [7, None, [['type.googleapis.com/google.rpc.ErrorInfo', ['PUBLIC_ERROR_MODEL_ACCESS_DENIED']]]]
  ```

- Log line (`agent.sdk.services.operations`):

  ```
  [ERROR] agent.sdk.services.operations [DEBUG] Video gen submit_result IS_ERROR: {'status': 502, 'error': "RpcError: eb1hJf failed: [7, None, [['type.googleapis.com/google.rpc.ErrorInfo', ['PUBLIC_ERROR_MODEL_ACCESS_DENIED']]]]"}
  ```

- Not recognised today: `_is_retryable_error` matches `"permission denied"`,
  not `ACCESS_DENIED`, so the worker scheduled it as `retryable_error`
  (`reason: "retryable_error"`). A retry cannot fix it — it is a model/tier
  permission answer — so it should fail once, like `UNSUPPORTED_ON_BATCH_API`.
  (Done since: exact-code match, `MODEL_ACCESS_DENIED` → `NEEDS_USER_ACTION`.)

**Video accepted, then failed at Google and refunded — no reason given (2026-09-26)**

- Same run, scene 0 (`a5cadbc8…`), model `veo_3_1_i2v_lite`. Submit accepted
  at 12:59:43, operation `09d7b8fb-10b5-43f9-8bde-52feb3e14490`. Flow credit
  history (seen by the user): **−10 at 12:59:38, +10 refunded at 13:00:15**.
- Polls up to 13:00:06 raised nothing. From 13:00:16 (one second after the
  refund) the listing held a media id for the operation, and every media
  lookup on it failed:

  ```
  [WARNING] agent.services.flow_client Operation 09d7b8fb-10b5-43f9-8 poll failed: as29s failed: [5]
  ```

  (`[5]` = gRPC NOT_FOUND), every 10 s until the 420 s poll timeout.
- After a server restart (cache empty), the operation record itself, verbatim
  `jwpduf` payload:

  ```
  [null, 250, [["09d7b8fb-10b5-43f9-8bde-52feb3e14490", null, null, null, null, [null, null, null, null, null, null, null, null, [4, [null, "Media not found."], ["Media not found."]]]]]]
  ```

  No project id, no status (a `CAE` done status never came), outcome code `4`
  with "Media not found." and no `ErrorInfo` reason. The operation was also
  **gone from the project listing** (no entry for it any more).
- Stored `request.error_message` after the second 420 s poll:

  ```
  Polling timeout after 420s: Media not found.
  ```

- Flow gives no reason on this path — not unsafe content, not a minor, not a
  model code. What distinguishes it from a slow job is only circumstantial:
  the refund, `as29s` NOT_FOUND on the listed media id, then the operation
  vanishing from the listing. The worker treats it as a poll timeout: it
  re-polls the stored operation (no second submit, no credit) up to
  `MAX_RETRIES`, then the item ends `FAILED_AFTER_RETRIES`. Since `f8ecf59`
  the record is logged and the timeout message carries outcome, poll error and
  media id. No failure rule yet — one capture only.

**Outcome `4` "Media not found." is not a failure signal (2026-09-26)**

- The same scene redone with a short prompt (same model, image, aspect)
  **succeeded** in 64 s: operation `23fe114e-8e41-4ead-9bc8-d3ee42c88dcc`,
  8 s 720x1280 clip with audio. Its operation record, verbatim, 10 s after submit:

  ```
  [null, 230, [["23fe114e-8e41-4ead-9bc8-d3ee42c88dcc", null, null, null, null, [null, null, null, null, null, null, null, null, [4, [null, "Media not found."], ["Media not found."]]]]]]
  ```

  — identical in shape to the failed job's. **Outcome code `4` cannot tell a
  failed job from a working one.**
- The signs that did mark the real failure: **the credit was refunded**, the
  media id from the listing answered **`as29s` `[5]`** (NOT_FOUND) on every
  poll, and the operation **disappeared from the project listing**. A job that
  works stays in the listing and its media lookup answers with a poster, then
  the `/video/` url.
- The failed and the successful submit differed only in the prompt: 828
  characters with a line of dialogue, a "Then cut to" second shot, `(no
  subtitles)`, Audio/SFX/Negative lines and character voices, against 299
  characters for one continuous shot (worker suffixes included).

**Conclusion of the prompt test (2026-09-26): dialogue is not the cause**

The other two scenes were rewritten as one continuous shot of about 335
characters, no "cut to", no Audio/SFX/Negative lines (the worker adds its own),
and sent once each on `veo_3_1_i2v_lite`:

| Scene | Prompt | Sent → done | Result |
|---|---|---|---|
| 2 | no dialogue, 337 chars (517 with suffixes) | 13:53:30 → 13:54:27 | COMPLETED, op `e442cc31-…`, 8 s 720x1280 + audio |
| 1 | one line of dialogue from the child character, `Minh whispers: "Hello... who are you?" (no subtitles)`, 333 chars (674 with voices + suffixes) | 13:54:32 → 13:55:29 | COMPLETED, op `2e6326ac-…`, 8 s 720x1280 + audio |

**Dialogue is not the cause; the remaining suspects are the scene cut
("Then cut to …", several shots in 8 s) and the prompt length.** One sample
each: not yet proven which of the two, or whether the original failure was
just Google's.

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
  never reached the worker or was skipped as already done counts 0. **It is the
  number of generations SENT, not the credit Flow charged**: a job Flow fails
  on its side is refunded, and nothing on the batch path says so for certain
  (see "Mẫu lỗi thật"), so it still counts here. Run `dcae218e` reports 4 for
  3 actually billed. The status response says so in `generations_spent_note`.

### Redo

While the run waits for you (`AWAITING_APPROVAL`, `NEEDS_USER_ACTION`,
`PAUSED`), and only for the current stage. While it is `RUNNING`, an item of
the current stage that is `FAILED` or `NEEDS_USER_ACTION` can be redone too:
a stage with other items still in flight never stops for a person, so without
this the failed item would wait for all of them. A redo is always explicit;
the runner never cascades by itself.

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
- **Orphaned holds** — at startup and on every runner tick, a `PENDING`
  request whose `next_retry_at` is at or after `9000-01-01` and whose run is
  `CANCELLED` / `COMPLETED` / `FAILED` or no longer exists is freed: deleted if
  it never reached Flow, otherwise released (`next_retry_at` cleared). Logged
  as "Orphaned pipeline holds …" and noted in the item history. Without this, a
  run that ended between holding and cleaning up (a crash) would leave the
  request unclaimable, and a manual `/fk-gen-images` would keep being handed
  that dead request by the deduplicating enqueue.

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

## Follow-ups from the 2026-09-26 test run (done)

- **Story prompts** (`STORY_SYSTEM`): each `video_prompt` is one continuous shot
  of at most ~350 characters, no "cut to", at most one short line of dialogue
  with `(no subtitles)`, no Audio/SFX/Negative lines (the worker adds them). A
  post-check retries a plan whose `video_prompt` cuts to another shot or runs
  past 400 characters.
- **Audio suffix**: with `allow_voice` off the worker bans a narrator
  ("no narrator voiceover"), not the character's line of dialogue.
- **Worker**: a request that ends `COMPLETED` clears the `error_message` of an
  earlier attempt (`last_failure_reason` keeps it).
- **Redo** works on a `FAILED` / `NEEDS_USER_ACTION` item while the run is
  `RUNNING` (see Redo).
- **`generations_spent`** stays a count of generations sent; the response
  and this page say it is not the credit charged (refunds are not detected).

The `/fk-create-project` and `/fk-camera-guide` skills teach the same rule for hand-written video prompts.
