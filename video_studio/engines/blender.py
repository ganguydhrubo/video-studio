"""Blender engine driver: spec -> frames (resumable) -> encoded clip."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

from video_studio import detect, memory, resources
from video_studio.engines import ff
from video_studio.looks import LOOKS, font_file
from video_studio.util import ToolError, console, kill_tree, popen, release, resource_path

BUILDER = resource_path("blender", "scene_builder.py")


def blender() -> str:
    path = detect.blender_path()
    if not path:
        raise ToolError("Blender not found.", "video-studio install blender  (3D scenes fall back to 2D without it)")
    return str(path)


def _spec(visual: dict, w: int, h: int, fps: int, duration: float, look: str, camera: str, quality: str,
          frames_dir: Path, style: str, engine: str) -> dict:
    q = ff.QUALITY[quality]
    scale = q["scale"]
    rw, rh = int(w * scale) // 2 * 2, int(h * scale) // 2 * 2
    return {"visual": visual, "width": rw, "height": rh, "fps": fps, "duration": duration,
            "look": LOOKS[look], "camera": camera, "samples": q["samples"], "frames_dir": str(frames_dir),
            "font": font_file("heavy"), "style": style, "engine": engine, "threads": resources.render_threads()}


def render_clip(visual: dict, dest: Path, w: int, h: int, fps: int, duration: float, look: str, camera: str,
                quality: str, work: Path, style: str = "cinematic") -> Path:
    fixes = memory.learned_fixes()
    engine = "cycles" if fixes.get("blender_gpu") == "blender_engine=cycles" else "eevee"
    resources.ensure_memory(1.2, "3D rendering")
    try:
        return _render(visual, dest, w, h, fps, duration, look, camera, quality, work, style, engine)
    except ToolError as exc:
        if engine == "eevee":
            console.warn(f"EEVEE failed ({str(exc).splitlines()[0][:120]}); retrying with Cycles on the CPU.")
            sig, _ = memory.record_failure(work.parent.name, "blender", "EEVEE gpu " + str(exc))
            shutil.rmtree(work / "frames", ignore_errors=True)
            out = _render(visual, dest, w, h, fps, duration, look, camera, quality, work, style, "cycles")
            memory.mark_fixed("blender_gpu")
            return out
        raise


def _render(visual, dest, w, h, fps, duration, look, camera, quality, work: Path, style, engine) -> Path:
    frames_dir = work / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    spec = _spec(visual, w, h, fps, duration, look, camera, quality, frames_dir, style, engine)
    spec_path = work / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    total = max(2, int(round(duration * fps)))
    resources.ensure_disk(work, total * 3_000_000, "3D frames")
    log = work / "blender.log"
    cmd = [blender(), "--background", "--factory-startup", "--python-exit-code", "3",
           "--python", str(BUILDER), "--", str(spec_path)]
    started = time.time()
    with log.open("w", encoding="utf-8", errors="replace") as fh:
        proc = popen(cmd, stdout=fh, stderr=subprocess.STDOUT)
        try:
            while proc.poll() is None:
                done = sum(1 for p in frames_dir.glob("f_*.png") if p.stat().st_size > 0)
                console.progress(f"3D {visual['type']} ({engine})", min(done, total), total)
                time.sleep(1.0)
        except BaseException:
            kill_tree(proc)
            raise
        finally:
            release(proc)
    text = log.read_text(encoding="utf-8", errors="replace")
    frames = sorted(p for p in frames_dir.glob("f_*.png") if p.stat().st_size > 0)
    if proc.returncode != 0 or "VS_RENDER_DONE" not in text or len(frames) < total:
        tail = "\n".join(line for line in text.splitlines()[-25:] if line.strip())
        raise ToolError(f"Blender render failed (exit {proc.returncode}, {len(frames)}/{total} frames):\n{tail[-1500:]}",
                        f"See {log}. Try `video-studio config set defaults.use_blender false` to skip 3D.")
    console.progress(f"3D {visual['type']} ({engine})", total, total)
    console.debug(f"blender {visual['type']} {total} frames in {time.time() - started:.0f}s")
    tmp = dest.with_suffix(".part.mp4")
    ff.ff(["-framerate", str(fps), "-start_number", "1", "-i", str(frames_dir / "f_%04d.png"),
           "-vf", f"scale={w}:{h}:flags=lanczos,format=yuv420p", *ff.x264_args(quality, intermediate=True),
           "-r", str(fps), str(tmp)])
    tmp.replace(dest)
    shutil.rmtree(frames_dir, ignore_errors=True)  # frames are only a resumable cache
    return dest


def preview_frame(visual: dict, dest: Path, w: int, h: int, look: str, camera: str, work: Path, style: str = "cinematic") -> Path:
    """Single still for the storyboard (fast, low samples)."""
    work.mkdir(parents=True, exist_ok=True)
    spec = _spec(visual, w, h, 24, 2.0, look, camera, "draft", work, style, "eevee")
    spec.update({"preview_frame": 40, "preview_path": str(dest), "samples": 8})
    spec_path = work / "preview.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    from video_studio.util import run
    res = run([blender(), "--background", "--factory-startup", "--python-exit-code", "3", "--python", str(BUILDER), "--",
               str(spec_path)], timeout=600, check=False, low_priority=True)
    if not dest.exists():
        raise ToolError("Blender preview failed: " + (res.stdout or "")[-600:])
    return dest
