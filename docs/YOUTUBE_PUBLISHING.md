# YouTube Publishing Lifecycle

The backend separates local preparation, metadata validation, authentication, upload, and publish state.

## States

Upload state:

`NOT_STARTED -> UPLOADING -> UPLOADED`

Failure states:

`UPLOADING -> FAILED`
`UPLOADING -> AUTH_EXPIRED`

Publish state:

`NOT_PUBLISHED -> PUBLISHING -> PUBLISHED`

Failure states:

`PUBLISHING -> FAILED`
`PUBLISHING -> AUTH_EXPIRED`

A successful upload stores the remote YouTube video ID before publishing. Publishing failure therefore does not lose the uploaded remote ID and can be retried independently.

## Idempotency

Uploads derive an idempotency key from the local video ID, source path, source size, title, and privacy. Repeating the same upload after a successful upload returns the stored remote ID without calling YouTube again.

Resumable upload URIs returned by the adapter are persisted in `youtube_resumable_uri` and passed back to a retry-capable adapter.

## Adapter boundary

`YouTubePublisher` depends on the `YouTubeClient` protocol. The current repository does not include a concrete Google OAuth client. Production OAuth/token handling must be implemented behind that adapter and must never expose credentials through API responses or logs.

Tests use a fake client and never require real accounts, credentials, or network calls.
