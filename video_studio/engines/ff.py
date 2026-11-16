"""Thin, explicit FFmpeg/ffprobe wrappers."""

from __future__ import annotations

import json
import re
from pathlib import Path

from video_studio import detect, resources
from video_studio.util import ToolError, run

QUALITY = {
    # crf, x264 preset, intermediate crf, 3D render scale, Blender samples
    "draft": {"crf": 26, "preset": "veryfast", "icrf": 22, "scale": 0.5, "samples": 8},
    "standard": {"crf": 20, "preset": "medium", "icrf": 17, "scale": 0.75, "samples": 16},
    "high": {"crf": 17, "preset": "slow", "icrf": 14, "scale": 1.0, "samples": 32},
}


def ffmpeg() -> str:
    path = detect.ffmpeg_path()
    if not path:
        raise ToolError("FFmpeg not found.", "Run `video-studio install ffmpeg` or `video-studio doctor`.")
    return str(path)


def ffprobe() -> str:
    path = detect.ffprobe_path()
    if not path:
        raise ToolError("ffprobe not found.", "Run `video-studio install ffmpeg`.")
    return str(path)


def ff(args: list, *, cwd: Path | None = None, timeout: float | None = 7200, check: bool = True):
    cmd = [ffmpeg(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-threads", str(resources.render_threads()), *args]
    return run(cmd, cwd=cwd, timeout=timeout, check=check, low_priority=True, log_name="ffmpeg")


def probe(path: Path) -> dict:
    res = run([ffprobe(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)], timeout=120)
    return json.loads(res.stdout or "{}")


def duration(path: Path) -> float:
    info = probe(path)
    try:
        return float(info["format"]["duration"])
    except (KeyError, ValueError):
        durs = [float(s.get("duration", 0) or 0) for s in info.get("streams", [])]
        return max(durs or [0.0])


def stream(info: dict, kind: str) -> dict:
    return next((s for s in info.get("streams", []) if s.get("codec_type") == kind), {})


def fps_of(s: dict) -> float:
    num, _, den = (s.get("avg_frame_rate") or s.get("r_frame_rate") or "0/1").partition("/")
    try:
        return float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return 0.0


def x264_args(quality: str, intermediate: bool = False) -> list[str]:
    q = QUALITY[quality]
    crf = q["icrf"] if intermediate else q["crf"]
    preset = "ultrafast" if intermediate else q["preset"]
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p", "-profile:v", "high"]


def filter_escape(path: Path | str) -> str:
    """Escape a path for use inside an FFmpeg filter argument (Windows-safe)."""
    text = str(path).replace("\\", "/")
    return text.replace(":", r"\:").replace("'", r"\'").replace(",", r"\,").replace("[", r"\[").replace("]", r"\]")


def loudness_measure(path: Path, target: float) -> dict:
    res = ff(["-i", str(path), "-af", f"loudnorm=I={target}:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"],
             check=False)
    text = (res.stderr or "") + (res.stdout or "")
    match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", text, re.S)
    if not match:
        # loglevel error hides the report; rerun with info level
        cmd = [ffmpeg(), "-hide_banner", "-nostdin", "-i", str(path), "-af",
               f"loudnorm=I={target}:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"]
        res = run(cmd, check=False, timeout=1800)
        match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", res.stderr or "", re.S)
    if not match:
        raise ToolError("Could not measure loudness.")
    return json.loads(match.group(0))


def loudnorm_filter(measured: dict, target: float) -> str:
    return (f"loudnorm=I={target}:TP=-1.5:LRA=11:measured_I={measured['input_i']}:measured_TP={measured['input_tp']}:"
            f"measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}:"
            f"offset={measured['target_offset']}:linear=true")


def frame_at(video: Path, t: float, dest: Path, width: int = 0) -> Path:
    vf = ["-vf", f"scale={width}:-2"] if width else []
    ff(["-ss", f"{max(0.0, t):.3f}", "-i", str(video), "-frames:v", "1", *vf, "-q:v", "3", str(dest)])
    return dest
