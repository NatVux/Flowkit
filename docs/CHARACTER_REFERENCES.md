# Character Reference Assets

Characters keep their existing `media_id` and `reference_image_url` fields as the current-reference compatibility pointer. Each successful reference generation also creates an immutable row in `character_reference_asset`:

- `character_id` and monotonically increasing `version` identify stable identity history.
- `media_id`, URL, optional source file, and JSON metadata describe the generated asset.
- `ACTIVE`, `RETIRED`, and `INVALID` describe asset usability without deleting history.

When a scene image is generated, the current reference version for every named character is copied into `scene_character_reference`. These snapshots are append-only, so replacing a character reference never rewrites the references used by historical scene generations.

Legacy characters with a populated `character.media_id` are promoted to version 1 on first scene use. Missing, invalid, or deleted source files are rejected before generation. Assets are invalidated explicitly rather than deleted; character deletion is restricted when reference history exists, preventing orphaned assets and historical references.

Regeneration creates the next version and updates the character's compatibility pointer. Dependent current scene assets may be invalidated by the pipeline, but historical reference snapshot rows remain queryable.
