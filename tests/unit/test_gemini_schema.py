"""to_gemini_schema: provider JSON Schema → the subset Gemini's response_json_schema serves.

Fixture storyplan_provider_schema.json is json.dumps(provider_schema(StoryPlan)) from the real code.
"""

import copy
import inspect
import json
from pathlib import Path
from typing import Optional

import pytest
from pydantic import BaseModel, ValidationError

from agent.models import ai_content
from agent.models.ai_content import StoryPlan, provider_schema
from agent.services.ai.gemini_schema import to_gemini_schema
from agent.services.ai.mock import default_story_plan

FIXTURE = Path(__file__).parent / "fixtures" / "storyplan_provider_schema.json"
UNSUPPORTED = {
    "minLength", "maxLength", "maxItems", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "pattern", "format", "title", "default", "examples", "$schema", "$defs", "definitions", "const",
    "oneOf", "allOf",
}


def _load() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _keywords(node, path="#"):
    """Yield (path, keyword, value) for every schema keyword; property names are not keywords."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "properties":
                for name, sub in value.items():
                    yield from _keywords(sub, f"{path}/properties/{name}")
            else:
                yield path, key, value
                yield from _keywords(value, f"{path}/{key}")
    elif isinstance(node, list):
        for i, item in enumerate(node):
            yield from _keywords(item, f"{path}/{i}")


def _unsupported(schema) -> list[str]:
    found = [f"{p}:{k}" for p, k, _ in _keywords(schema) if k in UNSUPPORTED]
    found += [f"{p}:minItems={v}" for p, k, v in _keywords(schema) if k == "minItems" and v > 1]
    found += [f"{p}:anyOf-null" for p, k, v in _keywords(schema)
              if k == "anyOf" and any(s == {"type": "null"} for s in v) and len(v) == 2]
    return found


def _node(schema, *path):
    for step in path:
        schema = schema["properties"][step] if step != "[]" else schema["items"]
    return schema


# ─── fixture ────────────────────────────────────────────────

def test_fixture_matches_current_provider_schema():
    # If StoryPlan changes, regenerate the fixture from provider_schema(StoryPlan).
    assert _load() == json.loads(json.dumps(provider_schema(StoryPlan)))


def test_raw_fixture_has_the_problem_keywords():
    kinds = {k for _, k, _ in _keywords(_load())}
    assert {"minLength", "maxLength", "maxItems", "anyOf"} <= kinds


# ─── StoryPlan conversion ───────────────────────────────────

class TestStoryPlanSchema:
    def test_output_has_no_unsupported_keywords(self):
        assert _unsupported(to_gemini_schema(_load())) == []

    def test_structure_required_and_enum_are_kept(self):
        raw, out = _load(), to_gemini_schema(_load())
        assert out["type"] == "object"
        assert out["required"] == raw["required"]
        assert list(out["properties"]) == list(raw["properties"])
        for field in ("characters", "locations", "scenes"):
            assert out["properties"][field]["type"] == "array"
            assert out["properties"][field]["items"]["type"] == "object"
            assert _node(out, field, "[]")["required"] == _node(raw, field, "[]")["required"]
            assert list(_node(out, field, "[]")["properties"]) == list(_node(raw, field, "[]")["properties"])
        assert _node(out, "characters", "[]", "entity_type")["enum"] == \
            ["character", "creature", "visual_asset", "generic_troop", "faction"]
        assert _node(out, "scenes", "[]", "continues_previous") == {"type": "boolean"}
        assert _node(out, "scenes", "[]", "character_names", "[]") == {"type": "string"}

    def test_min_items_one_is_kept(self):
        assert to_gemini_schema(_load())["properties"]["scenes"]["minItems"] == 1

    def test_optional_becomes_nullable_type(self):
        out = to_gemini_schema(_load())
        voice = _node(out, "characters", "[]", "voice_description")
        narration = _node(out, "scenes", "[]", "narration")
        assert voice == {"type": ["string", "null"], "description":
                         "ENGLISH. Optional, for characters and creatures: voice tone and pace, "
                         "at most ~30 words (max 300 characters)"}
        assert narration == {"type": ["string", "null"], "description":
                             "In the story language. Voice-over, about 8 seconds (max 600 characters)"}

    def test_limits_move_into_description(self):
        out = to_gemini_schema(_load())
        assert _node(out, "characters", "[]", "description")["description"] == \
            "ENGLISH. Visual appearance only (max 600 characters)"
        assert _node(out, "story")["description"] == "In the story language (max 4000 characters)"
        assert _node(out, "scenes", "[]", "image_prompt")["description"] == \
            "ENGLISH. Still frame: action, composition, setting; entity names exactly as declared (max 1500 characters)"
        assert _node(out, "scenes")["description"] == "Max 30 items"
        assert _node(out, "characters")["description"] == "Max 12 items"
        assert _node(out, "scenes", "[]", "character_names")["description"] == "Max 10 items"

    def test_input_is_not_mutated(self):
        raw = _load()
        before = copy.deepcopy(raw)
        to_gemini_schema(raw)
        assert raw == before

    def test_is_idempotent(self):
        once = to_gemini_schema(_load())
        assert to_gemini_schema(once) == once


# ─── every AI content model ─────────────────────────────────

_MODELS = [
    cls for _, cls in inspect.getmembers(ai_content, inspect.isclass)
    if issubclass(cls, BaseModel) and cls.__module__ == ai_content.__name__ and not cls.__name__.startswith("_")
]


def test_models_include_story_plan_and_youtube_metadata():
    names = {cls.__name__ for cls in _MODELS}
    assert {"StoryPlan", "YouTubeMetadata"} <= names


@pytest.mark.parametrize("model", _MODELS, ids=lambda m: m.__name__)
def test_every_model_converts_cleanly(model):
    # Both the pipeline's provider_schema() and Pydantic's raw schema (with $defs/$ref/title/default).
    for schema in (provider_schema(model), model.model_json_schema()):
        out = to_gemini_schema(schema)
        assert _unsupported(out) == []
        assert "$ref" not in json.dumps(out)  # none of these models is recursive
        assert set(out.get("required", [])) <= set(out.get("properties", {}))


def test_youtube_metadata_limits_become_hints():
    out = to_gemini_schema(provider_schema(ai_content.YouTubeMetadata))
    assert out["properties"]["description"]["description"] == "Max 5000 characters"
    assert out["properties"]["tags"] == {"items": {"type": "string"}, "type": "array", "description": "Max 30 items"}


# ─── generic JSON Schema handling ───────────────────────────

class TestGenericSchema:
    def test_refs_are_inlined_and_defs_dropped(self):
        raw = StoryPlan.model_json_schema()
        out = to_gemini_schema(raw)
        assert "$defs" not in out and "$ref" not in json.dumps(out)
        assert _node(out, "scenes", "[]")["required"] == ["summary", "image_prompt", "video_prompt"]

    def test_recursive_ref_is_kept_with_its_definition(self):
        class TreeNode(BaseModel):
            name: str
            children: list["TreeNode"] = []
            parent: Optional["TreeNode"] = None

        out = to_gemini_schema(TreeNode.model_json_schema())
        assert out["properties"]["children"]["items"] == {"$ref": "#/$defs/TreeNode"}
        assert out["properties"]["parent"] == {"anyOf": [{"$ref": "#/$defs/TreeNode"}, {"type": "null"}]}
        assert out["$defs"]["TreeNode"]["properties"]["name"] == {"type": "string"}
        assert "title" not in json.dumps(out)

    def test_const_oneof_allof(self):
        raw = {
            "type": "object",
            "properties": {
                "kind": {"const": "scene"},
                "value": {"oneOf": [{"type": "string"}, {"type": "integer", "minimum": 0}]},
                "wrapped": {"allOf": [{"type": "string", "description": "inner", "maxLength": 5}],
                            "description": "outer"},
            },
            "required": ["kind", "ghost"],
        }
        out = to_gemini_schema(raw)
        assert out["properties"]["kind"] == {"enum": ["scene"]}
        assert out["properties"]["value"] == {"anyOf": [{"type": "string"},
                                                        {"type": "integer", "description": ">= 0"}]}
        assert out["properties"]["wrapped"] == {"type": "string", "description": "outer. inner (max 5 characters)"}
        assert out["required"] == ["kind"]  # names missing from properties are dropped

    def test_other_constraints_become_hints(self):
        raw = {"type": "object", "properties": {
            "n": {"type": "number", "exclusiveMinimum": 0, "maximum": 10},
            "code": {"type": "string", "pattern": "^[A-Z]+$", "format": "date", "minLength": 3},
            "xs": {"type": "array", "items": {"type": "string"}, "minItems": 2},
        }}
        props = to_gemini_schema(raw)["properties"]
        assert props["n"] == {"type": "number", "description": "> 0, <= 10"}
        assert props["code"] == {"type": "string", "description": "Min 3 characters, must match regex ^[A-Z]+$, format: date"}
        assert props["xs"] == {"type": "array", "items": {"type": "string"}, "description": "At least 2 items"}


# ─── server-side validation is still the gate ───────────────

class TestPydanticStillValidates:
    def test_valid_plan_passes(self):
        StoryPlan.model_validate(default_story_plan())

    def test_story_over_4000_chars_is_rejected(self):
        plan = default_story_plan() | {"story": "x" * 4001}
        with pytest.raises(ValidationError, match="story"):
            StoryPlan.model_validate(plan)

    def test_empty_scenes_are_rejected(self):
        with pytest.raises(ValidationError, match="scenes"):
            StoryPlan.model_validate(default_story_plan() | {"scenes": []})
