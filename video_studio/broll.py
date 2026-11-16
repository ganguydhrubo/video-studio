"""B-roll: cut away to footage of what you are talking about, the way a human editor does.

Library: your own clips in %LOCALAPPDATA%/VideoStudio/broll (or any folder via `--broll <folder>`).
Each clip is indexed once (cached): what it shows over time, from its file/folder name and - if it has narration -
the local Whisper transcript translated to English (any language). For each kept line of your edit, the line's
English meaning is matched against every moment of every clip; on a real match (shared, specific words) the edit
cuts away to that moment for up to ~2.8 s while your voice continues. Never over the cold open, the payoff line or
a giant-text moment; never the same moment twice; at most one cutaway per ~7 s.
"""

from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path

from video_studio.util import app_data_dir, console, read_json, write_json

MEDIA = {".mp4", ".mov", ".mkv", ".m4v", ".webm"}
# Words too general to justify a cutaway on their own ('people' or 'explain' says nothing about what to SHOW).
GENERIC = set("""people person thing things explain understand know think want need make made making use using used work
working works worked time times day days today way ways good great best better new old big small lot lots many much
come coming go going get getting give giving take taking look looking see seeing say saying said tell talk talking
start started try trying help helps life world platform system app tool tools video videos clip raw final english
hindi part first second next last every everything something anything nothing right left real really just also
problem problems question answer idea ideas reason point case kind type number minute minutes hour hours year years
month months week weeks money free easy hard simple change changes going done doing does""".split())


def _stems(text: str) -> set[str]:
    from video_studio import brain

    words = (w.split("'")[0] for w in re.findall(r"[A-Za-z0-9']+", text.lower()) if w not in brain.EN_STOP)
    return {brain._stem(w) for w in words if w not in brain.EN_STOP and len(w) > 2}


def _named(text: str) -> set[str]:
    """Stems of the names in a line: acronyms and capitalised words that do not start a sentence
    ('GST', 'Excel', 'Windows'). One shared NAME is enough for a cutaway; one shared ordinary word is not."""
    from video_studio import brain

    names = re.findall(r"\b[A-Z]{2,}\b|(?<=[a-z,;] )[A-Z][a-z]{2,}", text)
    return {brain._stem(n.lower()) for n in names} - {brain._stem(w) for w in GENERIC} - {"ai"}


def _specific(stems: set[str]) -> set[str]:
    from video_studio import brain

    generic = {brain._stem(w) for w in GENERIC}
    return {w for w in stems if w not in generic}


def library_dirs(extra: str | None = None) -> list[Path]:
    dirs = [app_data_dir() / "broll"]
    if extra:
        dirs.insert(0, Path(extra))
    return [d for d in dirs if d.is_dir()]


def index_clip(clip: Path) -> dict:
    from video_studio.engines import ff, ml

    key = hashlib.sha1(f"{clip.resolve()}|{clip.stat().st_size}|{clip.stat().st_mtime}".encode()).hexdigest()[:16]
    cache = app_data_dir() / "broll_index" / f"{key}.json"
    data = read_json(cache)
    if data:
        return data
    dur = ff.duration(clip)
    name_tags = " ".join(re.split(r"[_\-\s.]+", clip.stem + " " + clip.parent.name))
    segs = []
    try:
        tr = ml.transcribe(clip, model="small")  # light model: B-roll only needs the gist, and it runs once per clip
        src = tr.get("translation") or (tr.get("segments") if tr.get("language") == "en" else []) or []
        segs = [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in src if s.get("text")]
    except Exception as exc:  # noqa: BLE001 - a clip without speech is still usable by its name
        console.warn(f"B-roll {clip.name}: no narration index ({str(exc)[:80]})")
    if not segs:
        segs = [{"start": 0.0, "end": dur, "text": ""}]
    data = {"path": str(clip), "duration": dur, "name": name_tags, "segments": segs}
    write_json(cache, data)
    return data


def build_index(extra: str | None = None) -> list[dict]:
    clips = [f for d in library_dirs(extra) for f in sorted(d.rglob("*")) if f.suffix.lower() in MEDIA]
    out = []
    for i, c in enumerate(clips):
        console.step(f"B-roll library: learning what clip {i + 1}/{len(clips)} shows ({c.name})")
        out.append(index_clip(c))
    return out


def plan(lines: list[dict], index: list[dict], busy: list[tuple[float, float]], total: float) -> list[dict]:
    """lines: kept lines in output order [{id, t0, t1, en}]. Returns cutaways [{t0, t1, path, src, why}]."""
    if not index or len(lines) < 3:
        return []
    moments = []
    for clip in index:
        name = _stems(clip.get("name", ""))
        for s in clip["segments"]:
            moments.append({"path": clip["path"], "start": s["start"], "end": s["end"],
                            "stems": _stems(s["text"]) | name, "text": s["text"] or clip.get("name", "")})
    # rarity: a word that appears in few moments is a more specific match ('gst' beats 'work')
    df: dict[str, int] = {}
    for m in moments:
        for w in m["stems"]:
            df[w] = df.get(w, 0) + 1
    idf = {w: math.log((1 + len(moments)) / (1 + n)) + 1 for w, n in df.items()}
    top = math.log((1 + len(moments)) / 2) + 1  # the weight of a word that appears in one moment only
    cands = []
    for ln in lines[1:-1]:  # never the cold open (your face sells the hook) or the payoff
        said = _specific(_stems(ln["en"]))
        want = said | _specific(_stems(ln.get("visual", "")))  # what is said + what the AI editor would show
        named = _named(ln["en"] + " . " + ln.get("visual", ""))
        a = ln["t0"] + 0.35
        b = min(ln["t1"] - 0.1, a + 2.8, total - 0.3)
        if not want or b - a < 1.0 or any(a < y and b > x for x, y in busy):
            continue
        for m in moments:
            shared = want & m["stems"]
            val = sum(idf.get(w, 1.0) for w in shared)
            strong = bool(shared & named)
            # a shared name is evidence on its own; ordinary words must add up
            if (strong and val >= 2.5) or (len(shared) >= 2 and val >= 1.5 * top):
                cands.append((strong, val, a, b, m, shared, said))
    cuts, used = [], set()
    for strong, val, a, b, m, shared, said in sorted(cands, key=lambda c: (c[0], c[1]), reverse=True):  # best matches claim their slot first
        key = (m["path"], round(m["start"], 1))
        if key in used or any(abs(a - c["t0"]) < 7.0 for c in cuts):
            continue
        used.add(key)
        cuts.append({"t0": round(a, 3), "t1": round(b, 3), "path": m["path"], "src": round(m["start"] + 0.2, 3),
                     "why": (f"you say '{', '.join(sorted(shared & said)[:3])}'" if shared <= said else
                             f"shows '{', '.join(sorted(shared)[:3])}' (the AI editor's idea of what to show)")
                            + f" -> {Path(m['path']).name} at {m['start']:.0f}s"})
    cuts.sort(key=lambda c: c["t0"])
    return cuts


def apply(base: Path, cuts: list[dict], w: int, h: int, fps: int, dest: Path, quality: str) -> Path:
    """Overlay the cutaways on the edit (picture only; your voice continues). Short cross-fades in and out."""
    from video_studio.engines import ff

    args, chains, last = ["-i", str(base)], [], "[0:v]"
    for i, c in enumerate(cuts, 1):
        d = c["t1"] - c["t0"]
        args += ["-ss", f"{c['src']:.3f}", "-t", f"{d + 0.1:.3f}", "-i", c["path"]]
        chains.append(f"[{i}:v]fps={fps},scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,"
                      f"format=yuva420p,fade=t=in:st=0:d=0.12:alpha=1,fade=t=out:st={max(0.0, d - 0.12):.3f}:d=0.12:alpha=1,"
                      f"setpts=PTS-STARTPTS+{c['t0']:.3f}/TB[b{i}]")
        chains.append(f"{last}[b{i}]overlay=eof_action=pass:enable='between(t,{c['t0']:.3f},{c['t1']:.3f})'[o{i}]")
        last = f"[o{i}]"
    ff.ff([*args, "-filter_complex", ";".join(chains), "-map", last, "-an", *ff.x264_args(quality), str(dest)])
    return dest
