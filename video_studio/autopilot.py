"""Autopilot: everything after "the clip is on this PC" happens by itself.

The window starts the edit once the clips are copied (studio_ui/autopilot.js). The server then:
  - retries a stopped edit once, from the stage where it stopped (finished stages are reused);
  - makes the three share covers (reel 9:16, profile grid 3:4, YouTube 16:9) from the finished film,
    so the Share tab is complete without anyone opening the cover maker.

Covers made here are marked in exports/covers_auto.json. They are remade when the film changes (a refine or
re-edit). Covers the person saved from the cover maker are never replaced.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

from video_studio.util import console, read_json, write_json

COVER_SHAPES = {"reel": (1080, 1920), "grid": (1080, 1440), "youtube": (1280, 720)}
MARK = "covers_auto.json"
FONT_DIR = Path(r"C:\Windows\Fonts")
LATIN_FONTS = ("segoeuib.ttf", "arialbd.ttf", "ariblk.ttf", "calibrib.ttf")
INDIC_FONTS = ("NirmalaB.ttf", "Nirmala.ttc", "Nirmala.ttf")   # Devanagari, Bengali and other Indian scripts
INDIC = re.compile(r"[\u0900-\u0DFF]")
RETRIES = 1   # one automatic second try per job; a clip that fails twice needs a person


def cover_title(root: Path) -> str:
    """The same title the Share tab offers: the hook words, else the plan title, else the project name."""
    plan = read_json(root / "build" / "edit_plan.json") or read_json(root / "build" / "plan.json") or {}
    data = read_json(root / "project.json", {}) or {}
    return " ".join(str(plan.get("hook_text") or plan.get("title") or data.get("name") or "").split())


def pick_font(text: str, font_dir: Path = FONT_DIR) -> Path | None:
    for name in (INDIC_FONTS + LATIN_FONTS) if INDIC.search(text) else LATIN_FONTS:
        if (font_dir / name).is_file():
            return font_dir / name
    return None


def wrap_title(text: str, width: int, height: int) -> tuple[list[str], int]:
    """Bold cover title: big words, at most three lines, about 86% of the width (same rules as the cover maker)."""
    text = text.upper().strip()
    if not text:
        return [], 0
    size = round(min(width, height * 0.75) * 0.13)
    while True:
        per_line = max(1, int(width * 0.86 / (size * 0.66)))  # bold capitals are about 0.66 of the size wide
        lines, line = [], ""
        for word in text.split():
            trial = f"{line} {word}" if line else word
            if len(trial) > per_line and line:
                lines.append(line)
                line = word
            else:
                line = trial
        if line:
            lines.append(line)
        longest = max(len(x) for x in lines)
        if (len(lines) <= 3 and longest <= per_line) or size <= 14:
            return lines, size
        size = round(size * 0.88)


def cover_filter(src_w: int, src_h: int, width: int, height: int, lines: list[str], size: int,
                 font: Path | None, text_files: list[Path], keep_top: bool = False) -> str:
    from video_studio.engines.ff import filter_escape

    src, dst = src_w / src_h, width / height
    if max(src, dst) / min(src, dst) > 1.6:   # tall film on a wide cover (or the reverse): blurred fill, nothing cut off
        graph = (f"[0:v]split=2[bgs][fgs];[bgs]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
                 f"gblur=sigma={max(width, height) // 40},eq=brightness=-0.12[bg];"
                 f"[fgs]scale={width}:{height}:force_original_aspect_ratio=decrease[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2")
    else:
        # keep_top: the film's own title sits near the top of the frame, so crop from the top instead of the centre
        graph = f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}" + (":(iw-ow)/2:0" if keep_top else "")
    graph += ",setsar=1,format=yuv420p"
    if lines and font:
        top, strips = height * 0.45, 12
        for i in range(strips):   # dark fade at the bottom so the words stay readable on any picture
            y0 = int(top + i * (height - top) / strips)
            graph += f",drawbox=x=0:y={y0}:w=iw:h={int((height - top) / strips) + 1}:color=black@{0.72 * (i + 1) / strips:.3f}:t=fill"
        line_h, bottom = size * 1.08, height * 0.86
        for i, path in enumerate(text_files):
            baseline = bottom - (len(lines) - 1 - i) * line_h
            graph += (f",drawtext=fontfile='{filter_escape(font)}':textfile='{filter_escape(path)}':expansion=none:"
                      f"fontsize={size}:fontcolor=white:borderw={max(2, round(size * 0.07))}:bordercolor=black@0.85:"
                      f"x=(w-text_w)/2:y={baseline:.0f}-ascent")
    return graph


def _film(root: Path) -> Path | None:
    master = root / "output" / "master.mp4"
    return master if master.is_file() else None


def needs_covers(root: Path) -> bool:
    """True when there are no covers yet, or when the covers were made here and the film is newer."""
    film, exports = _film(root), root / "exports"
    if not film:
        return False
    have = [exports / f"cover_{k}.jpg" for k in COVER_SHAPES]
    if not all(p.is_file() for p in have):
        return True
    mark = read_json(exports / MARK, None)
    if not mark:
        return False   # saved by the person from the cover maker: never replace
    return film.stat().st_mtime > float(mark.get("film_time", 0)) + 0.5


def make_covers(root: Path, font_dir: Path = FONT_DIR) -> dict:
    """Reel, grid and YouTube covers from the hook frame of the finished film. The reel cover becomes the thumbnail."""
    from video_studio.engines import ff

    film = _film(root)
    if not film:
        return {"error": "There is no finished film yet."}
    exports = root / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    info = ff.probe(film)
    v = ff.stream(info, "video")
    dur = float(info["format"].get("duration") or 0)
    plan = read_json(root / "build" / "edit_plan.json", {}) or read_json(root / "build" / "plan.json", {}) or {}
    at = 1.1 if plan.get("hook_text") else min(dur * 0.3, 4.0)
    title = cover_title(root)
    font = pick_font(title, font_dir)
    slams = (read_json(root / "build" / "style_plan.json", {}) or {}).get("slams") or []
    in_film = any(float(x.get("t0", 99)) <= at <= float(x.get("t1", -1)) and x.get("text") for x in slams)
    saved: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="tb-cover-") as tmp:
        frame = ff.frame_at(film, min(at, max(0.0, dur - 0.1)), Path(tmp) / "frame.jpg")
        sw, sh = int(v["width"]), int(v["height"])
        for key, (w, h) in COVER_SHAPES.items():
            # the frame already shows the film's own big title: a second one only where the film sits small (blurred fill)
            far = max(sw / sh, w / h) / min(sw / sh, w / h) > 1.6
            lines, size = wrap_title(title, w, h) if font and (far or not in_film) else ([], 0)
            files = []
            for i, line in enumerate(lines):
                path = Path(tmp) / f"{key}_{i}.txt"
                path.write_text(line, encoding="utf-8")
                files.append(path)
            dest = exports / f"cover_{key}.jpg"
            ff.ff(["-i", str(frame), "-filter_complex", cover_filter(sw, sh, w, h, lines, size, font, files, keep_top=in_film and not far),
                   "-frames:v", "1", "-q:v", "3", str(dest)], timeout=120)
            saved[key] = str(dest)
    thumb = next(iter(sorted(p for p in exports.glob("*_thumbnail.jpg"))), None)
    if thumb:   # same rule as the cover maker: the plain frame is kept as *_thumbnail_auto.jpg
        auto = thumb.with_name(thumb.stem + "_auto.jpg")
        if not auto.exists():
            shutil.copy2(thumb, auto)
        shutil.copy2(saved["reel"], thumb)
    write_json(exports / MARK, {"film_time": film.stat().st_mtime, "title": title, "font": font.name if font else ""})
    return {"ok": True, "covers": saved, "title": title, "font": font.name if font else ""}


def finish(root: Path, say=print) -> dict:
    """The last automatic step after a finished edit. Never fails the edit: a problem here is reported, not raised."""
    if not needs_covers(root):
        return {"skipped": True}
    say("Making covers for Instagram, the profile grid and YouTube")
    try:
        result = make_covers(root)
    except Exception as exc:  # noqa: BLE001 - the film is finished; covers can still be made by hand
        console.debug(f"autopilot covers failed: {exc}")
        say("Covers could not be made by themselves. The film is ready; open Share to make covers.")
        return {"error": str(exc)}
    if result.get("ok"):
        say("Covers ready: reel 9:16, profile grid 3:4, YouTube 16:9")
    return result
