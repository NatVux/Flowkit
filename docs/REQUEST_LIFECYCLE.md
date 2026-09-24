# Request Lifecycle

Flow Kit uses four request states. `RUNNING` and `SUCCEEDED` are not separate states in this repository.

```text
PENDING -> PROCESSING -> COMPLETED
                    \-> FAILED
                    \-> PENDING  (retry or transient recovery)
FAILED  -> PENDING  (explicit retry)
COMPLETED             (terminal)
```

## Transition rules

| From | To | Meaning |
|---|---|---|
| `PENDING` | `PROCESSING` | An atomic worker claim started the request. |
| `PROCESSING` | `COMPLETED` | The operation returned a usable result. |
| `PROCESSING` | `FAILED` | The request is no longer retryable or exhausted retries. |
| `PROCESSING` | `PENDING` | A retryable failure, missing prerequisite, or stale-worker recovery. |
| `FAILED` | `PENDING` | An explicit retry request. |
| `COMPLETED` | none | Completed requests are terminal. |

A status update that is not in this table is rejected. Reapplying the current state is allowed for idempotent metadata updates.

## Lifecycle metadata

- `created_at` and `updated_at` record row creation and mutation.
- `started_at` records the current worker claim lease.
- `finished_at` records completion or terminal failure.
- `retry_count` records retry attempts.
- `next_retry_at` persists retry backoff across restarts.
- `error_message` contains the most recent human-readable failure.
- `last_failure_reason` preserves the last failure cause after a retry.

Worker completion, failure, and retry updates include the `started_at` value used during the claim. If a stale request has already been reclaimed, the old worker cannot transition it or overwrite the new attempt.

On startup, only `PROCESSING` rows older than `STALE_PROCESSING_TIMEOUT` are returned to `PENDING`. Recovery records `stale processing recovery` as the last failure reason. Fresh processing rows are left alone.

Queue claims and state transitions run inside database transactions. The existing partial unique indexes and triggers prevent more than one active request for the same logical scene/type or character/type from being queued.
