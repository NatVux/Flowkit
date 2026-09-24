"""OmniVoice TTS service — subprocess-based for compatibility."""
import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Optional

from agent.config import TTS_MODEL, TTS_SAMPLE_RATE
from agent.services.media_process import run_media_command, validate_output_file

logger = logging.getLogger(__name__)

# Default to python3.10 (has torch/torchaudio/omnivoice); override with TTS_PYTHON_BIN if needed
PYTHON_BIN = os.environ.get("TTS_PYTHON_BIN", "python3.10")

# Inline script template for TTS generation via subprocess
_TTS_SCRIPT = """
import sys, json, torch, torchaudio

args = json.loads(sys.argv[1])
from omnivoice import OmniVoice

model = OmniVoice.from_pretrained(args["model"], device_map="cpu", dtype=torch.float32)

kwargs = {"text": args["text"]}
if args.get("ref_audio") and args.get("ref_text"):
    kwargs["ref_audio"] = args["ref_audio"]
    kwargs["ref_text"] = args["ref_text"]
elif args.get("instruct"):
    kwargs["instruct"] = args["instruct"]
if args.get("speed") and args["speed"] != 1.0:
    kwargs["speed"] = args["speed"]

audio = model.generate(**kwargs)
torchaudio.save(args["output"], audio[0], args["sample_rate"])
print(json.dumps({"ok": True, "path": args["output"]}))
"""

# Batch script — loads model once, generates for multiple texts
_TTS_BATCH_SCRIPT = """
import sys, json, torch, torchaudio
from pathlib import Path

args = json.loads(sys.argv[1])
from omnivoice import OmniVoice

model = OmniVoice.from_pretrained(args["model"], device_map="cpu", dtype=torch.float32)

results = []
for item in args["items"]:
    try:
        kwargs = {"text": item["text"]}
        if args.get("ref_audio") and args.get("ref_text"):
            kwargs["ref_audio"] = args["ref_audio"]
            kwargs["ref_text"] = args["ref_text"]
        elif args.get("instruct"):
            kwargs["instruct"] = args["instruct"]
        if args.get("speed") and args["speed"] != 1.0:
            kwargs["speed"] = args["speed"]

        audio = model.generate(**kwargs)
        Path(item["output"]).parent.mkdir(parents=True, exist_ok=True)
        torchaudio.save(item["output"], audio[0], args["sample_rate"])

        info = torchaudio.info(item["output"])
        duration = info.num_frames / info.sample_rate
        results.append({"id": item["id"], "ok": True, "path": item["output"], "duration": duration})
    except Exception as e:
        results.append({"id": item["id"], "ok": False, "error": str(e)})

print(json.dumps(results))
"""


async def generate_speech(
    text: str,
    output_path: str,
    instruct: Optional[str] = None,
    ref_audio: Optional[str] = None,
    ref_text: Optional[str] = None,
    speed: float = 1.0,
) -> str:
    """Generate speech for text via subprocess. Returns path to WAV file."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    args = {
        "model": TTS_MODEL,
        "text": text,
        "output": output_path,
        "sample_rate": TTS_SAMPLE_RATE,
        "speed": speed,
    }
    if instruct:
        args["instruct"] = instruct
    if ref_audio:
        args["ref_audio"] = ref_audio
    if ref_text:
        args["ref_text"] = ref_text

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _run_tts_subprocess, args)

    if not result.get("ok"):
        Path(output_path).unlink(missing_ok=True)
        raise RuntimeError(f"TTS failed: {result.get('error', 'unknown')}")

    logger.info("TTS saved to %s", output_path)
    return output_path


def _run_tts_subprocess(args: dict) -> dict:
    """Run TTS subprocess."""
    proc = run_media_command([PYTHON_BIN, "-c", _TTS_SCRIPT, json.dumps(args)], timeout=120)
    if not proc.ok:
        return {"ok": False, "error": proc.error or "unknown TTS error"}
    try:
        result = json.loads(proc.stdout.strip().split("\n")[-1])
        validate_output_file(result.get("path") or args["output"], "TTS output")
        return result
    except (json.JSONDecodeError, IndexError):
        return {"ok": False, "error": proc.stdout[-200:] + proc.stderr[-200:]}
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


async def generate_video_narration(
    scenes: list[dict],
    output_dir: str,
    instruct: Optional[str] = None,
    ref_audio: Optional[str] = None,
    ref_text: Optional[str] = None,
    speed: float = 1.0,
) -> list[dict]:
    """Generate narration WAVs for scenes with narrator_text.

    Uses batch subprocess — loads model once for all scenes.
    Returns list of result dicts.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Build batch items (only scenes with narrator_text)
    items = []
    scene_map = {}
    for scene in scenes:
        scene_id = scene.get("id")
        display_order = scene.get("display_order", 0)
        narrator_text = scene.get("narrator_text")

        if not narrator_text:
            continue

        wav_path = str(out_dir / f"scene_{display_order:03d}_{scene_id}.wav")
        # Skip if WAV already exists and is non-trivial (>1KB)
        if Path(wav_path).exists() and Path(wav_path).stat().st_size > 1024:
            logger.info("Skipping scene %03d (WAV exists: %s)", display_order, wav_path)
            scene_map[scene_id] = {"display_order": display_order, "narrator_text": narrator_text, "skipped": True, "wav_path": wav_path}
            continue
        items.append({"id": scene_id, "text": narrator_text, "output": wav_path})
        scene_map[scene_id] = {"display_order": display_order, "narrator_text": narrator_text}

    # Run batch subprocess if there are items
    batch_results = {}
    if items:
        args = {
            "model": TTS_MODEL,
            "sample_rate": TTS_SAMPLE_RATE,
            "speed": speed,
            "items": items,
        }
        if instruct:
            args["instruct"] = instruct
        if ref_audio:
            args["ref_audio"] = ref_audio
        if ref_text:
            args["ref_text"] = ref_text

        loop = asyncio.get_event_loop()
        raw = await loop.run_in_executor(None, _run_batch_subprocess, args)
        for r in raw:
            batch_results[r["id"]] = r

    # Build final results for all scenes
    results = []
    for scene in scenes:
        scene_id = scene.get("id")
        display_order = scene.get("display_order", 0)
        narrator_text = scene.get("narrator_text")

        if not narrator_text:
            results.append({
                "scene_id": scene_id,
                "display_order": display_order,
                "narrator_text": None,
                "audio_path": None,
                "duration": None,
                "status": "SKIPPED",
                "error": None,
            })
            continue

        sm = scene_map.get(scene_id, {})
        if sm.get("skipped"):
            results.append({
                "scene_id": scene_id,
                "display_order": display_order,
                "narrator_text": narrator_text,
                "audio_path": sm["wav_path"],
                "duration": None,
                "status": "COMPLETED",
                "error": None,
            })
            continue

        br = batch_results.get(scene_id, {})
        if br.get("ok"):
            results.append({
                "scene_id": scene_id,
                "display_order": display_order,
                "narrator_text": narrator_text,
                "audio_path": br.get("path"),
                "duration": br.get("duration"),
                "status": "COMPLETED",
                "error": None,
            })
        else:
            results.append({
                "scene_id": scene_id,
                "display_order": display_order,
                "narrator_text": narrator_text,
                "audio_path": None,
                "duration": None,
                "status": "FAILED",
                "error": br.get("error", "not processed"),
            })

    return results


def _run_batch_subprocess(args: dict) -> list[dict]:
    """Run batch TTS subprocess. Model loads once."""
    timeout = 180 + len(args.get("items", [])) * 45  # ~180s model load + ~45s per scene
    proc = run_media_command([PYTHON_BIN, "-c", _TTS_BATCH_SCRIPT, json.dumps(args)], timeout=timeout)
    if not proc.ok:
        error = proc.error or "unknown"
        return [{"id": item["id"], "ok": False, "error": error} for item in args["items"]]
    try:
        results = json.loads(proc.stdout.strip().split("\n")[-1])
        for result in results:
            if result.get("ok"):
                try:
                    validate_output_file(result.get("path"), "TTS output")
                except (FileNotFoundError, RuntimeError, ValueError) as exc:
                    result.update(ok=False, error=str(exc))
            if not result.get("ok") and result.get("path"):
                Path(result["path"]).unlink(missing_ok=True)
        return results
    except (json.JSONDecodeError, IndexError):
        error = proc.stdout[-200:] + proc.stderr[-200:]
        return [{"id": item["id"], "ok": False, "error": error} for item in args["items"]]
