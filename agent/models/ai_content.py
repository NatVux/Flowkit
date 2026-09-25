"""Pydantic models for AI-generated content.

Two roles:
- *Output* models (StoryPlan, YouTubeMetadata) are both the JSON Schema sent to the
  provider and the validator for what comes back. Model output is never stored or
  applied until it passes these.
- *API* models are the request/response bodies for agent/api/ai.py.
"""

from __future__ import annotations

import copy
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_NAME = Field(min_length=1, max_length=80)
CharacterEntityType = Literal["character", "creature", "visual_asset", "generic_troop", "faction"]


def _clean(value: str) -> str:
    return " ".join(value.split())


# ─── Model output: story plan ───────────────────────────────

class _Output(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class PlannedCharacter(_Output):
    name: str = _NAME
    entity_type: CharacterEntityType = "character"
    description: str = Field(min_length=1, max_length=600, description="Visual appearance only")
    voice_description: Optional[str] = Field(None, max_length=300, description="At most ~30 words")

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return _clean(v)

    @field_validator("voice_description")
    @classmethod
    def _voice(cls, v: Optional[str]) -> Optional[str]:
        if v is None or not v.strip():
            return None
        if len(v.split()) > 40:
            raise ValueError("voice_description must be at most ~30 words")
        return _clean(v)


class PlannedLocation(_Output):
    name: str = _NAME
    description: str = Field(min_length=1, max_length=600, description="Visual appearance only")

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return _clean(v)


class PlannedScene(_Output):
    summary: str = Field(min_length=1, max_length=200)
    image_prompt: str = Field(min_length=1, max_length=1500, description="Still frame: action, composition, setting")
    video_prompt: str = Field(min_length=1, max_length=1500, description="Motion with timing, e.g. '0-4s: ... 4-8s: ...'")
    narration: Optional[str] = Field(None, max_length=600)
    character_names: list[str] = Field(default_factory=list, max_length=10)
    continues_previous: bool = False

    @field_validator("narration")
    @classmethod
    def _narration(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v and v.strip() else None

    @field_validator("character_names")
    @classmethod
    def _names(cls, v: list[str]) -> list[str]:
        seen, out = set(), []
        for name in v:
            name = _clean(name)
            if name and name.lower() not in seen:
                seen.add(name.lower())
                out.append(name)
        return out


class StoryPlan(_Output):
    title: str = Field(min_length=1, max_length=100)
    logline: str = Field(min_length=1, max_length=300)
    story: str = Field(min_length=1, max_length=4000)
    characters: list[PlannedCharacter] = Field(default_factory=list, max_length=12)
    locations: list[PlannedLocation] = Field(default_factory=list, max_length=12)
    scenes: list[PlannedScene] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def _consistent(self) -> "StoryPlan":
        names: dict[str, str] = {}
        for entity in [*self.characters, *self.locations]:
            key = entity.name.lower()
            if key in names:
                raise ValueError(f"duplicate entity name: {entity.name!r}")
            names[key] = entity.name
        for index, scene in enumerate(self.scenes):
            unknown = [n for n in scene.character_names if n.lower() not in names]
            if unknown:
                raise ValueError(f"scene {index} references undefined entities: {unknown}")
            # canonicalise to the declared spelling
            scene.character_names = [names[n.lower()] for n in scene.character_names]
        if self.scenes[0].continues_previous:
            raise ValueError("the first scene cannot continue a previous scene")
        return self


# ─── Model output: YouTube metadata ─────────────────────────

class YouTubeMetadata(_Output):
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=5000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    hashtags: list[str] = Field(default_factory=list, max_length=15)

    @field_validator("hashtags")
    @classmethod
    def _hashtags(cls, v: list[str]) -> list[str]:
        out = []
        for tag in v:
            tag = tag.strip()
            if not tag:
                continue
            tag = tag if tag.startswith("#") else f"#{tag}"
            if " " in tag or len(tag) > 60:
                raise ValueError(f"invalid hashtag: {tag!r}")
            out.append(tag)
        return out

    @model_validator(mode="after")
    def _youtube_limits(self) -> "YouTubeMetadata":
        # Same limits the publisher enforces before an upload.
        from agent.services.youtube_publisher import validate_metadata
        checked = validate_metadata(self.title, self.description, self.tags, "private")
        self.tags = checked.tags
        # YouTube counts quotes around tags containing spaces toward a 500-char total.
        total = sum(len(t) + (2 if " " in t else 0) for t in self.tags) + max(len(self.tags) - 1, 0)
        if total > 500:
            raise ValueError(f"tags exceed YouTube's 500-character limit ({total})")
        return self


# ─── JSON Schema for the provider ───────────────────────────

def provider_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic JSON Schema with $refs inlined and cosmetic keys dropped.

    Validation still happens on our side; the schema only steers the model.
    """
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(copy.deepcopy(defs[node["$ref"].split("/")[-1]]))
            out = {}
            for key, value in node.items():
                if key == "properties":  # field names, never schema keywords
                    out[key] = {name: resolve(sub) for name, sub in value.items()}
                elif key not in ("title", "default"):
                    out[key] = resolve(value)
            return out
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve(schema)


# ─── API models ─────────────────────────────────────────────

AIOperation = Literal["STORY_PLAN", "YOUTUBE_METADATA"]
AIGenerationStatus = Literal["GENERATED", "APPLIED"]


class StoryPlanRequest(BaseModel):
    project_id: str
    brief: str = Field(min_length=1, max_length=4000, description="What the video is about")
    scene_count: int = Field(6, ge=1, le=30)
    language: Optional[str] = Field(None, max_length=32, description="Defaults to the project's language")
    audience: Optional[str] = Field(None, max_length=300)
    tone: Optional[str] = Field(None, max_length=300)


class YouTubeMetadataRequest(BaseModel):
    video_id: str
    instructions: Optional[str] = Field(None, max_length=1000)


class ApplyGenerationRequest(BaseModel):
    video_id: Optional[str] = Field(None, description="Required for STORY_PLAN")
    chain_scenes: bool = Field(
        False,
        description="Create CONTINUATION scenes where the plan says a scene continues the previous one. "
                    "Off by default: a continuation turns its parent's video into start+end-frame chaining, "
                    "which is unsupported on the Veo path (use Omni Flash or FLOW_ALLOW_DEGRADED=1).",
    )


class AIGeneration(BaseModel):
    id: str
    operation: AIOperation
    status: AIGenerationStatus
    provider: str
    model: str
    project_id: str
    video_id: Optional[str] = None
    request_id: str
    attempts: int
    output: dict[str, Any]
    created_at: Optional[str] = None
    applied_at: Optional[str] = None


class ApplyResult(BaseModel):
    generation_id: str
    status: AIGenerationStatus
    video_id: str
    characters_created: list[str] = Field(default_factory=list)
    characters_reused: list[str] = Field(default_factory=list)
    scenes_created: list[str] = Field(default_factory=list)
    video_updated: bool = False


class AIStatus(BaseModel):
    enabled: bool
    provider: Optional[str] = None
    model: Optional[str] = None
    configured: bool = False


