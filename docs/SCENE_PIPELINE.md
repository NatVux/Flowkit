# Scene Pipeline

```text
Project ─► Video ─► Scene ─┬─► scene image ─► scene video ─► upscale (Veo: unported)
   │                       │                        │
   └─ linked characters ───┘                        └─► narration mix
      (reference images)        narrator_text ─► narration audio ─┘
```

Each scene keeps separate `vertical_*` and `horizontal_*` slots for image,
video and upscale (`url`, `media_id`, `status`). Statuses are `PENDING`,
`PROCESSING`, `COMPLETED`, `FAILED`.

## Typical order

1. `POST /api/projects` with `characters[]` (entities + appearance
   descriptions) and a `material`.
2. `POST /api/videos`, then `POST /api/scenes` per scene with `prompt`,
   optional `video_prompt`, `character_names`, `chain_type`
   (`ROOT` / `CONTINUATION` / `INSERT`) and `parent_scene_id`.
3. `GENERATE_CHARACTER_IMAGE` for each entity.
4. `GENERATE_IMAGE` per scene and orientation.
5. `GENERATE_VIDEO` (or `GENERATE_VIDEO_REFS`) per scene.
6. Optional `UPSCALE_VIDEO` — fails with `UNSUPPORTED_ON_BATCH_API` on the Veo
   path.
7. Optional narration: set `narrator_text`, then
   `POST /api/videos/{vid}/narrate`.
8. Download and concatenate — done by skills (`/fk-concat`,
   `/fk-concat-fit-narrator`) with ffmpeg; there is no concat endpoint.

Steps 3–6 are requests (`POST /api/requests/batch`); see
[REQUEST_LIFECYCLE.md](REQUEST_LIFECYCLE.md).

## Prerequisites the worker enforces

| Request | Needs | If missing |
|---|---|---|
| `GENERATE_IMAGE` with `character_names` | Every matched project character has a `media_id` | Error `Waiting for reference images: …` (retried, counts toward `MAX_RETRIES`) |
| `GENERATE_VIDEO`, `REGENERATE_VIDEO`, `GENERATE_VIDEO_REFS` | `{orientation}_image_media_id` | Deferred 30 s, not counted |
| `UPSCALE_VIDEO` | `{orientation}_video_media_id` | Deferred 30 s, not counted |
| `EDIT_IMAGE` | Parent's image (for chained scenes) or the scene's own image | Deferred 30 s, not counted |
| `EDIT_CHARACTER_IMAGE` | Character `media_id` | Deferred 30 s, not counted |

`character_names` entries match a linked character by display name or slug.
Characters not linked to the project are ignored.

## Invalidation on success

| Completed request | Effect on the scene (same orientation unless noted) |
|---|---|
| Image (`GENERATE_IMAGE`, `REGENERATE_IMAGE`, `EDIT_IMAGE`) | Image set `COMPLETED`; video and upscale reset to `PENDING` with null media id/URL; narration mix reset. If the scene has a parent, the parent's `{orientation}_end_scene_media_id` is set to this image |
| Video (`GENERATE_VIDEO`, `REGENERATE_VIDEO`, `GENERATE_VIDEO_REFS`) | Video set `COMPLETED`; upscale and narration mix reset |
| `UPSCALE_VIDEO` | Upscale set `COMPLETED` |
| Character reference | New `character_reference_asset` version; on **every** scene naming the character, both orientations' image/video/upscale and the narration mix reset to `PENDING` |

`REGENERATE_IMAGE` / `REGENERATE_VIDEO` apply the downstream reset at dispatch
time too, before the new result arrives. Narration audio is never reset by
image/video changes. Files on disk are never deleted.

## Prompts

- Image: `image_prompt` if set, else `prompt`. Continuation scenes without an
  `image_prompt` get extra continuation context.
- Video: `video_prompt` (or `prompt`), then automatically appended:
  - `Character voices: …` from linked characters' `voice_description`, when
    the prompt contains dialogue;
  - an `Audio: … no background music …` line, unless the project has
    `allow_music` or the prompt already contains `Audio:`/`Music:` (wording
    depends on `allow_voice`);
  - `Negative: subtitles, captions, watermark, …` unless `Negative:` is present.

## Video modes

| Mode | Request / endpoint | Status on current Flow API |
|---|---|---|
| Image-to-video | `GENERATE_VIDEO` | Works |
| Start + end frame (chain) | `GENERATE_VIDEO` with `*_end_scene_media_id` | Veo: `UNSUPPORTED_ON_BATCH_API` (i2v with `FLOW_ALLOW_DEGRADED=1`). Omni Flash first+last works |
| Reference-to-video | `GENERATE_VIDEO_REFS` | Veo: unsupported (i2v off first reference with `FLOW_ALLOW_DEGRADED=1`). Omni Flash works |
| Omni Flash text-to-video | `POST /api/flow/generate-video-omni-text` | Works (4/6/8/10 s) |
| Upscale | `UPSCALE_VIDEO` | Veo: unsupported, no fallback |

## Narration

`POST /api/videos/{vid}/narrate` with `project_id`, `orientation` (default
`HORIZONTAL`), a voice `template` or `ref_audio`, and `mix` (default `true`):

- Scenes without `narrator_text` are skipped.
- `narration_audio_status` becomes `COMPLETED` only if the WAV exists on disk.
- With `mix=true`, a scene is mixed (`output/<slug>/narrated/scene_NNN_<id>_mixed.mp4`)
  only when its video status is `COMPLETED` **and** `{orientation}_video_url`
  is a plain filesystem path to an existing file. Remote signed URLs and the
  `file://…` URLs stored for low-priority (workflow-mode) videos do not
  qualify, so mixing fails (`narration_mix_status=FAILED`) unless the column
  was set to a downloaded file's path (e.g. with `PATCH /api/scenes/{id}`).
