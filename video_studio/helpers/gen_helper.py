"""Optional local AI video generation, run with the user's own Python (never imported by the app).

Uses Hugging Face diffusers with an open model (default Wan 2.2 TI2V-5B, Apache-2.0) on an NVIDIA card.
Writes a JSON result to --out. Missing libraries give {"ok": false, "missing": ...}; no CUDA gives a plain error.

  check     report torch / CUDA / diffusers / free VRAM, no download
  generate  text (and optional first picture) -> MP4
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def _write(out: str, data: dict) -> None:
    Path(out).write_text(json.dumps(data), encoding="utf-8")


def check(args) -> None:
    info = {"ok": True}
    try:
        import torch
        info["torch"] = torch.__version__
        info["cuda"] = bool(torch.cuda.is_available())
        if info["cuda"]:
            free, total = torch.cuda.mem_get_info()
            info.update(gpu=torch.cuda.get_device_name(0), vram_total_gb=round(total / 1024**3, 1), vram_free_gb=round(free / 1024**3, 1))
    except ImportError:
        info.update(ok=False, missing="torch")
    try:
        import diffusers
        info["diffusers"] = diffusers.__version__
    except ImportError:
        info.setdefault("missing", "diffusers")
        info["ok"] = False
    _write(args.out, info)


def generate(args) -> None:
    try:
        import torch
        from diffusers.utils import export_to_video, load_image
    except ImportError as exc:
        _write(args.out, {"ok": False, "missing": "torch diffusers transformers accelerate", "error": str(exc)})
        sys.exit(3)
    if not torch.cuda.is_available():
        _write(args.out, {"ok": False, "error": "No CUDA graphics card is available to PyTorch."})
        sys.exit(4)
    started = time.time()
    dtype = torch.bfloat16
    kwargs = {"torch_dtype": dtype}
    if not args.allow_download:
        kwargs["local_files_only"] = True
    try:
        if args.image:
            from diffusers import WanImageToVideoPipeline as Pipe
        else:
            from diffusers import WanPipeline as Pipe
        pipe = Pipe.from_pretrained(args.model, **kwargs)
    except Exception as exc:  # noqa: BLE001 - model missing locally, offline, or an older diffusers
        _write(args.out, {"ok": False, "error": f"Could not load {args.model}: {str(exc)[:400]}",
                          "needs_download": not args.allow_download})
        sys.exit(5)
    free, _ = torch.cuda.mem_get_info()
    if free / 1024**3 < args.full_gpu_gb:
        pipe.enable_model_cpu_offload()  # slower, fits smaller cards
        placement = "offload"
    else:
        pipe.to("cuda")
        placement = "gpu"
    call = {"prompt": args.prompt, "negative_prompt": args.negative, "height": args.height, "width": args.width,
            "num_frames": args.frames, "guidance_scale": args.guidance, "num_inference_steps": args.steps,
            "generator": torch.Generator(device="cpu").manual_seed(args.seed)}
    if args.image:
        call["image"] = load_image(args.image)
    frames = pipe(**call).frames[0]
    export_to_video(frames, args.dest, fps=args.fps)
    _write(args.out, {"ok": True, "output": args.dest, "frames": len(frames), "seconds": round(time.time() - started, 1),
                      "placement": placement, "model": args.model})


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    g = sub.add_parser("generate")
    g.add_argument("--prompt", required=True)
    g.add_argument("--negative", default="blurry, distorted, low quality, text, watermark")
    g.add_argument("--image", default="")
    g.add_argument("--dest", required=True)
    g.add_argument("--model", default="Wan-AI/Wan2.2-TI2V-5B-Diffusers")
    g.add_argument("--width", type=int, default=1280)
    g.add_argument("--height", type=int, default=704)
    g.add_argument("--frames", type=int, default=121)
    g.add_argument("--fps", type=int, default=24)
    g.add_argument("--steps", type=int, default=40)
    g.add_argument("--guidance", type=float, default=5.0)
    g.add_argument("--seed", type=int, default=7)
    g.add_argument("--full-gpu-gb", type=float, default=22.0)
    g.add_argument("--allow-download", type=int, default=0)
    for sp in (c, g):
        sp.add_argument("--out", required=True)
    args = p.parse_args()
    {"check": check, "generate": generate}[args.cmd](args)


if __name__ == "__main__":
    main()
