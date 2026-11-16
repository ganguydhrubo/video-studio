"""User-level configuration stored in %LOCALAPPDATA%\\VideoStudio\\config.json."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from video_studio.util import app_data_dir, read_json, write_json

DEFAULTS: dict[str, Any] = {
    # AI
    "ai.provider": "auto",            # auto (free providers) | opencode | ollama | gemini | qwen | claude | codex | none
    "ai.fallback": ["opencode", "ollama", "gemini", "qwen", "claude", "codex"],
    "ai.allow_fallback": True,
    "ai.paid_fallback": False,        # paid plans (claude, codex) are used only when you pick one, never as a silent fallback
    "ai.models": {},                  # provider -> model name, empty = CLI default
    "ai.timeout": 240,
    "ai.max_calls_per_project": 12,
    # updates: read the public latest-release record on GitHub (nothing about you is sent)
    "updates.check": True,
    # local AI video generation model (Hugging Face id), used only on PCs with a large NVIDIA card
    "generate.model": "",
    # tool path overrides (empty = auto-detect)
    "tools.ffmpeg": "",
    "tools.ffprobe": "",
    "tools.blender": "",
    "tools.node": "",
    "tools.piper": "",
    "tools.piper_model": "",
    # production defaults
    "defaults.aspect": "9:16",
    "defaults.height": 1920,
    "defaults.fps": 30,
    "defaults.quality": "standard",   # draft | standard | high
    "defaults.voice": "auto",
    "defaults.voice_rate": 0,         # SAPI rate -10..10
    "defaults.captions": True,
    "defaults.music": True,
    "defaults.motion_engine": "canvas",  # canvas | hyperframes
    "defaults.use_blender": True,
    "defaults.loudness": -14.0,
    # resources
    "resources.max_threads": 0,       # 0 = auto (cores - 2)
    "resources.min_free_disk_gb": 3,
    "workspace": "",
}


def config_path() -> Path:
    return app_data_dir() / "config.json"


def load() -> dict[str, Any]:
    stored = read_json(config_path(), {}) or {}
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in stored.items() if k in DEFAULTS})
    return merged


def get(key: str) -> Any:
    return load().get(key, DEFAULTS.get(key))


def set_value(key: str, value: Any) -> None:
    if key not in DEFAULTS:
        raise KeyError(f"Unknown setting '{key}'. Run `video-studio config list` to see valid keys.")
    default = DEFAULTS[key]
    if isinstance(value, str):
        value = _coerce(value, default)
    stored = read_json(config_path(), {}) or {}
    stored[key] = value
    write_json(config_path(), stored)


def reset(key: str | None = None) -> None:
    stored = read_json(config_path(), {}) or {}
    if key is None:
        stored = {}
    else:
        stored.pop(key, None)
    write_json(config_path(), stored)


def _coerce(raw: str, default: Any) -> Any:
    if isinstance(default, bool):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(default, int):
        return int(raw)
    if isinstance(default, float):
        return float(raw)
    if isinstance(default, list):
        return [p.strip() for p in raw.split(",") if p.strip()]
    if isinstance(default, dict):
        out = {}
        for pair in raw.split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                out[k.strip()] = v.strip()
        return out
    return raw
