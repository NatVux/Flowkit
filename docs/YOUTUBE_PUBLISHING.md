# YouTube Pipeline

There are two unrelated YouTube paths. Neither works out of the box from this
repository.

## 1. Backend publisher (`agent/services/youtube_publisher.py`)

### Status

| Piece | State |
|---|---|
| State machine, metadata validation, idempotency | Implemented |
| Endpoints `POST /api/videos/{vid}/youtube/upload` and `/publish` | Present, but return **503 `YouTube publishing is not configured`** |
| Concrete Google API / OAuth client | **Not in the repository.** `set_youtube_client()` in `agent/api/videos.py` is never called |
| Database columns | **Missing.** `youtube_upload_status`, `youtube_upload_key`, `youtube_upload_error`, `youtube_upload_attempts`, `youtube_resumable_uri`, `youtube_publish_status`, `youtube_publish_error`, `youtube_uploaded_at`, `youtube_published_at` and `upload_url` are not created by `schema.py`; the first state write fails with `sqlite3.OperationalError: no such column` |

`GET /api/videos/{id}` still returns these fields with their model defaults
(`NOT_STARTED`, `NOT_PUBLISHED`, …) because they are read with fallbacks.

### Intended behavior

Upload request body: `source_path`, `title`, `description`, `tags`,
`privacy` (`private` / `unlisted` / `public`, default `unlisted`), optional
`thumbnail_path`.

Validation: title 1–100 chars, description ≤ 5000 chars, ≤ 500 tags each
1–100 chars, source and thumbnail must be existing non-empty files.

```text
upload:  NOT_STARTED → UPLOADING → UPLOADED
                           ├────→ FAILED
                           └────→ AUTH_EXPIRED
publish: NOT_PUBLISHED → PUBLISHING → PUBLISHED   (requires youtube_id)
                              ├────→ FAILED
                              └────→ AUTH_EXPIRED
```

- The idempotency key is SHA-256 of video id, resolved source path, file size,
  title and privacy. Repeating an upload with the same key after success
  returns the stored `youtube_id` without calling the client.
- A resumable-upload URI returned by the client is stored and passed back on
  the next attempt.
- Publishing an already `PUBLISHED` video returns immediately.
- HTTP mapping: auth error → 401, client error → 502, validation → 400.

To make it usable, a deployment must (a) add the columns above to the `video`
table in `schema.py` (table definition and a migration), and (b) call
`agent.api.videos.set_youtube_client(<YouTubeClient implementation>)` at
startup.

## 2. Skill-driven upload (`/fk-youtube-upload`)

The `/fk-youtube-seo`, `/fk-brand-logo`, `/fk-thumbnail` and
`/fk-youtube-upload` skills run outside the agent. The upload skill expects a
`youtube/` directory with `auth.py`, `upload.py` and
`channels/<channel>/{client_secrets.json, token.json, channel_rules.json}`.

That directory is **gitignored and not part of this repository**. Without it,
only the SEO, thumbnail and watermark steps work. `channel_rules.json` is also
the fallback source for `SUNO_API_KEY` (`api_keys.suno`).

Backups deliberately exclude `client_secrets.json`, `token.json` and
`channel_rules.json`.
