"""Genre-aware, arranged music beds (pure NumPy, royalty-free because it is synthesised here).

A bed is arranged around the edit, the way an editor cuts music: a filtered intro under the hook, a riser into the
DROP when the hook ends, a 1-bar breakdown every 8 bars, the music stopping for a beat before each punch-word, and an
outro. Six genres with their own drums, bass and instruments; key, tempo and progression vary per video (seed), so no
two edits share a bed.
"""

from __future__ import annotations

import numpy as np

SR = 48000

#           bpm range, scale, progressions, swing, instrument set
GENRES = {
    "lofi":      ((78, 88), [0, 2, 3, 5, 7, 9, 10], [[0, 3, 4, 0], [0, 5, 3, 4], [1, 4, 0, 0]], 0.18, "keys"),
    "trap":      ((136, 146), [0, 2, 3, 5, 7, 8, 10], [[0, 5, 3, 4], [0, 0, 5, 6], [0, 3, 5, 4]], 0.0, "808"),
    "house":     ((120, 125), [0, 2, 4, 5, 7, 9, 11], [[5, 3, 0, 4], [0, 4, 5, 3], [0, 5, 3, 4]], 0.0, "stabs"),
    "cinematic": ((84, 96), [0, 2, 3, 5, 7, 8, 10], [[0, 5, 2, 6], [0, 3, 5, 4], [5, 3, 0, 4]], 0.0, "strings"),
    "desi":      ((96, 108), [0, 1, 4, 5, 7, 8, 11], [[0, 0, 3, 4], [0, 5, 4, 0], [0, 3, 4, 3]], 0.08, "tabla"),
    "pop":       ((108, 118), [0, 2, 4, 5, 7, 9, 11], [[0, 4, 5, 3], [0, 5, 3, 4], [3, 4, 0, 5]], 0.0, "arp"),
}
MOOD_GENRES = {
    "uplifting": ["pop", "house", "desi"],
    "calm": ["lofi", "cinematic"],
    "tense": ["trap", "cinematic"],
    "epic": ["cinematic", "trap"],
    "playful": ["house", "pop", "desi"],
}


def choose(mood: str, seed: int) -> tuple[str, float]:
    """Genre and tempo for this video: varies with the seed, so consecutive edits do not share a bed."""
    options = MOOD_GENRES.get(mood) or ["lofi"]
    genre = mood if mood in GENRES else options[seed % len(options)]
    lo, hi = GENRES[genre][0]
    return genre, float(np.random.default_rng(seed).integers(lo, hi + 1))


def _midi(n: float) -> float:
    return 440.0 * 2 ** ((n - 69) / 12)


def _lp_fast(x: np.ndarray, cutoff: float) -> np.ndarray:
    """FFT brick-ish lowpass with a soft knee (fast on long buffers)."""
    n = len(x)
    f = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(n, 1 / SR)
    f *= 1 / (1 + (freqs / cutoff) ** 4)
    return np.fft.irfft(f, n)


def _hit(buf: np.ndarray, at: int, sig: np.ndarray, gain: float) -> None:
    if at < 0 or at >= len(buf):
        return
    m = min(len(sig), len(buf) - at)
    buf[at:at + m] += gain * sig[:m]


def _t(sec: float) -> np.ndarray:
    return np.arange(int(sec * SR)) / SR


def _kick(punch: float = 1.0) -> np.ndarray:
    t = _t(0.38)
    return np.sin(2 * np.pi * (48 + 110 * punch * np.exp(-t * 32)) * t) * np.exp(-t * 8.5)


def _snare(rng) -> np.ndarray:
    t = _t(0.22)
    noise = np.diff(rng.standard_normal(len(t)), prepend=0) * np.exp(-t * 18)
    return 0.6 * noise + 0.5 * np.sin(2 * np.pi * 190 * t) * np.exp(-t * 25)


def _clap(rng) -> np.ndarray:
    t = _t(0.18)
    env = sum(np.exp(-np.clip(t - d, 0, None) * 60) * (t >= d) for d in (0, 0.011, 0.023))
    return np.diff(rng.standard_normal(len(t)), prepend=0) * env * 0.7


def _hat(rng, open_: bool = False) -> np.ndarray:
    t = _t(0.25 if open_ else 0.05)
    return np.diff(rng.standard_normal(len(t)), prepend=0) * np.exp(-t * (12 if open_ else 85))


def _tabla(kind: str, root_hz: float) -> np.ndarray:
    """dayan ('na'/'tin': ringing pitched stroke) and bayan ('ge': low stroke with a pitch glide)."""
    t = _t(0.45)
    if kind == "ge":
        f = 75 + 45 * np.exp(-t * 6)
        return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 6)
    hz = root_hz * (2 if kind == "na" else 2.5)
    return (np.sin(2 * np.pi * hz * t) + 0.4 * np.sin(2 * np.pi * hz * 2.76 * t)) * np.exp(-t * (14 if kind == "na" else 22))


def _tone(hz: float, sec: float, kind: str, rng) -> np.ndarray:
    t = _t(sec)
    if kind == "keys":  # soft electric-piano: sine + bell partial, quick decay
        return (np.sin(2 * np.pi * hz * t) + 0.25 * np.sin(2 * np.pi * hz * 3 * t) * np.exp(-t * 6)) * np.exp(-t * 1.6)
    if kind == "strings":  # slow-attack detuned saws
        saw = sum(((hz * (1 + d) * t) % 1.0) * 2 - 1 for d in (-0.003, 0.0, 0.004)) / 3
        return saw * np.minimum(1, t / 0.6)
    if kind == "stabs":
        saw = ((hz * t) % 1.0) * 2 - 1
        return saw * np.exp(-t * 9)
    if kind == "pluck":
        return np.sin(2 * np.pi * hz * t) * np.exp(-t * 7) + 0.3 * np.sin(2 * np.pi * hz * 2 * t) * np.exp(-t * 12)
    return np.sin(2 * np.pi * hz * t) * np.exp(-t * 2.5)


def _chord(scale: list[int], root: int, degree: int) -> list[int]:
    s = scale
    return [root + s[(degree + k) % 7] + 12 * ((degree + k) // 7) for k in (0, 2, 4, 6)]


def arrange(genre: str, seconds: float, seed: int = 7, drop_at: float = 0.0, stops: list[float] | None = None,
            bpm: float | None = None) -> tuple[np.ndarray, dict]:
    """Stereo float32 bed of `seconds`, its DROP landing on `drop_at` (seconds into the video)."""
    (lo, hi), scale, progs, swing, inst = GENRES.get(genre, GENRES["lofi"])
    rng = np.random.default_rng(seed)
    bpm = float(bpm or rng.integers(lo, hi + 1))
    rng = np.random.default_rng(seed + 1)
    beat = 60.0 / bpm
    bar = beat * 4
    root = 45 + int(rng.integers(0, 7))  # A2..D#3
    prog = progs[int(rng.integers(0, len(progs)))]
    lead_t = beat  # pre-roll so the drop sits exactly on a bar line at drop_at
    intro_bars = max(1, int(np.ceil(max(drop_at, 0.01) / bar))) if drop_at > 0.4 else 0
    offset = intro_bars * bar - drop_at if intro_bars else 0.0  # music time = video time + offset
    total = int((seconds + offset + lead_t + 2) * SR)
    L, R, drums = np.zeros(total), np.zeros(total), np.zeros(total)
    sub = np.zeros(total)
    n_bars = int(np.ceil((seconds + offset + 1) / bar))

    def at(sec: float) -> int:
        return int(sec * SR)

    for b in range(n_bars):
        b0 = b * bar
        intro = b < intro_bars
        breakdown = not intro and b > intro_bars and (b - intro_bars) % 8 == 7
        degree = prog[b % len(prog)]
        notes = _chord(scale, root, degree)
        # harmony
        if inst in ("keys", "strings", "stabs", "arp", "tabla", "808"):
            pad_kind = "strings" if inst in ("strings", "808", "tabla") else "keys"
            for k, nt in enumerate(notes[:3 if inst != "keys" else 4]):
                tone = _tone(_midi(nt + 12), bar, pad_kind, rng) * (0.045 if pad_kind == "strings" else 0.06)
                _hit(L, at(b0), tone * (1.0 - 0.15 * k), 1.0)
                _hit(R, at(b0), tone * (0.85 + 0.15 * k), 1.0)
        if inst == "stabs" and not intro:
            for k in (1.5, 2.5, 3.5):
                for nt in notes[:3]:
                    s = _tone(_midi(nt + 12), beat * 0.4, "stabs", rng) * 0.05
                    _hit(L, at(b0 + k * beat), s, 1.0); _hit(R, at(b0 + k * beat), s, 1.0)
        if inst in ("arp", "tabla") and not intro:
            seq = [0, 1, 2, 1, 3, 2, 1, 2] if inst == "arp" else [0, 2, 1, 2, 3, 2, 0, 1]
            for k in range(8):
                nt = notes[seq[k] % 4] + 24
                s = _tone(_midi(nt), beat * 0.5, "pluck", rng) * (0.06 if inst == "arp" else 0.05)
                pan = 0.5 + 0.35 * np.sin(k + b)
                _hit(L, at(b0 + k * beat / 2), s * (1 - pan), 1.0); _hit(R, at(b0 + k * beat / 2), s * pan, 1.0)
        if inst == "tabla":  # tanpura-like drone: root + fifth, always on
            dr = _tone(_midi(root), bar, "strings", rng) + 0.6 * _tone(_midi(root + 7), bar, "strings", rng)
            _hit(L, at(b0), dr * 0.03, 1.0); _hit(R, at(b0), dr * 0.03, 1.0)
        # bass
        bass_hz = _midi(notes[0] - 12)
        if inst == "808" and not intro:
            for k in (0, 2.5):
                tt = _t(beat * 1.4)
                glide = bass_hz * (1 + 0.5 * np.exp(-tt * 20))
                _hit(sub, at(b0 + k * beat), np.tanh(1.6 * np.sin(2 * np.pi * np.cumsum(glide) / SR)) * np.exp(-tt * 1.2), 0.32)
        elif not intro:
            for k in (range(4) if inst == "stabs" else (0, 2)):
                tt = _t(beat * (0.9 if inst == "stabs" else 1.8))
                _hit(sub, at(b0 + k * beat + (beat / 2 if inst == "stabs" else 0)),
                     np.sin(2 * np.pi * bass_hz * tt) * np.exp(-tt * 2.2), 0.2)
        # drums
        if breakdown:
            continue
        sw = lambda k: (k + (swing if k % 1 else 0.0)) * beat  # noqa: E731 - swing the off-beats
        g = 0.35 if intro else 1.0
        if inst == "keys":
            for k in (0, 2.5):
                _hit(drums, at(b0 + sw(k)), _kick(0.6), 0.28 * g)
            for k in (1, 3):
                _hit(drums, at(b0 + k * beat), _snare(rng), 0.13 * g)
            for k in np.arange(0, 4, 0.5):
                _hit(drums, at(b0 + sw(k)), _hat(rng), 0.03 * g)
        elif inst == "808":
            _hit(drums, at(b0), _kick(1.2), 0.3 * g)
            _hit(drums, at(b0 + 2 * beat), _clap(rng), 0.22 * g)
            roll = b % 2 == 1
            for k in np.arange(0, 4, 0.25 if not roll else 0.125):
                if roll and k < 3:
                    continue
                _hit(drums, at(b0 + k * beat), _hat(rng), 0.035 * g)
            for k in np.arange(0, 3, 0.5):
                _hit(drums, at(b0 + k * beat), _hat(rng), 0.035 * g)
        elif inst == "stabs":
            for k in range(4):
                _hit(drums, at(b0 + k * beat), _kick(1.0), 0.32 * g)
                _hit(drums, at(b0 + (k + 0.5) * beat), _hat(rng, open_=True), 0.03 * g)
            for k in (1, 3):
                _hit(drums, at(b0 + k * beat), _clap(rng), 0.18 * g)
        elif inst == "strings":
            for k in (0, 2.5, 3):
                tt = _t(0.6)
                tom = np.sin(2 * np.pi * (70 + 40 * np.exp(-tt * 15)) * tt) * np.exp(-tt * 5)
                _hit(drums, at(b0 + k * beat), tom, 0.3 * g)
        elif inst == "tabla":
            theka = [("ge", 0), ("na", 0.5), ("tin", 1), ("na", 1.5), ("ge", 2), ("na", 2.5), ("tin", 3), ("na", 3.25), ("na", 3.5)]
            for kind, k in theka:
                _hit(drums, at(b0 + sw(k)), _tabla(kind, _midi(root + 24)), (0.3 if kind == "ge" else 0.12) * g)
        else:  # pop
            for k in (0, 2, 2.5):
                _hit(drums, at(b0 + k * beat), _kick(1.0), 0.3 * g)
            for k in (1, 3):
                _hit(drums, at(b0 + k * beat), _snare(rng), 0.15 * g)
            for k in np.arange(0, 4, 0.5):
                _hit(drums, at(b0 + k * beat), _hat(rng), 0.03 * g)
    # sidechain pump on the harmony for house / pop / trap
    if inst in ("stabs", "arp", "808"):
        tt = (np.arange(total) / SR) % beat
        pump = 1 - 0.55 * np.exp(-tt / 0.09)
        L *= pump; R *= pump
    if inst == "keys":  # lo-fi: dusty top end + vinyl crackle
        L, R = _lp_fast(L, 4200), _lp_fast(R, 4200)
        crackle = (rng.random(total) > 0.9993) * rng.standard_normal(total) * 0.12
        L += crackle; R += crackle
    # riser into the drop + impact on it
    if intro_bars:
        drop_i = at(intro_bars * bar)
        rise_n = min(drop_i, at(bar))
        tt = np.arange(rise_n) / SR
        noise = np.diff(rng.standard_normal(rise_n), prepend=0)
        riser = _lp_fast(noise, 2000) * (tt / max(tt[-1], 1e-6)) ** 2 * 0.25
        _hit(L, drop_i - rise_n, riser, 1.0); _hit(R, drop_i - rise_n, riser, 1.0)
        boom = _kick(1.6) * 0.5
        _hit(drums, drop_i, boom, 1.0)
        # intro: harmony filtered (the "underwater" build)
        L[:drop_i] = _lp_fast(L[:drop_i], 900); R[:drop_i] = _lp_fast(R[:drop_i], 900)
    mix_l = L + sub + drums
    mix_r = R + sub + drums
    start = at(offset)
    stereo = np.stack([mix_l, mix_r], axis=1)[start:start + int(seconds * SR)]
    # music stops for a beat right before each punch-word (the classic "drop out" emphasis)
    for s_t in stops or []:
        a, b = int((s_t - 0.32) * SR), int((s_t + 0.02) * SR)
        if 0 < a < b < len(stereo):
            ramp = min(int(0.03 * SR), (b - a) // 3)
            env = np.ones(b - a); env[:ramp] = np.linspace(1, 0.08, ramp); env[ramp:] = 0.08
            stereo[a:b] *= env[:, None]
    fade_out = min(int(1.5 * SR), len(stereo) // 3)
    stereo[-fade_out:] *= np.linspace(1, 0, fade_out)[:, None]
    stereo = np.tanh(stereo * 1.4) / 1.4  # glue
    peak = np.max(np.abs(stereo)) or 1.0
    return (stereo / peak * 0.7).astype(np.float32), {"genre": genre, "bpm": bpm, "drop_at": drop_at if intro_bars else 0.0}
