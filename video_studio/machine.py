"""What THIS computer can make, decided from its real hardware before any job starts.

Takebloom runs on any Windows PC, from an 8 GB laptop with built-in graphics to a workstation with a large
NVIDIA card. This module looks at the machine it is running on and answers, in plain words:

* what it can make here, and what needs a better graphics card or a cloud service;
* the largest practical resolution and frame rate, and a sensible length for one job;
* which local AI models are present;
* how fast a render is likely to be (measured on this PC once it has finished an edit, otherwise an estimate
  that says it is an estimate);
* which hardware video encoders really work (each is tested with a one-frame encode, not just listed).

`check()` is called before a job starts and returns warnings or a refusal with advice; it never promises what
the hardware cannot do. Nothing here is sent anywhere.
"""
from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import time
from pathlib import Path

from video_studio import resources
from video_studio.util import IS_WINDOWS, app_data_dir, read_json, write_json

SCAN_FILE = "machine.json"
SCAN_MAX_AGE = 24 * 3600
_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0

# Rough render cost on CPU, seconds of work per second of finished video, used only until this PC has measured
# its own speed. Labelled as estimates wherever they are shown.
TIER_SPEED = {"basic": 32.0, "standard": 22.0, "plus": 16.0, "gpu": 9.0, "workstation": 5.0}
TIER_LABEL = {
    "basic": "Basic laptop (under 8 GB memory or 4 processor threads)",
    "standard": "Standard PC (8 to 15 GB memory)",
    "plus": "Capable PC (16 GB memory or more, no strong graphics card)",
    "gpu": "Graphics-card PC (dedicated graphics card with 6 GB or more)",
    "workstation": "Workstation (NVIDIA card with 16 GB or more and 32 GB memory)",
}
LIMITS = {  # height, fps, recommended minutes of footage in one job (longer still works, it is just slower)
    "basic": {"height": 720, "fps": 30, "minutes": 3, "quality": "draft", "whisper": "base"},
    "standard": {"height": 1080, "fps": 30, "minutes": 10, "quality": "standard", "whisper": "small"},
    "plus": {"height": 1080, "fps": 60, "minutes": 20, "quality": "standard", "whisper": "small"},
    "gpu": {"height": 2160, "fps": 60, "minutes": 60, "quality": "high", "whisper": "large-v3-turbo"},
    "workstation": {"height": 2160, "fps": 60, "minutes": 120, "quality": "high", "whisper": "large-v3-turbo"},
}

# ------------------------------------------------------------------ scanning

def _ps_json(script: str, timeout: float = 25) -> object:
    if not IS_WINDOWS:
        return None
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True,
                             timeout=timeout, creationflags=_NO_WINDOW).stdout.strip()
        return json.loads(out) if out else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _as_list(value) -> list:
    return value if isinstance(value, list) else ([value] if value else [])


def _gpu_kind(name: str) -> tuple[str, bool]:
    low = name.lower()
    vendor = "nvidia" if any(k in low for k in ("nvidia", "geforce", "rtx", "quadro", "tesla")) else \
        "amd" if any(k in low for k in ("amd", "radeon")) else "intel" if "intel" in low else "other"
    discrete = vendor == "nvidia" or (vendor == "amd" and re.search(r"\b(rx|pro w|firepro|vega)\b", low) is not None) or \
        (vendor == "intel" and re.search(r"\barc\b", low) is not None and "graphics" not in low.replace("arc graphics", "arc"))
    if vendor == "intel" and re.search(r"\barc\(tm\)\s*graphics\b|\barc graphics\b", low):
        discrete = False  # "Intel Arc Graphics" in Core Ultra laptops is built in
    if any(k in low for k in ("microsoft basic", "remote", "virtual", "parsec", "displaylink")):
        vendor, discrete = "other", False
    return vendor, discrete


def scan_gpus() -> list[dict]:
    """Graphics adapters with real memory. Win32 AdapterRAM stops at 4 GB, so the driver's registry value is used,
    and nvidia-smi when it exists (exact, and proves the NVIDIA driver works)."""
    gpus: list[dict] = []
    smi = None
    for candidate in ("nvidia-smi", r"C:\Windows\System32\nvidia-smi.exe"):
        try:
            res = subprocess.run([candidate, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=15, creationflags=_NO_WINDOW)
            if res.returncode == 0 and res.stdout.strip():
                smi = res.stdout.strip()
                break
        except (OSError, subprocess.SubprocessError):
            continue
    if smi:
        for line in smi.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2:
                gpus.append({"name": parts[0], "vendor": "nvidia", "discrete": True, "vram_gb": round(float(parts[1]) / 1024, 1),
                             "driver": parts[2] if len(parts) > 2 else "", "source": "nvidia-smi"})
    script = (r"$c='HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}';"
              r"$r=@(Get-ChildItem $c -ErrorAction SilentlyContinue | % { $p=Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue;"
              r" if($p.DriverDesc){[pscustomobject]@{name=$p.DriverDesc;mem=$p.'HardwareInformation.qwMemorySize';driver=$p.DriverVersion}} });"
              r"$w=@(Get-CimInstance Win32_VideoController | % {[pscustomobject]@{name=$_.Name;mem=$_.AdapterRAM;driver=$_.DriverVersion}});"
              r"[pscustomobject]@{reg=$r;wmi=$w} | ConvertTo-Json -Depth 3 -Compress")
    data = _ps_json(script) or {}
    reg = {str(r.get("name")): r for r in _as_list(data.get("reg")) if isinstance(r, dict)}
    for w in _as_list(data.get("wmi")):
        if not isinstance(w, dict) or not w.get("name"):
            continue
        name = str(w["name"])
        if any(g["name"].lower() == name.lower() for g in gpus):
            continue
        vendor, discrete = _gpu_kind(name)
        mem = reg.get(name, {}).get("mem") or w.get("mem") or 0
        try:
            mem = float(mem[0] if isinstance(mem, list) else mem)
        except (TypeError, ValueError):
            mem = 0.0
        gpus.append({"name": name, "vendor": vendor, "discrete": discrete,
                     "vram_gb": round(mem / 1024**3, 1) if discrete else 0.0,
                     "shared_memory": not discrete, "driver": str(w.get("driver") or ""), "source": "windows"})
    return gpus


def scan_cpu() -> dict:
    data = _ps_json("Get-CimInstance Win32_Processor | Select-Object -First 1 Name,NumberOfCores,NumberOfLogicalProcessors "
                    "| ConvertTo-Json -Compress") or {}
    return {"name": str(data.get("Name") or platform.processor() or "Unknown processor").strip(),
            "cores": int(data.get("NumberOfCores") or max(1, resources.cpu_count() // 2)),
            "threads": int(data.get("NumberOfLogicalProcessors") or resources.cpu_count())}


def test_encoders(ffmpeg: str | None = None) -> dict:
    """Hardware H.264 encoders that really encode a frame on this PC (a listed encoder may still fail)."""
    try:
        from video_studio.engines import ff
        ffmpeg = ffmpeg or ff.ffmpeg()
    except Exception:  # noqa: BLE001 - FFmpeg missing is reported by doctor; here it just means none work
        return {"tested": False, "working": [], "error": "FFmpeg was not found."}
    try:
        listed = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=30,
                                creationflags=_NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        return {"tested": False, "working": [], "error": str(exc)[:120]}
    working = []
    for enc in ("h264_nvenc", "h264_qsv", "h264_amf"):
        if enc not in listed:
            continue
        try:
            res = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=black:s=320x240:d=0.2",
                                  "-c:v", enc, "-frames:v", "3", "-f", "null", "-"], capture_output=True, text=True, timeout=30,
                                 creationflags=_NO_WINDOW)
            if res.returncode == 0:
                working.append(enc)
        except (OSError, subprocess.SubprocessError):
            pass
    return {"tested": True, "working": working, "ffmpeg": str(ffmpeg)}


def _local_models() -> dict:
    report = read_json(app_data_dir() / "system_report.json", {}) or {}
    scan = report.get("scan") or {}
    models = dict(scan.get("models") or {})
    hub = Path(os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface") / "hub"
    whisper = sorted(p.name.split("faster-whisper-")[-1] for p in hub.glob("models--*faster-whisper-*")) if hub.exists() else []
    mdir = app_data_dir() / "models"
    return {"speech": whisper, "voices": sorted(p.name for p in (mdir.iterdir() if mdir.exists() else []) if p.is_dir()),
            "segmentation": (mdir / "mediapipe" / "selfie_multiclass.tflite").exists(),
            "reported": models, "tools": {k: bool((v or {}).get("ok")) for k, v in (scan.get("tools") or {}).items()},
            "packages": scan.get("packages") or {}}


def scan(fresh: bool = False) -> dict:
    """Hardware facts, cached for a day (memory and disk are always read fresh)."""
    path = app_data_dir() / SCAN_FILE
    cached = read_json(path, None)
    if fresh or not cached or time.time() - cached.get("at", 0) > SCAN_MAX_AGE:
        gpus = scan_gpus()
        cached = {"at": time.time(), "os": f"{platform.system()} {platform.release()} ({platform.version()})",
                  "arch": platform.machine(), "cpu": scan_cpu(), "gpus": gpus, "encoders": test_encoders(),
                  "cuda": {"cuda": False, "skipped": "not needed: Takebloom edits your own footage"}}
        write_json(path, cached)
    mem = resources.memory()
    cached = {**cached, "ram_total_gb": round(mem.total / 1024**3, 1), "ram_free_gb": round(mem.available_gb, 1),
              "disk_free_gb": round(resources.disk_free(workspace_dir()) / 1024**3, 1),
              "models": _local_models(), "speed": measured_speed()}
    return cached


def workspace_dir() -> Path:
    from video_studio import config
    return Path(config.get("workspace") or (Path.home() / "Videos" / "Video Studio"))


def measured_speed() -> dict:
    hist = (read_json(app_data_dir() / "speed.json", {}) or {}).get("history") or []
    ratios = sorted(h["seconds"] / h["footage"] for h in hist if h.get("footage", 0) > 3 and h.get("seconds", 0) > 0)
    if not ratios:
        return {"measured": False}
    return {"measured": True, "edits": len(ratios), "seconds_per_second": round(ratios[len(ratios) // 2], 1)}


# ------------------------------------------------------------------ deciding

def tier(s: dict) -> str:
    ram, threads = s.get("ram_total_gb", 0), (s.get("cpu") or {}).get("threads", 2)
    best = max((g for g in s.get("gpus", []) if g.get("discrete")), key=lambda g: g.get("vram_gb", 0), default=None)
    vram = best.get("vram_gb", 0) if best else 0
    if best and best["vendor"] == "nvidia" and vram >= 16 and ram >= 31:
        return "workstation"
    if best and vram >= 6 and ram >= 12:
        return "gpu"
    if ram < 7.5 or threads < 4:
        return "basic"
    return "standard" if ram < 15 else "plus"


def _feature(key, name, status, why, **extra) -> dict:
    return {"key": key, "name": name, "status": status, "why": why, **extra}


def assess(s: dict | None = None) -> dict:
    s = s or scan()
    t = tier(s)
    lim = dict(LIMITS[t])
    tools = (s.get("models") or {}).get("tools") or {}
    packages = (s.get("models") or {}).get("packages") or {}
    nvidia = max((g for g in s.get("gpus", []) if g["vendor"] == "nvidia"), key=lambda g: g.get("vram_gb", 0), default=None)
    vram = nvidia["vram_gb"] if nvidia else 0.0
    cuda = (s.get("cuda") or {}).get("cuda")
    encoders = (s.get("encoders") or {}).get("working") or []
    speed = s.get("speed") or {}
    per_second = speed.get("seconds_per_second") or TIER_SPEED[t]
    if encoders and not speed.get("measured"):
        per_second *= 0.85
    if s.get("ram_free_gb", 99) < 2:
        lim["minutes"] = max(1, lim["minutes"] // 2)
    if s.get("disk_free_gb", 999) < 20:
        lim["minutes"] = max(1, min(lim["minutes"], int(s["disk_free_gb"] // 2) or 1))
    ffmpeg_ok = tools.get("ffmpeg", True)
    features = [
        _feature("edit", "Edit your own footage", "ready" if ffmpeg_ok else "install",
                 "FFmpeg does the cutting, colour, sound and encoding on this PC." if ffmpeg_ok else "FFmpeg is missing. Setup installs it."),
        _feature("captions", "Captions and speech-to-text", "ready" if packages.get("faster_whisper", True) else "install",
                 f"Speech model size picked for this PC: {lim['whisper']}."),
        _feature("photos", "Your pictures in the edit, or a film from pictures alone", "ready", "Pan and zoom, transitions and music, made locally."),
        _feature("restyle", "Restyle a video (look, format, steadier, slow motion)", "ready" if t != "basic" else "slow",
                 "Steadying and smooth slow motion are heavy on this PC; keep clips short." if t == "basic" else "Made locally with FFmpeg."),
        _feature("cutout", "Remove or replace the background", "ready" if (s.get("models") or {}).get("segmentation") else "install",
                 "A person-cutout model runs on the processor; about real time to a few times slower."),
        _feature("3d", "3D titles and scenes", "ready" if tools.get("blender") else "install",
                 "Blender renders 3D; on a basic PC keep 3D to short titles." if tools.get("blender") else "Install Blender (free) from Setup."),
        _feature("hd4k", "4K export", "ready" if lim["height"] >= 2160 else ("slow" if t == "plus" else "not_here"),
                 "Works but is slow without a graphics card." if t == "plus" else
                 ("Supported." if lim["height"] >= 2160 else "Needs 16 GB memory or a graphics card; 1080p is the practical limit here.")),
        _feature("hw_encode", "Hardware video encoding", "ready" if encoders else "not_here",
                 ("Tested and working: " + ", ".join(e.split("_")[1].upper() for e in encoders)) if encoders else
                 "No working hardware encoder; the processor encodes (slower, same quality)."),
    ]
    ram = s.get("ram_total_gb", 0)
    features.append(_feature("local_llm", "Fully offline AI editor (Ollama)", "ready" if ram >= 15 or vram >= 8 else ("slow" if ram >= 7.5 else "not_here"),
                             "A 7-8B model fits." if ram >= 15 or vram >= 8 else
                             ("Only small models fit; free online AI editors are better here." if ram >= 7.5 else "Not enough memory.")))
    return {"tier": t, "tier_label": TIER_LABEL[t], "limits": lim, "features": features,
            "speed": {"seconds_per_second": round(per_second, 1), "measured": bool(speed.get("measured")),
                      "text": (f"About {per_second:.0f} seconds of work per second of footage "
                               + ("(measured from your finished edits)." if speed.get("measured") else "(estimate for this kind of PC; it is measured after your first edit)."))},
            "encoder": encoders[0] if encoders else "libx264", "facts": _facts(s), "summary": _summary(t, lim, features)}


def _facts(s: dict) -> list[str]:
    cpu = s.get("cpu") or {}
    out = [f"Processor: {cpu.get('name', '?')} ({cpu.get('cores', '?')} cores, {cpu.get('threads', '?')} threads)",
           f"Memory: {s.get('ram_total_gb', 0):g} GB ({s.get('ram_free_gb', 0):g} GB free now)",
           f"Free space for projects: {s.get('disk_free_gb', 0):g} GB"]
    for g in s.get("gpus", []):
        mem = f"{g['vram_gb']:g} GB" if g.get("vram_gb") else "shares main memory"
        out.append(f"Graphics: {g['name']} ({'dedicated' if g.get('discrete') else 'built in'}, {mem})")
    if not s.get("gpus"):
        out.append("Graphics: not detected")
    return out


def _summary(t: str, lim: dict, features: list[dict]) -> str:
    ready = [f["name"] for f in features if f["status"] == "ready"]
    cloud = [f["name"] for f in features if f["status"] == "cloud_only"]
    text = (f"This PC can make videos up to {lim['height']}p at {lim['fps']} fps. A comfortable single job is up to about "
            f"{lim['minutes']} minutes of footage; longer works but takes longer. Ready here: {len(ready)} of {len(features)} features.")
    if cloud:
        text += " " + " and ".join(cloud) + " need a cloud service on this PC."
    return text


# Work per second of output compared with a full edit (speech-to-text, planning, effects), measured on a
# 13th-gen i7 laptop: a photo video took 2 s per second, a restyle about 1.3, a person cut-out about 13.
TASK_WORK = {"photos": 0.15, "restyle": 0.3, "cutout": 1.0, "script": 1.0, "edit": 1.0}


def check(task: str, seconds: float | None = None, short_side: int | None = None, fps: float | None = None,
          s: dict | None = None) -> dict:
    """Before a job: can this PC do it, and what should the person know? level = ok | warn | stop.
    short_side is the smaller frame dimension (1080 for both 1920x1080 and 1080x1920)."""
    a = assess(s)
    lim, msgs, level = a["limits"], [], "ok"
    feat = {f["key"]: f for f in a["features"]}
    want = {"edit": "edit", "make": "script", "script": "script", "photos": "photos", "restyle": "restyle", "cutout": "cutout"}.get(task, task)
    f = feat.get(want)
    if f and f["status"] in ("cloud_only", "not_here"):
        return {"level": "stop", "messages": [f["why"]], "assessment_summary": a["summary"], "tier": a["tier"]}
    if f and f["status"] == "install":
        level, msgs = "warn", [f["why"]]
    if f and f["status"] == "slow":
        level, msgs = "warn", msgs + [f["why"]]
    if short_side and short_side > lim["height"]:
        level = "warn"
        msgs.append(f"{short_side}p is above this PC's practical limit of {lim['height']}p. It will work but slowly; {lim['height']}p is recommended.")
    if fps and fps > lim["fps"]:
        level = "warn"
        msgs.append(f"{fps:g} fps is above the {lim['fps']} fps this PC handles comfortably.")
    if seconds:
        work = seconds * a["speed"]["seconds_per_second"] * TASK_WORK.get(want, 1.0)
        if seconds > lim["minutes"] * 60:
            level = "warn"
            msgs.append(f"{seconds / 60:.0f} minutes of footage is more than the comfortable {lim['minutes']} minutes for this PC. "
                        "It will still finish; you can keep using the PC meanwhile.")
        msgs.append(f"Expected time: about {_duration(work)} ({'measured' if a['speed']['measured'] else 'estimate'}).")
        free = scan(False).get("disk_free_gb", 999) if s is None else s.get("disk_free_gb", 999)
        need_gb = seconds * 0.06 + 1
        if free < need_gb:
            return {"level": "stop", "messages": [f"About {need_gb:.0f} GB of free space is needed and only {free:g} GB is free. "
                                                  "Free some space or run Clean caches."], "tier": a["tier"]}
    return {"level": level, "messages": msgs, "tier": a["tier"], "recommended": {"quality": lim["quality"], "height": lim["height"]}}


def _duration(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f} seconds"
    if seconds < 5400:
        return f"{seconds / 60:.0f} minutes"
    return f"{seconds / 3600:.1f} hours"


def default_quality() -> str:
    """Quality used when the person did not choose one: lighter on a basic laptop."""
    try:
        return LIMITS[tier(scan())]["quality"]
    except Exception:  # noqa: BLE001 - never block a job on a scan problem
        return "standard"
