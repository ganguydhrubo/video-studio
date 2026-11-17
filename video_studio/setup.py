"""First-run setup: scan this computer, install everything in one approved step, explain the limits in plain English."""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import time
from pathlib import Path

from video_studio import detect, resources, toolbox
from video_studio.engines import ml
from video_studio.util import IS_WINDOWS, console, confirm, run, write_json, app_data_dir

MODULES = ("faster_whisper", "kokoro_onnx", "soundfile", "noisereduce", "scipy", "cv2", "mediapipe", "librosa")
PIP_PACKAGES = ["faster-whisper", "kokoro-onnx", "soundfile", "noisereduce", "scipy", "opencv-python", "mediapipe", "librosa"]
MODELS = {
    "kokoro-v1.0.int8.onnx": ("https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.int8.onnx",
                              "kokoro", "92 MB", "Kokoro voice model (offline English voice-overs)"),
    "voices-v1.0.bin": ("https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin",
                        "kokoro", "28 MB", "Kokoro voices"),
    "selfie_multiclass.tflite": ("https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_multiclass_256x256/float32/latest/selfie_multiclass_256x256.tflite",
                                 "mediapipe", "16 MB", "MediaPipe cut-out model (words behind you)"),
}
# Exact sizes of the fixed-release files (from the GitHub release, 17 Nov 2026): a short or cut download is refused.
MODEL_BYTES = {"kokoro-v1.0.int8.onnx": 92_361_271, "voices-v1.0.bin": 28_214_398}
# Pinned SHA-256 for fixed-release model files (checked on download). The selfie model URL is 'latest', so not pinned.
MODEL_SHA: dict[str, str] = {}


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
    speech = ml.best_whisper_model("auto") if whisper_cached() else ""
    try:
        free = shutil.disk_usage(Path.home()).free
    except OSError:
        free = 0
    return {
        "os": f"{platform.system()} {platform.release()}", "cpu": _cpu_name(), "threads": resources.cpu_count(),
        "ram_gb": round(mem.total / 1024**3, 1), "free_disk_gb": round(free / 1024**3, 1),
        "gpus": resources.gpus(), "nvidia": _nvidia(), "python": str(py or ""), "tools": tools, "packages": pkgs, "models": models,
        "speech_model": speech,
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
    speech = s.get("speech_model", "")
    if s["packages"].get("faster_whisper") and speech:
        can.append(f"Understand speech offline with the '{speech}' speech model.")
        if ram >= 12 and "large" not in speech:
            tips.append(f"For sharper Hindi and Bengali captions, add the bigger speech model "
                        f"({_mb(WHISPER_SIZES['large-v3-turbo'])}): video-studio setup --with speech-large")
    else:
        limits.append("Speech understanding is off until the speech model is installed (setup does it).")
    if s["models"].get("kokoro-v1.0.int8.onnx"):
        can.append("Make an English voice-over version of a finished edit with a natural offline voice.")
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


# Sizes are what each step downloads (measured on the 1.9.0 fresh-folder test); licences are shown before anything runs.
PYTHON_SIZE = 650_000_000      # uv + Python 3.12 + the helpers above
HYPERFRAMES_SIZE = 260_000_000  # npm package + its headless browser
# faster-whisper model files on Hugging Face (model.bin + tokenizer + vocabulary), checked 17 Nov 2026
WHISPER_SIZES = {"small": 486_215_847, "large-v3-turbo": 1_620_000_000}
WHISPER_SIZE = WHISPER_SIZES["small"]
# Optional items: never in the default plan (Blender is only for 3D, Ollama and the big speech model are large).
EXTRAS = ("blender", "ollama", "speech-large")


def _mb(n: int) -> str:
    return toolbox.fmt_mb(n)


def _model_size(text: str) -> int:
    try:
        return int(float(text.split()[0]) * 1_000_000)
    except (ValueError, IndexError):
        return 0


def plan(s: dict, skip: tuple[str, ...] = (), extra: tuple[str, ...] = ()) -> list[dict]:
    """Everything this PC still needs, in install order. Each item: what, size (bytes), licence, how."""
    items: list[dict] = []
    tools = s.get("tools", {})
    ok = lambda key: tools.get(key, {}).get("ok")  # noqa: E731
    P = toolbox.PACKAGES

    def portable(key: str, desc: str) -> None:
        items.append({"kind": "portable", "key": key, "desc": desc, "size": P[key].size, "license": P[key].licence})

    if not ok("ffmpeg"):
        portable("ffmpeg", f"{P['ffmpeg'].label}: cutting, sound, captions, every export")
    private = toolbox.private_python()
    need = [p for p, mod in zip(PIP_PACKAGES, MODULES) if not (s.get("packages") or {}).get(mod)]
    if not private and (not s.get("python") or need):
        items.append({"kind": "python", "key": "python", "packages": list(PIP_PACKAGES), "size": PYTHON_SIZE,
                      "license": "PSF, MIT, Apache-2.0, BSD, ISC",
                      "desc": f"Private Python {toolbox.PY_VERSION} with the offline AI helpers (speech, voice, faces, beats)"})
    elif need:
        items.append({"kind": "pip", "key": "pip", "packages": need, "size": 60_000_000 * len(need),
                      "license": "MIT, Apache-2.0, BSD, ISC", "desc": "Offline AI helpers: " + ", ".join(need)})
    for name, (url, sub, size, desc) in MODELS.items():
        if not (s.get("models") or {}).get(name):
            items.append({"kind": "model", "key": name, "url": url, "dest": str(ml.models_dir() / sub / name),
                          "desc": desc, "size": MODEL_BYTES.get(name) or _model_size(size), "license": "Apache-2.0"})
    if not ok("node"):
        portable("node", f"{P['node'].label}: runs HyperFrames")
    if not ok("hyperframes"):
        items.append({"kind": "hyperframes", "key": "hyperframes", "size": HYPERFRAMES_SIZE, "license": "Apache-2.0",
                      "desc": f"HyperFrames {detect.HYPERFRAMES_VERSION} and its browser: timeline, motion titles"})
    if "blender" in extra and not ok("blender"):
        portable("blender", f"{P['blender'].label}: 3D titles and objects (optional)")
    large = "speech-large" in extra
    if not whisper_cached(large):
        model = "large-v3-turbo" if large else "small"
        items.append({"kind": "whisper", "key": "whisper-model", "size": WHISPER_SIZES[model], "license": "MIT",
                      "desc": f"Speech model '{model}' (understands what you say, offline)", "model": model})
    if "ollama" in extra and not (toolbox.ollama() or shutil.which("ollama")):
        items.append({"kind": "portable", "key": "ollama", "size": P["ollama"].size + 1_400_000_000,
                      "license": "MIT, Apache-2.0",
                      "desc": f"{P['ollama'].label} + the free {toolbox.OLLAMA_MODEL} model: AI editor with no internet"})
    return [i for i in items if i["key"] not in skip]


def whisper_cached(large: bool = False) -> bool:
    """A usable speech model is already on disk: 'small' or better (or a large one, when `large` is asked for)."""
    hub = ml.hf_hub()
    names = ["models--mobiuslabsgmbh--faster-whisper-large-v3-turbo", "models--Systran--faster-whisper-large-v3"]
    if not large:
        names += ["models--Systran--faster-whisper-medium", "models--Systran--faster-whisper-small"]
    return any(ml.model_on_disk(hub / f) for f in names)


def missing(s: dict) -> list[dict]:
    """Kept for older callers: the default plan."""
    return plan(s)


def plan_summary(items: list[dict]) -> dict:
    return {"items": [{k: it.get(k) for k in ("key", "desc", "size", "license")} for it in items],
            "total": sum(it.get("size", 0) for it in items), "total_text": _mb(sum(it.get("size", 0) for it in items)),
            "folder": str(toolbox.tools_dir())}


def print_plan(items: list[dict]) -> None:
    total = sum(i.get("size", 0) for i in items)
    console.step(f"Setup will download and install (free, about {_mb(total)} in total, no admin needed):")
    for n, it in enumerate(items, 1):
        console.info(f"  {n}. {it['desc']} - {_mb(it.get('size', 0))}, licence {it.get('license', '-')}")
    console.info(f"  Tools go into {toolbox.tools_dir()} (Takebloom's own folder). "
                 "Your system settings and PATH are not changed.")


def install_item(it: dict) -> bool:
    """Install one plan item. Never raises: a failure is explained and the rest of the plan carries on."""
    kind, key = it["kind"], it["key"]
    try:
        if kind == "portable":
            if key == "ollama":
                return toolbox.install_ollama()
            if toolbox.install_with_fallback(key):
                return True
            return bool({"ffmpeg": detect.ffmpeg_path, "blender": detect.blender_path, "node": detect.node_path}[key]())
        if kind in ("python", "whisper") and key in ("python", "whisper"):
            pkgs = it.get("packages") or (PIP_PACKAGES if key == "python" else ["faster-whisper"])
            mods = MODULES if key == "python" else ("faster_whisper",)
            return toolbox.install_python(pkgs, mods) is not None
        if kind == "pip":
            py = detect.system_python()
            if not py:
                return toolbox.install_python(PIP_PACKAGES, MODULES) is not None
            console.step("Installing offline AI helpers: " + ", ".join(it["packages"]))
            return toolbox.pip_install(py, it["packages"]) == 0
        if kind == "hyperframes":
            return toolbox.install_hyperframes(detect.HYPERFRAMES_VERSION)
        if kind == "model":
            dest = Path(it["dest"])
            console.step(f"Downloading {dest.name} ({_mb(it.get('size', 0))})")
            toolbox.download([it["url"]], dest, MODEL_SHA.get(dest.name, ""), MODEL_BYTES.get(dest.name, 0), label=dest.name)
            return True
        if kind == "whisper":
            py = detect.system_python()
            if not py or not ml.available("faster_whisper"):
                console.warn("The speech model needs the AI helpers first; it was skipped.")
                return False
            console.step(f"Fetching the speech model '{it['model']}' (one time)")
            code = toolbox.stream([py, "-E", "-P", "-c", "from faster_whisper import WhisperModel; "
                                   f"WhisperModel('{it['model']}', device='cpu', compute_type='int8'); "
                                   "print('speech model ready')"], timeout=3600)
            return code == 0
    except toolbox.ToolboxError as exc:
        console.error(str(exc))
        if exc.hint:
            console.info("  " + exc.hint)
        return False
    except Exception as exc:  # noqa: BLE001 - one broken step must not stop the rest of setup
        console.error(f"{it.get('desc', key)} failed: {exc}")
        return False
    return False


def verify(s: dict) -> list[tuple[str, bool, str]]:
    """What works now and where it came from (the scan ran each tool once)."""
    rows = []
    for key, name in (("ffmpeg", "FFmpeg"), ("node", "Node.js"), ("hyperframes", "HyperFrames"), ("blender", "Blender")):
        t = s["tools"].get(key, {})
        src = (t.get("extra") or {}).get("source", "")
        where = t.get("path") if t.get("found") else t.get("problem")
        rows.append((name, bool(t.get("ok")), f"{t.get('version') or '-'} ({src or 'not found'}) {where or ''}".strip()))
    py = s.get("python", "")
    pk = s.get("packages") or {}
    good = [m for m, v in pk.items() if v]
    rows.append(("Python helpers", bool(pk.get("faster_whisper")),
                 f"{len(good)} of {len(pk)} load ({'portable' if toolbox.is_portable(py) else 'system'}) {py}"))
    return rows


def install_all(items: list[dict], assume_yes: bool) -> bool:
    if not items:
        return True
    print_plan(items)
    if not confirm("Install all of this now?", assume_yes):
        console.info("Nothing was installed. Run `video-studio setup` any time.")
        return False
    started, failed = time.time(), []
    for n, it in enumerate(items, 1):
        console.step(f"Step {n} of {len(items)}: {it['desc']}")
        t0 = time.time()
        if install_item(it):
            console.ok(f"Step {n} done in {time.time() - t0:.0f} s")
        else:
            failed.append(it["desc"])
    console.info(f"Setup steps finished in {(time.time() - started) / 60:.1f} minutes.")
    for d in failed:
        console.warn(f"Not finished: {d}. Press Setup again to retry (finished parts are kept).")
    return not failed


def print_tools(s: dict) -> None:
    console.step("Tools and where they came from")
    for name, good, text in verify(s):
        (console.ok if good else console.warn)(f"{name}: {text}")


def run_setup(assume_yes: bool = False, report_only: bool = False, skip: tuple[str, ...] = (),
              extra: tuple[str, ...] = ()) -> int:
    console.step("Scanning this computer")
    s = scan()
    ok = True
    if not report_only:
        todo = plan(s, tuple(skip), tuple(extra))
        if todo:
            ok = install_all(todo, assume_yes)
            toolbox.activate(force=True)
            console.step("Checking every tool again (no restart needed)")
            s = scan()
        else:
            console.ok("Everything is already installed.")
    r = report(s)
    print_report(s, r)
    print_tools(s)
    write_json(app_data_dir() / "system_report.json", {"scan": s, "report": r})
    return 0 if ok else 2
