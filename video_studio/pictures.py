"""Your pictures inside a video edit.

Drop pictures together with your clips and each picture appears once in the finished film as a short moving
cutaway (gentle zoom or pan, blurred fill so nothing is cut off). The sound carries on underneath, so your voice or
the music never stops. Pictures are spread evenly across the film in the order you added them; never over the first
moment (your opening), the last second, a giant-text moment or a B-roll cutaway.

If a film is too short for every picture, the first ones that fit are used and the log says how many were left out.
"""

from __future__ import annotations

from pathlib import Path

from video_studio.photos import IMAGE_TYPES, MOVES, clip_filter, still_filter
from video_studio.util import console

SHORTEST, LONGEST, GAP = 1.2, 2.6, 0.6   # seconds on screen per picture, and the least time back on the film between two


def split_media(paths: list[str | Path]) -> dict[str, list[Path]]:
    """Sort dropped files into clips, pictures and music by their type."""
    from video_studio.editor import MUSIC_EXT

    out: dict[str, list[Path]] = {"videos": [], "pictures": [], "music": []}
    for raw in paths:
        p = Path(raw)
        ext = p.suffix.lower()
        out["pictures" if ext in IMAGE_TYPES else "music" if ext in MUSIC_EXT else "videos"].append(p)
    return out


def slots(n: int, total: float, busy: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Where n pictures go in a film of `total` seconds, avoiding the busy spans. Fewer slots when they cannot fit."""
    start, end = min(1.6, total * 0.12), total - 1.0
    span = end - start
    if n <= 0 or span < SHORTEST:
        return []
    fit = max(1, int((span + GAP) // (SHORTEST + GAP)))
    n = min(n, fit)
    each = span / n
    dur = max(SHORTEST, min(LONGEST, each - GAP))
    out: list[tuple[float, float]] = []
    for i in range(n):
        lo, hi = start + i * each, start + (i + 1) * each   # this picture's share of the film
        a = lo + (each - dur) / 2
        for x, y in sorted(busy):
            if a < y and a + dur > x:   # clashes: move just after the busy span if the share still has room
                a = y + 0.05
        if a + dur > min(hi, end) + 1e-6 or (out and a < out[-1][1] + GAP * 0.5):
            continue
        out.append((round(a, 3), round(a + dur, 3)))
    return out


def render_still(pic: Path, dest: Path, w: int, h: int, fps: int, seconds: float, move: str) -> Path:
    """One picture as a short moving clip at the film's size (the same look as Photos to video)."""
    from video_studio.engines import ff

    frames = max(2, round(seconds * fps))
    graph = still_filter(w, h, 2) + ";[v]" + clip_filter(w, h, fps, frames, move) + "[out]"
    ff.ff(["-i", str(pic), "-filter_complex", graph, "-map", "[out]", "-frames:v", str(frames), "-an",
           *ff.x264_args("standard", intermediate=True), str(dest)], timeout=600)
    return dest


def apply_pass(proj, m: dict, video: Path, splan: dict, w: int, h: int, total: float) -> Path:
    """Lay the person's pictures over the edit. Returns the new picture track video (or the same one when none)."""
    from video_studio import broll

    if "pictures" in (m.get("timeline") or {}):  # timeline.py: the pictures the person kept, where they put them
        kept = [x for x in m["timeline"]["pictures"] if Path(x["path"]).is_file() and x["t1"] - x["t0"] >= 0.3]
        pics, places = [Path(x["path"]) for x in kept], [(x["t0"], x["t1"]) for x in kept]
        moves = [x.get("move") if x.get("move") in MOVES else MOVES[i % len(MOVES)] for i, x in enumerate(kept)]
        m["pictures"] = []
    else:
        pics = [Path(x) for x in proj.data.get("edit_options", {}).get("pictures") or []]
        pics = [p for p in pics if p.is_file() and p.suffix.lower() in IMAGE_TYPES]
        if not pics:
            return video
        busy = [(s["t0"] - 0.2, s["t1"] + 0.2) for s in (splan or {}).get("slams", []) if "t0" in s]
        busy += [(c["t0"] - 0.3, c["t1"] + 0.3) for c in m.get("broll") or []]
        places = slots(len(pics), total, busy)
        moves = [MOVES[i % len(MOVES)] for i in range(len(places))]
        if len(places) < len(pics):
            console.warn(f"Your pictures: the film is {total:.0f} s long, so {len(places)} of {len(pics)} pictures fit. "
                         "Ask for a longer video to show them all.")
    if not places:
        return video
    folder = proj.build / "pictures"
    folder.mkdir(parents=True, exist_ok=True)
    cuts = []
    for i, ((t0, t1), pic) in enumerate(zip(places, pics)):
        console.step(f"Adding your pictures: {i + 1} of {len(places)} ({pic.name})")
        clip = folder / f"pic_{i:02d}_{w}x{h}.mp4"
        render_still(pic, clip, w, h, m["fps"], t1 - t0 + 0.15, moves[i])
        cuts.append({"t0": t0, "t1": t1, "path": str(clip), "src": 0.0, "why": f"your picture {pic.name}", "picture": True})
    m["pictures"] = [{"t0": c["t0"], "t1": c["t1"], "name": Path(p).name, "path": str(p), "move": mv}
                     for c, p, mv in zip(cuts, pics, moves)]
    proj.write("edit_manifest.json", m)
    console.ok(f"Your pictures: {len(cuts)} placed across the film")
    return broll.apply(video, cuts, w, h, m["fps"], proj.build / "with_pictures.mp4", proj.settings["quality"])
