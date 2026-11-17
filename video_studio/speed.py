"""Speed helpers for the footage edit (1.9.0): work that gives exactly the same result, done side by side.

* The voice clean-up (noise reduction, single core, ~1 s per second of voice) starts in the background as soon as the
  footage is ingested, so it runs while the footage is being watched and planned instead of before the cut.
* The cut pieces are encoded a few at a time (each is a separate short FFmpeg run; FFmpeg start-up and seeking
  leave most of the processor idle when they run one by one).
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Iterable

from video_studio import resources
from video_studio.util import ToolError, console

_voice: dict[str, threading.Thread] = {}


def _voice_job(raw: Path, dest: Path) -> None:
    from video_studio.engines import ml

    tmp = dest.with_name(dest.stem + ".part.wav")
    try:
        ml.call("denoise", audio=raw, dest=tmp, strength=0.8)
        if tmp.exists():
            tmp.replace(dest)
    except (ToolError, OSError) as exc:  # the cut stage simply runs it again and reports the problem
        console.debug(f"background noise reduction failed: {exc}")
        tmp.unlink(missing_ok=True)


def start_voice_cleanup(build: Path, source: Path, enhance: bool = True) -> bool:
    """Extract the voice and start its noise reduction in the background (same command the cut stage runs).
    Returns True when a background job was started."""
    from video_studio.engines import ff, ml

    clean, dn, raw = build / "voice_clean.wav", build / "voice_dn.wav", build / "voice_raw.wav"
    if not enhance or clean.exists() or dn.exists() or str(build) in _voice:
        return False
    try:
        if not ml.available("noisereduce"):
            return False
        ff.ff(["-i", str(source), "-vn", "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(raw)])
    except ToolError:
        return False
    th = threading.Thread(target=_voice_job, args=(raw, dn), daemon=True, name="voice-cleanup")
    _voice[str(build)] = th
    th.start()
    return True


def wait_voice_cleanup(build: Path) -> Path | None:
    """The cleaned voice from the background job (waits for it), or None if there was none or it failed."""
    th = _voice.pop(str(build), None)
    if th is not None:
        th.join()
    dn = build / "voice_dn.wav"
    return dn if th is not None and dn.exists() else None


def piece_workers(free_gb: float | None = None, cores: int | None = None) -> int:
    """Short FFmpeg encodes run at once while cutting (each ~0.3 GB at 1080x1920)."""
    cores = cores or resources.cpu_count()
    free_gb = resources.memory().available_gb if free_gb is None else free_gb
    return max(1, min(3, cores // 4, int((free_gb - 1.0) / 0.3)))


def run_all(fn: Callable, jobs: Iterable, workers: int, on_done: Callable[[int], None] | None = None) -> list:
    """fn(job) for every job, `workers` at a time; results in job order; the first error is raised."""
    jobs = list(jobs)
    if workers <= 1 or len(jobs) <= 1:
        out = []
        for i, job in enumerate(jobs):
            out.append(fn(job))
            if on_done:
                on_done(i + 1)
        return out
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(fn, job) for job in jobs]
        out = []
        for i, fut in enumerate(futures):
            out.append(fut.result())
            if on_done:
                on_done(i + 1)
        return out
