"""First-run setup: scan this computer, install everything in one approved step, explain the limits in plain English."""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import urllib.request
from pathlib import Path

from video_studio import detect, resources
from video_studio.engines import ml
from video_studio.util import IS_WINDOWS, console, confirm, run, run_interactive, write_json, app_data_dir

MODULES = ("faster_whisper", "kokoro_onnx", "soundfile", "noisereduce", "scipy", "cv2", "mediapipe", "librosa")
PIP_PACKAGES = ["faster-whisper", "kokoro-onnx", "soundfile", "noisereduce", "scipy", "opencv-python", "mediapipe", "librosa"]
MODELS = {
    "kokoro-v1.0.int8.onnx": ("https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.int8.onnx",
                              "kokoro", "88 MB", "natural voiceover (Apache-2.0)"),
    "voices-v1.0.bin": ("https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin",
                        "kokoro", "27 MB", "voices for the narrator"),
    "selfie_multiclass.tflite": ("https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_multiclass_256x256/float32/latest/selfie_multiclass_256x256.tflite",
                                 "mediapipe", "16 MB", "person cut-out for words behind you (Apache-2.0)"),
}


def _nvidia() -> dict | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"], capture_output=True,
                             text=True, timeout=15, creationflags=0x08000000 if IS_WINDOWS else 0).stdout.strip().splitlines()
        name, mem = out[0].rsplit(",", 1)
        return {"name": name.strip(), "vram_gb": round(int(mem) / 1024, 1)}
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def _cpu_name() -> str:
    if IS_WINDOWS:
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor).Name"],
                                 capture_output=True, text=True, timeout=20, creationflags=0x08000000).stdout.strip()
            if out:
                return out.splitlines()[0].strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return platform.processor() or "CPU"


def _modules(py, names) -> dict:
    """One interpreter start to check every helper module."""
    code = ("import importlib.util,json;print(json.dumps({m: importlib.util.find_spec(m) is not None for m in %r}))" % (list(names),))
    res = run([str(py), "-E", "-P", "-c", code], check=False, timeout=90)
    try:
        return json.loads(res.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {m: False for m in names}


def scan() -> dict:
    mem = resources.memory()
    tools = {t.key: t.to_dict() for t in detect.check_all(include_ai=False)}
    py = detect.system_python()
    pkgs = _modules(py, MODULES) if py else {}
    models = {name: (ml.models_dir() / sub / name).exists() for name, (_, sub, _, _) in MODELS.items()}
    try:
        free = shutil.disk_usage(Path.home()).free
    except OSError:
        free = 0
    return {
        "os": f"{platform.system()} {platform.release()}", "cpu": _cpu_name(), "threads": resources.cpu_count(),
        "ram_gb": round(mem.total / 1024**3, 1), "free_disk_gb": round(free / 1024**3, 1),
        "gpus": resources.gpus(), "nvidia": _nvidia(), "python": str(py or ""), "tools": tools, "packages": pkgs, "models": models,
    }


def _speed_factor(s: dict) -> float:
    """Rough speed relative to the reference laptop (i7-13620H, 16 threads): measured on real edits."""
    return max(0.25, min(2.5, s["threads"] / 16))


def report(s: dict) -> dict:
    """Plain-English capabilities and limits."""
    can, limits, tips = [], [], []
    k = _speed_factor(s)
    ram, nv = s["ram_gb"], s["nvidia"]
    ok = lambda key: s["tools"].get(key, {}).get("ok")  # noqa: E731
    if not ok("ffmpeg"):
        limits.append("Video editing is OFF until FFmpeg is installed (setup installs it for you).")
    else:
        # measured on the reference laptop (i7-13620H, 16 threads, 7 GB free): 10 s clip -> 154 s with full effects
        mins = 15 / k
        can.append(f"Edit your own clips into reels, Shorts and YouTube videos (up to 4K in, 1080p out). With full effects "
                   f"it needs about {mins:.0f} minutes for every 1 minute of footage (a 30-second reel: about {mins / 2:.0f} "
                   f"minutes); 'Clean' or 'Off' effects are much faster.")
        can.append("Remove mistakes, long pauses and filler words automatically, add captions (English, Hindi, Hinglish "
                   "in English letters and 90+ languages), music, sound effects and clean loud sound.")
    if s["packages"].get("mediapipe") and s["models"].get("selfie_multiclass.tflite"):
        can.append("Put big words BEHIND you, pop you out of a frame and add glow around you (works best when you are "
                   "clearly separated from the background).")
    else:
        limits.append("Words-behind-you effects are off until the cut-out model is installed (setup does it).")
    if s["packages"].get("faster_whisper"):
        model = "large-v3-turbo" if ram >= 12 else ("small" if ram >= 6 else "base")
        can.append(f"Understand speech offline with the '{model}' speech model (picked for your {ram:.0f} GB memory).")
    else:
        limits.append("Speech understanding is off until the speech model is installed (setup does it).")
    if s["models"].get("kokoro-v1.0.int8.onnx"):
        can.append("Turn a written script into a narrated video with a natural offline voice.")
    if ok("blender"):
        if nv and nv["vram_gb"] >= 6:
            can.append(f"Render 3D titles and objects fast on your {nv['name']}.")
        else:
            limits.append("3D scenes are SLOW on this computer (no strong graphics card): about 20-40 seconds per frame. "
                          "Use them for short 2-5 second moments, or pick 'Draft' quality.")
    if ram < 8:
        limits.append(f"Only {ram:.0f} GB memory: keep clips under 3 minutes and close other apps while rendering.")
    elif ram < 16:
        tips.append("Close heavy apps (games, many browser tabs) while a long video renders.")
    if s["free_disk_gb"] < 20:
        limits.append(f"Only {s['free_disk_gb']:.0f} GB free disk. Each project needs about 2-5 GB while working "
                      "(run 'video-studio clean' after exporting).")
    tips.append("Best results: phone on a stand, face lit from the front, quiet room, 1080p or 4K.")
    level = "Great" if (nv and nv["vram_gb"] >= 8 and ram >= 16) else ("Good" if ram >= 12 and s["threads"] >= 8 else "Basic")
    return {"level": level, "can": can, "limits": limits, "tips": tips}


def print_report(s: dict, r: dict) -> None:
    console.step("Your computer")
    gpu = s["nvidia"]["name"] if s["nvidia"] else (", ".join(s["gpus"]) or "built-in graphics")
    console.info(f"  {s['cpu']} | {s['threads']} threads | {s['ram_gb']:.0f} GB memory | {gpu} | {s['free_disk_gb']:.0f} GB free")
    console.step(f"Editing power: {r['level']}")
    for line in r["can"]:
        console.ok(line)
    for line in r["limits"]:
        console.warn(line)
    for line in r["tips"]:
        console.info("  Tip: " + line)


def missing(s: dict) -> list[dict]:
    items = []
    for key in ("ffmpeg",):
        if not s["tools"].get(key, {}).get("ok"):
            items.append({"kind": "tool", "key": key, "desc": detect.INSTALL_RECIPES[key]["desc"], "size": "~120 MB"})
    if not s["python"]:
        items.append({"kind": "tool", "key": "python", "desc": "Python 3.12 (python.org, via winget) for the AI helpers", "size": "~30 MB"})
    else:
        need = [p for p, mod in zip(PIP_PACKAGES, MODULES)
                if not s["packages"].get(mod)]
        if need:
            items.append({"kind": "pip", "key": "pip", "packages": need, "desc": "Offline AI helpers: " + ", ".join(need), "size": "~600 MB"})
    for name, (url, sub, size, desc) in MODELS.items():
        if not s["models"].get(name):
            items.append({"kind": "model", "key": name, "url": url, "dest": str(ml.models_dir() / sub / name), "desc": desc, "size": size})
    items.append({"kind": "whisper", "key": "whisper-model", "desc": "Speech model download (first use, cached)", "size": "~800 MB",
                  "model": "large-v3-turbo" if s["ram_gb"] >= 12 else "small", "optional_check": True})
    return items


def install_all(items: list[dict], assume_yes: bool) -> bool:
    if not items:
        return True
    console.step("To unlock everything, setup will install (free, open-source, about 1.5 GB in total):")
    for it in items:
        console.info(f"  - {it['desc']} ({it['size']})")
    if not confirm("Install all of this now?", assume_yes):
        console.info("Nothing was installed. Run `video-studio setup` any time.")
        return False
    py = detect.system_python()
    if any(i["key"] == "python" for i in items):
        run_interactive(["winget", "install", "--id", "Python.Python.3.12", "-e", "--source", "winget"])
        py = detect.system_python()
    for it in items:
        if it["kind"] == "tool":
            if it["key"] == "ffmpeg":
                detect.install("ffmpeg", True)
        elif it["kind"] == "pip" and py:
            console.step("Installing offline AI helpers (pip, your user account only)")
            run_interactive([str(py), "-m", "pip", "install", "--user", "--upgrade", *it["packages"]])
        elif it["kind"] == "model":
            dest = Path(it["dest"])
            dest.parent.mkdir(parents=True, exist_ok=True)
            console.step(f"Downloading {dest.name} ({it['size']})")
            try:
                tmp = dest.with_suffix(dest.suffix + ".part")
                urllib.request.urlretrieve(it["url"], tmp)
                tmp.replace(dest)
            except OSError as exc:
                console.warn(f"Download failed ({exc}); you can re-run setup later.")
        elif it["kind"] == "whisper" and py:
            if not ml.available("faster_whisper"):
                continue
            console.step(f"Fetching the speech model '{it['model']}' (one time)")
            run([str(py), "-E", "-P", "-c", f"from faster_whisper import WhisperModel; WhisperModel('{it['model']}', device='cpu', compute_type='int8')"],
                check=False, timeout=3600)
    return True


def run_setup(assume_yes: bool = False, report_only: bool = False) -> int:
    console.step("Scanning this computer")
    s = scan()
    if not report_only:
        todo = [i for i in missing(s) if not (i.get("optional_check") and ml.best_whisper_model("auto") in ("large-v3-turbo", "small"))]
        if todo:
            install_all(todo, assume_yes)
            s = scan()
    r = report(s)
    print_report(s, r)
    write_json(app_data_dir() / "system_report.json", {"scan": s, "report": r})
    return 0
