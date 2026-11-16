"""Video sense: what is in the footage (shots, quality, motion, people, music beats) -> footage type + edit recipe."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from video_studio import detect
from video_studio.engines import ff, style
from video_studio.util import ToolError, console, resource_path

TYPE_WORDS = {"talking_head": "a talking-to-camera video", "vlog": "a vlog (you talking while moving around)",
              "action": "fast action footage", "people_lifestyle": "people / lifestyle footage",
              "scenic_or_product": "scenic or product shots", "b_roll": "general b-roll footage", "unknown": "footage"}
RECIPES = {"talking_head": "talking-head reel: cut mistakes, angle changes, words behind you",
           "vlog": "vlog: tight talk cuts with scene inserts", "action": "velocity beat edit: cuts and hits on the beat",
           "people_lifestyle": "lifestyle montage on the beat", "scenic_or_product": "cinematic montage: slow pushes, cuts on the bar",
           "b_roll": "beat-synced montage of the best shots", "unknown": "beat-synced montage"}


def analyse(src: Path, dest: Path, speech_ratio: float | None = None) -> dict:
    if dest.exists():
        return json.loads(dest.read_text(encoding="utf-8"))
    py = detect.system_python()
    if not py:
        raise ToolError("Video sense needs Python 3.10+ with OpenCV (video-studio setup).")
    model = style.matte_model()
    helper = resource_path("helpers", "sense_helper.py")
    res = subprocess.run([str(py), "-E", "-P", str(helper), str(src), str(dest), str(model or ""), str(ff.ffmpeg()),
                          "" if speech_ratio is None else f"{speech_ratio:.3f}"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600,
                         creationflags=0x08000000 | 0x00004000)
    if res.returncode != 0 or not dest.exists():
        raise ToolError("Video analysis failed: " + (res.stderr or res.stdout)[-400:])
    return json.loads(dest.read_text(encoding="utf-8"))


def describe(sense: dict) -> str:
    sig = sense.get("signals", {})
    aud = sense.get("audio", {})
    bits = [f"{TYPE_WORDS.get(sense.get('type'), 'footage')}", f"{sig.get('shots', 0)} shot(s)"]
    if aud.get("tempo") and aud.get("speechiness", 1) < 0.25:
        bits.append(f"music at ~{aud['tempo']:.0f} BPM")
    if sig.get("person_area", 0) > 0.05:
        bits.append("a person on screen")
    return ", ".join(bits)


def report(sense: dict) -> None:
    console.ok("Understood: " + describe(sense))
    console.info(f"    style recipe: {RECIPES.get(sense.get('type'), RECIPES['unknown'])}")
    for issue in sense.get("issues", []):
        console.warn(f"    footage note: {issue} (auto-fix applied where possible)")
