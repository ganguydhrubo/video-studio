"""Best-quality cut-out (optional): BiRefNet lite, an MIT-licensed open model, run with onnxruntime on this PC.

The default cut-out stays MediaPipe (fast). "Best quality" is turned on by the person, and the model (214 MB) is
downloaded only when they allow it. The SHA-256 is checked before the file is used.

To keep it practical on a laptop processor, BiRefNet runs only on keyframes (a few each second, and at every shot
change); the frames in between carry its edge with optical flow, then the guided filter snaps it to the real frame
(helpers/matte_helper.py). The time estimate shown before a run comes from speeds measured on this PC: a short
test right after the download, then every real run.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import urllib.request
from pathlib import Path

from video_studio import detect
from video_studio.util import ToolError, app_data_dir, read_json, resource_path, run, write_json

QUALITIES = ("fast", "best")
MODEL = {
    "name": "BiRefNet-general-bb_swin_v1_tiny-epoch_232.onnx",
    "sub": "birefnet",
    "url": "https://github.com/ZhengPeng7/BiRefNet/releases/download/v1/BiRefNet-general-bb_swin_v1_tiny-epoch_232.onnx",
    "bytes": 224_005_088,
    "size": "214 MB",
    # the publisher gives no checksum; this is the SHA-256 of the release file, checked on every download
    "sha256": "5600024376f572a557870a5eb0afb1e5961636bef4e1e22132025467d0f03333",
    "licence": "MIT (BiRefNet by ZhengPeng7)",
}
KEYS_PER_SECOND = 2.0  # BiRefNet keyframes per second of footage (plus shot changes and the last frame)
BEST_MAX_SIDE = 1920  # best quality works at up to full HD; larger videos are refined at this size and scaled up
SPEED_FILE = "cutout_speed.json"
HELPER = resource_path("helpers", "matte_helper.py")


# ---------------------------------------------------------------- model file

def model_path() -> Path:
    from video_studio.engines import ml

    return ml.models_dir() / MODEL["sub"] / MODEL["name"]


def model_present() -> bool:
    path = model_path()
    return path.is_file() and path.stat().st_size == MODEL["bytes"]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_text() -> str:
    return (f"Best quality needs a one-time download of the BiRefNet lite model ({MODEL['size']}, "
            f"licence {MODEL['licence']}).")


def ensure(allow_download: bool = False, progress=None) -> Path:
    """The BiRefNet model on disk. Without allow_download it never downloads; it says what is needed instead."""
    path = model_path()
    if model_present():
        return path
    if not allow_download:
        raise ToolError(download_text() + " It is not on this PC yet.",
                        "Tick 'Allow the one-time model download' (or pass --allow-download), or choose Fast.")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.unlink(missing_ok=True)
    last = [0.0]

    def hook(blocks: int, block: int, total: int) -> None:
        if progress and time.time() - last[0] > 2:
            last[0] = time.time()
            progress(min(blocks * block, total or MODEL["bytes"]), total or MODEL["bytes"])

    try:
        urllib.request.urlretrieve(MODEL["url"], tmp, hook)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise ToolError(f"Could not download the best-quality model ({exc}).",
                        f"Check the internet connection and try again, or save {MODEL['url']} as {path}.") from exc
    got = _sha256(tmp)
    if got != MODEL["sha256"]:
        tmp.unlink(missing_ok=True)
        raise ToolError("The downloaded model failed its safety check (SHA-256 did not match), so it was deleted.",
                        "Try again later. If it keeps failing, use Fast.")
    tmp.replace(path)
    return path


# ---------------------------------------------------------------- measured speed and the estimate

def _speeds() -> dict:
    return read_json(app_data_dir() / SPEED_FILE, {}) or {}


def record_speed(quality: str, frames: int, seconds: float, *, key_seconds: float | None = None,
                 keyframes: int = 0, source: str = "run") -> None:
    """Remember how fast a cut-out really was on this PC (last 12 of each kind)."""
    if frames <= 0 or seconds <= 0:
        return
    data = _speeds()
    hist = [h for h in data.get(quality, []) if isinstance(h, dict)][-11:]
    hist.append({"at": round(time.time()), "frames": int(frames), "seconds": round(float(seconds), 3),
                 "key_seconds": round(float(key_seconds), 3) if key_seconds else None,
                 "keyframes": int(keyframes), "source": source})
    data[quality] = hist
    write_json(app_data_dir() / SPEED_FILE, data)


def _median(values: list[float]) -> float | None:
    values = sorted(v for v in values if v and v > 0)
    return values[len(values) // 2] if values else None


def measured(quality: str) -> dict:
    """{'per_frame': s per video frame (whole run), 'per_key': s per BiRefNet keyframe, 'runs': n} - None if unknown."""
    hist = _speeds().get(quality, [])
    runs = [h for h in hist if h.get("source") == "run"]
    per_frame = _median([h["seconds"] / h["frames"] for h in runs if h.get("frames")])
    per_key = _median([h.get("key_seconds") for h in hist if h.get("key_seconds")])
    between = _median([(h["seconds"] - h["key_seconds"] * h["keyframes"]) / max(1, h["frames"] - h["keyframes"])
                       for h in runs if h.get("key_seconds") and h.get("keyframes") and h["frames"] > h["keyframes"]])
    return {"per_frame": per_frame, "per_key": per_key, "per_between": between, "runs": len(runs)}


def keyframe_count(frames: int, fps: float) -> int:
    gap = max(1, round((fps or 30.0) / KEYS_PER_SECOND))
    return max(1, -(-int(frames) // gap) + 1) if frames > 1 else 1


def estimate(seconds: float, fps: float, quality: str = "best") -> dict:
    """Seconds the matte step should take for a clip, from speeds measured on THIS PC only.
    {'known': False} when nothing has been measured yet (the first run measures it)."""
    frames = max(1, round(float(seconds or 0) * float(fps or 30)))
    m = measured(quality)
    if quality == "best" and m["per_key"]:
        keys = min(frames, keyframe_count(frames, fps))
        between = m["per_between"] if m["per_between"] is not None else 0.06  # flow + edge filter, see KNOWN_LIMITATIONS
        total = keys * m["per_key"] + max(0, frames - keys) * between
        basis = "measured on this PC" + (f" over {m['runs']} run(s)" if m["runs"] else " by the test after the download")
        return {"known": True, "seconds": round(total, 1), "frames": frames, "keyframes": keys, "basis": basis}
    if m["per_frame"]:
        return {"known": True, "seconds": round(frames * m["per_frame"], 1), "frames": frames,
                "basis": f"measured on this PC over {m['runs']} run(s)"}
    return {"known": False, "frames": frames}


def describe(est: dict) -> str:
    if not est.get("known"):
        return "Best-quality speed is not measured on this PC yet; the first run measures it."
    s = est["seconds"]
    text = f"{s:.0f} seconds" if s < 90 else f"{s / 60:.0f} minutes" if s < 5400 else f"{s / 3600:.1f} hours"
    return f"Expected time for the cut-out: about {text} ({est['basis']})."


# ---------------------------------------------------------------- helper calls

def _python() -> Path:
    py = detect.system_python()
    if not py:
        raise ToolError("Best quality needs Python on this PC, and none was found.",
                        "Install Python 3.10 or newer from python.org, then run `video-studio setup`.")
    return py


def call(cmd: str, timeout: float, **kwargs) -> dict:
    """Run helpers/matte_helper.py with the user's Python (never imported by the app) and return its JSON."""
    py = _python()
    fd, name = tempfile.mkstemp(prefix=f"vs-{cmd}-", suffix=".json")
    os.close(fd)
    out = Path(name)
    args = [str(py), "-E", "-P", str(HELPER), cmd, "--out", str(out)]
    for key, value in kwargs.items():
        args += [f"--{key.replace('_', '-')}", str(value)]
    try:
        res = run(args, timeout=timeout, check=False, low_priority=True)
        data = read_json(out, None)
    finally:
        out.unlink(missing_ok=True)
    if not data:
        raise ToolError(f"The best-quality cut-out stopped: {(res.stderr or '')[-600:]}",
                        "Choose Fast, or run `video-studio doctor`.")
    if not data.get("ok"):
        raise ToolError(f"Best quality needs: {data.get('missing')} ({str(data.get('error') or '')[:200]})",
                        f"py -m pip install --user {data.get('missing')}")
    return data


def benchmark(threads: int = 0) -> dict:
    """Time BiRefNet on two test frames right after the download, so the first estimate is a real one."""
    data = call("bench", timeout=600, model=model_path(), threads=threads or default_threads())
    record_speed("best", 1, float(data["key_seconds"]), key_seconds=float(data["key_seconds"]), keyframes=1,
                 source="bench")
    return data


def default_threads() -> int:
    """onnxruntime threads: the physical cores, leaving one for the rest of the PC (measured best on a 13th-gen i7)."""
    n = os.cpu_count() or 4
    return max(2, min(8, n // 2))


def info(seconds: float | None = None, fps: float | None = None) -> dict:
    """Everything the UI shows next to the Best quality choice."""
    est = estimate(seconds or 0, fps or 30) if seconds else {"known": False}
    return {"model_present": model_present(), "size": MODEL["size"], "licence": MODEL["licence"],
            "download": download_text(), "estimate": est, "estimate_text": describe(est) if seconds else "",
            "keys_per_second": KEYS_PER_SECOND}


def cache_part() -> str:
    """Extra text for the mask cache key, so a best-quality mask is never mixed up with a fast one."""
    return json.dumps({"q": "best", "m": MODEL["sha256"][:12], "k": KEYS_PER_SECOND, "s": BEST_MAX_SIDE})
