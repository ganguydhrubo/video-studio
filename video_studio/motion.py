"""Motion graphics and 3D inside an automatic edit.

The person asks in the prompt ("3D title that spins", "neon lower third with my name", "confetti at the end") or picks
a template that suggests a few tasteful pieces. Each piece is a small HTML page (GSAP for 2D, three.js for real 3D)
from `motion_templates/`, rendered by HyperFrames (Apache-2.0) as a transparent PNG sequence and laid over the cut by
FFmpeg in the same encode as the captions and grade. Nothing is downloaded at edit time: three.js (MIT) and the
fonts (SIL OFL) ship with Takebloom; GSAP comes from the HyperFrames install, like the rest of the HyperFrames engine.

Placement never covers the film's own giant-text moments (slams), B-roll cutaways or your pictures. Big centre pieces
(titles, kinetic words, the end card) hide the captions while they show; the name tag sits above the captions.
Every rendered piece is cached by a hash of its page, so a re-edit with the same text costs nothing.
If Node.js or HyperFrames is missing, the edit still finishes and says plainly that motion was skipped.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path

from video_studio.util import ToolError, cache_dir, console, resource_path, run

VERSION = 1   # bump when a template changes in a way the page hash would not catch (it normally does)
FPS_CAP = 30  # overlays are drawn at most 30 times a second; FFmpeg holds each frame on 50/60 fps films

# kind: what it is called, default seconds, whether captions pause under it, render scale, 3D, sound
KINDS: dict[str, dict] = {
    "title": {"name": "Animated title", "seconds": 3.0, "quiet": True, "scale": 1.0, "three": False, "sfx": [("whoosh", 0.0, 0.3)]},
    "title3d": {"name": "3D title", "seconds": 3.6, "quiet": True, "scale": 1.0, "three": True,
                "sfx": [("whoosh", 0.0, 0.3), ("hit", 1.15, 0.28)]},
    "lower_third": {"name": "Name tag", "seconds": 3.8, "quiet": False, "scale": 1.0, "three": False, "sfx": [("whoosh", 0.05, 0.22)]},
    "callout": {"name": "Kinetic text", "seconds": 1.6, "quiet": True, "scale": 1.0, "three": False, "sfx": [("pop", 0.02, 0.3)]},
    "end_card": {"name": "End card", "seconds": 3.0, "quiet": True, "scale": 1.0, "three": False, "sfx": [("whoosh", 0.1, 0.25)]},
    "shape3d": {"name": "3D object", "seconds": 2.8, "quiet": False, "scale": 1.0, "three": True, "sfx": [("pop", 0.1, 0.22)]},
    "confetti": {"name": "Confetti", "seconds": 2.8, "quiet": False, "scale": 0.5, "three": False, "sfx": [("pop", 0.05, 0.35), ("pop", 0.2, 0.25)]},
    "particles": {"name": "Sparkles", "seconds": 3.0, "quiet": False, "scale": 0.5, "three": False, "sfx": []},
    "light_leak": {"name": "Light leak", "seconds": 1.3, "quiet": False, "scale": 0.34, "three": False, "sfx": []},
}
TEXT_LAYER = {"title", "title3d", "lower_third", "callout", "end_card"}   # these never overlap each other
CENTRE = {"title", "title3d", "callout", "end_card"}                      # these also keep clear of the style chips

# Templates may add a light touch of motion when the prompt names none. Kept short on purpose: motion should help.
TEMPLATE_MOTION: dict[str, list[str]] = {
    "wedding": ["title", "particles", "end_card"], "travel": ["title", "light_leak"], "product_ad": ["callout", "end_card"],
    "shorts_viral": ["callout"], "event_recap": ["title", "confetti"], "testimonial": ["lower_third"],
    "brand_film": ["title", "end_card"], "documentary": ["title"], "music_video": ["light_leak"],
    "company_update": ["title", "end_card"], "fitness": ["callout"],
}
STYLE_BY_TEMPLATE = {"wedding": "elegant", "brand_film": "elegant", "music_video": "neon", "product_ad": "bold",
                     "shorts_viral": "bold", "fitness": "bold", "travel": "clean", "documentary": "clean"}
STYLE_BY_GRADE = {"cyberpunk": "neon", "golden": "gold", "warm_film": "gold", "sepia": "gold", "vintage": "gold"}

FONTS = {  # family -> file in assets/fonts (all SIL Open Font License, bundled with Takebloom)
    "TB Mukta": "Mukta-ExtraBold.ttf", "TB Barlow": "BarlowCondensed-ExtraBoldItalic.ttf",
    "TB Serif": "InstrumentSerif-Italic.ttf", "TB Barlow Medium": "BarlowCondensed-MediumItalic.ttf",
}
STYLE_FONTS = {"clean": ("TB Mukta", "TB Mukta"), "gold": ("TB Mukta", "TB Mukta"), "neon": ("TB Mukta", "TB Mukta"),
               "bold": ("TB Barlow", "TB Barlow Medium"), "elegant": ("TB Serif", "TB Serif")}


# ------------------------------------------------------------------ what the person wants

def _template_key(proj) -> str:
    opts = proj.data.get("edit_options", {})
    if opts.get("template"):
        return str(opts["template"])
    m = re.search(r"Template - ([^:\n]+):", proj.data.get("brief", ""))
    if m:
        from video_studio import library

        for t in library.TEMPLATES:
            if t["name"].lower() == m.group(1).strip().lower():
                return t["key"]
    return ""


def own_words(brief: str) -> str:
    """The person's prompt without the template's own aim line (that line is the app's words, not theirs)."""
    return "\n".join(line for line in (brief or "").splitlines() if not line.startswith("Template - "))


def default_name() -> str:
    """A name for the name tag when the prompt says "my name" without writing it: the app setting, else the Windows
    user name when it looks like a name."""
    from video_studio import config

    name = str(config.get("profile.name") or "").strip()
    if name:
        return name
    user = os.environ.get("USERNAME", "")
    return user.replace(".", " ").title() if re.fullmatch(r"[A-Za-z][A-Za-z .]{1,30}", user or "") else ""


def wanted(proj) -> dict:
    """The motion pieces for this edit: prompt words first, then a template's light default. {} when none."""
    from video_studio.understand import motion_wishes

    opts = proj.data.get("edit_options", {})
    choice = str(opts.get("graphics") or "auto").strip().lower()
    wish = motion_wishes(own_words(proj.data.get("brief", "")))
    if choice in ("off", "none", "no") or wish["off"]:
        return {}
    kinds = [k for k in wish["kinds"] if k in KINDS]
    if choice not in ("auto", ""):
        kinds = [k for k in choice.split(",") if k in KINDS] or kinds
    template = _template_key(proj)
    source = "prompt"
    if not kinds and template in TEMPLATE_MOTION:
        kinds, source = list(TEMPLATE_MOTION[template]), "template"
    if not kinds:
        return {}
    grade = str(proj.settings.get("grade") or "")
    style = wish["style"] or STYLE_BY_TEMPLATE.get(template) or STYLE_BY_GRADE.get(grade) or "clean"
    name = wish["name"] or (default_name() if "lower_third" in kinds else "")
    return {**wish, "kinds": kinds, "style": style, "name": name, "source": source, "template": template}


# ------------------------------------------------------------------ where each piece goes

def _free(lo: float, hi: float, dur: float, busy: list[tuple[float, float]]) -> float | None:
    """Earliest start in [lo, hi] where [start, start + dur] touches no busy span."""
    cands = sorted({lo, *[b + 0.05 for _a, b in busy if lo <= b + 0.05 <= hi]})
    for s in cands:
        if s > hi + 1e-6:
            break
        if not any(s < b and s + dur > a for a, b in busy):
            return round(s, 3)
    return None


def place(want: dict, total: float, busy: list[tuple[float, float]], chips: list[tuple[float, float]] | None = None,
          cuts: list[float] | None = None, words: list[dict] | None = None) -> tuple[list[dict], list[str]]:
    """Times for each wanted piece. Returns (items, notes about what was left out and why)."""
    items: list[dict] = []
    notes: list[str] = []
    kinds = list(want.get("kinds") or [])
    if total < 3.5:
        return [], ["The film is too short for motion graphics."] if kinds else []
    busy = sorted(busy)
    text: list[tuple[float, float]] = []   # the text layer: titles, name tag, kinetic words, end card
    chips = chips or []

    def fit(kind: str) -> float:
        return round(min(KINDS[kind]["seconds"], max(1.2, total * (0.3 if kind != "end_card" else 0.25))), 3)

    def add(kind: str, start: float, dur: float, **extra) -> dict:
        it = {"kind": kind, "start": round(start, 3), "end": round(min(total, start + dur), 3), **extra}
        items.append(it)
        if kind in TEXT_LAYER:
            text.append((it["start"] - 0.25, it["end"] + 0.25))
        return it

    title = next((k for k in ("title3d", "title") if k in kinds), None)
    title_item = None
    if title:
        dur = fit(title)
        s = _free(0.1, max(0.1, total * 0.35), dur, busy + chips)
        if s is None:
            notes.append(f"{KINDS[title]['name']} left out: the opening is busy with the film's own big text.")
        else:
            title_item = add(title, s, dur)
    end_item = None
    if "end_card" in kinds:
        dur = fit("end_card")
        s = total - dur
        last = max([b for a, b in busy + chips if b > s] or [s])
        if last > s and total - last >= 1.6:
            s, dur = last + 0.05, total - last - 0.05
        if any(s < b and s + dur > a for a, b in busy + chips + text):
            notes.append("End card left out: the last seconds are busy with the film's own big text.")
        else:
            end_item = add("end_card", s, dur)
    if "lower_third" in kinds:
        if not want.get("name") and not want.get("place"):
            notes.append("Name tag left out: no name was given. Write “name: Your Name” in the prompt.")
        else:
            dur = fit("lower_third")
            lo = (title_item["end"] + 0.4) if title_item else 0.8
            hi = (end_item["start"] - dur - 0.3) if end_item else total - dur - 0.3
            s = _free(lo, max(lo, min(hi, total * 0.6)), dur, busy + text)
            if s is None:
                notes.append("Name tag left out: no free moment for it.")
            else:
                add("lower_third", s, dur)
    if "callout" in kinds:
        dur = fit("callout")
        lo = (title_item["end"] + 0.6) if title_item else 1.0
        hi = (end_item["start"] - dur - 0.3) if end_item else total - dur - 0.5
        room = 3 if total > 40 else 2
        done = 0
        for w in want.get("callout_words") or []:
            if done >= room:
                break
            t = float(w["start"])
            if t < lo or t > hi or any(abs(t - x["start"]) < 4.0 for x in items if x["kind"] == "callout"):
                continue
            s = _free(t, min(hi, t + 0.8), dur, busy + chips + text)
            if s is not None:
                add("callout", s, dur, text=w["text"])
                done += 1
        if not done:
            notes.append("Kinetic text left out: no key word landed in a free moment.")
    if "shape3d" in kinds:
        dur = fit("shape3d")
        if end_item:
            add("shape3d", end_item["start"] + 0.2, end_item["end"] - end_item["start"] - 0.2, with_end=True)
        else:
            lo = (title_item["end"] + 0.3) if title_item else 0.6
            s = _free(lo, max(lo, total * 0.7), dur, busy)
            if s is None:
                notes.append("3D object left out: no free moment for it.")
            else:
                add("shape3d", s, dur, with_end=False)
    if "confetti" in kinds:
        dur = fit("confetti")
        if want.get("confetti_at") == "start":
            s = _free(title_item["start"] + 0.4 if title_item else 0.2, total * 0.4, dur, busy)
        elif end_item:   # bursts just after the end card starts and runs to the last frame
            s = min(end_item["start"] + 0.3, total - 1.5)
            dur = min(dur, total - s)
            if any(s < b and s + dur > a for a, b in busy):
                s = _free(max(0.2, total * 0.6), total - dur, dur, busy)
        else:
            s = _free(total - dur, total - dur, dur, busy)
            if s is None:
                s = _free(max(0.2, total * 0.6), total - dur, dur, busy)
        if s is None:
            notes.append("Confetti left out: no free moment for it.")
        else:
            add("confetti", s, dur)
    if "particles" in kinds:
        dur = fit("particles")
        s = _free(title_item["start"] if title_item else 0.3, total * 0.5, dur, busy)
        if s is not None:
            add("particles", s, dur)
    if "light_leak" in kinds:
        dur = fit("light_leak")
        n = 0
        for c in sorted(cuts or []):
            if n >= 3 or c < 1.0 or c > total - 1.5:
                continue
            s = c - dur / 2
            if any(abs(s - x["start"]) < 3.0 for x in items if x["kind"] == "light_leak"):
                continue
            if not any(s < b and s + dur > a for a, b in busy):
                add("light_leak", s, dur)
                n += 1
        if not n:
            s = _free(max(0.5, total * 0.4), total * 0.8, dur, busy)
            if s is not None:
                add("light_leak", s, dur)
    items.sort(key=lambda x: x["start"])
    for i, it in enumerate(items):
        it["seed"] = 11 + i * 7
    return items, notes


def quiet_spans(items: list[dict]) -> list[tuple[float, float]]:
    """Times where captions step aside for a big centre piece."""
    return [(it["start"] - 0.05, it["end"] + 0.05) for it in items if KINDS[it["kind"]]["quiet"]]


def callout_words(pieces: list[dict], emphasis: list[str], skip: set[str] | None = None) -> list[dict]:
    """Where the key (emphasis) words are first said in the film, with the word as spoken."""
    want = [e.lower().strip(".,!?") for e in emphasis if e]
    skip = {s.lower() for s in skip or set()}
    seen: set[str] = set()
    out = []
    for pc in pieces:
        for wd in pc.get("words") or []:
            key = re.sub(r"[^\w']", "", str(wd.get("text", "")).lower())
            if key and key in want and key not in seen and key not in skip and len(key) >= 3:
                seen.add(key)
                out.append({"text": re.sub(r"[^\w'₹$%-]", "", str(wd["text"])), "start": float(wd["start"])})
    return sorted(out, key=lambda x: x["start"])


# ------------------------------------------------------------------ boxes, text and pages

def box(item: dict, w: int, h: int, cap_y: float = 0.72, cap_top: bool = False) -> tuple[int, int, int, int]:
    """(x, y, width, height) of the piece on the film, in pixels."""
    portrait = h > w
    k = item["kind"]
    if k == "title":
        x, y, bw, bh = 0, int(h * (0.22 if portrait else 0.25)), w, int(h * (0.34 if portrait else 0.5))
    elif k == "title3d":
        x, y, bw, bh = 0, int(h * (0.16 if portrait else 0.12)), w, int(h * (0.42 if portrait else 0.66))
    elif k == "lower_third":
        bh = int(h * (0.15 if portrait else 0.24))
        bottom = (0.86 if cap_top else (cap_y - 0.06 if portrait else 0.84)) * h
        x, y, bw = 0, int(max(h * 0.3, min(h - bh, bottom - bh))), w
    elif k == "callout":
        bh = int(h * (0.16 if portrait else 0.26))
        centre = (cap_y if portrait and not cap_top else (0.7 if not portrait else 0.62)) * h
        x, y, bw = 0, int(max(0, min(h - bh, centre - bh / 2))), w
    elif k == "shape3d":
        side = int(min(w, h) * (0.55 if item.get("with_end") else 0.4))
        if item.get("with_end"):
            x, y = (w - side) // 2, int(h * (0.3 if portrait else 0.27) - side / 2)
        else:
            x, y = int(w - side - w * 0.04), int(h * 0.07)
        bw = bh = side
    else:   # end card, confetti, sparkles, light leak: the whole frame
        x, y, bw, bh = 0, 0, w, h
    return x, max(0, y), bw - bw % 2, bh - bh % 2


def _colours(style: str, look: str) -> dict:
    from video_studio.looks import LOOKS

    pal = LOOKS.get(look) or LOOKS["midnight"]
    accent = pal["accent"] if pal["accent"].lower() not in ("#ffffff", "#000000") else "#ff4d2e"
    base = {"ink": "#ffffff", "muted": "rgba(255,255,255,.88)", "accent": accent, "accent2": pal.get("accent2", "#ff4d6d"),
            "plate": "rgba(12,14,22,.74)", "glow": "#22e6ff", "glow2": "#ff2bd6",
            "shadow": "0 4px 18px rgba(0,0,0,.6), 0 1px 3px rgba(0,0,0,.5)", "font": "", "font2": ""}
    if style == "neon":
        base.update(accent="#22e6ff", accent2="#ff2bd6")
    elif style == "gold":
        base.update(accent="#f5c04a", ink="#fff3d6", muted="#f8e7bd", plate="rgba(24,16,6,.72)")
    elif style == "elegant":
        base.update(accent="#e8d3a2", ink="#fffaf0", muted="#f1e6cc", plate="rgba(0,0,0,0)",
                    shadow="0 2px 14px rgba(0,0,0,.55)")
    head, body = STYLE_FONTS.get(style, STYLE_FONTS["clean"])
    base["font"] = f'"{head}", "Nirmala UI", sans-serif'
    base["font2"] = f'"{body}", "Nirmala UI", sans-serif'
    return base


def _font3d_glyphs() -> set[str]:
    data = json.loads(resource_path("motion_templates", "vendor", "mukta_extrabold.typeface.json").read_text(encoding="utf-8"))
    return set(data["glyphs"])


def can_draw_3d(text: str) -> bool:
    """3D letters exist for English letters, digits and common signs (not for Indian scripts yet)."""
    glyphs = _font3d_glyphs()
    return bool(text.strip()) and all(ch in glyphs or ch.isspace() for ch in text)


def _lines(text: str, limit: int = 13) -> list[str]:
    words = text.split()
    if len(text) <= limit or len(words) < 2:
        return [text]
    best = min(range(1, len(words)), key=lambda i: abs(len(" ".join(words[:i])) - len(" ".join(words[i:]))))
    return [" ".join(words[:best]), " ".join(words[best:])]


def values(item: dict, want: dict, bw: int, bh: int, w: int, h: int, look: str) -> dict:
    """window.TB for the page: text, colours and sizes."""
    style = want.get("style") or "clean"
    k = item["kind"]
    tb: dict = {"style": style, "seed": item.get("seed", 7), "colors": _colours(style, look), "sizes": {}}
    tb["sizes"]["pad"] = int(bw * 0.06)
    u = min(w, h)
    tb["colors"]["track"] = "-0.01em" if style != "elegant" else "0.02em"
    if k in ("title", "title3d"):
        text = item.get("text") or want.get("title") or ""
        n = len(text)
        tb.update(title=text, sub=item.get("sub", ""), lines=_lines(text), spin=bool(want.get("spin")))
        tb["sizes"]["size"] = int(min(bw * (0.12 if n <= 14 else 0.095 if n <= 26 else 0.075), bh * 0.3))
    elif k == "lower_third":
        tb.update(name=want.get("name") or want.get("place") or "", place=want.get("place") if want.get("name") else "")
        tb["sizes"]["size"] = int(u * 0.062)
    elif k == "callout":
        tb.update(text=item.get("text", ""))
        tb["sizes"]["size"] = int(min(bw * 0.16, bh * 0.55, bw * 1.5 / max(4, len(item.get("text", "")))))
    elif k == "end_card":
        tb.update(text=want.get("end") or "Thanks for watching", sub=want.get("name") or want.get("title") or "")
        tb["sizes"]["size"] = int(u * 0.085)
        tb["colors"]["text-y"] = "62%" if want.get("_end_shape") else "50%"
    elif k == "shape3d":
        tb.update(shape=want.get("shape") or "gem", count=int(want.get("count") or 1))
    elif k == "confetti":
        tb.update(count=150 if h * w > 1_500_000 else 110)
    elif k == "particles":
        tb.update(count=70)
    tb["fonts"] = [f'40px "{f}"' for f in STYLE_FONTS.get(style, STYLE_FONTS["clean"])]
    return tb


def page(kind: str, tb: dict, bw: int, bh: int, seconds: float, fps: int) -> str:
    """The HyperFrames composition for one piece (a full HTML page)."""
    src = resource_path("motion_templates", f"{kind}.html").read_text(encoding="utf-8")
    faces = "".join(f'@font-face{{font-family:"{fam}";src:url("assets/fonts/{file}") format("truetype");font-display:block}}'
                    for fam, file in FONTS.items())
    head = ['<script src="gsap.min.js"></script>']
    if KINDS[kind]["three"]:
        head += ['<script src="three-motion.min.js"></script>', '<script src="font3d.js"></script>']
    head += [f"<script>window.TB = {json.dumps(tb, ensure_ascii=False)};</script>", '<script src="kit.js"></script>',
             f"<style>{faces}:root{{--font:{tb['colors']['font']};--font2:{tb['colors']['font2']}}}</style>"]
    out = src.replace("{{HEAD}}", "\n".join(head))
    for key, val in (("{{W}}", str(bw)), ("{{H}}", str(bh)), ("{{DUR}}", f"{seconds:.3f}"), ("{{FPS}}", str(fps))):
        out = out.replace(key, val)
    return out


# ------------------------------------------------------------------ rendering (HyperFrames, cached)

def _npx_roots() -> list[Path]:
    return [Path(os.environ.get("LOCALAPPDATA", "")) / "npm-cache" / "_npx", Path.home() / ".npm" / "_npx"]


def hyperframes_cli() -> Path | None:
    """The HyperFrames command file in the npm cache (run with node directly: about 8 s faster than npx)."""
    from video_studio import detect

    for root in _npx_roots():
        if not root.is_dir():
            continue
        for pkg in sorted(root.glob("*/node_modules/hyperframes/package.json")):
            try:
                if json.loads(pkg.read_text(encoding="utf-8")).get("version") == detect.HYPERFRAMES_VERSION:
                    cli = pkg.parent / "bin" / "hyperframes.mjs"
                    if cli.is_file():
                        return cli
            except (OSError, ValueError):
                continue
    return None


def available() -> tuple[bool, str]:
    """(ready, plain reason when not)."""
    from video_studio import detect
    from video_studio.engines.hyperframes import _vendored_gsap

    if not detect.node_path():
        return False, ("Motion graphics need Node.js and HyperFrames, which are free. Node.js is not installed. "
                       "Open System and install Node.js, then HyperFrames (or run: video-studio install node).")
    if not hyperframes_cli() or not _vendored_gsap():
        return False, ("Motion graphics need HyperFrames, which is free. It is not set up on this PC yet. "
                       "Open System and install HyperFrames (or run: video-studio install hyperframes).")
    return True, ""


_READY: dict = {}


def ready_note() -> str:
    """For the live "Takebloom will" card: '' when motion can be drawn, else the plain reason (checked once a minute)."""
    if not _READY or time.time() - _READY.get("at", 0) > 60:
        try:
            ok, why = available()
        except Exception:  # never let a tool check break the prompt card
            ok, why = True, ""
        _READY.update(at=time.time(), note="" if ok else why)
    return _READY["note"]


def _key(html: str, fps: int, scale: float) -> str:
    vendor = resource_path("motion_templates", "vendor", "three-motion.min.js")
    tag = f"{VERSION}|{fps}|{scale}|{vendor.stat().st_size if vendor.exists() else 0}|"
    return hashlib.sha256((tag + html).encode("utf-8")).hexdigest()[:24]


def _prune(folder: Path, keep_mb: int = 600) -> None:
    """Keep the motion cache under a size limit, oldest pieces go first."""
    entries = []
    for d in folder.iterdir():
        if d.is_dir():
            size = sum(f.stat().st_size for f in d.glob("*.png"))
            entries.append((d.stat().st_mtime, size, d))
    total = sum(s for _t, s, _d in entries)
    for _mtime, size, d in sorted(entries):
        if total <= keep_mb * 1024 * 1024:
            break
        shutil.rmtree(d, ignore_errors=True)
        total -= size


def _stage(work: Path, html: str, three: bool) -> None:
    from video_studio.engines.hyperframes import _vendored_gsap

    work.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_vendored_gsap(), work / "gsap.min.js")
    shutil.copy2(resource_path("motion_templates", "kit.js"), work / "kit.js")
    fonts = work / "assets" / "fonts"
    fonts.mkdir(parents=True, exist_ok=True)
    for file in FONTS.values():
        shutil.copy2(resource_path("assets", "fonts", file), fonts / file)
    if three:
        vendor = resource_path("motion_templates", "vendor")
        shutil.copy2(vendor / "three-motion.min.js", work / "three-motion.min.js")
        (work / "font3d.js").write_text("window.TB_FONT = " + (vendor / "mukta_extrabold.typeface.json").read_text(encoding="utf-8")
                                        + ";", encoding="utf-8")
    (work / "index.html").write_text(html, encoding="utf-8")


def render_piece(html: str, kind: str, rw: int, rh: int, seconds: float, fps: int, work: Path) -> tuple[Path, bool, float]:
    """Render one page to a transparent PNG sequence. Returns (frames folder, came from cache, seconds spent)."""
    from video_studio import detect

    scale = KINDS[kind]["scale"]
    folder = cache_dir("motion")
    key = _key(html, fps, scale)
    dest = folder / key
    need = max(1, int(seconds * fps) - 1)
    if dest.is_dir() and len(list(dest.glob("frame_*.png"))) >= need:
        os.utime(dest)
        return dest, True, 0.0
    cli, node = hyperframes_cli(), detect.node_path()
    if not cli or not node:
        raise ToolError("HyperFrames is not set up.", "video-studio install hyperframes")
    t0 = time.time()
    _stage(work, html, KINDS[kind]["three"])
    out = work / "frames"
    shutil.rmtree(out, ignore_errors=True)
    workers = "3" if (os.cpu_count() or 4) >= 12 else "2"
    env = {**os.environ, "DO_NOT_TRACK": "1", "HYPERFRAMES_NO_UPDATE_CHECK": "1"}
    res = run([str(node), str(cli), "render", str(work), "--format", "png-sequence", "--output", str(out), "--fps", str(fps),
               "--workers", workers, "--quiet"], check=False, timeout=180 + seconds * 60, cwd=work, low_priority=True, env=env)
    frames = sorted(out.glob("frame_*.png")) if out.is_dir() else []
    if res.returncode != 0 or len(frames) < need:
        tail = ((res.stderr or "") + (res.stdout or "")).strip().splitlines()[-3:]
        raise ToolError(f"HyperFrames could not draw the {KINDS[kind]['name'].lower()}: " + " | ".join(tail)[:300],
                        "npx hyperframes doctor")
    shutil.rmtree(dest, ignore_errors=True)
    shutil.move(str(out), str(dest))
    _prune(folder)
    return dest, False, time.time() - t0


# ------------------------------------------------------------------ the pass inside an edit

def apply(proj, m: dict, p: dict, splan: dict, w: int, h: int, total: float, look: str, cap_y: float = 0.72,
          cap_top: bool = False) -> dict:
    """Plan, render and return the motion overlays for this edit.
    {"overlays": [...for compose.assemble_video...], "quiet": [(t0, t1)], "sfx": [(kind, at, gain)], "items": [...]}"""
    empty = {"overlays": [], "quiet": [], "sfx": [], "items": []}
    want = wanted(proj)
    if not want:
        return empty
    t_start = time.time()
    busy = [(s["t0"] - 0.2, s["t1"] + 0.2) for s in (splan or {}).get("slams", []) if "t0" in s]
    busy += [(c["t0"] - 0.3, c["t1"] + 0.3) for c in m.get("broll") or []]
    busy += [(c["t0"] - 0.3, c["t1"] + 0.3) for c in m.get("pictures") or []]
    chips = [(c["t0"] - 0.1, c["t1"] + 0.1) for c in (splan or {}).get("chips", []) if "t0" in c]
    cuts = [pc["out_start"] for pc in m.get("pieces", [])[1:]]
    chip_words = {w_ for c in (splan or {}).get("chips", []) for w_ in str(c.get("text", "")).lower().split()}
    want["callout_words"] = callout_words(m.get("pieces", []), p.get("emphasis") or [], chip_words)
    if not want.get("title"):
        want["title"] = str(p.get("title") or proj.data.get("name") or "").strip()
    if "title3d" in want["kinds"] and not can_draw_3d(want["title"]):
        console.info("Motion: 3D letters exist for English letters only, so the title is a flat animated title instead.")
        want["kinds"] = ["title" if k == "title3d" else k for k in want["kinds"]]
    items, notes = place(want, total, busy, chips, cuts)
    for note in notes:
        console.info("Motion: " + note)
    record = {"asked": want["kinds"], "source": want["source"], "style": want["style"], "items": [], "notes": notes}
    if not items:
        m["motion"] = record
        return empty
    ok, why = available()
    if not ok:
        console.warn("Motion graphics skipped. " + why)
        m["motion"] = {**record, "skipped": why}
        return empty
    want["_end_shape"] = any(it["kind"] == "shape3d" and it.get("with_end") for it in items)
    fps = min(FPS_CAP, int(m.get("fps") or 30))
    overlays, sfx, done = [], [], []
    names = ", ".join(KINDS[it["kind"]]["name"].lower() for it in items)
    console.step(f"Motion graphics: {names}")
    for i, it in enumerate(items):
        x, y, bw, bh = box(it, w, h, cap_y, cap_top)
        scale = KINDS[it["kind"]]["scale"]
        rw, rh = max(2, int(bw * scale) // 2 * 2), max(2, int(bh * scale) // 2 * 2)
        seconds = it["end"] - it["start"]
        tb = values(it, want, rw, rh, w, h, look)
        html = page(it["kind"], tb, rw, rh, seconds, fps)
        try:
            frames, cached, spent = render_piece(html, it["kind"], rw, rh, seconds, fps, proj.build / "motion" / f"{i:02d}_{it['kind']}")
        except ToolError as e:
            console.warn(f"Motion: {KINDS[it['kind']]['name']} skipped. {e}")
            record["notes"].append(f"{KINDS[it['kind']]['name']} skipped: {e}")
            continue
        console.info(f"Motion {it['start']:.1f}-{it['end']:.1f}s: {KINDS[it['kind']]['name']}"
                     + (f" “{tb.get('title') or tb.get('name') or tb.get('text')}”" if tb.get('title') or tb.get('name') or tb.get('text') else "")
                     + (" (from cache)" if cached else f" ({spent:.0f} s to draw)"))
        overlays.append({"frames": str(frames), "start": it["start"], "end": it["end"], "x": x, "y": y, "w": bw, "h": bh, "fps": fps})
        sfx += [(kind, it["start"] + at, gain) for kind, at, gain in KINDS[it["kind"]]["sfx"]]
        done.append(it)
        record["items"].append({**{k: v for k, v in it.items() if k != "seed"}, "box": [x, y, bw, bh], "cached": cached,
                                "draw_seconds": round(spent, 1)})
    record["seconds"] = round(time.time() - t_start, 1)
    m["motion"] = record
    if done:
        console.ok(f"Motion graphics: {len(done)} piece(s) in {record['seconds']:.0f} s")
    return {"overlays": overlays, "quiet": quiet_spans(done), "sfx": sfx, "items": done}


def skips_canvas_title(proj) -> bool:
    """With a motion title the old still title card is not drawn as well."""
    want = wanted(proj)
    return bool(want) and any(k in ("title", "title3d") for k in want["kinds"])
