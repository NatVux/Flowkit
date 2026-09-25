"""Live check: which JSON Schema keyword makes Gemini reject a structured-output request?

Sends the same request AIContentService would send, varying only
response_json_schema. Sequential, 2s apart; 503/429 retried twice (5s, 10s) and
every attempt is reported in `tries`. No DB writes, no Flowkit server. The API
key is never printed; every provider message goes through gemini._redact.

    python scripts/verify_gemini_live.py                    # StoryPlan, 6 variants (raw/gemini twice)
    python scripts/verify_gemini_live.py --target youtube   # YouTubeMetadata, raw + gemini
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import config  # noqa: E402
from agent.models.ai_content import StoryPlan, YouTubeMetadata, provider_schema  # noqa: E402
from agent.services.ai.base import AIProviderError, AIRequest  # noqa: E402
from agent.services.ai.gemini import GeminiProvider, _enum_name, _redact  # noqa: E402
from agent.services.ai.gemini_schema import to_gemini_schema  # noqa: E402
from agent.services.ai_content import METADATA_SYSTEM, STORY_SYSTEM, _parse  # noqa: E402

PAUSE_S = 2
RETRY_DELAYS_S = (5, 10)
TRANSIENT = {429, 503}

# ─── prompts (same lines AIContentService builds; it has no standalone builder) ───

BRIEF = "Viết một câu chuyện phiêu lưu ngắn về một cậu bé khám phá khu rừng bí ẩn."


def story_prompt() -> str:
    # generate_story_plan for a fresh project: no story, no existing entities, no audience.
    return "\n".join([
        f"Brief: {BRIEF}",
        "Number of scenes: exactly 3",
        "Language for story, narration and summaries: vi. Keep prompts in English.",
        "Tone: adventure, cinematic",
    ])


def youtube_prompt() -> str:
    # generate_youtube_metadata reads these from the DB; fixed sample values stand in for them.
    story = ("Minh, một cậu bé mười tuổi, lạc vào khu rừng bí ẩn sau làng. Cậu theo một con đom đóm "
             "phát sáng, vượt qua cây cầu gỗ cũ và tìm thấy một cây cổ thụ biết nói, người canh giữ khu rừng.")
    scenes = [
        "Minh bước vào bìa rừng lúc hoàng hôn, tò mò nhìn ánh sáng lấp lánh giữa những tán cây.",
        "Cậu theo con đom đóm qua cây cầu gỗ ọp ẹp bắc ngang dòng suối.",
        "Cây cổ thụ mở mắt và chào đón Minh như một người bạn cũ.",
    ]
    lines = ["Project: Khu rừng bí ẩn", "Working title: Minh và khu rừng bí ẩn", f"Story: {story[:3000]}"]
    lines += [f"Scene {i + 1}: {text[:300]}" for i, text in enumerate(scenes)]
    lines.append("Language: vi")
    return "\n".join(lines)


TARGETS = {
    "story": {"operation": "story_plan", "model": StoryPlan, "system": STORY_SYSTEM, "prompt": story_prompt,
              "plan": [("raw", 1), ("no_string_len", 1), ("no_maxItems", 1), ("no_null_anyOf", 1),
                       ("no_len_no_maxItems", 1), ("gemini", 1), ("raw", 2), ("gemini", 2)]},
    "youtube": {"operation": "youtube_metadata", "model": YouTubeMetadata, "system": METADATA_SYSTEM,
                "prompt": youtube_prompt, "plan": [("raw", 1), ("gemini", 1)]},
}


# ─── schema variants ────────────────────────────────────────

def _strip(node, keys: set[str]):
    """Remove schema keywords (never property names) recursively."""
    if isinstance(node, list):
        return [_strip(n, keys) for n in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for key, value in node.items():
        if key == "properties":
            out[key] = {name: _strip(sub, keys) for name, sub in value.items()}
        elif key not in keys:
            out[key] = _strip(value, keys)
    return out


def _no_null_anyof(node):
    """anyOf[X, {"type": "null"}] → X with type [X.type, "null"]; everything else (lengths too) kept."""
    if isinstance(node, list):
        return [_no_null_anyof(n) for n in node]
    if not isinstance(node, dict):
        return node
    any_of = node.get("anyOf")
    if isinstance(any_of, list) and len(any_of) == 2 and {"type": "null"} in any_of:
        inner = next(s for s in any_of if s != {"type": "null"})
        node = {**{k: v for k, v in node.items() if k != "anyOf"}, **inner, "type": [inner["type"], "null"]}
    out = {}
    for key, value in node.items():
        if key == "properties":
            out[key] = {name: _no_null_anyof(sub) for name, sub in value.items()}
        else:
            out[key] = _no_null_anyof(value)
    return out


def variants(model_cls) -> dict[str, dict]:
    raw = provider_schema(model_cls)
    return {
        "raw": copy.deepcopy(raw),
        "no_string_len": _strip(raw, {"minLength", "maxLength"}),
        "no_maxItems": _strip(raw, {"maxItems"}),
        "no_null_anyOf": _no_null_anyof(raw),
        "no_len_no_maxItems": _strip(raw, {"minLength", "maxLength", "maxItems"}),
        "gemini": to_gemini_schema(raw),
    }


# ─── one live run (with retries on 503/429) ─────────────────

async def run_once(provider: GeminiProvider, request: AIRequest, model_cls, schema: dict,
                   variant: str, run: int) -> dict:
    from google.genai import errors as genai_errors

    # Exactly GeminiProvider's config; only the schema is swapped.
    cfg = provider._config(request).model_copy(update={"response_json_schema": schema})
    client = provider._get_client()
    tries: list[dict] = []
    row = {"variant": variant, "run": run, "tries": tries}
    for attempt in range(len(RETRY_DELAYS_S) + 1):
        started = time.monotonic()
        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(model=provider.model, contents=request.prompt, config=cfg),
                timeout=request.timeout_seconds + 5,
            )
        except genai_errors.APIError as exc:
            message = _redact(exc.message or "", provider._api_key)[:500]
            tries.append({"attempt": attempt + 1, "code": exc.code, "status": exc.status,
                          "seconds": round(time.monotonic() - started, 1)})
            row.update(result="ERROR", code=exc.code, status=exc.status, message=message, finish_reason=None)
            if exc.code in TRANSIENT and attempt < len(RETRY_DELAYS_S):
                tries[-1]["message"] = message
                await asyncio.sleep(RETRY_DELAYS_S[attempt])
                continue
            if exc.code in TRANSIENT:
                row["result"] = "INCONCLUSIVE"
            return row
        except Exception as exc:  # noqa: BLE001 - timeouts, transport errors
            tries.append({"attempt": attempt + 1, "code": None, "status": type(exc).__name__,
                          "seconds": round(time.monotonic() - started, 1)})
            row.update(result="ERROR", code=None, status=type(exc).__name__,
                       message=_redact(str(exc), provider._api_key)[:500], finish_reason=None)
            return row

        candidates = response.candidates or []
        finish = _enum_name(candidates[0].finish_reason) if candidates else None
        parts = (candidates[0].content.parts or []) if candidates and candidates[0].content else []
        text = "".join(p.text for p in parts if p.text and not p.thought)
        tries.append({"attempt": attempt + 1, "code": 200, "status": "OK",
                      "seconds": round(time.monotonic() - started, 1)})
        row.update(result="OK", code=200, status=None, message=None, finish_reason=finish, response_chars=len(text))
        try:
            parsed = _parse(text, model_cls)  # the service's own parse + Pydantic validation
            detail = f"{len(parsed.scenes)} scenes" if hasattr(parsed, "scenes") else \
                f"{len(parsed.tags)} tags, {len(parsed.hashtags)} hashtags"
            row["pydantic"] = f"OK ({detail})"
        except AIProviderError as exc:
            row["pydantic"] = f"FAIL: {str(exc)[:400]}"
        return row
    raise AssertionError("unreachable")


async def main(target_name: str) -> int:
    if not config.GEMINI_API_KEY:
        print(json.dumps({"error": "GEMINI_API_KEY is not set"}))
        return 2
    target = TARGETS[target_name]
    provider = GeminiProvider(api_key=config.GEMINI_API_KEY, model=config.GEMINI_MODEL)
    request = AIRequest(operation=target["operation"], system_instruction=target["system"],
                        prompt=target["prompt"](), response_schema=provider_schema(target["model"]),
                        request_id="verify-live", timeout_seconds=config.GEMINI_TIMEOUT_SECONDS)
    schemas = variants(target["model"])
    import google.genai
    print(json.dumps({"target": target_name, "model": provider.model, "sdk": google.genai.__version__,
                      "timeout_s": request.timeout_seconds,
                      "schema_bytes": {k: len(json.dumps(schemas[k])) for k, _ in target["plan"]}},
                     ensure_ascii=False))

    for index, (variant, run) in enumerate(target["plan"]):
        if index:
            await asyncio.sleep(PAUSE_S)
        row = await run_once(provider, request, target["model"], schemas[variant], variant, run)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", choices=sorted(TARGETS), default="story")
    sys.exit(asyncio.run(main(parser.parse_args().target)))
