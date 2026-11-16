"""AI video generation from text or a picture, on PCs that can actually run it.

Local generation needs an NVIDIA card with enough memory (see machine.GEN_MIN_VRAM_GB). On every other PC this
says so plainly and suggests generating the shot with a cloud service and editing it here; it never pretends.
The open model (default Wan 2.2 TI2V-5B, Apache-2.0) is several GB and is downloaded only when the person
allows it. The model name can be changed with `video-studio config set generate.model <hugging-face id>`.

Status: the code path is tested here only up to the hardware gate; it has not yet been run on a real NVIDIA card.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from video_studio import config, detect, machine
from video_studio.util import ToolError, console, read_json, resource_path, run, write_json

HELPER = resource_path("helpers", "gen_helper.py")
DEFAULT_MODEL = "Wan-AI/Wan2.2-TI2V-5B-Diffusers"
SHAPES = {"16:9": (1280, 704), "9:16": (704, 1280), "1:1": (960, 960), "4:5": (832, 1024)}
LOW_VRAM_SHAPES = {"16:9": (832, 480), "9:16": (480, 832), "1:1": (640, 640), "4:5": (576, 704)}


def readiness() -> dict:
    """Can this PC generate locally? {"local": bool, "reason": str, "vram_gb": float}."""
    a = machine.assess()
    feat = {f["key"]: f for f in a["features"]}
    t2v = feat.get("gen_text_to_video", {})
    s = machine.scan()
    vram = max((g.get("vram_gb", 0) for g in s.get("gpus", []) if g.get("vendor") == "nvidia"), default=0.0)
    return {"local": t2v.get("status") == "ready", "status": t2v.get("status", "cloud_only"), "reason": t2v.get("why", ""), "vram_gb": vram}


def _call(cmd: str, timeout: float, **kwargs) -> dict:
    py = detect.system_python()
    if not py:
        raise ToolError("Python is needed for local AI generation.", "Install Python 3.11+ from python.org, then PyTorch with CUDA.")
    fd, name = tempfile.mkstemp(prefix=f"vs-gen-{cmd}-", suffix=".json")
    import os
    os.close(fd)
    out = Path(name)
    args = [str(py), "-E", "-P", str(HELPER), cmd, "--out", str(out)]
    for key, value in kwargs.items():
        args += [f"--{key.replace('_', '-')}", str(value)]
    res = run(args, timeout=timeout, check=False, low_priority=True)
    data = read_json(out, None) or {}
    out.unlink(missing_ok=True)
    if not data:
        raise ToolError("The generation helper stopped: " + (res.stderr or "")[-500:])
    return data


def run_generation(prompt: str, out_root: Path, *, image: str | None = None, aspect: str = "16:9", seconds: float = 5.0,
                   allow_download: bool = False, seed: int = 7) -> dict:
    if not prompt.strip():
        raise ToolError("Describe the shot you want, for example: a slow drone shot over tea gardens at sunrise.")
    ready = readiness()
    if not ready["local"]:
        raise ToolError("AI video generation cannot run on this PC. " + ready["reason"],
                        "Generate the shot with a cloud service (for example Veo, Kling, Runway or Luma), save it, "
                        "then edit or restyle it in Takebloom.")
    vram = ready["vram_gb"]
    w, h = (SHAPES if vram >= 16 else LOW_VRAM_SHAPES).get(aspect, SHAPES["16:9"])
    fps = 24
    frames = int(min(seconds, 5.0 if vram < 24 else 8.0) * fps) // 4 * 4 + 1  # Wan wants 4k+1 frames
    out_root = Path(out_root)
    (out_root / "output").mkdir(parents=True, exist_ok=True)
    dest = out_root / "output" / "generated.mp4"
    model = str(config.get("generate.model") or DEFAULT_MODEL)
    console.step(f"Generating {frames / fps:.1f}s at {w}x{h} with {model} on your graphics card")
    if allow_download:
        console.info("  The model may download first (several GB, saved for next time).")
    data = _call("generate", 6 * 3600, prompt=prompt, image=image or "", dest=str(dest), model=model, width=w, height=h,
                 frames=frames, fps=fps, seed=seed, allow_download=int(allow_download))
    if not data.get("ok"):
        if data.get("needs_download"):
            raise ToolError("The AI video model is not on this PC yet.",
                            "Run again with --allow-download (several GB, downloaded once from Hugging Face).")
        if data.get("missing"):
            raise ToolError(f"Missing Python packages for generation: {data['missing']}",
                            "py -m pip install --user torch --index-url https://download.pytorch.org/whl/cu128 ; "
                            "py -m pip install --user diffusers transformers accelerate")
        raise ToolError("Generation failed: " + str(data.get("error", ""))[:300])
    result = {"output": data["output"], "seconds": round(frames / fps, 2), "size": [w, h], "model": model,
              "took_seconds": data.get("seconds"), "placement": data.get("placement"), "prompt": prompt, "image": image or ""}
    write_json(out_root / "build" / "generate_manifest.json", result)
    console.ok(f"Generated clip ready: {dest}")
    return result


def describe() -> str:
    r = readiness()
    return json.dumps(r)
