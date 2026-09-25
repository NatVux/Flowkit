# Request Lifecycle

A `request` row is one unit of Flow work, processed by the worker in
`agent/worker/processor.py`. Four states exist: `PENDING`, `PROCESSING`,
`COMPLETED`, `FAILED`.

```text
PENDING ──claim──► PROCESSING ──► COMPLETED   (terminal)
   ▲                  │   │
   └── retry/defer ───┘   └──► FAILED ──(explicit retry)──► PENDING
```

## Transitions

Enforced by `crud.REQUEST_TRANSITIONS` in `crud.transition_request`:

| From | To | When |
|---|---|---|
| `PENDING` | `PROCESSING` | Worker claim (sets `started_at`, clears `finished_at`) |
| `PROCESSING` | `COMPLETED` | Usable result, or the asset was already completed (skip) |
| `PROCESSING` | `FAILED` | Permanent error or retries exhausted (sets `finished_at`) |
| `PROCESSING` | `PENDING` | Retryable error, missing prerequisite, cancellation, stale recovery |
| `FAILED` | `PENDING` | Explicit retry: `PATCH /api/requests/{id}` `{"status":"PENDING"}` |
| `COMPLETED` | — | Terminal |

Setting the current state again is allowed (metadata-only update). Anything
else raises `InvalidRequestTransition`. The API has no dedicated handler for
it, so an invalid `PATCH` surfaces as HTTP 500 `INTERNAL_ERROR`.

Worker updates pass the `started_at` value from their claim. If the row has
since been reclaimed, the update raises `RequestTransitionConflict` and the old
worker cannot overwrite the new attempt.

## Creating requests

| Endpoint | Duplicate active request (same scene + type) |
|---|---|
| `POST /api/requests` | HTTP 409 |
| `POST /api/requests/batch` `{"requests":[…]}` | Existing active request returned in place of a new one; also de-duplicates character requests |

Both set `video.orientation` from the request's `orientation`. The database
also rejects duplicates through partial unique indexes and triggers, and
rejects a request whose `video_id` / `scene_id` do not belong to its
`project_id` / `video_id`.

Poll progress with `GET /api/requests/batch-status?video_id=…&type=…&orientation=…`
(returns counts plus `done` and `all_succeeded`) rather than polling each id.

## Processing

1. **Claim** — every `POLL_INTERVAL` s, while the extension is connected, up
   to the free concurrency slots (`MAX_CONCURRENT_REQUESTS`) of `PENDING` rows
   with `next_retry_at` null or past, **oldest `created_at` first**.
2. **Rate limit** — at most `MAX_CONCURRENT_REQUESTS` running and at least
   `API_COOLDOWN` s between starts.
3. **Skip** — non-regenerate image/video/upscale requests whose scene asset is
   already `COMPLETED` finish immediately (`error_message = "skipped: already completed"`).
4. **Prerequisites** — video needs the scene image media id; upscale needs the
   video media id; edits need a source image. Missing → back to `PENDING`,
   ignored for 30 s, no retry counted.
5. **Dispatch** with a `WORKER_OPERATION_TIMEOUT` (900 s) limit.
6. **Result** — success writes `media_id` / `output_url` and updates the
   scene or character ([SCENE_PIPELINE.md](SCENE_PIPELINE.md)).

## Failure handling

Evaluated in order on the error text (case-insensitive):

| Match | Outcome | `retry_count` |
|---|---|---|
| `not found` + source image re-uploaded successfully | `PENDING` | unchanged |
| `unsupported_on_batch_api`, `no_flow_project`, `invalid request`, `permission denied`, `unauthorized`, `forbidden`, `not configured`, `unknown request type`, `scene not found`, `character not found` | `FAILED` | unchanged |
| `extension reconnected` / `extension disconnected` / `extension not connected` | `PENDING`, retry in 5 s | unchanged |
| `captcha` / `recaptcha` | `PENDING`, backoff `min(2^n·10, 300)` s; `FAILED` at n = 10 | +1 |
| anything else | `PENDING` with backoff `min(2^n·10, 300)` s while n < `MAX_RETRIES`; else `FAILED` | +1 |

Backoffs are stored in `next_retry_at` (so they survive restarts) plus up to
`RETRY_JITTER_SECONDS` of jitter. When a request ends `FAILED`, the matching
scene asset status is set to `FAILED` too.

Flow error payloads with `data.error.details[].reason` are recorded as
`"<message> [<reason>]"`, e.g. `… [PUBLIC_ERROR_UNSAFE_GENERATION]`. Those
reasons are not special-cased; they follow the default row above.

## Lifecycle columns

| Column | Meaning |
|---|---|
| `created_at` / `updated_at` | Row creation / last change |
| `started_at` | Current claim lease |
| `finished_at` | Set on `COMPLETED` / `FAILED`, cleared otherwise |
| `retry_count` | Counted failures |
| `next_retry_at` | Earliest next claim |
| `error_message` | Latest human-readable error |
| `last_failure_reason` | Last failure cause, kept across retries |

## Recovery

- **Startup:** `PROCESSING` rows whose `updated_at` is older than
  `STALE_PROCESSING_TIMEOUT` (rounded up to minutes; default 10) become
  `PENDING` with `error_message = "reset: stale processing"`. This runs only
  once, when the worker starts.
- **Shutdown:** in-flight requests still running after 30 s are cancelled and
  returned to `PENDING`.
