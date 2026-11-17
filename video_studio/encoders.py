"""Pick the fastest video encoder that really works on THIS PC, for final encodes only.

A hardware H.264 encoder (Intel Quick Sync, NVIDIA NVENC, AMD AMF) can be about twice as fast as the processor
encoder (x264) at the same picture quality, but only on some PCs, and a listed encoder can still fail or look
worse. So, once per PC (and again after FFmpeg changes), a 2-second test clip is encoded with x264 at the normal
final settings and with every hardware encoder that `machine.test_encoders()` found working. A hardware encoder is
chosen only when it is clearly faster AND its picture is as good (VMAF when FFmpeg has it, PSNR otherwise).
The result is kept in `encoder.json` in the app folder.

Final encodes call `encode()`: if a hardware encode fails, the same encode is redone with x264 at once and the
hardware encoder is not used again on this PC. Intermediate files and the style pass always use x264.
Setting `render.encoder` to `libx264` (or one hardware encoder) in the config overrides the automatic choice.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from video_studio import config
from video_studio.util import IS_WINDOWS, ToolError, app_data_dir, console, read_json, write_json

CACHE = "encoder.json"
HARDWARE = ("h264_qsv", "h264_nvenc", "h264_amf")
MIN_SPEEDUP = 1.25      # a hardware encoder must be at least this much faster than x264 to be used
MAX_VMAF_LOSS = 1.0     # ... and lose at most this much VMAF (0-100) against x264
MAX_PSNR_LOSS = 0.6     # ... or this many dB of PSNR when FFmpeg has no VMAF
_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0
# x264 constant-quality numbers per quality preset (see engines/ff.py QUALITY); the hardware encoders get the
# same number on their own constant-quality scale, then the one-time test checks the picture really matches.
_Q = {"draft": 26, "standard": 20, "high": 17}


def hw_args(encoder: str, quality: str) -> list[str]:
    """Constant-quality settings for a hardware H.264 encoder (8-bit 4:2:0, High profile)."""
    q = _Q.get(quality, 20)
    if encoder == "h264_qsv":
        return ["-c:v", "h264_qsv", "-preset", "medium" if quality == "high" else "veryfast", "-global_quality", str(q),
                "-pix_fmt", "nv12", "-profile:v", "high"]
    if encoder == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p5" if quality == "high" else "p4", "-tune", "hq", "-rc", "vbr", "-cq", str(q),
                "-b:v", "0", "-pix_fmt", "yuv420p", "-profile:v", "high"]
    if encoder == "h264_amf":
        return ["-c:v", "h264_amf", "-quality", "quality" if quality == "high" else "balanced", "-rc", "cqp",
                "-qp_i", str(q), "-qp_p", str(q + 1), "-qp_b", str(q + 2), "-pix_fmt", "nv12", "-profile:v", "high"]
    raise ValueError(encoder)


def _ffmpeg() -> str:
    from video_studio.engines import ff

    return ff.ffmpeg()


def _stamp(ffmpeg: str) -> str:
    """Changes when FFmpeg is replaced, so the test runs again."""
    try:
        st = Path(ffmpeg).stat()
        return f"{Path(ffmpeg).resolve()}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        return str(ffmpeg)


def _run(cmd: list[str], timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                          creationflags=_NO_WINDOW)


def _quality(ffmpeg: str, encoded: Path, ref: Path, vmaf: bool) -> float | None:
    graph = "[0:v][1:v]libvmaf=n_threads=4" if vmaf else "[0:v][1:v]psnr"
    res = _run([ffmpeg, "-hide_banner", "-i", str(encoded), "-i", str(ref), "-lavfi", graph, "-f", "null", "-"], timeout=180)
    m = re.search(r"VMAF score: ([\d.]+)", res.stderr) if vmaf else re.search(r"average:([\d.]+|inf)", res.stderr)
    if not m:
        return None
    return 99.0 if m.group(1) == "inf" else float(m.group(1))


SHORT, LONG = 15, 90   # frames in the two test encodes; their time difference is the encoder's real speed
TYPICAL = 1350         # frames in a typical 45-second final encode, used to compare start-up + speed


def benchmark(ffmpeg: str | None = None, candidates: list[str] | None = None) -> dict:
    """Encode the same 1080x1920 test clip with x264 and each candidate, twice (15 and 90 frames). The difference
    is the speed per frame (a hardware encoder needs a second or two just to start, which a 2-second clip would
    wrongly count as slowness). Quality is measured on the 90-frame encode."""
    ffmpeg = ffmpeg or _ffmpeg()
    if candidates is None:
        from video_studio import machine

        candidates = (machine.test_encoders(ffmpeg) or {}).get("working") or []
    candidates = [c for c in candidates if c in HARDWARE]
    listed = _run([ffmpeg, "-hide_banner", "-filters"], timeout=30).stdout
    vmaf = "libvmaf" in listed
    from video_studio.engines import ff

    with tempfile.TemporaryDirectory(prefix="tb-enc-") as tmp:
        ref = Path(tmp) / "ref.mkv"
        # moving detail plus light grain, like a finished edit (the final encode adds grain too). Kept lossless and
        # quick to decode, so the timing is the encoder's, not the reader's.
        src = f"testsrc2=s=1080x1920:r=30:d={LONG / 30:.3f},noise=alls=4:allf=t+u,format=yuv420p"
        res = _run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", src, "-c:v", "libx264", "-preset",
                    "ultrafast", "-qp", "0", str(ref)])
        if res.returncode != 0:
            return {"ok": False, "error": (res.stderr or "")[-300:], "results": {}}
        results: dict[str, dict] = {}
        for enc in ["libx264", *candidates]:
            args = ff.x264_args("standard") if enc == "libx264" else hw_args(enc, "standard")
            times, out, err = [], None, ""
            for n in (SHORT, LONG):
                out = Path(tmp) / f"{enc}_{n}.mp4"
                t0 = time.perf_counter()
                res = _run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(ref), "-frames:v", str(n), *args, "-an",
                            str(out)], timeout=300)
                times.append(time.perf_counter() - t0)
                if res.returncode != 0 or not out.exists() or out.stat().st_size < 1000:
                    err = (res.stderr or "").strip()[-200:] or "no output"
                    break
            if err:
                results[enc] = {"ok": False, "error": err}
                continue
            per_frame = max(1e-4, (times[1] - times[0]) / (LONG - SHORT))
            start = max(0.0, times[0] - SHORT * per_frame)
            results[enc] = {"ok": True, "start_s": round(start, 2), "ms_per_frame": round(per_frame * 1000, 1),
                            "typical_s": round(start + TYPICAL * per_frame, 1), "mb": round(out.stat().st_size / 1e6, 2),
                            "quality": _quality(ffmpeg, out, ref, vmaf)}
    return {"ok": True, "metric": "vmaf" if vmaf else "psnr", "results": results}


def decide(bench: dict) -> str:
    """The encoder to use for final encodes from a benchmark result (x264 unless a hardware one clearly wins)."""
    res = bench.get("results") or {}
    base = res.get("libx264") or {}
    if not bench.get("ok") or not base.get("ok") or not base.get("typical_s") or base.get("quality") is None:
        return "libx264"
    loss = MAX_VMAF_LOSS if bench.get("metric") == "vmaf" else MAX_PSNR_LOSS
    best, best_time = "libx264", base["typical_s"]
    for enc, r in res.items():
        if enc == "libx264" or not r.get("ok") or r.get("quality") is None or not r.get("typical_s"):
            continue
        if r["quality"] < base["quality"] - loss:
            continue  # faster but visibly worse: never traded
        if r["typical_s"] * MIN_SPEEDUP <= base["typical_s"] and r["typical_s"] < best_time:
            best, best_time = enc, r["typical_s"]
    return best


def _path() -> Path:
    return app_data_dir() / CACHE


def choice(ffmpeg: str | None = None, fresh: bool = False) -> dict:
    """Cached {encoder, bench, ...}; runs the one-time test when there is no result for this FFmpeg yet."""
    try:
        ffmpeg = ffmpeg or _ffmpeg()
    except ToolError:
        return {"encoder": "libx264", "reason": "no FFmpeg"}
    stamp = _stamp(ffmpeg)
    cached = read_json(_path(), None)
    if not fresh and isinstance(cached, dict) and cached.get("ffmpeg") == stamp and cached.get("encoder"):
        return cached
    if os.environ.get("TAKEBLOOM_NO_ENCODER_TEST"):
        return {"encoder": "libx264", "reason": "test disabled"}
    console.step("Testing this PC's video encoders once (under a minute)")
    try:
        bench = benchmark(ffmpeg)
    except (OSError, subprocess.SubprocessError) as exc:
        bench = {"ok": False, "error": str(exc)[:200], "results": {}}
    enc = decide(bench)
    data = {"encoder": enc, "ffmpeg": stamp, "at": time.time(), "bench": bench, "failed": []}
    write_json(_path(), data)
    r = (bench.get("results") or {})
    if enc != "libx264":
        console.info(f"    Final encodes use {label(enc)}: {r['libx264']['ms_per_frame']:.0f} -> {r[enc]['ms_per_frame']:.0f} ms a frame "
                     f"on the test clip, same picture ({bench['metric'].upper()} {r[enc]['quality']:.1f} vs {r['libx264']['quality']:.1f}).")
    else:
        console.info("    Final encodes use the processor (x264): no faster hardware encoder with the same picture here.")
    return data


def label(enc: str) -> str:
    return {"h264_qsv": "Intel Quick Sync", "h264_nvenc": "NVIDIA NVENC", "h264_amf": "AMD AMF", "libx264": "x264"}.get(enc, enc)


def final_encoder() -> str:
    forced = str(config.get("render.encoder") or "auto")
    if forced != "auto":
        return forced if forced == "libx264" or forced in HARDWARE else "libx264"
    data = choice()
    enc = data.get("encoder", "libx264")
    return "libx264" if enc in (data.get("failed") or []) else enc


def video_args(quality: str) -> list[str]:
    """Encoder arguments for a FINAL encode at this quality preset."""
    from video_studio.engines import ff

    enc = final_encoder()
    return ff.x264_args(quality) if enc == "libx264" else hw_args(enc, quality)


def _mark_failed(enc: str, error: str) -> None:
    data = read_json(_path(), {}) or {}
    data["failed"] = sorted(set((data.get("failed") or []) + [enc]))
    data["last_error"] = error[-300:]
    write_json(_path(), data)


def encode(before: list[str], quality: str, after: list[str], **kw):
    """ff.ff(before + <encoder args> + after) with the chosen final encoder; on a hardware failure, x264 at once."""
    from video_studio.engines import ff

    args = video_args(quality)
    try:
        return ff.ff([*before, *args, *after], **kw)
    except ToolError as exc:
        enc = args[args.index("-c:v") + 1]
        if enc == "libx264":
            raise
        console.warn(f"{label(enc)} could not encode this video; using the processor encoder instead.")
        _mark_failed(enc, str(exc))
        return ff.ff([*before, *ff.x264_args(quality), *after], **kw)
