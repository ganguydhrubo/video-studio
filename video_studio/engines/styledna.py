"""Style DNA: learn the editing style of reference videos YOU choose (any genre), locally, and apply it to new edits.

Measured per reference: cut rhythm (shot lengths, cuts per beat), how often cuts land on the beat, motion energy,
zoom/punch-in frequency, colour grade (Lab means and spread, saturation, contrast), brightness, text-free assumptions.
Nothing is uploaded; only the numbers are stored (app data / style_refs/<name>.json). Footage itself is never copied.
"""

from __future__ import annotations

import hashlib
import statistics
from pathlib import Path

from video_studio.engines import sense
from video_studio.util import app_data_dir, console, read_json, write_json

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}


def refs_dir() -> Path:
    d = app_data_dir() / "style_refs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache(video: Path) -> Path:
    st = video.stat()
    h = hashlib.sha1(f"{video.resolve()}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:16]
    d = app_data_dir() / "cache" / "sense"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{h}.json"


def dna_of(video: Path) -> dict:
    s = sense.analyse(video, _cache(video))
    rows, shots, aud = s.get("timeline", []), s.get("shots", []), s.get("audio", {})
    if not rows:
        return {}
    dur = rows[-1]["t"] + 1 / 6
    lens = [x["end"] - x["start"] for x in shots] or [dur]
    cuts = [x["start"] for x in shots[1:]]
    beats = aud.get("beats") or []
    beat = (beats[-1] - beats[0]) / max(1, len(beats) - 1) if len(beats) > 1 else 0.0
    on_beat = (sum(1 for c in cuts if beats and min(abs(c - b) for b in beats) <= 0.1) / len(cuts)) if cuts and beats else 0.0
    zooms = sum(1 for a, b in zip(rows, rows[1:]) if b.get("zoom", 0) > 0.8 and a.get("zoom", 0) <= 0.8)
    med = lambda k: statistics.median([r[k] for r in rows if r.get(k) is not None])  # noqa: E731
    lab = [statistics.median([r["lab"][i] for r in rows if r.get("lab")]) for i in range(3)] if rows[0].get("lab") else None
    lab_sd = [statistics.median([r["lab_sd"][i] for r in rows if r.get("lab_sd")]) for i in range(3)] if rows[0].get("lab_sd") else None
    return {"file": video.name, "duration": round(dur, 2), "type": s.get("type"), "shot_len": round(statistics.median(lens), 2),
            "cuts_per_min": round(len(cuts) / max(0.1, dur / 60), 1), "beats_per_cut": round(statistics.median(lens) / beat, 2) if beat else None,
            "cuts_on_beat": round(on_beat, 2), "tempo": aud.get("tempo"), "motion": round(med("motion"), 2),
            "zooms_per_min": round(zooms / max(0.1, dur / 60), 1), "brightness": round(med("bright"), 3), "contrast": round(med("contrast"), 3),
            "saturation": round(med("sat"), 3) if rows[0].get("sat") is not None else None, "lab": lab, "lab_sd": lab_sd}


def learn(folder: Path, name: str) -> dict:
    vids = sorted(p for p in folder.rglob("*") if p.suffix.lower() in VIDEO_EXT) if folder.is_dir() else [folder]
    if not vids:
        console.warn(f"No videos found in {folder}.")
        return {}
    dnas = []
    for i, v in enumerate(vids):
        console.step(f"Studying reference {i + 1}/{len(vids)}: {v.name}")
        try:
            d = dna_of(v)
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the rest
            console.warn(f"  skipped ({exc})")
            continue
        if d:
            dnas.append(d)
            console.info(f"  shot {d['shot_len']} s | {d['cuts_per_min']} cuts/min | on-beat {int(d['cuts_on_beat'] * 100)}% | "
                         f"{d['zooms_per_min']} zooms/min | sat {d['saturation']}")
    if not dnas:
        return {}

    def agg(k):
        vals = [d[k] for d in dnas if d.get(k) is not None]
        return statistics.median(vals) if vals else None

    profile = {"name": name, "references": [d["file"] for d in dnas], "count": len(dnas),
               **{k: agg(k) for k in ("shot_len", "cuts_per_min", "beats_per_cut", "cuts_on_beat", "motion", "zooms_per_min",
                                      "brightness", "contrast", "saturation")},
               "lab": [statistics.median([d["lab"][i] for d in dnas if d.get("lab")]) for i in range(3)] if any(d.get("lab") for d in dnas) else None,
               "lab_sd": [statistics.median([d["lab_sd"][i] for d in dnas if d.get("lab_sd")]) for i in range(3)] if any(d.get("lab_sd") for d in dnas) else None,
               "videos": dnas}
    write_json(refs_dir() / f"{_slug(name)}.json", profile)
    console.ok(f"Style '{name}' learned from {len(dnas)} video(s): {describe(profile)}")
    return profile


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in name.lower()).strip("_") or "style"


def load(name: str | None) -> dict | None:
    if not name:
        return None
    return read_json(refs_dir() / f"{_slug(name)}.json", None)


def available() -> list[str]:
    return sorted(p.stem for p in refs_dir().glob("*.json"))


def describe(p: dict) -> str:
    pace = "very fast" if p["shot_len"] < 1.0 else "fast" if p["shot_len"] < 2.0 else "medium" if p["shot_len"] < 4.0 else "slow"
    bits = [f"{pace} cutting (~{p['shot_len']:.1f} s per shot)"]
    if p.get("cuts_on_beat") and p["cuts_on_beat"] > 0.5:
        bits.append("cuts on the beat")
    if p.get("zooms_per_min") and p["zooms_per_min"] > 6:
        bits.append("lots of punch-in zooms")
    if p.get("saturation") is not None:
        bits.append("rich colour" if p["saturation"] > 0.42 else "muted colour" if p["saturation"] < 0.25 else "natural colour")
    return ", ".join(bits)


def apply(profile: dict | None) -> dict:
    """Edit parameters derived from a learned style (used by the planner and the style engine)."""
    if not profile:
        return {}
    shot = max(0.4, min(5.0, profile.get("shot_len") or 2.0))
    out = {"shot_lo": round(max(0.5, shot * 0.6), 2), "shot_hi": round(max(1.0, shot * 1.4), 2),
           "trans_every": round(max(0.6, shot * 1.5), 2), "punch_rate": profile.get("zooms_per_min") or 0}
    if profile.get("beats_per_cut"):
        bpc = profile["beats_per_cut"]
        out["beats_per_cut"] = 1 if bpc < 1.5 else 2 if bpc < 3 else 4 if bpc < 6 else 8
    if profile.get("lab") and profile.get("lab_sd"):
        out["grade_ref"] = {"lab": profile["lab"], "lab_sd": profile["lab_sd"]}
    return out
