"""Word timing helpers for speech in real footage (NumPy only, runs inside the app).

faster-whisper word starts are good to about 50 ms on clean speech, but the first word after a pause is often
started inside the pause, up to 0.8 s before it is heard (measured on synthetic speech, 1.9.0). A caption or karaoke
highlight then lights up on silence. `trim_silent_starts` moves only those starts to where the sound comes back.

(A general "snap every start to the nearest loudness rise" was tried in 1.9.0 and made the mean error worse,
71 -> 77 ms, because inside running speech words do not start with a clear rise. It was left out.)
"""

from __future__ import annotations

import wave
from pathlib import Path

HOP = 0.005  # 5 ms analysis step


def read_wav(path: Path) -> tuple["np.ndarray", int]:  # noqa: F821 - numpy imported lazily
    """Mono float32 samples and the sample rate of a 16-bit PCM wav."""
    import numpy as np

    with wave.open(str(path), "rb") as wf:
        sr, ch = wf.getframerate(), wf.getnchannels()
        x = np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2").astype("float32") / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, sr


def loudness(pcm, sr: int):
    """Loudness in dB every 5 ms (10 ms windows)."""
    import numpy as np

    hop = max(1, int(sr * HOP))
    n = len(pcm) // hop
    if n < 4:
        return np.zeros(0, dtype="float32")
    frames = pcm[: n * hop].reshape(n, hop).astype("float64")
    energy = np.convolve((frames ** 2).mean(axis=1), np.ones(2) / 2, mode="same")
    return (10 * np.log10(energy + 1e-10)).astype("float32")


def trim_silent_starts(words: list[dict], pcm, sr: int, quiet_db: float = 0.0, max_move: float = 0.9,
                       min_quiet: float = 0.12, max_tail: float = 0.15) -> list[dict]:
    """Whisper often starts the first word after a pause too early, inside the pause (up to ~0.8 s measured), often
    on the fading end of the word before. If, after a word's start, there is a real pause (at least `min_quiet` s of
    quiet) with at most `max_tail` s of sound before it, the word starts where the sound comes back.
    Starts inside running speech are never moved (short stop-consonant gaps are under `min_quiet`). Returns new dicts."""
    import numpy as np

    level = loudness(pcm, sr)
    out = [dict(w) for w in words]
    if len(level) < 10:
        return out
    floor, loud = float(np.percentile(level, 10)), float(np.percentile(level, 95))
    if loud - floor < 12:  # no clear speech above the noise: nothing safe to do
        return out
    quiet = floor + 0.3 * (loud - floor) + quiet_db
    need, tail = int(round(min_quiet / HOP)), int(round(max_tail / HOP))
    for i, w in enumerate(out):
        c = int(round(w["start"] / HOP))
        stop = min(w["start"] + max_move, w["end"] - 0.05)
        if i + 1 < len(out):
            stop = min(stop, out[i + 1]["start"] - 0.05)
        e = min(len(level) - 1, int(round(stop / HOP)))
        if e - c < need + 2:
            continue
        q = level[c:e + 1] < quiet
        run, sound, back = 0, 0, -1
        for k, is_quiet in enumerate(q):
            if is_quiet:
                run += 1
            else:
                if run >= need:  # sound comes back after a real pause: this is where the word starts
                    back = k
                    break
                sound += 1 + run  # a short gap is part of the sound before
                run = 0
                if sound > tail:
                    break  # the word was already sounding at its start: leave it
        if back < 0:
            continue
        t = round(max(w["start"], (c + back - 2) * HOP), 3)  # 10 ms before it gets loud
        w["start"] = t
        w["end"] = round(max(w["end"], t + 0.05), 3)
    return out
