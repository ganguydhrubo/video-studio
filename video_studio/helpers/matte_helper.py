"""Best-quality person matte worker (BiRefNet lite + optical flow), run with the user's own Python.

Never imported by the app (video_studio/matting.py runs it). Writes a JSON result to --out and exits.

  best   greyscale matte video (255 = person) at the input's size and frame rate
  bench  time BiRefNet on test frames, so the app can show a real time estimate

How "best" stays practical on a laptop processor:
  * BiRefNet (fixed 1024 x 1024 input, a few seconds a frame on a CPU) runs only on KEYFRAMES: a few each second,
    every shot change, and usually the last frame.
  * Frames in between take the alpha of the keyframe before and after, moved with DIS optical flow, blended by
    distance. Where the two disagree a lot (fast moves), MediaPipe's person mask decides, if it is installed.
  * Every frame then goes through the guided filter (edges snap to the real frame) and a light temporal smooth
    that backs off on movement, so keyframes do not flicker.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def _fail(out: Path, missing: str, err: str = "") -> None:
    out.write_text(json.dumps({"ok": False, "missing": missing, "error": err}), encoding="utf-8")
    sys.exit(3)


def _ml_helper():
    """ml_helper.py beside this file (guided filter, MediaPipe segmenter, mask encoder). Run with -P, so no sys.path."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("tb_ml_helper", Path(__file__).with_name("ml_helper.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _peak_mb() -> float:
    """Peak memory of this process (Windows), 0 elsewhere."""
    if sys.platform != "win32":
        return 0.0
    import ctypes
    from ctypes import wintypes

    class PMC(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

    pmc = PMC()
    pmc.cb = ctypes.sizeof(PMC)
    try:
        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        if not k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            return 0.0
    except (AttributeError, OSError):
        return 0.0
    return round(pmc.PeakWorkingSetSize / 2**20, 1)


# ---------------------------------------------------------------- the keyframe walk (pure Python, unit-tested)

def walk(frames, gap: int, key_fn, carry_fn, emit, is_cut=None, tail: bool = True) -> dict:
    """Go through `frames` once, in order, and emit an alpha for every one of them.

    key_fn(item) -> alpha                        (the expensive model; called only on keyframes)
    carry_fn(item, key, next_key, t) -> alpha    (key / next_key are (item, alpha); next_key None = forwards only;
                                                 t = 0..1, how far the item is from key towards next_key)
    emit(item, alpha, is_key)                    (called once per frame, in the original order)
    is_cut(prev_item, item) -> bool              (a shot change: the item becomes a keyframe straight away)

    Keyframes: the first frame, every `gap` frames after the last keyframe, every shot change, and the last frame when
    the final stretch is longer than half a gap. Only `gap` frames are ever held in memory."""
    gap = max(1, int(gap))
    key, window, prev = None, [], None
    stats = {"frames": 0, "keyframes": 0, "cuts": 0}

    def new_key(item):
        stats["keyframes"] += 1
        return item, key_fn(item)

    def flush(items, k0, k1):
        n = len(items) + 1
        for j, it in enumerate(items, 1):
            emit(it, carry_fn(it, k0, k1, j / n), False)

    for item in frames:
        stats["frames"] += 1
        if key is None:
            key = new_key(item)
            emit(item, key[1], True)
        elif is_cut is not None and is_cut(prev, item):
            stats["cuts"] += 1
            flush(window, key, None)
            window = []
            key = new_key(item)
            emit(item, key[1], True)
        else:
            window.append(item)
            if len(window) >= gap:
                nxt = new_key(item)
                flush(window[:-1], key, nxt)
                emit(item, nxt[1], True)
                key, window = nxt, []
        prev = item
    if window:
        if tail and len(window) * 2 > gap:
            nxt = new_key(window[-1])
            flush(window[:-1], key, nxt)
            emit(window[-1], nxt[1], True)
        else:
            flush(window, key, None)
    return stats


def shot_change(prev_thumb, thumb, threshold: float = 38.0) -> bool:
    """Mean difference of two tiny greyscale thumbnails (0..255) above `threshold` = a new shot."""
    import numpy as np

    if prev_thumb is None or thumb is None or prev_thumb.shape != thumb.shape:
        return False
    return float(np.mean(np.abs(prev_thumb.astype(np.float32) - thumb.astype(np.float32)))) > threshold


# ---------------------------------------------------------------- the model

class BiRefNet:
    """BiRefNet lite ONNX: fixed 1024 x 1024 RGB input (ImageNet normalised), logits out (sigmoid applied here).
    DirectML is tried when present; any failure there falls back to the processor for the rest of the video."""

    SIZE = 1024
    MEAN = (0.485, 0.456, 0.406)
    STD = (0.229, 0.224, 0.225)

    def __init__(self, model: str, threads: int) -> None:
        import onnxruntime as ort

        self.ort, self.model, self.threads = ort, model, max(1, int(threads))
        wanted = ["DmlExecutionProvider"] if "DmlExecutionProvider" in ort.get_available_providers() else []
        self.fallback = ""
        try:
            self._open(wanted + ["CPUExecutionProvider"])
        except Exception as exc:  # noqa: BLE001 - a graphics driver problem must not stop the cut-out
            if not wanted:
                raise
            self.fallback = f"graphics card failed ({str(exc)[:120]}); used the processor"
            self._open(["CPUExecutionProvider"])

    def _open(self, providers: list[str]) -> None:
        so = self.ort.SessionOptions()
        so.intra_op_num_threads = self.threads
        so.inter_op_num_threads = 1
        so.graph_optimization_level = self.ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        if providers[0] == "DmlExecutionProvider":
            so.enable_mem_pattern = False
        self.session = self.ort.InferenceSession(self.model, so, providers=providers)
        self.input = self.session.get_inputs()[0].name
        self.provider = self.session.get_providers()[0]

    @classmethod
    def prepare(cls, bgr):
        import cv2
        import numpy as np

        rgb = cv2.cvtColor(cv2.resize(bgr, (cls.SIZE, cls.SIZE), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB)
        x = (rgb.astype(np.float32) / 255.0 - np.array(cls.MEAN, np.float32)) / np.array(cls.STD, np.float32)
        return np.ascontiguousarray(x.transpose(2, 0, 1)[None])

    @staticmethod
    def to_alpha(logits, width: int, height: int):
        import cv2
        import numpy as np

        z = np.squeeze(np.asarray(logits, dtype=np.float32))
        prob = 1.0 / (1.0 + np.exp(-np.clip(z, -30.0, 30.0)))
        return cv2.resize(prob, (width, height), interpolation=cv2.INTER_LINEAR)

    def __call__(self, bgr):
        x = self.prepare(bgr)
        try:
            out = self.session.run(None, {self.input: x})[0]
        except Exception as exc:  # noqa: BLE001
            if self.provider == "CPUExecutionProvider":
                raise
            self.fallback = f"graphics card failed ({str(exc)[:120]}); used the processor"
            self._open(["CPUExecutionProvider"])
            out = self.session.run(None, {self.input: x})[0]
        return self.to_alpha(out, bgr.shape[1], bgr.shape[0])


# ---------------------------------------------------------------- flow carry

def warp(alpha, flow):
    """Pull `alpha` (keyframe) into the current frame: flow maps current-frame pixels to keyframe pixels."""
    import cv2
    import numpy as np

    h, w = alpha.shape[:2]
    if flow.shape[:2] != (h, w):
        sy, sx = h / flow.shape[0], w / flow.shape[1]
        flow = cv2.resize(flow, (w, h), interpolation=cv2.INTER_LINEAR) * np.array([sx, sy], np.float32)
    gx, gy = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    return cv2.remap(alpha, gx + flow[..., 0], gy + flow[..., 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def best(args: argparse.Namespace) -> None:
    import subprocess
    import tempfile

    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        _fail(Path(args.out), "opencv-python numpy", str(exc))
    try:
        import onnxruntime  # noqa: F401
    except ImportError as exc:
        _fail(Path(args.out), "onnxruntime", str(exc))
    ml = _ml_helper()
    started = time.time()
    cap = cv2.VideoCapture(args.video)
    ok, frame = cap.read()
    if not ok:
        raise RuntimeError(f"OpenCV could not read a frame from {args.video}")
    fps_text = args.fps or f"{cap.get(cv2.CAP_PROP_FPS) or 30:.6f}"
    num, _, den = fps_text.partition("/")
    fps = float(num) / float(den or 1)
    height, width = frame.shape[:2]
    scale = min(1.0, args.max_side / max(width, height))
    ww, wh = max(2, round(width * scale)), max(2, round(height * scale))
    fs = min(1.0, 480 / max(ww, wh))  # optical flow works on a small grey copy: fast, and enough for the edge
    fw, fh = max(8, round(ww * fs)), max(8, round(wh * fs))
    gap = max(1, round(fps / max(0.1, args.keys_per_second)))
    radius = max(2, round(max(ww, wh) / 240))

    t0 = time.time()
    net = BiRefNet(args.model, args.threads)
    load_seconds = time.time() - t0
    seg = None
    if args.mp_model and Path(args.mp_model).exists():
        try:
            seg = ml._Segmenter(args.mp_model, fps)
            if not seg.engine.startswith("mediapipe"):
                seg = None
        except Exception:  # noqa: BLE001 - MediaPipe only helps with fast moves; best quality works without it
            seg = None
    dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)

    dest = Path(args.dest)
    log = tempfile.TemporaryFile()
    cmd = [args.ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "gray",
           "-s", f"{width}x{height}", "-r", fps_text, "-i", "-", *ml._mask_encoder(args.ffmpeg, dest), str(dest)]
    flags = (0x08000000 | 0x00004000) if sys.platform == "win32" else 0  # no window, below-normal priority
    enc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log, creationflags=flags)

    timing = {"key": 0.0, "fixes": 0}
    prev = [None]

    def items():
        nonlocal ok, frame
        while ok:
            work = frame if scale >= 1.0 else cv2.resize(frame, (ww, wh), interpolation=cv2.INTER_AREA)
            small = cv2.cvtColor(cv2.resize(work, (fw, fh), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
            thumb = cv2.resize(small, (64, max(8, round(64 * fh / fw))), interpolation=cv2.INTER_AREA)
            yield work, small, thumb
            ok, frame = cap.read()

    def key_fn(item):
        t = time.time()
        alpha = net(item[0])
        timing["key"] += time.time() - t
        return alpha

    def carry_fn(item, k0, k1, t):
        a0 = warp(k0[1], dis.calc(item[1], k0[0][1], None))
        if k1 is None:
            return a0
        a1 = warp(k1[1], dis.calc(item[1], k1[0][1], None))
        alpha = (1.0 - t) * a0 + t * a1
        unsure = np.abs(a0 - a1) > 0.5
        if seg is not None and unsure.mean() > 0.004:  # the two keyframes disagree: a fast move; MediaPipe decides there
            person = cv2.resize(seg(item[0]), (alpha.shape[1], alpha.shape[0]), interpolation=cv2.INTER_LINEAR)
            alpha = np.where(unsure, person, alpha)
            timing["fixes"] += 1
        return alpha

    def emit(item, alpha, is_key):
        work = item[0]
        guide = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
        m = ml._guided(guide, alpha.astype(np.float32), radius, 2e-3, sub=1 if max(ww, wh) <= 960 else 2)
        m = np.clip((m - 0.04) / 0.92, 0.0, 1.0)  # no faint veil outside, fully solid inside
        if prev[0] is not None and args.smooth > 0:  # light flicker guard that backs off where the matte moves
            weight = args.smooth * np.clip(1.0 - np.abs(m - prev[0]) * 3.0, 0.0, 1.0)
            m = weight * prev[0] + (1.0 - weight) * m
        prev[0] = m
        out = m if scale >= 1.0 else cv2.resize(m, (width, height), interpolation=cv2.INTER_LINEAR)
        enc.stdin.write((out * 255.0 + 0.5).astype(np.uint8).tobytes())

    def cut(a, b):
        return shot_change(a[2], b[2])

    code = 1
    try:
        stats = walk(items(), gap, key_fn, carry_fn, emit, is_cut=cut)
        enc.stdin.close()
        code = enc.wait()
    except (BrokenPipeError, OSError):
        enc.kill()
        code = enc.wait()
        stats = {"frames": 0, "keyframes": 0, "cuts": 0}
    finally:
        cap.release()
        if enc.poll() is None:
            enc.kill()
    if code != 0:
        log.seek(0)
        raise RuntimeError("FFmpeg could not write the mask video: " + log.read().decode("utf-8", "replace")[-800:])
    seconds = time.time() - started
    keys = max(1, stats["keyframes"])
    Path(args.out).write_text(json.dumps({
        "ok": True, "dest": str(dest), "engine": "birefnet-lite", "provider": net.provider, "fallback": net.fallback,
        "frames": stats["frames"], "keyframes": stats["keyframes"], "cuts": stats["cuts"], "gap": gap,
        "key_seconds": round(timing["key"] / keys, 3), "load_seconds": round(load_seconds, 2),
        "mediapipe_fixes": timing["fixes"], "mediapipe": bool(seg), "fps": round(fps, 3), "width": width,
        "height": height, "work_size": [ww, wh], "seconds": round(seconds, 2),
        "speed_fps": round(stats["frames"] / seconds, 3) if seconds else 0.0, "threads": net.threads,
        "peak_mb": _peak_mb()}), encoding="utf-8")


def bench(args: argparse.Namespace) -> None:
    try:
        import numpy as np
        import onnxruntime  # noqa: F401
    except ImportError as exc:
        _fail(Path(args.out), "onnxruntime numpy", str(exc))
    t0 = time.time()
    net = BiRefNet(args.model, args.threads)
    load = time.time() - t0
    rng = np.random.default_rng(1)
    frame = (rng.random((1080, 1920, 3)) * 255).astype(np.uint8)
    times = []
    for _ in range(max(2, args.runs)):
        t = time.time()
        net(frame)
        times.append(time.time() - t)
    steady = times[1:]  # the first run also warms up
    Path(args.out).write_text(json.dumps({
        "ok": True, "provider": net.provider, "fallback": net.fallback, "threads": net.threads,
        "load_seconds": round(load, 2), "first_seconds": round(times[0], 3),
        "key_seconds": round(sum(steady) / len(steady), 3), "peak_mb": _peak_mb()}), encoding="utf-8")


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("best")
    b.add_argument("--video", required=True)
    b.add_argument("--dest", required=True)
    b.add_argument("--model", required=True)
    b.add_argument("--mp-model", default="")
    b.add_argument("--ffmpeg", default="ffmpeg")
    b.add_argument("--fps", default="")
    b.add_argument("--smooth", type=float, default=0.3)
    b.add_argument("--max-side", type=int, default=1920)
    b.add_argument("--keys-per-second", type=float, default=4.0)
    b.add_argument("--threads", type=int, default=6)
    t = sub.add_parser("bench")
    t.add_argument("--model", required=True)
    t.add_argument("--threads", type=int, default=6)
    t.add_argument("--runs", type=int, default=2)
    for sp in (b, t):
        sp.add_argument("--out", required=True)
    args = p.parse_args()
    {"best": best, "bench": bench}[args.cmd](args)


if __name__ == "__main__":
    main()
