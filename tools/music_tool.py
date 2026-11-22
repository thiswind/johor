#!/usr/bin/env python3
"""
Music Generation Tool (Suno via OpenAI-compatible gateway)

Task-based flow: POST /suno/submit/music -> poll /suno/fetch/<id> ->
download the first clip to the local audio cache -> return MEDIA: tag.

Uses the same gateway credentials as the OpenAI audio tools
(OPENAI_API_KEY + OPENAI_BASE_URL env vars), so no extra key setup.
"""

import json
import logging
import os
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SUBMIT_URL = "{base}/suno/submit/music"
FETCH_URL = "{base}/suno/fetch/{task_id}"

POLL_INTERVAL_SECONDS = 10
POLL_TIMEOUT_SECONDS = 300  # generation usually completes in 60-120s; allow 5min

DEFAULT_OUTPUT_DIR = Path.home() / ".hermes" / "audio_cache"


def _get_env_value(name: str) -> str:
    """Read an env var, honoring the .hermes/.env file if the var is unset."""
    value = os.environ.get(name, "")
    if value:
        return value.strip()
    env_path = Path.home() / ".hermes" / ".env"
    if env_path.exists():
        try:
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if line.startswith(f"{name}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            pass
    return ""


def _resolve_gateway() -> tuple[str, str]:
    """Return (base_url, api_key) for the Suno-capable gateway."""
    # Gateway endpoint is deployment-specific: always require the env pair
    # (OPENAI_BASE_URL + OPENAI_API_KEY, e.g. an aggregator or Suno proxy).
    base = _get_env_value("OPENAI_BASE_URL")
    # /suno/* endpoints live at the site root, strip the /v1 suffix
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    key = _get_env_value("OPENAI_API_KEY")
    return base.rstrip("/"), key


def check_music_requirements() -> bool:
    """Return True when the gateway key is configured."""
    _, key = _resolve_gateway()
    return bool(key)


# ---------------------------------------------------------------------------
# Gateway helpers (urllib keeps this dependency-free)
# ---------------------------------------------------------------------------
def _http_json(url: str, api_key: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST" if payload is not None else "GET",
    )
    timeout = 30 if payload is not None else 20
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _submit_music(prompt: str, instrumental: bool) -> str:
    """Submit a Suno generation task, return the task id."""
    base, key = _resolve_gateway()
    body = {
        "gpt_description_prompt": prompt,
        "make_instrumental": bool(instrumental),
    }
    result = _http_json(SUBMIT_URL.format(base=base), key, body)
    if result.get("code") != "success":
        raise RuntimeError(f"submit failed: {result.get('message') or result}")
    task_id = result.get("data")
    if not task_id or not isinstance(task_id, str):
        raise RuntimeError(f"submit returned no task id: {result}")
    return task_id


def _fetch_task(task_id: str) -> Dict[str, Any]:
    base, key = _resolve_gateway()
    return _http_json(FETCH_URL.format(base=base, task_id=task_id), key)


def _download_file(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "johor-agent/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp, dest.open("wb") as fh:
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            fh.write(chunk)


# ---------------------------------------------------------------------------
# Tool entry point
# ---------------------------------------------------------------------------
def music_generate_tool(prompt: str, instrumental: bool = False) -> str:
    """Generate music from a description. Returns JSON with a MEDIA: tag.

    Args:
        prompt: English description of the desired song (style, mood,
            instruments, vocal gender, language, theme). The calling model
            should translate user requests into a rich English prompt.
        instrumental: True for instrumental-only (no vocals).

    Returns:
        JSON string: {"success": bool, "file_path": str, "media_tag": str,
        "title": str, "duration": float, "clips": int, "error": str}
    """
    result: Dict[str, Any] = {
        "success": False, "file_path": "", "media_tag": "",
        "title": "", "duration": 0.0, "clips": 0, "error": "",
    }
    if not prompt or not prompt.strip():
        result["error"] = "prompt is required"
        return json.dumps(result)

    if not check_music_requirements():
        result["error"] = "OPENAI_API_KEY not configured for music gateway"
        return json.dumps(result)

    try:
        task_id = _submit_music(prompt.strip(), instrumental)
        logger.info("music task submitted: %s", task_id)
    except Exception as exc:
        result["error"] = f"submit failed: {exc}"
        return json.dumps(result)

    deadline = time.time() + POLL_TIMEOUT_SECONDS
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL_SECONDS)
        try:
            task = _fetch_task(task_id)
        except Exception as exc:
            logger.warning("music poll error (will retry): %s", exc)
            continue
        status = (task.get("data") or {}).get("status", "")
        if status == "SUCCESS":
            clips = (task.get("data") or {}).get("data") or []
            if not clips:
                result["error"] = "task succeeded but no clips returned"
                return json.dumps(result)
            clip = clips[0]
            audio_url = clip.get("cld2AudioUrl") or clip.get("audio_url") or ""
            if not audio_url:
                result["error"] = "no audio url in completed clip"
                return json.dumps(result)
            DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            dest = DEFAULT_OUTPUT_DIR / f"music_{stamp}.m4a"
            try:
                _download_file(audio_url, dest)
            except Exception as exc:
                result["error"] = f"download failed: {exc}"
                return json.dumps(result)
            result.update({
                "success": True,
                "file_path": str(dest),
                "media_tag": f"MEDIA:{dest}",
                "title": clip.get("title", ""),
                "duration": clip.get("duration", 0.0),
                "clips": len(clips),
            })
            return json.dumps(result)
        if status in {"FAILURE", "FAILED", "FAIL"}:
            reason = (task.get("data") or {}).get("fail_reason", "unknown")
            result["error"] = f"generation failed: {reason}"
            return json.dumps(result)
        # still IN_PROGRESS / QUEUED -> keep polling

    result["error"] = f"timed out after {POLL_TIMEOUT_SECONDS}s (task {task_id})"
    return json.dumps(result)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
from tools.registry import registry

MUSIC_SCHEMA = {
    "name": "music_generate",
    "description": (
        "Generate a song from a text description (Suno). Returns 2 clips; "
        "the first is delivered as an audio file via MEDIA: tag. Takes about "
        "1-2 minutes. Write the prompt in English covering: genre/style, mood, "
        "instruments, tempo, vocal gender + language, and theme. Set "
        "instrumental=true for no vocals. Examples: 'Chinese traditional "
        "instrumental, guzheng and dizi flute, gentle and poetic' | "
        "'Levantine Arabic pop, cheerful dabke rhythm, oud and qanun, warm "
        "male vocals, festive wedding mood'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "English song description: style, mood, instruments, tempo, vocals, language, theme",
            },
            "instrumental": {
                "type": "boolean",
                "description": "True = instrumental only (no vocals). Default false.",
            },
        },
        "required": ["prompt"],
    },
}

registry.register(
    name="music_generate",
    toolset="music",
    schema=MUSIC_SCHEMA,
    handler=lambda args, **kw: music_generate_tool(
        prompt=args.get("prompt", ""),
        instrumental=bool(args.get("instrumental", False)),
    ),
    check_fn=check_music_requirements,
    emoji="🎵",
)
