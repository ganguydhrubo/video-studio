"""J-cuts and L-cuts: at a join between two clips or two moments, the sound leads or trails the picture.

A human editor rarely cuts sound and picture at the same instant when the film moves to another clip or another
moment. Either the next clip's sound starts a little before its picture (a J-cut), or the last clip's sound runs on a
little under the new picture (an L-cut). It makes a join feel smooth instead of abrupt.

How Takebloom does it (our own simple rules, written down so they can be checked):

Which joins
- Only joins that change place or moment: the two pieces come from different clips you dropped, from different camera
  shots (the footage check in sense.json), or from different lines that are more than 2 s apart (or out of order) in
  your footage. Pieces inside one line, and lines that simply follow each other, keep a plain cut.
- Never in a music montage cut on the beat, and never when you ask for hard cuts (--hard-cuts, "hard cuts only").

J or L
- L-cut when the outgoing clip has sound running on after the cut (music, room sound, the end of a breath: the next
  0.4 s of that clip is louder than -45 dBFS) and no word is spoken there.
- Otherwise a J-cut when the incoming line starts speaking straight away (its first word within 0.3 s of the cut):
  the incoming clip's own sound (its room, the breath before the line) starts under the outgoing picture.
- When neither is true (quiet outgoing clip, incoming piece not speaking at once), an L-cut is tried first.
- If the preferred kind cannot be done, the other kind is tried. If neither can, the plain cut stays.

How much, and what stays exact
- 0.4 s, or as little as 0.3 s when that is all the clean extra footage there is; never more than 40% of the piece
  it plays under. Less than 0.3 s of clean extra footage means no J or L cut at that join.
- The extra sound comes only from real extra footage just outside the kept piece, inside the same clip, with no
  spoken word in it (so a dropped word or retake never comes back) and not overlapping any kept piece.
- The voice track of the plain cut is not changed at all; the extra sound is added on top. So every kept word plays
  at exactly the same moment as before, in sync with its picture, and the film's length does not change.
- No clicks: the extra sound fades in and out with short soft fades, and where it meets the piece it came from it
  takes over the piece's own 12-15 ms fade exactly, so the joined sound is continuous.
- The voice track itself is laid sample by sample, each piece's sound exactly under its own picture (plain_voice).
  Before 1.9.0 the pieces' sound was joined end to end, which ran a few milliseconds early per piece (each piece's
  picture is a whole number of frames, a little longer than its sound): 30 to 95 ms early in a 9-piece test edit.
- If the extra sound would be silent (below -60 dBFS, for example cleaned voice with no room sound), that join is
  left as a plain cut, because an inaudible J-cut is not a J-cut.
"""

from __future__ import annotations

import math
import wave
from pathlib import Path

from video_studio.util import console

AMOUNT, SHORTEST = 0.4, 0.3     # seconds the sound leads or trails the picture
SHARE = 0.4                     # never more than this share of the piece the extra sound plays under
RAMP_IN, RAMP_OUT = 0.05, 0.12  # soft start of a J-cut's sound, soft end of an L-cut's sound
FADE_IN, FADE_OUT = 0.012, 0.015  # the fades every cut piece already has (editor.cut)
WORD_GAP = 0.03                 # keep this far from any spoken word
TAIL_DB = -45.0                 # the outgoing clip's next 0.4 s is louder than this -> it has sound to carry over
SILENT_DB = -60.0               # extra sound quieter than this would not be heard
SPEECH_START = 0.3              # the incoming line speaks within this of its cut -> J-cut
SCENE_JUMP = 2.0                # lines further apart than this in the footage are different moments


# ---------------------------------------------------------------- planning (pure, unit-tested)

def clip_bounds(ingest: dict) -> list[tuple[float, float]]:
    """Where each dropped clip sits inside the joined source (ingest joins them in order)."""
    out, t = [], 0.0
    for c in ingest.get("clips") or []:
        out.append((t, t + float(c["duration"])))
        t += float(c["duration"])
    return out or [(0.0, float(ingest.get("duration") or 1e9))]


def _index(t: float, spans: list[tuple[float, float]]) -> int | None:
    for i, (a, b) in enumerate(spans):
        if a - 1e-6 <= t < b + 1e-6:
            return i
    return None


def join_kind(left: dict, right: dict, bounds: list, shots: list) -> str | None:
    """Why this join changes place or moment ('new clip', 'new shot', 'new moment'), or None for a plain join."""
    if left["utt"] == right["utt"]:
        return None
    a, b = left["src_end"] - 1e-3, right["src_start"] + 1e-3
    if _index(a, bounds) != _index(b, bounds):
        return "new clip"
    spans = [(float(s["start"]), float(s["end"])) for s in shots or [] if "start" in s and "end" in s]
    if spans and _index(a, spans) is not None and _index(a, spans) != _index(b, spans):
        return "new shot"
    jump = right["src_start"] - left["src_end"]
    if jump > SCENE_JUMP or jump < -0.05:
        return "new moment"
    return None


def free_before(t: float, lo: float, words: list[dict], kept: list[tuple[float, float]]) -> float:
    """Seconds of clean extra footage just before source time t: no word, no kept piece, inside the clip."""
    limit = lo
    for w in words:
        if w["start"] < t - 1e-6:
            limit = max(limit, w["end"] + WORD_GAP)
    for a, b in kept:
        if a < t - 1e-6:
            limit = max(limit, b)
    return max(0.0, t - limit)


def free_after(t: float, hi: float, words: list[dict], kept: list[tuple[float, float]]) -> float:
    """Seconds of clean extra footage just after source time t."""
    limit = hi
    for w in words:
        if w["end"] > t + 1e-6:
            limit = min(limit, w["start"] - WORD_GAP)
    for a, b in kept:
        if b > t + 1e-6:
            limit = min(limit, a)
    return max(0.0, limit - t)


def plan(pieces: list[dict], words: list[dict], bounds: list, shots: list, loudness) -> tuple[list[dict], list[str]]:
    """pieces: the cut's pieces in output order (src_start, src_end, out_start, utt, words in output time).
    words: every transcript word in source time. loudness(t0, t1) -> dBFS of the source sound there.
    Returns (joins to make, plain-language notes on joins left plain)."""
    kept = [(pc["src_start"], pc["src_end"]) for pc in pieces]
    made, notes = [], []
    for i, (left, right) in enumerate(zip(pieces, pieces[1:])):
        why = join_kind(left, right, bounds, shots)
        if not why:
            continue
        e, s = left["src_end"], right["src_start"]
        cut_at = right["out_start"]
        out_clip = bounds[_index(e - 1e-3, bounds) or 0]
        in_clip = bounds[_index(s + 1e-3, bounds) or 0]
        l_room = min(AMOUNT, free_after(e, out_clip[1], words, kept), SHARE * (right["src_end"] - s))
        j_room = min(AMOUNT, free_before(s, in_clip[0], words, kept), SHARE * (e - left["src_start"]))
        tail_db = loudness(e, e + l_room) if l_room > 0 else -math.inf
        lead_db = loudness(s - j_room, s) if j_room > 0 else -math.inf
        first = (right["words"][0]["start"] - right["out_start"]) if right.get("words") else None
        speaks = first is not None and first <= SPEECH_START
        order = ["L", "J"] if tail_db > TAIL_DB or not speaks else ["J", "L"]
        options = {"L": (l_room, tail_db), "J": (j_room, lead_db)}
        chosen = None
        for kind in order:
            room, db = options[kind]
            if room >= SHORTEST - 1e-6 and db > SILENT_DB:
                chosen = kind
                break
        if not chosen:
            if max(l_room, j_room) < SHORTEST - 1e-6:
                notes.append(f"{cut_at:.1f}s: plain cut, less than {SHORTEST:g} s of clean extra footage on either side")
            else:
                notes.append(f"{cut_at:.1f}s: plain cut, the extra footage there is silent")
            continue
        room, db = options[chosen]
        amount = round(min(AMOUNT, room), 3)
        rule = ("the outgoing clip's sound runs on" if chosen == "L" and tail_db > TAIL_DB else
                "the incoming line speaks straight away" if chosen == "J" and speaks else
                "the other kind had no clean footage" if chosen != order[0] else
                "the incoming line does not speak at once, so the outgoing sound carries on")
        made.append({"join": i, "kind": chosen, "amount": amount, "cut_at": round(cut_at, 3), "why": why, "rule": rule,
                     "level_db": round(db, 1),
                     # source time and output time where the extra sound's source is anchored
                     "src": e if chosen == "L" else s,
                     "out": round(left["out_start"] + (e - left["src_start"]), 4) if chosen == "L" else right["out_start"]})
    return made, notes


# ---------------------------------------------------------------- sound (NumPy, sample-accurate)

def _read(path: Path, t0: float | None = None, t1: float | None = None):
    import numpy as np

    with wave.open(str(path), "rb") as wf:
        sr, ch, n = wf.getframerate(), wf.getnchannels(), wf.getnframes()
        a = 0 if t0 is None else max(0, min(n, int(round(t0 * sr))))
        b = n if t1 is None else max(a, min(n, int(round(t1 * sr))))
        wf.setpos(a)
        raw = wf.readframes(b - a)
        width = wf.getsampwidth()
    if width != 2:
        raise ValueError(f"{path.name}: expected 16-bit sound")
    pcm = np.frombuffer(raw, dtype="<i2").astype("float32") / 32768.0
    if ch > 1:
        pcm = pcm.reshape(-1, ch).mean(axis=1)
    return pcm, sr


def _write(path: Path, pcm, sr: int) -> None:
    import numpy as np

    data = (np.clip(pcm, -1.0, 32767 / 32768) * 32768.0).round().astype("<i2").tobytes()
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(data)


def level_db(path: Path, t0: float, t1: float) -> float:
    import numpy as np

    pcm, _ = _read(path, max(0.0, t0), t1)
    if pcm.size == 0:
        return -math.inf
    rms = float(np.sqrt(np.mean(pcm.astype("float64") ** 2)))
    return 20 * math.log10(rms) if rms > 0 else -math.inf


def _gain(kind: str, amount: float, sr: int):
    """The extra sound's gain curve. J: [soft start | full | the piece's own fade-in, mirrored]; L: the mirror."""
    import numpy as np

    n_main, n_edge = int(round(amount * sr)), int(round((FADE_IN if kind == "J" else FADE_OUT) * sr))
    g = np.ones(n_main + n_edge, dtype="float32")
    edge = (np.arange(n_edge, dtype="float32") + 0.5) / max(1, n_edge)   # the piece's linear fade, sample by sample
    if kind == "J":
        r = min(int(RAMP_IN * sr), n_main // 2)
        g[:r] = 0.5 - 0.5 * np.cos(np.pi * (np.arange(r) + 0.5) / r)
        g[n_main:] = 1.0 - edge           # the piece fades in from here, so together they stay at full level
    else:
        r = min(int(RAMP_OUT * sr), n_main // 2)
        g[:n_edge] = edge                 # the piece fades out here (1 -> 0), the extra sound takes over (0 -> 1)
        g[len(g) - r:] = 0.5 + 0.5 * np.cos(np.pi * (np.arange(r) + 0.5) / r)
    return g


def plain_voice(pieces: list[dict], source_wav: Path, total: float, dest: Path) -> Path:
    """The cut's voice track, built sample by sample: each piece's sound placed exactly where its picture starts
    (out_start), with the same 12 ms / 15 ms fades the cut pieces have. Joining the pieces' own sound end to end
    (the cut file) lets the sound run early by the few milliseconds each piece's picture is longer than its sound;
    over a film that adds up (30 to 95 ms in a 9-piece test edit), so the voice is placed here instead."""
    import numpy as np

    sr = 48000
    out = np.zeros(int(round(total * sr)), dtype="float32")
    for pc in pieces:
        seg, ssr = _read(source_wav, pc["src_start"], pc["src_end"])
        if ssr != sr:
            raise ValueError("the cleaned voice is not 48 kHz")
        seg = seg.copy()
        fi, fo = min(len(seg), int(FADE_IN * sr)), min(len(seg), int(FADE_OUT * sr))
        seg[:fi] *= (np.arange(fi, dtype="float32") + 0.5) / max(1, fi)
        if fo:
            seg[-fo:] *= 1.0 - (np.arange(fo, dtype="float32") + 0.5) / fo
        k0 = int(round(pc["out_start"] * sr))
        n = max(0, min(len(seg), len(out) - k0))
        out[k0:k0 + n] += seg[:n]
    _write(dest, out, sr)
    return dest


def render(base_wav: Path, source_wav: Path, joins: list[dict], dest: Path) -> dict:
    """Add each join's extra sound on top of the plain-cut voice track. Returns sample facts for the log and tests."""
    import numpy as np

    base, sr = _read(base_wav)
    out = base.copy()
    facts = []
    for j in joins:
        a = j["amount"]
        if j["kind"] == "J":
            s0, s1, o0 = j["src"] - a, j["src"] + FADE_IN, j["out"] - a
        else:
            s0, s1, o0 = j["src"] - FADE_OUT, j["src"] + a, j["out"] - FADE_OUT
        seg, ssr = _read(source_wav, s0, s1)
        if ssr != sr:
            raise ValueError("the cut and the source have different sample rates")
        g = _gain(j["kind"], a, sr)
        n = min(len(seg), len(g))
        k0 = int(round(o0 * sr))
        lo, hi = max(0, k0), min(len(out), k0 + n)
        if hi <= lo:
            continue
        out[lo:hi] += seg[lo - k0:hi - k0] * g[lo - k0:hi - k0]
        facts.append({"kind": j["kind"], "from_sample": lo, "to_sample": hi})
    _write(dest, out, sr)
    return {"samples": int(len(out)), "rate": sr, "added": facts}


# ---------------------------------------------------------------- the editor's hook

def apply(proj, p: dict, words: list[dict], out_pieces: list[dict], audio_src: Path, cut_file: Path) -> dict:
    """The cut's voice track for the render: every piece placed exactly under its picture, plus the J/L-cuts.
    Returns manifest fields {"voice": wav, "jl": [...]}; a music montage keeps the cut file's own sound."""
    from video_studio.engines import ff

    opts = proj.data.get("edit_options") or {}
    if p.get("mode") == "montage":
        return {"jl": [], "jl_off": "music montage cut on the beat"}
    audio_dir = proj.build / "audio"
    audio_dir.mkdir(exist_ok=True)
    plain = plain_voice(out_pieces, audio_src, ff.duration(cut_file), audio_dir / "voice_cut_plain.wav")
    if opts.get("hard_cuts"):
        console.info("Joins: hard cuts only, as you asked (no J-cuts or L-cuts)")
        return {"voice": str(plain), "voice_plain": str(plain), "jl": [], "jl_off": "you asked for hard cuts"}
    bounds = clip_bounds(proj.read("ingest.json") or {})
    shots = (proj.read("sense.json") or {}).get("shots") or []
    joins, notes = plan(out_pieces, words, bounds, shots, lambda a, b: level_db(audio_src, a, b))
    for n in notes[:4]:
        console.info(f"    J/L-cut {n}")
    if not joins:
        if notes:
            console.info(f"Joins: {len(notes)} change(s) of clip or moment, all left as plain cuts (see above)")
        return {"voice": str(plain), "voice_plain": str(plain), "jl": [], "jl_notes": notes}
    dest = audio_dir / "voice_jl.wav"
    render(plain, audio_src, joins, dest)
    for j in joins:
        console.info(f"    {j['kind']}-cut at {j['cut_at']:.1f}s ({j['why']}): sound {'leads' if j['kind'] == 'J' else 'trails'} "
                     f"the picture by {j['amount']:.2f} s, because {j['rule']}")
    console.ok(f"Joins: {len(joins)} J/L-cut(s) for smoother changes of clip or moment; every word stays in sync")
    return {"voice": str(dest), "voice_plain": str(plain), "jl": joins, "jl_notes": notes}
