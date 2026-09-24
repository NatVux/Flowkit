# Performance Audit

## Findings

### Fixed

- Flow project URL refresh performed one scene query per video. It now uses one indexed `scene JOIN video` query via `list_scenes_by_project`.
- Dashboard and pipeline pages reloaded all data for every request event. A 250 ms debounce now coalesces event bursts into one refresh.
- Worker actionable-job throughput logs were emitted at `INFO` for every poll with work. They are now `DEBUG` to reduce log volume at normal operation.

### Existing bounded behavior

- Worker concurrency is bounded by `MAX_CONCURRENT_REQUESTS` and active task tracking.
- EventBus subscriber queues have `maxsize=100`; slow dashboard clients drop events rather than growing memory without bound.
- Extension request logs are capped at 100 entries.
- Worker task sets remove completed tasks via done callbacks.
- Media output locks and temporary-file cleanup prevent output collisions and temp-file accumulation.
- Queue polling uses indexed request status/retry columns.

## Remaining risks

- Dashboard initial loads still fan out: projects -> videos -> scenes, plus requests and characters. This is acceptable for current local project sizes but becomes N+1 at large scale. A measured aggregate dashboard endpoint or bulk project snapshot should be added before large-project support.
- Project detail and gallery pages also fetch scenes per video. This is a known API-shape limitation, not duplicated business logic.
- Flow operation polling intentionally waits on external completion and should not be shortened without measuring provider behavior.
- Media processing is subprocess-bound by design, but each command has explicit timeouts and atomic outputs.

## Measurement approach

For large-project work, measure:

- SQL statement count and wall time per dashboard load.
- Number and size of scene/request JSON responses.
- WebSocket event rate during batch generation.
- Worker poll duration and queue claim latency.
- Media subprocess duration and output validation overhead.

Use mocked Flow/media services in automated tests; do not benchmark against real accounts in the normal test suite.
