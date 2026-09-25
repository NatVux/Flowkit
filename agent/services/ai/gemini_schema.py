"""JSON Schema → the subset Gemini's `response_json_schema` accepts for serving.

Gemini compiles the schema into a decoding constraint. String length and array
size bounds are not part of the supported subset, and large ones (maxLength 4000
inside an array of maxItems 30) can make the constraint "too many states for
serving" (400 INVALID_ARGUMENT). Those bounds are moved into `description` as a
hint instead; the Pydantic models still enforce them on the returned JSON.

Kiểm chứng 2026-09-25 với gemini-3.1-flash-lite, google-genai 2.25.0: với schema StoryPlan,
có maxItems thì Gemini trả 400 INVALID_ARGUMENT với message chung chung 'Request contains an
invalid argument.'; bỏ maxItems thì OK. Schema YouTubeMetadata (maxItems 30/15 trên mảng
string phẳng) thì vẫn OK, nên lỗi phụ thuộc vào vị trí/tổ hợp maxItems (mảng object, mảng
lồng nhau), không phải mọi maxItems; ở đây bỏ hết cho chắc. minLength/maxLength được API
chấp nhận nhưng vẫn bị bỏ (chuyển thành gợi ý), vì Pydantic mới là lớp cưỡng chế giới hạn.
(Evidence: scripts/verify_gemini_live.py, --target story / --target youtube.)

Pure function: the input schema is never mutated.
"""

from __future__ import annotations

import copy
from typing import Any

# Keywords passed through to Gemini. Everything else is dropped or turned into a hint.
_KEEP = {
    "type", "description", "properties", "required", "additionalProperties", "items",
    "prefixItems", "minItems", "enum", "anyOf", "propertyOrdering",
}


def to_gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a Gemini-compatible copy of `schema`."""
    defs = {**schema.get("definitions", {}), **schema.get("$defs", {})}
    kept_defs: dict[str, Any] = {}
    out = _convert(schema, defs, (), kept_defs)
    if kept_defs:  # recursive $refs stay as $refs and need their targets
        out["$defs"] = kept_defs
    return out


def _ref_name(ref: Any) -> str | None:
    for prefix in ("#/$defs/", "#/definitions/"):
        if isinstance(ref, str) and ref.startswith(prefix):
            return ref[len(prefix):]
    return None


def _normalize(node: dict, defs: dict, stack: tuple[str, ...], kept_defs: dict) -> tuple[dict | None, tuple]:
    """Inline a non-recursive $ref and flatten allOf/oneOf/const, without converting children.

    Returns (None, stack) when the node must stay a $ref (recursive or unresolvable).
    """
    node = dict(node)
    if "$ref" in node:
        name = _ref_name(node["$ref"])
        if name is None or name not in defs or name in stack:
            return None, stack
        siblings = {k: v for k, v in node.items() if k != "$ref"}
        node, stack = _merge(dict(defs[name]), siblings), (*stack, name)
    if "allOf" in node:
        base: dict[str, Any] = {}
        for part in node.pop("allOf"):
            resolved, _ = _normalize(part, defs, stack, kept_defs) if isinstance(part, dict) else (None, stack)
            base = _merge(base, resolved if resolved is not None else part)
        node = _merge(base, node)
    if "oneOf" in node:
        node["anyOf"] = node.pop("oneOf")
    if "const" in node:
        node["enum"] = [node.pop("const")]
    return node, stack


def _is_null(sub: Any) -> bool:
    return isinstance(sub, dict) and sub.get("type") == "null" and len(sub) == 1


def _convert(node: Any, defs: dict, stack: tuple[str, ...], kept_defs: dict) -> Any:
    if not isinstance(node, dict):
        return copy.deepcopy(node)
    normalized, stack = _normalize(node, defs, stack, kept_defs)
    if normalized is None:
        name = _ref_name(node["$ref"])
        if name is None or name not in defs:
            return {"$ref": node["$ref"]}
        if name not in kept_defs:
            kept_defs[name] = {}  # placeholder stops re-entry while converting it
            kept_defs[name] = _convert(defs[name], defs, (name,), kept_defs)
        return {"$ref": f"#/$defs/{name}"}
    node = normalized

    # anyOf[X, null] → X with a nullable type; the outer description comes first.
    any_of = node.get("anyOf")
    if isinstance(any_of, list) and len(any_of) == 2 and sum(map(_is_null, any_of)) == 1:
        other = next(s for s in any_of if not _is_null(s))
        inner, inner_stack = _normalize(other, defs, stack, kept_defs) if isinstance(other, dict) else (None, stack)
        if inner is not None and "type" in inner and "anyOf" not in inner:
            outer = {k: v for k, v in node.items() if k != "anyOf"}
            node, stack = _merge(inner, outer), inner_stack
            types = inner["type"] if isinstance(inner["type"], list) else [inner["type"]]
            node["type"] = types if "null" in types else [*types, "null"]

    hints = _hints(node)
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key not in _KEEP:
            continue
        if key == "properties":  # field names, never schema keywords
            out[key] = {name: _convert(sub, defs, stack, kept_defs) for name, sub in value.items()}
        elif key in ("items", "additionalProperties"):
            out[key] = _convert(value, defs, stack, kept_defs)
        elif key in ("anyOf", "prefixItems"):
            out[key] = [_convert(sub, defs, stack, kept_defs) for sub in value]
        elif key == "minItems":
            if value <= 1:
                out[key] = value
        else:
            out[key] = copy.deepcopy(value)

    if "required" in out:
        props = out.get("properties", {})
        out["required"] = [name for name in out["required"] if name in props]
    if hints:
        text = ", ".join(hints)
        desc = out.get("description")
        out["description"] = f"{desc} ({text})" if desc else text[0].upper() + text[1:]
    return out


def _merge(base: dict, extra: dict) -> dict:
    """`extra` wins, except descriptions are joined and properties/required are unioned."""
    merged = {**base, **extra}
    descs = [d for d in (extra.get("description"), base.get("description")) if d]
    if descs:
        merged["description"] = descs[0] if len(descs) == 1 or descs[0] == descs[1] else f"{descs[0]}. {descs[1]}"
    if "properties" in base and "properties" in extra:
        merged["properties"] = {**base["properties"], **extra["properties"]}
    if "required" in base and "required" in extra:
        merged["required"] = list(dict.fromkeys([*base["required"], *extra["required"]]))
    return merged


def _hints(node: dict) -> list[str]:
    """Human-readable versions of the constraints Gemini does not take as keywords."""
    hints = []
    min_len, max_len = node.get("minLength"), node.get("maxLength")
    if isinstance(min_len, int) and min_len > 1:
        hints.append(f"min {min_len} characters")
    if isinstance(max_len, int):
        hints.append(f"max {max_len} characters")
    min_items, max_items = node.get("minItems"), node.get("maxItems")
    if isinstance(min_items, int) and min_items > 1:
        hints.append(f"at least {min_items} items")
    if isinstance(max_items, int):
        hints.append(f"max {max_items} items")
    if node.get("uniqueItems"):
        hints.append("unique items")
    for key, op in (("minimum", ">="), ("exclusiveMinimum", ">"), ("maximum", "<="), ("exclusiveMaximum", "<")):
        value = node.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            hints.append(f"{op} {value}")
    if node.get("pattern"):
        hints.append(f"must match regex {node['pattern']}")
    if node.get("format"):
        hints.append(f"format: {node['format']}")
    return hints
