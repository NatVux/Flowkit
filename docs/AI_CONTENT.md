# AI Content Planning (Gemini)

Optional. Gemini writes **text only**: stories, characters, locations, scene
plans, image prompts, video prompts, narration scripts and YouTube metadata.
Google Flow still generates every image and video through the existing worker.
With no key configured, the feature is disabled and everything else works as
before.

```
POST /api/ai/story-plan ─► AIContentService ─► AIProvider (Gemini | Mock)
                                 │ validate (Pydantic)            │ JSON
                                 ▼                                 │
                          ai_generation row (GENERATED) ◄──────────┘
POST /api/ai/generations/{id}/apply
                                 ▼  one transaction
          characters + project links, scenes, project.story / video metadata
                                 ▼
          POST /api/requests/batch  →  worker  →  FlowClient  →  Flow   (unchanged)
```

## Configuration

| Variable | Default | Effect |
|---|---|---|
| `GEMINI_API_KEY` | empty | Enables Gemini when `AI_PROVIDER` is empty. Secret: export it, never commit it. Never logged |
| `AI_PROVIDER` | empty (auto) | `gemini`, `mock` (canned output, no network), or `none` to force-disable |
| `GEMINI_MODEL` | `gemini-flash-latest` | Any Gemini model id that supports structured JSON output |
| `GEMINI_TIMEOUT_SECONDS` | `90` | Per-attempt timeout |
| `GEMINI_MAX_RETRIES` | `2` | Extra attempts after the first, for retryable errors only |

Requires the `google-genai` package (in `requirements.txt`). If it is missing,
the provider reports "not configured" instead of failing at startup. The startup
log prints `AI content provider: disabled` or `gemini (<model>, configured=True)`.

## Workflow

1. **Generate** a plan for an existing project (the project must already exist;
   project creation still needs the extension and a Flow project):

   ```bash
   curl -s -X POST http://127.0.0.1:8100/api/ai/story-plan -H "Content-Type: application/json" \
     -d '{"project_id":"<PID>","brief":"A cat who sells fish finds a golden fish","scene_count":6,"tone":"warm"}'
   ```

   Returns a generation (`id`, `status: "GENERATED"`, `request_id`, `attempts`,
   `output`). Nothing is written to characters/scenes yet. Review `output` first.

2. **Apply** it to a video in the same project:

   ```bash
   curl -s -X POST http://127.0.0.1:8100/api/ai/generations/<GEN_ID>/apply \
     -H "Content-Type: application/json" -d '{"video_id":"<VID>"}'
   ```

   In a single transaction this:
   - creates each character/location with the same description and
     reference-image prompt builder as `POST /api/projects`, and links it to the
     project. An entity already linked to the project (same slug or name) is
     reused; a slug taken by another project gets a suffix (`fish_stall_2`);
   - appends one scene per planned scene after the video's last scene:
     `prompt` (material `scene_prefix` + image prompt), `video_prompt`,
     `narrator_text` and `character_names`. Scenes are independent (`ROOT`) by
     default. With `"chain_scenes": true`, scenes the plan marks as continuing
     the previous one become `CONTINUATION` with `parent_scene_id`. Leave it off
     on the Veo path: a continuation turns its parent's video into
     start+end-frame chaining, which fails with `UNSUPPORTED_ON_BATCH_API`
     (use Omni Flash or `FLOW_ALLOW_DEGRADED=1` if you enable it);
   - sets `project.story` if it was empty;
   - marks the generation `APPLIED`.

   A second apply returns **409**. Any failure rolls everything back.

3. Generate media as usual (`/fk-gen-refs`, `/fk-gen-images`, … or
   `POST /api/requests/batch`).

YouTube metadata works the same way:
`POST /api/ai/youtube-metadata {"video_id": "<VID>", "instructions": "…"}`,
then apply with a `{}` body. Apply sets `video.title`, `video.description`
(hashtags appended) and `video.tags` (JSON array).

Other endpoints: `GET /api/ai/status`, `GET /api/ai/generations?project_id=`,
`GET /api/ai/generations/{id}`.

## Validation

Model output is never trusted:

- The request asks for JSON (`response_mime_type=application/json`) with a JSON
  Schema generated from the Pydantic models in `agent/models/ai_content.py`.
- The response is parsed and validated against the same models: field lengths,
  entity types (existing `EntityType` values), unique entity names, scenes may
  only reference defined entities, the first scene cannot continue a previous
  one, ≤ 30 scenes, voice descriptions ≤ ~30 words, and YouTube limits
  (the publisher's own `validate_metadata` plus the 500-character tag total).
- Only validated output is stored. Stored output is validated again before apply.

## Errors and retries

| Condition | Error code | Retried | HTTP |
|---|---|---|---|
| No provider / no key / SDK missing | `AI_NOT_CONFIGURED` | no | 503 |
| Timeout (SDK timeout, 408/504) | `AI_TIMEOUT` | yes | 504 |
| Rate limit (429 / RESOURCE_EXHAUSTED) | `AI_RATE_LIMITED` | yes | 429 |
| 5xx, connection error | `AI_SERVER_ERROR` | yes | 502 |
| Empty response / no candidates | `AI_EMPTY_RESPONSE` | yes | 502 |
| Invalid JSON or failed validation | `AI_MALFORMED_RESPONSE` | yes | 502 |
| Output truncated (MAX_TOKENS) | `AI_PARTIAL_RESPONSE` | no | 422 |
| Safety / policy block | `AI_CONTENT_BLOCKED` | no | 422 |
| Other 4xx (bad key, bad model, bad schema) | `AI_REQUEST_REJECTED` | no | 502 |

Retries use exponential backoff (2 s, 4 s, …, max 30 s). The SDK's own retry is
left off, so attempts are never multiplied. Generating is side-effect free, so
it is safe to retry. **Apply is never retried automatically.**

Error responses carry `details.request_id`. Google reason codes such as
`API_KEY_INVALID` are included; provider free-text messages are not.

## Logging

Every attempt logs JSON events with one `request_id` per operation:
`ai_request_started`, `ai_request_completed` (duration, finish reason, token
usage), `ai_request_failed` (error code, retryable, HTTP status),
`ai_retry_scheduled`, `ai_generation_stored`, `ai_generation_applied`,
`ai_generation_apply_failed`. Prompts and outputs are not logged (only their
lengths). The API key is never logged or returned.

## Testing

`tests/unit/test_ai_providers.py` and `tests/unit/test_ai_content_service.py`
use `MockAIProvider` and a fake Gemini client built from real `google-genai`
response and error types. No network is used. `AI_PROVIDER=mock` gives canned
output for manual testing without a key.

## Limitations

- Not verified against the live Gemini API with a valid key: structured output
  acceptance of the generated schema by the model, and output quality.
- No dashboard UI yet; use the API or skills.
- Plans append scenes; there is no "regenerate this one scene's prompts" operation.
- Story plans do not pick per-scene camera/transition prompts (`transition_prompt`).
