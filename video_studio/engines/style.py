"""Style engine: plans a layered, motion-designed edit and runs helpers/style_helper.py.

The plan is built from the edit itself (cut points, word timings, the AI director's hook / slam words /
brands) so every video gets edit-specific motion instead of one preset applied everywhere.

Intensity:
  clean - camera angle changes + hook slam + gentle grade (talking-head, corporate)
  bold  - + transitions on most cuts, emphasis slams behind the speaker, hits, chips, 3D logos
  viral - + a transition on almost every cut, more slams and hits (fast reels)
"""

from __future__ import annotations

import json
import random
import re
import subprocess
from pathlib import Path

from video_studio.engines import ff, ml
from video_studio.looks import LOOKS
from video_studio.util import ToolError, console, resource_path

INTENSITY = {
    "clean": {"trans_every": 9.0, "slams": 1, "hits_on_cuts": False, "chips": 2, "grain": 0.016, "bloom": 0.22},
    "bold": {"trans_every": 1.7, "slams": 4, "hits_on_cuts": False, "chips": 3, "grain": 0.022, "bloom": 0.32},
    "viral": {"trans_every": 1.2, "slams": 6, "hits_on_cuts": False, "chips": 4, "grain": 0.026, "bloom": 0.4},
}
TRANSITIONS = ["whip", "zoom", "glitch", "flash", "whip", "invert", "blurscale", "zoom"]
SHOT_KINDS = ["close", "wide", "dutch", "push", "close", "pull", "wide"]


def guess_intensity(brief: str) -> str:
    low = brief.lower()
    if any(k in low for k in ("calm", "minimal", "clean", "corporate", "subtle", "simple", "professional")):
        return "clean"
    if any(k in low for k in ("viral", "energetic", "punchy", "hype", "crazy", "fast", "aggressive", "top 1%")):
        return "viral"
    return "bold"


def _darken(hex_value: str, k: float) -> str:
    h = hex_value.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return "#%02X%02X%02X" % (int(r * k), int(g * k), int(b * k))


def palette(look: str) -> dict:
    pal = LOOKS.get(look) or LOOKS["midnight"]
    acc = pal["accent"]
    bg = pal["bg"] if isinstance(pal["bg"], (list, tuple)) else [pal["bg"], pal["bg"]]
    return {"accent": acc, "accent_dark": _darken(acc, 0.42), "rim": pal.get("accent2", acc), "ink": "#FFFFFF",
            "chip_ink": "#0B0B10", "bg0": bg[0], "bg1": bg[-1]}


def _norm(w: str) -> str:
    return re.sub(r"[^\w']", "", w.lower())


def _similar(a: str, b: str) -> float:
    from difflib import SequenceMatcher

    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def _shot_bounds(cuts: list[float], words: list[dict], total: float, lo: float = 1.2, hi: float = 2.6) -> list[float]:
    """Cut points plus extra angle changes inside long pieces, placed in the gaps between words."""
    bounds = [0.0] + list(cuts) + [total]
    out = [0.0]
    # any word boundary works for an angle change on a continuous take (Whisper often reports zero gaps)
    gaps = [(words[i]["end"] + words[i + 1]["start"]) / 2 for i in range(len(words) - 1)]
    for a, b in zip(bounds, bounds[1:]):
        cur = a
        while b - cur > hi:
            cands = [g for g in gaps if cur + lo <= g <= min(cur + hi, b - lo)]
            nxt = min(cands, key=lambda g: abs(g - (cur + 1.8))) if cands else None
            if nxt is None:
                break
            out.append(round(nxt, 3))
            cur = nxt
        if b < total:
            out.append(round(b, 3))
    return sorted(set(out)) + [total]


QUESTION = {"kya", "kaise", "kyun", "kyon", "kyu", "kab", "kaun", "kahan", "kitna", "kitne", "why", "how", "what", "when",
            "who", "where", "which", "kya?", "kaisa"}
CONTRAST = {"lekin", "par", "magar", "but", "however", "nahi", "nahin", "not", "never", "instead", "balki", "phir bhi", "ulta"}
NEW_POINT = {"pehla", "pahla", "pehle", "doosra", "dusra", "teesra", "tisra", "first", "second", "third", "next", "finally",
             "step", "number", "aakhir", "last"}


def _meaning(text: str, info: dict | None) -> str:
    t = _norm(text)
    f = (info or {}).get("features", {})
    if t in QUESTION or text.strip().endswith("?") or f.get("question"):
        return "question"
    if t in CONTRAST or f.get("negation"):
        return "contrast"
    if t in NEW_POINT or f.get("number") or f.get("cue"):
        return "point"
    return ""


def _stress(info: dict | None) -> float:
    """How hard the voice hit this word (pitch, loudness and stretch vs your own baseline)."""
    f = (info or {}).get("features", {})
    return 0.4 * f.get("f0", 0) + 0.35 * f.get("db", 0) + 0.25 * f.get("stretch", 0) + 0.3 * (info or {}).get("score", 0)


MEANING_TRANSITION = {"question": ("zoom", "zooms into the question"), "contrast": ("whip", "whips on the turn"),
                      "point": ("flash", "flashes on a new point")}
MEANING_SHOT = {"question": ("push", "leans in for the question"), "contrast": ("dutch", "tilts on the contrast"),
                "point": ("wide", "goes wide for a new point")}


def hits_near(hits: list[float], t: float, gap: float = 3.0) -> bool:
    return any(abs(h - t) < gap for h in hits)


def build_plan(pieces: list[dict], words: list[dict], total: float, director: dict, intensity: str, seed: int = 7,
               display_words: list[dict] | None = None) -> dict:
    """pieces: [{out_start}] on the output timeline; words: output-timed transcript words (as spoken);
    display_words: the same words in the caption script (e.g. Roman), index-aligned with `words`."""
    display_words = display_words if display_words and len(display_words) == len(words) else words
    cfg = dict(INTENSITY.get(intensity, INTENSITY["bold"]))
    sp = director.get("style_params") or {}
    if sp.get("trans_every"):
        cfg["trans_every"] = sp["trans_every"]
    rnd = random.Random(seed)
    cuts = [p["out_start"] for p in pieces[1:]]
    # shots: a new virtual camera angle on every cut hides jump cuts like a two-camera shoot
    info = director.get("word_info") or [None] * len(words)
    info = info if len(info) == len(words) else [None] * len(words)
    reasons: list[str] = []

    def word_after(t: float) -> int | None:
        return next((i for i, w in enumerate(words) if w["start"] >= t - 0.06), None)

    stress = [_stress(x) for x in info]
    loud = sorted(stress, reverse=True)
    peak_cut = loud[max(0, len(loud) // 6)] if loud else 9.0  # the top ~15% most-stressed words
    shots, prev = [], "push"
    bounds = _shot_bounds(cuts, words, total, sp.get("shot_lo", 1.2), sp.get("shot_hi", 2.6))
    for i in range(len(bounds) - 1):
        a, b = bounds[i], bounds[i + 1]
        idx = word_after(a)
        mean = _meaning(display_words[idx]["text"], info[idx]) if idx is not None and words[idx]["start"] < a + 0.5 else ""
        in_shot = [j for j, w in enumerate(words) if a <= w["start"] < b and info[j] and info[j].get("clean", True)
                   and not info[j].get("features", {}).get("stop")]
        if i == 0:
            kind, why = "push", ""
        elif mean and MEANING_SHOT[mean][0] != prev:
            kind, why = MEANING_SHOT[mean]
            why = f"{why} ('{display_words[idx]['text']}')"
        elif in_shot and max(stress[j] for j in in_shot) >= peak_cut and prev != "close":
            j = max(in_shot, key=lambda j: stress[j])
            kind, why = "close", f"tight shot for the stressed word '{display_words[j]['text']}'"
        else:
            options = [k for k in ("wide", "pull", "close", "push") if k != prev]
            kind, why = options[i % len(options)], ""
        shots.append({"t0": round(a, 3), "t1": round(b, 3), "kind": kind, "why": why})
        if why:
            reasons.append(f"{a:4.1f}s camera {why}")
        prev = kind
    # hook: giant words behind the speaker, flash + hit on frame 1
    hook = (director.get("hook_text") or director.get("title") or "").strip()
    first_end = min(2.4, (cuts[0] if cuts else total) - 0.05, total * 0.4)
    if director.get('hook_seconds'):
        first_end=min(float(director['hook_seconds']),total-.05)
    slams, hits, trans = [], [], [{"t": 0.0, "kind": "flash"}]
    if hook:
        slams.append({"t0": 0.06, "t1": max(1.2, first_end), "text": " ".join(hook.split()[:4]), "style": "accent"})
        hits.append(0.06)
    # transitions on cuts (varied, never the same twice in a row, spaced by intensity)
    last_t, last_kind = 0.0, "flash"
    pool = list(TRANSITIONS)
    for c in cuts:
        if c - last_t < cfg["trans_every"] or c > total - 0.4:
            continue
        idx = word_after(c)
        mean = _meaning(display_words[idx]["text"], info[idx]) if idx is not None else ""
        if mean and MEANING_TRANSITION[mean][0] != last_kind:
            kind, why = MEANING_TRANSITION[mean]
            reasons.append(f"{c:4.1f}s transition {why} ('{display_words[idx]['text']}')")
        else:  # no special meaning: energy decides - a hard glitch after a loud line, a soft blur after a calm one
            energy = max([stress[j] for j, w in enumerate(words) if c - 1.5 <= w["start"] < c] or [0.0])
            soft = [k for k in (["glitch", "invert", "whip"] if energy >= peak_cut else ["blurscale", "zoom", "whip"]) if k != last_kind]
            kind = soft[int(rnd.random() * len(soft))]
        trans.append({"t": round(c, 3), "kind": kind})
        if cfg["hits_on_cuts"] and kind in ("zoom", "glitch"):
            hits.append(round(c, 3))
        last_t, last_kind = c, kind
    # emphasis slams: director-chosen words at the moment they are spoken (fuzzy, either script)
    want = [x for x in director.get("slams") or [] if isinstance(x, dict) and x.get("text")]
    targets = [(_norm(x.get("word") or ""), _norm(x["text"].split()[0]), x["text"]) for x in want]
    targets += [(_norm(e), "", e) for e in director.get("emphasis") or []]
    taken_t = [x["t1"] for x in slams]
    n_front = 0
    for raw_key, disp_key, text in targets:
        if n_front >= cfg["slams"]:
            break
        best, best_score = None, 0.0
        for w, dw in zip(words, display_words):
            score = max(_similar(raw_key, _norm(w["text"])), _similar(disp_key, _norm(dw["text"])))
            if score > best_score:
                best, best_score = w, score
        if not best or best_score < 0.62:
            continue
        t = best["start"]
        if t < (slams[0]["t1"] + 0.6 if slams else 1.2) or any(abs(t - x) < 3.6 for x in taken_t) or t > total - 0.9:
            continue
        slams.append({"t0": round(t - 0.04, 3), "t1": round(min(t + 0.95, total - 0.2), 3), "text": text, "style": "ink"})
        hits.append(round(t - 0.02, 3))
        taken_t.append(t)
        n_front += 1
    slams.sort(key=lambda s: s["t0"])
    # keyword chips from the director's graphics
    chips = []
    for g in (director.get("chips") or [])[: cfg["chips"]]:
        if g.get("t") is not None and 1.0 < g["t"] < total - 1.2 and not any(s["t0"] - 0.3 < g["t"] < s["t1"] + 0.3 for s in slams):
            chips.append({"t0": round(g["t"], 3), "t1": round(min(g["t"] + 1.8, total - 0.2), 3), "text": g["text"][:22]})
    # punch-in zooms on the words you hit hardest with your voice (speech), spaced by intensity
    punches = []
    p_gap = {"viral": 2.2, "bold": 3.4}.get(intensity)
    if p_gap and not sp.get("punch_rate") and any(x for x in info):
        busy = [x["t"] for x in trans] + [s["t0"] for s in slams]
        order = sorted((j for j in range(len(words)) if info[j] and info[j].get("clean", True)
                        and not (info[j].get("features", {}).get("stop"))), key=lambda j: -stress[j])
        for j in order:
            t = words[j]["start"]
            if stress[j] < peak_cut * 0.8 or t < 1.0 or t > total - 0.5 or any(abs(t - b) < 0.5 for b in busy) \
                    or any(abs(t - q) < p_gap for q in punches):
                continue
            punches.append(round(t, 3))
            reasons.append(f"{t:4.1f}s punch-in: you stressed '{display_words[j]['text']}'")
            if intensity == "viral" and stress[j] >= loud[0] * 0.9 and not hits_near(hits, t):
                hits.append(round(t, 3))
                reasons.append(f"{t:4.1f}s shake + RGB hit: strongest word in the video")
        punches.sort()
    if sp.get("punch_rate"):
        gap = max(0.9, 60.0 / sp["punch_rate"])
        beat = director.get("beat")
        cands = [i * beat for i in range(1, int(total / beat))] if beat else [w["start"] for w in words]
        busy = [x["t"] for x in trans] + [s["t0"] for s in slams] + hits
        for c in cands:
            if c < 0.8 or c > total - 0.4 or any(abs(c - b) < 0.35 for b in busy) or (punches and c - punches[-1] < gap):
                continue
            punches.append(round(c, 3))
    reasons.sort(key=lambda r: float(r.split("s", 1)[0]))
    return {"shots": shots, "transitions": trans, "hits": sorted(set(hits)), "punches": punches, "slams": slams, "chips": chips, "logos": [],
            "reasons": reasons,
            "grain": cfg["grain"], "bloom": cfg["bloom"], "intensity": intensity, "grade_ref": sp.get("grade_ref")}


def sfx_events(plan: dict) -> list[tuple[str, float, float]]:
    ev = []
    for tr in plan["transitions"]:
        if tr["t"] > 0.05:
            ev.append(("whoosh", max(0.0, tr["t"] - 0.18), 0.22))
    for h in plan["hits"]:
        ev.append(("hit", max(0.0, h - 0.01), 0.3))
    for s in plan["slams"]:
        ev.append(("riser" if s["style"] == "accent" else "pop", max(0.0, s["t0"] - (0.0 if s["style"] != "accent" else 0.0)), 0.22))
    for c in plan["chips"]:
        ev.append(("pop", c["t0"], 0.3))
    for lg in plan["logos"]:
        ev.append(("ding", lg["t0"], 0.25))
    return ev


def matte_model() -> Path | None:
    p = ml.models_dir() / "mediapipe" / "selfie_multiclass.tflite"
    return p if p.exists() else None


def _parallelism() -> dict:
    """Frame-range workers: ~1.3 GB each, 3 threads each; leave room for the desktop."""
    from video_studio import resources

    cores = resources.cpu_count()
    free_gb = resources.memory().available_gb
    workers = max(1, min(6, (cores - 2) // 3, int((free_gb - 2) / 1.3)))
    return {"workers": workers, "enc_threads": 2 if workers > 1 else 0}


def render(cut: Path, dest: Path, plan: dict, w: int, h: int, fps: int, duration: float, look: str, quality: str, work: Path) -> Path:
    if not ml.available("cv2"):
        raise ToolError("The style engine needs OpenCV in the system Python.", "video-studio install whisper  (installs the ML extras)")
    model = matte_model()
    if not model:
        console.warn("Person matte model missing; words cannot go behind the speaker (video-studio install style).")
    spec = {**plan, "input": str(cut), "output": str(dest), "width": w, "height": h, "fps": fps, "duration": duration,
            "palette": palette(look), "fonts": str(resource_path("assets", "fonts")), "matte_model": str(model or ""),
            "ffmpeg": str(ff.ffmpeg()), "crf": {"draft": 20, "standard": 16, "high": 14}.get(quality, 16),
            "preset": "veryfast" if quality != "high" else "medium", **_parallelism()}
    work.mkdir(parents=True, exist_ok=True)
    spec_file = work / "style_plan.json"
    spec_file.write_text(json.dumps(spec, indent=1), encoding="utf-8")
    from video_studio import detect

    py = detect.system_python()
    helper = resource_path("helpers", "style_helper.py")
    proc = subprocess.Popen([str(py), "-E", "-P", str(helper), str(spec_file)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", creationflags=0x08000000 | 0x00004000)
    tail = []
    for line in proc.stdout:
        line = line.strip()
        if line.startswith("STYLE_PROGRESS"):
            _, a, b = line.split()
            console.progress("styling", int(a), max(1, int(b)))
        elif line:
            tail.append(line)
            tail = tail[-12:]
    if proc.wait() != 0 or not dest.exists():
        raise ToolError("Style render failed: " + " | ".join(tail)[-500:])
    return dest
