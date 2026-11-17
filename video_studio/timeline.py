"""Timeline: the finished edit as a HyperFrames composition the person can fix by hand.

After the automatic edit, `timeline/index.html` in the project IS the edit. It is a plain HyperFrames composition
(HTML with `data-start`, `data-duration`, `data-media-start`, `data-track-index`, `data-volume`), one track per kind:

    0 video   your pieces, in play order (a cached piece file, or build/source.mov when a cut was made longer)
    1 B-roll  cutaways from your B-roll library
    2 pictures your pictures
    3 overlays big text and any other timed overlays the edit placed (shown, not editable: placed again by the edit)
    4 captions one caption per short phrase; each is tied to the piece it is spoken in, so it moves with that piece
    5 music   the music bed and its level

The Studio's timeline panel reads and writes this file through `load` and `save`. Any HyperFrames tool can open it
too; cut timing, captions, picture and B-roll timing and the music level are read back from the standard attributes.

"Make the film again" (`render`) turns the composition back into Takebloom's own FFmpeg render: only changed pieces are
cut again (from the cached piece, or from the source with the same crop and colour), then the edit resumes from the
render stage with the person's captions, pictures, B-roll and music level. Transcription and footage analysis never run
again. Measured against a HyperFrames render of the same file in docs/KNOWN_LIMITATIONS.md (Timeline 1.9.0).
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import threading
import time
from html.parser import HTMLParser
from pathlib import Path

from video_studio.project import Project
from video_studio.util import ToolError, console, fmt_seconds, read_json, sha

COMP_ID = "takebloom-edit"
VERSION = 1
MIN_CUT = 0.25      # shortest piece the timeline keeps (seconds)
MIN_CAPTION = 0.2
TRACK = {"cut": 0, "broll": 1, "picture": 2, "overlay": 3, "caption": 4, "music": 5}
KNOWN_KEYS = {"source", "fps", "size", "total", "pieces", "segments", "colour", "grade", "look_grade", "cut", "broll",
              "pictures", "music", "srt", "caption_place", "caption_words", "timeline"}


def comp_path(proj: Project) -> Path:
    return proj.root / "timeline" / "index.html"


def signature(m: dict) -> str:
    """Which finished edit a composition was made from (changes whenever the cut changes)."""
    return sha([[Path(pc["file"]).name, round(pc["src_start"], 3), round(pc["src_end"], 3)] for pc in m.get("pieces") or []]
               + [round(float(m.get("total") or 0), 3)], 12)


def _manifest(proj: Project) -> dict:
    m = proj.read("edit_manifest.json") or {}
    if proj.kind != "edit" or not m.get("pieces") or not m.get("total"):
        raise ToolError("This project has no finished edit to show on the timeline yet.")
    return m


# ------------------------------------------------------------------------------------------------- the automatic edit

def _focus(vf: str, src_w: float) -> float:
    """Where the film's frame sits across the source picture (0-1), from the piece's crop."""
    hit = re.match(r"(?:deshake,)?crop=(\d+):(\d+):(\d+):(\d+)", vf or "")
    if not hit or not src_w:
        return 0.5
    w, x = float(hit.group(1)), float(hit.group(3))
    return round(min(1.0, max(0.0, (x + w / 2) / src_w)), 3)


def _cut_catalogue(proj: Project, m: dict) -> list[dict]:
    ing = proj.read("ingest.json") or {}
    source = Path(ing.get("source") or m.get("source") or "")
    longer_ok = source.is_file() and (proj.build / "voice_clean.wav").is_file()
    src_len = float(ing.get("duration") or 0)
    out = []
    for i, pc in enumerate(m["pieces"]):
        a, b = float(pc["src_start"]), float(pc["src_end"])
        vf = pc.get("vf") or ""
        lo, hi = (0.0, max(b, src_len)) if vf and longer_ok and src_len else (a, b)
        out.append({"id": f"c{i + 1}", "in": round(a, 3), "out": round(b, 3), "piece": pc["file"], "piece_in": round(a, 3),
                    "piece_out": round(b, 3), "vf": vf, "utt": pc.get("utt", ""), "punch": pc.get("punch", 1.0),
                    "min": round(lo, 3), "max": round(hi, 3), "focus": _focus(vf, float(ing.get("width") or 0))})
    return out


def _cues(m: dict, cuts: list[dict]) -> list[dict]:
    """Caption phrases, each tied to the piece it is spoken in (source time), so it moves with that piece."""
    saved = (m.get("timeline") or {}).get("cues")
    if saved is not None:
        return [dict(c) for c in saved]
    from video_studio.engines.captions import chunk_words

    words = m.get("caption_words")
    if words is None:
        words = [w for pc in m["pieces"] for w in pc.get("words") or []]
    windows = [(float(pc["out_start"]), float(pc["out_start"]) + float(pc["src_end"]) - float(pc["src_start"]))
               for pc in m["pieces"]]
    per_cut: list[list[dict]] = [[] for _ in cuts]
    for w in words:
        if not str(w.get("text", "")).strip():
            continue
        t = float(w["start"])
        k = next((i for i, (a, b) in enumerate(windows) if a - 0.02 <= t < b), None)
        if k is None:
            k = min(range(len(windows)), key=lambda i: min(abs(t - windows[i][0]), abs(t - windows[i][1])))
        per_cut[k].append(w)
    out = []
    for k, ws in enumerate(per_cut):
        for chunk in chunk_words(ws, 7, 0.8):
            c0 = float(chunk[0]["start"])
            dur = max(MIN_CAPTION, float(chunk[-1]["end"]) - c0)
            text = " ".join(str(w["text"]).strip() for w in chunk)
            out.append({"id": f"t{len(out) + 1}", "cut": cuts[k]["id"],
                        "at": round(cuts[k]["piece_in"] + c0 - windows[k][0], 3), "dur": round(dur, 3), "text": text,
                        "orig": text, "odur": round(dur, 3),
                        "words": [[round(float(w["start"]) - c0, 3), round(float(w["end"]) - float(w["start"]), 3)] for w in chunk]})
    return out


def _overlays(proj: Project, m: dict) -> list[dict]:
    """Every timed overlay the edit placed: big text from the style pass, and any other list of timed items
    (for example motion graphics) recorded in the manifest. Shown on the timeline, placed again by the edit."""
    out = []
    sp = proj.read("style_plan.json") or {}
    for x in sp.get("slams") or []:
        if "t0" in x and "t1" in x:
            out.append({"group": "Big text", "t0": float(x["t0"]), "t1": float(x["t1"]), "text": str(x.get("text", ""))})
    for key, val in m.items():
        if key in KNOWN_KEYS or not isinstance(val, list):
            continue
        for x in val:
            if not isinstance(x, dict):
                continue
            a, b = x.get("t0", x.get("start")), x.get("t1", x.get("end"))
            if isinstance(a, (int, float)) and isinstance(b, (int, float)) and b > a:
                label = x.get("text") or x.get("title") or x.get("name") or x.get("kind") or x.get("type") or key
                out.append({"group": key.replace("_", " ").capitalize(), "t0": float(a), "t1": float(b), "text": str(label)[:60]})
    out.sort(key=lambda o: o["t0"])
    return [{**o, "id": f"o{i + 1}", "t0": round(o["t0"], 3), "t1": round(o["t1"], 3)} for i, o in enumerate(out)]


def _music(proj: Project, m: dict) -> dict | None:
    s = proj.settings
    if not s.get("music_enabled", True):
        return None
    plan = proj.read("edit_plan.json") or {}
    info = m.get("music") or {}
    audio = proj.build / "audio"
    source = info.get("source") or ("own" if plan.get("own_music") else "")
    src: Path | None = None
    if plan.get("own_music"):
        src, name = audio / "own_music.wav", "The sound in your clips"
    elif s.get("music_file"):
        src, name, source = Path(s["music_file"]), "Your song " + str(info.get("file") or Path(s["music_file"]).name), "your song"
    elif source == "your library":
        src, name = audio / "music_library.wav", str(info.get("file") or "Your music library")
    else:
        beds = sorted((p for p in audio.glob("music_*.wav") if p.name != "music_library.wav"), key=lambda p: p.stat().st_mtime)
        src = beds[-1] if beds else None
        name = f"Original {info.get('genre', '')} music".replace("  ", " ")
    if not source and src is None:
        return None
    default = 1.0 if plan.get("mode") == "montage" else 0.14
    tlm = (m.get("timeline") or {}).get("music") or {}
    found = src is not None and src.is_file()
    return {"source": source or "generated", "name": name, "src": str(src) if found else "", "default": default,
            "volume": round(float(tlm.get("volume", default)), 3), "on": bool(tlm.get("on", True)),
            "reuse": str(src) if found and source == "your library" else ""}


def base_model(proj: Project) -> dict:
    """The automatic edit as the timeline model."""
    m = _manifest(proj)
    w, h = m["size"]
    cuts = _cut_catalogue(proj, m)
    opts_pics = [str(p) for p in (proj.data.get("edit_options") or {}).get("pictures") or []]
    from video_studio.photos import MOVES

    pictures = []
    for i, x in enumerate(m.get("pictures") or []):
        path = x.get("path") or (opts_pics[i] if i < len(opts_pics) else "")
        if path:
            pictures.append({"id": f"p{i + 1}", "t0": round(float(x["t0"]), 3), "t1": round(float(x["t1"]), 3), "path": str(path),
                             "name": x.get("name") or Path(path).name, "move": x.get("move") or MOVES[i % len(MOVES)]})
    broll = [{"id": f"b{i + 1}", "t0": round(float(c["t0"]), 3), "t1": round(float(c["t1"]), 3), "path": str(c["path"]),
              "src": round(float(c.get("src") or 0), 3), "name": Path(c["path"]).name, "why": str(c.get("why") or "")}
             for i, c in enumerate(x for x in m.get("broll") or [] if not x.get("picture"))]
    model = {"version": VERSION, "base": signature(m), "width": w, "height": h, "fps": m["fps"],
             "caption_y": float((m.get("caption_place") or {}).get("y") or (0.72 if h > w else 0.9)),
             "source": str((proj.read("ingest.json") or {}).get("source") or m.get("source") or ""),
             "cuts": cuts, "captions": _cues(m, cuts), "pictures": pictures, "broll": broll,
             "overlays": _overlays(proj, m), "music": _music(proj, m)}
    return layout(model)


# ------------------------------------------------------------------------------------------------------------ layout

def layout(model: dict) -> dict:
    """Pieces play back to back in order; captions follow the piece they are tied to. Fills start/len/total."""
    t = 0.0
    by_id = {}
    for c in model["cuts"]:
        c["start"], c["len"] = round(t, 3), round(c["out"] - c["in"], 3)
        by_id[c["id"]] = c
        t += c["out"] - c["in"]
    model["total"] = round(t, 3)
    for cap in model["captions"]:
        c = by_id.get(cap["cut"])
        a = max(cap["at"], c["in"]) if c else 0
        b = min(cap["at"] + cap["dur"], c["out"]) if c else 0
        if not c or b - a < 0.05:
            cap["start"], cap["len"] = None, 0.0   # its piece was cut away or trimmed past it: hidden, kept for undo
        else:
            cap["start"], cap["len"] = round(c["start"] + a - c["in"], 3), round(b - a, 3)
    return model


# ------------------------------------------------------------------------------------------------------------- write

class _Media:
    """HyperFrames reads media only from inside the composition's folder, so each file the edit uses is hard-linked
    into timeline/media (no extra disk space; a copy only for a small file on another drive)."""

    def __init__(self, folder: Path) -> None:
        self.dir, self.used = folder / "media", set()
        self.dir.mkdir(parents=True, exist_ok=True)

    def add(self, target: str, name: str) -> str:
        src, dest = Path(target), self.dir / name
        self.used.add(name)
        try:
            if dest.exists() and os.path.samefile(src, dest):
                return "media/" + name
        except OSError:
            pass
        try:
            dest.unlink(missing_ok=True)
            os.link(src, dest)
        except OSError:
            try:
                if src.stat().st_size > 300 * 1024 * 1024:
                    raise OSError("too big to copy")
                shutil.copy2(src, dest)
            except OSError:
                return src.as_posix()   # still the right file; HyperFrames tools may not reach it
        return "media/" + name

    def prune(self) -> None:
        for f in self.dir.iterdir():
            if f.name not in self.used:
                try:
                    f.unlink()
                except OSError:
                    pass


def _f(v: float) -> str:
    return "%.3f" % v


def _a(**attrs) -> str:
    """HTML attributes: data_tb_kind -> data-tb-kind, cls -> class; None values are left out."""
    return " ".join('%s="%s"' % ("class" if k == "cls" else k.replace("_", "-"), html.escape(str(v), quote=True))
                    for k, v in attrs.items() if v is not None)


def write(proj: Project, model: dict) -> Path:
    """Write the composition (the timeline's single source of truth)."""
    path = comp_path(proj)
    folder = path.parent
    folder.mkdir(parents=True, exist_ok=True)
    w, h, total = model["width"], model["height"], max(0.04, model["total"])
    media = _Media(folder)
    rows = []
    for c in model["cuts"]:
        inside = c["piece_in"] - 0.002 <= c["in"] and c["out"] <= c["piece_out"] + 0.002
        src, offset = (c["piece"], c["piece_in"]) if inside else (model["source"], 0.0)
        look = None if inside else "object-position:%.1f%% 50%%" % (c["focus"] * 100)
        rows.append("  <video %s playsinline></video>" % _a(
            id=c["id"], src=media.add(src, Path(src).name if inside else "source" + Path(src).suffix), data_start=_f(c["start"]), data_duration=_f(c["len"]),
            data_media_start=_f(max(0.0, c["in"] - offset)), data_track_index=TRACK["cut"], data_has_audio="true",
            data_tb_kind="cut", data_tb_src_offset=_f(offset), data_tb_utt=c.get("utt") or None, style=look))
    for b in model["broll"]:
        rows.append("  <video %s muted playsinline></video>" % _a(
            id=b["id"], src=media.add(b["path"], "broll-%s%s" % (b["id"], Path(b["path"]).suffix)), data_start=_f(b["t0"]), data_duration=_f(b["t1"] - b["t0"]),
            data_media_start=_f(b["src"]), data_track_index=TRACK["broll"], data_tb_kind="broll", data_tb_why=b["why"] or None))
    for pic in model["pictures"]:
        rows.append("  <img %s>" % _a(
            id=pic["id"], cls="clip", src=media.add(pic["path"], "picture-%s%s" % (pic["id"], Path(pic["path"]).suffix.lower())), alt=pic["name"], data_start=_f(pic["t0"]),
            data_duration=_f(pic["t1"] - pic["t0"]), data_track_index=TRACK["picture"], data_tb_kind="picture",
            data_tb_move=pic["move"]))
    for o in model["overlays"]:
        rows.append("  <div %s><span>%s</span></div>" % (_a(
            id=o["id"], cls="clip tb-ov", data_start=_f(o["t0"]), data_duration=_f(o["t1"] - o["t0"]),
            data_track_index=TRACK["overlay"], data_tb_kind="overlay", data_tb_group=o["group"], data_tb_readonly="true"),
            html.escape(o["text"])))
    for cap in model["captions"]:
        hidden = cap["start"] is None
        rows.append("  <div %s><span>%s</span></div>" % (_a(
            id=cap["id"], cls="clip tb-cap", data_start=_f(0.0 if hidden else cap["start"]),
            data_duration=_f(cap["dur"] if hidden else cap["len"]), data_track_index=TRACK["caption"],
            data_hidden="true" if hidden else None, data_tb_kind="caption", data_tb_cut=cap["cut"], data_tb_at=_f(cap["at"]),
            data_tb_dur=_f(cap["dur"]), data_tb_orig=cap["orig"], data_tb_odur=_f(cap.get("odur", cap["dur"])),
            data_tb_words=json.dumps(cap["words"], separators=(",", ":"))), html.escape(cap["text"])))
    mus = model.get("music")
    if mus and mus.get("src"):
        rows.append("  <audio %s></audio>" % _a(
            id="music", src=media.add(mus["src"], "music" + Path(mus["src"]).suffix.lower()), data_start="0", data_duration=_f(total), data_volume=_f(mus["volume"]),
            data_track_index=TRACK["music"], data_hidden=None if mus["on"] else "true", data_tb_kind="music",
            data_tb_default=_f(mus["default"]), data_tb_source=mus["source"], data_tb_name=mus["name"]))
    elif mus:
        rows.append("  <div %s hidden></div>" % _a(id="music-state", data_tb_kind="music", data_tb_volume=_f(mus["volume"]),
                                                   data_tb_on="true" if mus["on"] else "false"))
    cap_top = model["caption_y"] * 100
    doc = f"""<!doctype html>
<!-- Takebloom timeline: this file IS your edit. Open it in any HyperFrames tool, or fix it in Takebloom's timeline.
     Made from edit {model['base']}. Pieces: track 0, B-roll 1, pictures 2, overlays 3 (placed by the edit), captions 4, music 5. -->
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width={w}, height={h}">
<title>Takebloom timeline</title>
<style>
  html, body {{ margin: 0; background: #000; }}
  #root {{ position: relative; width: 100%; height: 100%; overflow: hidden; background: #000; }}
  #root > video, #root > img {{ position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }}
  .tb-cap {{ position: absolute; left: 8%; right: 8%; top: auto; bottom: {100 - cap_top:.1f}%; height: auto; text-align: center;
    font: 800 {int(h * 0.045)}px/1.15 "Segoe UI", system-ui, sans-serif; color: #fff; text-transform: uppercase;
    text-shadow: 0 0 6px #000, 0 2px 3px #000; }}
  .tb-ov {{ position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; text-align: center;
    font: 900 {int(h * 0.07)}px/1 "Segoe UI", system-ui, sans-serif; color: rgba(255,255,255,.85); }}
</style>
</head>
<body>
<div {_a(id="root", data_composition_id=COMP_ID, data_no_timeline="", data_width=w, data_height=h, data_duration=_f(total), data_fps=model["fps"], data_tb_version=VERSION, data_tb_base=model["base"])}>
{chr(10).join(rows)}
</div>
</body>
</html>
"""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(doc, encoding="utf-8")
    os.replace(tmp, path)
    media.prune()
    return path


# ------------------------------------------------------------------------------------------------------------- parse

class _Reader(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root: dict = {}
        self.items: list[dict] = []
        self._open: dict | None = None
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        a = {k: (v if v is not None else "") for k, v in attrs}
        if "data-composition-id" in a and not self.root:
            self.root = a
        if self._open is not None:
            self._depth += tag not in ("img", "br", "meta")
            return
        if "data-tb-kind" in a:
            item = {"tag": tag, "attrs": a, "text": ""}
            self.items.append(item)
            if tag == "div":
                self._open, self._depth = item, 1

    def handle_endtag(self, tag):
        if self._open is not None:
            self._depth -= 1
            if self._depth <= 0:
                self._open["text"] = re.sub(r"\s+", " ", self._open["text"]).strip()
                self._open = None

    def handle_data(self, data):
        if self._open is not None:
            self._open["text"] += data


def _num(a: dict, key: str, default: float = 0.0) -> float:
    try:
        v = float(a.get(key, default))
        return v if v == v and abs(v) < 1e7 else default
    except (TypeError, ValueError):
        return default


def parse(proj: Project, path: Path | None = None) -> dict:
    """Read a composition back into the timeline model (timing from the standard HyperFrames attributes)."""
    path = path or comp_path(proj)
    r = _Reader()
    r.feed(path.read_text(encoding="utf-8"))
    if r.root.get("data-composition-id") != COMP_ID:
        raise ToolError("This is not a Takebloom timeline.")
    folder = path.parent
    model: dict = {"base": r.root.get("data-tb-base", ""), "cuts": [], "captions": [], "pictures": [], "broll": [], "music": None}
    kind = lambda it: it["attrs"].get("data-tb-kind")  # noqa: E731
    for it in sorted((x for x in r.items if kind(x) == "cut"), key=lambda x: _num(x["attrs"], "data-start")):
        a = it["attrs"]
        start = _num(a, "data-tb-src-offset") + _num(a, "data-media-start")
        model["cuts"].append({"id": a.get("id", ""), "in": start, "out": start + _num(a, "data-duration"), "_t": _num(a, "data-start")})
    # where each piece sat in this file, to tell a caption that was moved here from one that only rippled
    lay, t = {}, 0.0
    for c in model["cuts"]:
        lay[c["id"]] = (t, c)
        t += c["out"] - c["in"]
    for it in r.items:
        a, k = it["attrs"], kind(it)
        if k == "caption":
            text = it["text"]
            cut, at, dur = a.get("data-tb-cut", ""), _num(a, "data-tb-at"), _num(a, "data-tb-dur", 1.0)
            if "data-hidden" not in a and cut in lay:
                c0, c = lay[cut]
                expect = c0 + max(at, c["in"]) - c["in"]
                start, length = _num(a, "data-start"), _num(a, "data-duration")
                if abs(start - expect) > 0.02:   # moved in another HyperFrames tool: tie it to the piece it now sits on
                    hit = next(((cid, v) for cid, v in lay.items() if v[0] - 1e-3 <= start < v[0] + v[1]["out"] - v[1]["in"]), None)
                    if hit:
                        cut, at = hit[0], hit[1][1]["in"] + start - hit[1][0]
                if abs(length - (min(at + dur, lay[cut][1]["out"]) - max(at, lay[cut][1]["in"]))) > 0.02:
                    dur = length
            try:
                words = [[float(o), float(d)] for o, d in json.loads(a.get("data-tb-words") or "[]")][:80]
            except (ValueError, TypeError):
                words = []
            model["captions"].append({"id": a.get("id", ""), "cut": cut, "at": at, "dur": dur, "text": text,
                                      "orig": a.get("data-tb-orig", text), "odur": _num(a, "data-tb-odur", dur), "words": words})
        elif k in ("picture", "broll"):
            t0 = _num(a, "data-start")
            item = {"id": a.get("id", ""), "t0": t0, "t1": t0 + _num(a, "data-duration")}
            if k == "picture":
                item["move"] = a.get("data-tb-move", "")
            model["pictures" if k == "picture" else "broll"].append(item)
        elif k == "music":
            if it["tag"] == "audio":
                model["music"] = {"volume": _num(a, "data-volume", 0.14), "on": "data-hidden" not in a}
            else:
                model["music"] = {"volume": _num(a, "data-tb-volume", 0.14), "on": a.get("data-tb-on") != "false"}
    _ = folder
    return model


# --------------------------------------------------------------------------------------------- normalise, load, save

def _clamp(v, lo: float, hi: float) -> float:
    try:
        v = float(v)
    except (TypeError, ValueError):
        v = lo
    if v != v:
        v = lo
    return min(hi, max(lo, v))


def normalize(proj: Project, edited: dict, base: dict | None = None) -> dict:
    """A checked model from an edited one (the Studio panel, or a parsed file). Paths and crops always come from the
    finished edit, never from the edited model; numbers are clamped; unknown items are ignored."""
    base = base or base_model(proj)
    cat = {c["id"]: c for c in base["cuts"]}
    cuts, seen = [], set()
    for c in edited.get("cuts") or []:
        b = cat.get(str(c.get("id")))
        if not b or b["id"] in seen:
            continue
        seen.add(b["id"])
        a = round(_clamp(c.get("in"), b["min"], b["max"] - MIN_CUT), 3)
        z = round(_clamp(c.get("out"), a + MIN_CUT, b["max"]), 3)
        cuts.append({**b, "in": a, "out": z})
    if not cuts:
        raise ToolError("Keep at least one piece of your video on the timeline.")
    caps = []
    for cap in edited.get("captions") or []:
        text = re.sub(r"\s+", " ", str(cap.get("text") or "")).strip()[:240]
        if not text or str(cap.get("cut")) not in cat:
            continue
        words = []
        for pair in (cap.get("words") or [])[:80]:
            try:
                words.append([round(max(0.0, float(pair[0])), 3), round(max(0.02, float(pair[1])), 3)])
            except (TypeError, ValueError, IndexError):
                break
        caps.append({"id": str(cap.get("id") or f"t{len(caps) + 1}")[:12], "cut": str(cap["cut"]),
                     "at": round(_clamp(cap.get("at"), 0.0, 1e6), 3), "dur": round(_clamp(cap.get("dur"), MIN_CAPTION, 15.0), 3),
                     "text": text, "orig": str(cap.get("orig") or "")[:240], "odur": round(_clamp(cap.get("odur") or cap.get("dur"), MIN_CAPTION, 15.0), 3),
                     "words": words})
    model = {**base, "cuts": cuts, "captions": caps}
    layout(model)
    total = model["total"]

    def placed(kind: str, minimum: float, maximum: float) -> list[dict]:
        known = {x["id"]: x for x in base[kind]}
        out, ids = [], set()
        for x in edited.get(kind) or []:
            b = known.get(str(x.get("id")))
            if not b or b["id"] in ids:
                continue
            ids.add(b["id"])
            length = _clamp(_clamp(x.get("t1"), 0, 1e6) - _clamp(x.get("t0"), 0, 1e6), minimum, maximum)
            t0 = round(_clamp(x.get("t0"), 0.0, max(0.0, total - length)), 3)
            t1 = round(min(total, t0 + length), 3)
            if t1 - t0 >= minimum - 1e-6:
                item = {**b, "t0": t0, "t1": t1}
                if kind == "pictures" and x.get("move"):
                    from video_studio.photos import MOVES

                    item["move"] = x["move"] if x["move"] in MOVES else b["move"]
                out.append(item)
        return sorted(out, key=lambda i: i["t0"])

    model["pictures"] = placed("pictures", 0.5, 8.0)
    model["broll"] = placed("broll", 0.5, 8.0)
    if base.get("music"):
        mus = edited.get("music") or {}
        model["music"] = {**base["music"], "volume": round(_clamp(mus.get("volume", base["music"]["volume"]), 0.0, base["music"]["default"] * 2.5), 3),
                          "on": bool(mus.get("on", True))}
    return model


def load(proj: Project) -> dict:
    """The timeline for this project: the saved composition if it belongs to the current edit, else a fresh one."""
    m = _manifest(proj)
    path = comp_path(proj)
    base = base_model(proj)
    if path.exists():
        try:
            edited = parse(proj, path)
        except (ToolError, OSError, ValueError):
            edited = None
        if edited and edited.get("base") == signature(m):
            try:
                return normalize(proj, edited, base)
            except ToolError:
                pass
    write(proj, base)
    return base


def save(proj: Project, edited: dict) -> dict:
    model = normalize(proj, edited)
    write(proj, model)
    return model


# ---------------------------------------------------------------------------------------------- caption words, render

def caption_words(model: dict) -> list[dict]:
    """Word timings for the burned-in captions, from the caption track (absolute film time)."""
    by_id = {c["id"]: c for c in model["cuts"]}
    out = []
    for cap in model["captions"]:
        c = by_id.get(cap["cut"])
        if not c or cap["start"] is None:
            continue
        texts = cap["text"].split()
        offs = cap.get("words") or []
        if cap["text"] == cap.get("orig") and len(offs) == len(texts):
            span = max(o + d for o, d in offs) or cap["dur"]
            k = cap["dur"] / span if abs(cap["dur"] - cap.get("odur", cap["dur"])) > 0.01 and span > 0 else 1.0
            timed = [(o * k, d * k) for o, d in offs]
        else:   # new words: share the phrase's time by word length, like speech
            weight = [len(t) + 2 for t in texts]
            step = cap["dur"] / max(1, sum(weight))
            pos, timed = 0.0, []
            for wt in weight:
                timed.append((pos, wt * step * 0.92))
                pos += wt * step
        for text, (o, d) in zip(texts, timed):
            src = cap["at"] + o
            if not (c["in"] - 1e-3 <= src < c["out"]):
                continue
            t = c["start"] + src - c["in"]
            out.append({"text": text, "start": round(t, 3), "end": round(min(t + max(0.05, d), c["start"] + c["len"]), 3)})
    out.sort(key=lambda w: w["start"])
    return out


def _cut_piece(proj: Project, c: dict, dest: Path, fps: int) -> None:
    from video_studio.engines import ff

    dur = c["out"] - c["in"]
    fade = f"afade=t=in:d=0.012,afade=t=out:st={max(0.0, dur - 0.015):.3f}:d=0.015,aresample=48000"
    enc = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "14", "-c:a", "pcm_s16le", "-t", f"{dur:.3f}"]
    part = dest.with_suffix(".part.mov")
    if c["piece_in"] - 0.002 <= c["in"] and c["out"] <= c["piece_out"] + 0.002 and Path(c["piece"]).is_file():
        ff.ff(["-ss", f"{c['in'] - c['piece_in']:.3f}", "-t", f"{dur:.3f}", "-i", c["piece"],
               "-filter_complex", f"[0:v]fps={fps},format=yuv420p[v];[0:a]{fade}[a]", "-map", "[v]", "-map", "[a]", *enc, str(part)])
    else:   # made longer than the automatic cut: from the source, with the same crop, punch-in and colour
        src, voice = Path((proj.read("ingest.json") or {})["source"]), proj.build / "voice_clean.wav"
        ff.ff(["-ss", f"{c['in']:.3f}", "-t", f"{dur:.3f}", "-i", str(src), "-ss", f"{c['in']:.3f}", "-t", f"{dur:.3f}", "-i", str(voice),
               "-filter_complex", f"[0:v]{c['vf']},fps={fps},format=yuv420p[v];[1:a]{fade}[a]", "-map", "[v]", "-map", "[a]", *enc, str(part)])
    part.replace(dest)


def rebuild(proj: Project) -> dict:
    """Turn the composition into the cut Takebloom renders: pieces (cached), the cut file and a manifest whose
    `timeline` entry carries the person's captions, pictures, B-roll and music level into the render stage."""
    from video_studio.engines import ff

    m = _manifest(proj)
    model = load(proj)
    fps = int(m["fps"])
    plan = proj.read("edit_plan.json") or {}
    words_src = [] if plan.get("mode") == "montage" else (proj.read("transcript.json") or {}).get("words") or []
    folder = proj.build / "timeline_pieces"
    folder.mkdir(parents=True, exist_ok=True)
    console.step(f"Cutting your timeline: {len(model['cuts'])} piece(s)")
    pieces, t_out, made = [], 0.0, 0
    for i, c in enumerate(model["cuts"]):
        same = abs(c["in"] - c["piece_in"]) < 1e-3 and abs(c["out"] - c["piece_out"]) < 1e-3 and Path(c["piece"]).is_file()
        if same:
            f = Path(c["piece"])
        else:
            f = folder / f"t_{sha([Path(c['piece']).name, c['piece_in'], c['in'], c['out'], c['vf'], fps], 14)}.mov"
            if not f.exists():
                _cut_piece(proj, c, f, fps)
                made += 1
        console.progress("cutting", i + 1, len(model["cuts"]))
        ws = [{"text": w["text"], "start": round(t_out + w["start"] - c["in"], 3), "end": round(t_out + w["end"] - c["in"], 3)}
              for w in words_src if w["start"] >= c["in"] - 0.01 and w["end"] <= c["out"] + 0.01]
        pieces.append({"file": str(f), "src_start": c["in"], "src_end": c["out"], "out_start": round(t_out, 3), "utt": c["utt"],
                       "punch": c["punch"], "vf": c["vf"], "words": ws})
        t_out += ff.duration(f)
    listing = proj.build / "timeline_pieces.txt"
    listing.write_text("\n".join(f"file '{Path(x['file']).as_posix()}'" for x in pieces), encoding="utf-8")
    cut_file = proj.build / "cut_timeline.mov"
    ff.ff(["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(cut_file)])
    total = ff.duration(cut_file)
    new_id = {c["id"]: f"c{i + 1}" for i, c in enumerate(model["cuts"])}
    cues = [{**{k: v for k, v in cap.items() if k not in ("start", "len")}, "cut": new_id[cap["cut"]]}
            for cap in model["captions"] if cap["cut"] in new_id]
    mus = model.get("music") or {}
    timeline = {"from": model["base"], "words": caption_words(model), "cues": cues,
                "pictures": [{k: p[k] for k in ("t0", "t1", "path", "move", "name")} for p in model["pictures"]],
                "broll": [{"t0": b["t0"], "t1": b["t1"], "path": b["path"], "src": b["src"], "why": b["why"]} for b in model["broll"]]}
    if mus:
        timeline["music"] = {"volume": mus["volume"], "on": mus["on"], "reuse": mus.get("reuse") or ""}
    new = {**m, "total": round(total, 3), "pieces": pieces, "segments": [{"start": x["src_start"], "end": x["src_end"]} for x in pieces],
           "cut": str(cut_file), "timeline": timeline}
    for key in ("caption_words", "srt"):
        new.pop(key, None)
    proj.write("edit_manifest.json", new)
    proj.mark("cut", "done", pieces=len(pieces), seconds=round(total, 1), timeline=True, recut=made)
    proj.invalidate_from("render")
    console.ok(f"Timeline cut: {len(pieces)} piece(s), {fmt_seconds(total)} ({made} cut again, the rest reused)")
    return new


def render(proj: Project) -> int:
    """Make the film again from the timeline: re-cut changed pieces, then resume from the render stage."""
    from video_studio import editor

    started = time.time()
    rebuild(proj)
    code = editor.resume(proj)
    load(proj)   # the composition now matches the new film
    console.ok(f"Film made again from your timeline in {fmt_seconds(time.time() - started)}")
    return code


# ---------------------------------------------------------------------------------------------------------- preview

_MAKING: set[str] = set()
_LOCK = threading.Lock()


def preview_file(proj: Project, start: bool = True) -> Path | None:
    """A small, quick-to-seek copy of your footage for the timeline's live preview (made once, in the background)."""
    src = Path((proj.read("ingest.json") or {}).get("source") or "")
    dest = proj.build / "timeline_preview.mp4"
    if dest.is_file() and src.is_file() and dest.stat().st_mtime >= src.stat().st_mtime:
        return dest
    if not start or not src.is_file():
        return None
    key = str(dest)
    with _LOCK:
        if key in _MAKING:
            return None
        _MAKING.add(key)

    def work():
        from video_studio.engines import ff

        try:
            part = dest.with_suffix(".part.mp4")
            ff.ff(["-i", str(src), "-vf", "scale=-2:'min(540,ih)',format=yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
                   "-crf", "30", "-g", "12", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(part)], timeout=1800)
            part.replace(dest)
        except (ToolError, OSError) as exc:
            console.warn(f"Timeline preview copy failed: {exc}")
        finally:
            with _LOCK:
                _MAKING.discard(key)

    threading.Thread(target=work, daemon=True).start()
    return None


# ------------------------------------------------------------------------------------------------------- API + CLI

def api_get(root: Path) -> dict:
    proj = Project(root)
    model = load(proj)
    prev = preview_file(proj)
    return {**model, "preview": str(prev) if prev else "", "file": str(comp_path(proj))}


def api_save(root: Path, edited: dict) -> dict:
    proj = Project(root)
    model = save(proj, edited if isinstance(edited, dict) else {})
    prev = preview_file(proj, start=False)
    return {**model, "preview": str(prev) if prev else "", "file": str(comp_path(proj))}


def run_cli(args) -> int:
    from video_studio.project import find

    proj = find(getattr(args, "project", None))
    if args.action == "export":
        model = load(proj)
        console.ok(f"Timeline written: {comp_path(proj)} ({len(model['cuts'])} pieces, {fmt_seconds(model['total'])})")
        return 0
    return render(proj)


__all__ = ["base_model", "layout", "write", "parse", "normalize", "load", "save", "caption_words", "rebuild", "render",
           "preview_file", "api_get", "api_save", "run_cli", "comp_path", "signature", "read_json"]
