"""Procedural, royalty-free music beds and sound effects (pure NumPy).

Everything is synthesised here, so there is no licensing question. A user's
own track can always replace the bed (`--music path.mp3`).
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

SR = 48000

MOODS = {
    #            bpm, root midi, progression (scale degrees), minor?, drums, arp
    "uplifting": (112, 57, [0, 5, 3, 4], False, True, True),     # I V vi IV
    "calm": (78, 55, [0, 3, 4, 0], False, False, True),
    "tense": (96, 50, [0, 5, 6, 4], True, True, False),
    "epic": (90, 50, [0, 5, 3, 6], True, True, True),
    "playful": (124, 60, [0, 3, 4, 4], False, True, True),
}
MAJOR = [0, 2, 4, 5, 7, 9, 11]
MINOR = [0, 2, 3, 5, 7, 8, 10]


def _midi(n: float) -> float:
    return 440.0 * 2 ** ((n - 69) / 12)


def _chord(root: int, degree: int, minor: bool) -> list[int]:
    scale = MINOR if minor else MAJOR
    notes = []
    for step in (0, 2, 4, 6 if degree in (3, 5) else 7):
        idx = degree + step
        notes.append(root + scale[idx % 7] + 12 * (idx // 7))
    return notes


def _saw(freq: float, t: np.ndarray, detune: float = 0.0) -> np.ndarray:
    phase = (freq * (1 + detune) * t) % 1.0
    return 2 * phase - 1


def _lowpass(x: np.ndarray, cutoff: float) -> np.ndarray:
    # Zero-phase 2nd-order lowpass response applied in the frequency domain (fast for long beds).
    spectrum = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(len(x), 1 / SR)
    spectrum *= 1.0 / (1.0 + (freqs / cutoff) ** 4) ** 0.5
    return np.fft.irfft(spectrum, len(x))


def _env(n: int, attack: float, release: float) -> np.ndarray:
    a = max(1, int(attack * SR))
    r = max(1, int(release * SR))
    env = np.ones(n)
    env[:a] = np.linspace(0, 1, a) if a < n else np.linspace(0, 1, n)[:a]
    if r < n:
        env[-r:] *= np.linspace(1, 0, r)
    return env


def _reverb(x: np.ndarray, seconds: float = 2.2, mix: float = 0.25, seed: int = 3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    ir = rng.standard_normal(n) * np.exp(-np.linspace(0, 7, n))
    ir[:int(0.012 * SR)] = 0
    ir /= np.sqrt(np.sum(ir ** 2))
    size = 1 << int(np.ceil(np.log2(len(x) + n)))
    wet = np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(ir, size), size)[: len(x)]
    return x * (1 - mix) + wet * mix


def bpm_for(mood: str, seed: int = 7) -> float:
    from video_studio.engines import arranger

    return arranger.choose(mood, seed)[1]


def bed(mood: str, seconds: float, seed: int = 7, drop_at: float = 0.0, stops: list[float] | None = None) -> tuple:
    """Arranged, genre-specific bed (see engines/arranger.py). Returns (stereo float32, info)."""
    from video_studio.engines import arranger

    genre, bpm = arranger.choose(mood, seed)
    return arranger.arrange(genre, seconds, seed, drop_at, stops, bpm)


def compose(mood: str, seconds: float, seed: int = 7) -> np.ndarray:
    """Return a stereo float32 bed of the requested length (arranged genre bed; legacy synth below is the fallback)."""
    try:
        return bed(mood, seconds, seed)[0]
    except Exception:  # noqa: BLE001 - never lose the music over an arrangement bug
        pass
    bpm, root, prog, minor, drums, arp = MOODS.get(mood, MOODS["calm"])
    rng = np.random.default_rng(seed)
    beat = 60.0 / bpm
    bar = beat * 4
    total = int((seconds + 2.5) * SR)
    t_all = np.arange(total) / SR
    left = np.zeros(total)
    right = np.zeros(total)
    bars = int(np.ceil((seconds + 2.5) / bar))
    for b in range(bars):
        start = int(b * bar * SR)
        n = min(int(bar * SR), total - start)
        if n <= 0:
            break
        t = t_all[:n]
        degree = prog[b % len(prog)]
        notes = _chord(root, degree, minor)
        env = _env(n, 0.35, 0.4)
        pad_l = sum(_saw(_midi(m), t, -0.004) for m in notes)
        pad_r = sum(_saw(_midi(m), t, 0.004) for m in notes)
        left[start:start + n] += 0.05 * pad_l * env
        right[start:start + n] += 0.05 * pad_r * env
        bass = np.sin(2 * np.pi * _midi(notes[0] - 12) * t) * _env(n, 0.02, 0.3)
        left[start:start + n] += 0.16 * bass
        right[start:start + n] += 0.16 * bass
        if arp and b >= 1:
            step = beat / 2
            for k in range(8):
                s = start + int(k * step * SR)
                m = int(step * SR * 0.95)
                if s + m > total:
                    break
                note = notes[[0, 1, 2, 3, 2, 1, 2, 3][k]] + 12
                tt = np.arange(m) / SR
                pluck = np.sin(2 * np.pi * _midi(note) * tt) * np.exp(-tt * 9)
                pan = 0.5 + 0.35 * np.sin(k)
                left[s:s + m] += 0.07 * pluck * (1 - pan)
                right[s:s + m] += 0.07 * pluck * pan
        if drums and b >= 2:
            for k in range(4):
                s = start + int(k * beat * SR)
                m = int(0.35 * SR)
                if s + m > total:
                    break
                tt = np.arange(m) / SR
                kick = np.sin(2 * np.pi * (45 + 90 * np.exp(-tt * 30)) * tt) * np.exp(-tt * 9)
                left[s:s + m] += 0.30 * kick
                right[s:s + m] += 0.30 * kick
                hs = s + int(beat * SR / 2)
                hm = int(0.05 * SR)
                if hs + hm < total:
                    hat = rng.standard_normal(hm) * np.exp(-np.arange(hm) / SR * 80)
                    hat = np.diff(hat, prepend=0)
                    left[hs:hs + hm] += 0.035 * hat
                    right[hs:hs + hm] += 0.035 * hat
    left = _lowpass(left, 9000)
    right = _lowpass(right, 9000)
    left = _reverb(left, mix=0.22, seed=seed)
    right = _reverb(right, mix=0.22, seed=seed + 1)
    stereo = np.stack([left, right], axis=1)[: int(seconds * SR)]
    fade_in, fade_out = int(1.0 * SR), int(2.0 * SR)
    stereo[:fade_in] *= np.linspace(0, 1, fade_in)[:, None]
    stereo[-fade_out:] *= np.linspace(1, 0, fade_out)[:, None]
    peak = np.max(np.abs(stereo)) or 1.0
    return (stereo / peak * 0.7).astype(np.float32)


def sfx(kind: str, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    if kind == "whoosh":
        n = int(0.55 * SR)
        noise = rng.standard_normal(n)
        sweep = np.linspace(0, 1, n)
        y = _lowpass(noise, 1500) * np.sin(np.pi * sweep) ** 2 + _lowpass(noise, 5000) * 0.3 * np.sin(np.pi * sweep) ** 4
    elif kind == "riser":
        n = int(1.4 * SR)
        t = np.arange(n) / SR
        y = np.sin(2 * np.pi * (200 + 900 * (t / t[-1]) ** 2) * t) * 0.3 + _lowpass(rng.standard_normal(n), 3000) * 0.5
        y *= np.linspace(0, 1, n) ** 2
    elif kind == "hit":
        n = int(0.9 * SR)
        t = np.arange(n) / SR
        y = np.sin(2 * np.pi * (38 + 120 * np.exp(-t * 18)) * t) * np.exp(-t * 4.5) + rng.standard_normal(n) * np.exp(-t * 40) * 0.4
    elif kind == "pop":
        n = int(0.12 * SR)
        t = np.arange(n) / SR
        y = np.sin(2 * np.pi * (900 - 500 * t / t[-1]) * t) * np.exp(-t * 40)
    elif kind == "ding":
        n = int(1.0 * SR)
        t = np.arange(n) / SR
        y = (np.sin(2 * np.pi * 1318 * t) + 0.5 * np.sin(2 * np.pi * 2637 * t)) * np.exp(-t * 5)
    elif kind == "click":
        n = int(0.04 * SR)
        y = rng.standard_normal(n) * np.exp(-np.arange(n) / SR * 200)
    else:
        return np.zeros((1, 2), dtype=np.float32)
    y = y / (np.max(np.abs(y)) or 1) * 0.8
    return np.stack([y, y], axis=1).astype(np.float32)


def write_wav(path: Path, data: np.ndarray) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(data, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(pcm.shape[1] if pcm.ndim > 1 else 1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(pcm.tobytes())
    return path
