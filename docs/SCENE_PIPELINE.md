# Scene Pipeline Lifecycle

The scene pipeline is dependency-aware and keeps stale outputs out of the current database view.

```text
Project -> Video -> Scene -> Reference entities
                         -> Scene image -> Scene video -> Optional upscale
                                                    \-> Narration audio -> Final narration mix
```

Each generated asset has an explicit status: `PENDING`, `PROCESSING`, `COMPLETED`, or `FAILED`. A downstream step may run only when its direct prerequisite is `COMPLETED` and its referenced media/file exists.

## Invalidation rules

- Reference image regeneration invalidates dependent scene images, videos, upscales, and final narration mixes. Existing files are retained and are not deleted automatically.
- Scene image regeneration clears that orientation's image media, video media, and upscale media, and resets the final narration mix. Narration audio remains usable because it depends on narrator text, not image pixels.
- Scene video regeneration clears that orientation's upscale media and resets the final narration mix.
- Missing audio files are marked `FAILED` even if a generator returned `COMPLETED`.
- Mixing requires a completed scene video and a local video file. Missing files produce `narration_mix_status=FAILED`; no stale path remains current.

Retries create or reuse the existing request job according to the worker's idempotency rules. Regeneration uses the explicit `REGENERATE_*` request types and follows the same invalidation rules when the new result succeeds.
