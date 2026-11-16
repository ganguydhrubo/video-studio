"""Visual looks, fonts, transitions and pacing presets shared by every engine."""

from __future__ import annotations

import os
from pathlib import Path

FONT_DIR = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"

LOOKS: dict[str, dict] = {
    "midnight": {"bg": ["#070B1F", "#18265A"], "blob": ["#3A5BFF", "#00C2FF", "#7B2FF7"], "ink": "#FFFFFF",
                 "muted": "#AFC3FF", "accent": "#4CC9F0", "accent2": "#F72585", "grade": "eq=contrast=1.04:saturation=1.08",
                 "world": (0.01, 0.015, 0.05), "metal": (0.30, 0.78, 0.94)},
    "studio": {"bg": ["#F5F2EC", "#E3DDD1"], "blob": ["#FFD6C9", "#FFE9B8", "#CFE3FF"], "ink": "#121212",
               "muted": "#5A5650", "accent": "#FF4D2E", "accent2": "#1F3BFF", "grade": "eq=contrast=1.02:saturation=1.02",
               "world": (0.78, 0.76, 0.72), "metal": (1.0, 0.30, 0.18)},
    "sunset": {"bg": ["#1E0B2E", "#7A1450"], "blob": ["#FF6B35", "#F7B32B", "#C2185B"], "ink": "#FFFFFF",
               "muted": "#FFD3C2", "accent": "#FFB703", "accent2": "#FF4D6D", "grade": "eq=contrast=1.05:saturation=1.12",
               "world": (0.06, 0.015, 0.04), "metal": (1.0, 0.68, 0.12)},
    "mono": {"bg": ["#080808", "#1C1C1C"], "blob": ["#3A3A3A", "#5C5C5C", "#262626"], "ink": "#FFFFFF",
             "muted": "#B5B5B5", "accent": "#FF3B30", "accent2": "#FFFFFF", "grade": "eq=contrast=1.08:saturation=0.9",
             "world": (0.01, 0.01, 0.01), "metal": (0.9, 0.9, 0.92)},
    "neon": {"bg": ["#04010D", "#120A36"], "blob": ["#00E5FF", "#39FF14", "#FF00E5"], "ink": "#FFFFFF",
             "muted": "#B9F6FF", "accent": "#39FF14", "accent2": "#00E5FF", "grade": "eq=contrast=1.06:saturation=1.15",
             "world": (0.005, 0.0, 0.02), "metal": (0.0, 0.9, 1.0)},
    "paper": {"bg": ["#F4EAD5", "#E7D5B0"], "blob": ["#F0C987", "#E8B4A0", "#D9C9A3"], "ink": "#2A2118",
              "muted": "#6B5B47", "accent": "#C0392B", "accent2": "#1E5A8A", "grade": "eq=contrast=1.03:saturation=0.98",
              "world": (0.80, 0.72, 0.58), "metal": (0.75, 0.22, 0.16)},
    "forest": {"bg": ["#07160F", "#1B4332"], "blob": ["#2D6A4F", "#95D5B2", "#40916C"], "ink": "#FFFFFF",
               "muted": "#CDEBD9", "accent": "#B7E36B", "accent2": "#FFD166", "grade": "eq=contrast=1.04:saturation=1.05",
               "world": (0.01, 0.03, 0.02), "metal": (0.72, 0.89, 0.42)},
}

# Fonts that ship with Windows; first existing file wins.
FONTS = {
    "display": ["bahnschrift.ttf", "segoeuib.ttf", "arialbd.ttf"],
    "heavy": ["seguibl.ttf", "segoeuib.ttf", "arialbd.ttf"],
    "body": ["segoeui.ttf", "arial.ttf"],
    "semibold": ["seguisb.ttf", "segoeuib.ttf", "arialbd.ttf"],
    "serif": ["georgiab.ttf", "georgia.ttf", "times.ttf"],
    "mono": ["consolab.ttf", "consola.ttf", "cour.ttf"],
}

# FFmpeg xfade names per transition family; picked in rotation.
TRANSITIONS = {
    "smooth": ["smoothup", "fade", "smoothleft", "circleopen"],
    "punchy": ["slideleft", "zoomin", "wipeup", "slideup"],
    "minimal": ["fade", "fade", "dissolve"],
    "cinematic": ["fadeblack", "fade", "radial", "hblur"],
}
TRANSITION_SECONDS = {"smooth": 0.45, "punchy": 0.3, "minimal": 0.5, "cinematic": 0.7}

PACING = {  # SAPI rate offset, Kokoro speed, scene padding seconds
    "relaxed": {"rate": -1, "speed": 0.95, "pad": 0.75},
    "balanced": {"rate": 0, "speed": 1.05, "pad": 0.5},
    "fast": {"rate": 2, "speed": 1.15, "pad": 0.3},
}

MUSIC_FOR_TONE = {
    "inspiring": "uplifting", "upbeat": "uplifting", "educational": "calm", "calm": "calm", "serious": "tense",
    "dramatic": "epic", "urgent": "tense", "funny": "playful", "playful": "playful", "epic": "epic", "neutral": "calm",
}

LOOK_FOR_TONE = {
    "inspiring": {"sunset": 0.8, "midnight": 0.7}, "educational": {"studio": 0.8, "paper": 0.6, "midnight": 0.6},
    "serious": {"mono": 0.8, "midnight": 0.7}, "dramatic": {"mono": 0.7, "midnight": 0.8}, "urgent": {"mono": 0.7, "neon": 0.6},
    "funny": {"studio": 0.7, "sunset": 0.6}, "playful": {"studio": 0.7, "neon": 0.6}, "upbeat": {"sunset": 0.7, "neon": 0.7},
    "calm": {"forest": 0.8, "paper": 0.7}, "epic": {"midnight": 0.8, "sunset": 0.6}, "neutral": {"midnight": 0.6, "studio": 0.6},
}


def font_file(role: str) -> str:
    for name in FONTS.get(role, FONTS["display"]):
        path = FONT_DIR / name
        if path.exists():
            return str(path)
    return ""


def hex_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def is_light(look: str) -> bool:
    r, g, b = hex_rgb(LOOKS[look]["bg"][0])
    return (0.299 * r + 0.587 * g + 0.114 * b) > 150
