"""Captions: word-timed ASS (burned in, styled) plus SRT/VTT sidecars."""

from __future__ import annotations

from pathlib import Path

from video_studio.looks import LOOKS, hex_rgb

STYLES = {
    # font size as a fraction of frame height, words per chunk, uppercase
    "bold_pop": {"size": 0.052, "words": 3, "upper": True, "font": "Bahnschrift", "bold": -1, "outline": 0.09, "pop": True},
    "karaoke": {"size": 0.044, "words": 5, "upper": False, "font": "Segoe UI Black", "bold": -1, "outline": 0.08, "pop": False},
    "reel": {"size": 0.072, "words": 3, "upper": True, "font": "Barlow Condensed ExtraBold Italic", "bold": 0, "italic": -1,
             "outline": 0.075, "pop": True},
    "clean": {"size": 0.040, "words": 6, "upper": False, "font": "Segoe UI Semibold", "bold": -1, "outline": 0.045, "pop": False},
}


def _ass_color(hex_value: str, alpha: int = 0) -> str:
    r, g, b = hex_rgb(hex_value)
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"


def _ts(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _srt_ts(t: float) -> str:
    t = max(0.0, t)
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def clean_words(words: list[dict]) -> list[dict]:
    """Sorted, non-overlapping word timings (scene cross-fades can make neighbouring lines overlap)."""
    out: list[dict] = []
    for w in sorted((dict(x) for x in words if x.get("text", "").strip()), key=lambda x: x["start"]):
        if out and w["start"] < out[-1]["end"]:
            out[-1]["end"] = max(out[-1]["start"] + 0.05, w["start"])
            w["start"] = max(w["start"], out[-1]["end"])
        w["end"] = max(w["end"], w["start"] + 0.05)
        out.append(w)
    return out


def chunk_words(words: list[dict], per_chunk: int, max_gap: float = 0.6) -> list[list[dict]]:
    words = clean_words(words)
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        if cur and (len(cur) >= per_chunk or w["start"] - cur[-1]["end"] > max_gap
                    or cur[-1]["text"].rstrip().endswith((".", "!", "?", ","))):
            chunks.append(cur)
            cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


def karaoke_body(chunk: list[dict], texts: list[str], line_start: float) -> str:
    """ASS karaoke text where each word's fill starts at that word's own start time.

    \\k times run one after another, so the pause between two words must be counted too: it goes on the space
    after the earlier word (a plain \\k, nothing visible changes). Positions are rounded from the line start, so
    rounding never adds up along a long line."""
    parts, cursor = [], 0
    for i, (w, t) in enumerate(zip(chunk, texts)):
        a = max(cursor, int(round((w["start"] - line_start) * 100)))
        b = max(a + 1, int(round((w["end"] - line_start) * 100)))
        if i + 1 < len(chunk):  # never fill past the next word's start
            b = max(a + 1, min(b, int(round((chunk[i + 1]["start"] - line_start) * 100))))
        if parts:
            parts.append(f"{{\\k{a - cursor}}} " if a > cursor else " ")
        parts.append(f"{{\\kf{b - a}}}{t}")
        cursor = b
    return "".join(parts)


def build_ass(words: list[dict], width: int, height: int, style: str, look: str, dest: Path,
              y_ratio: float = 0.72, overlays: list[dict] | None = None, top: bool = False) -> Path:
    """words: absolute-timed [{text,start,end}] for the whole video."""
    st = STYLES.get(style, STYLES["bold_pop"])
    palette = LOOKS[look]
    size = int(height * st["size"]) if height >= width else int(height * st["size"] * 1.25)
    outline = max(2, int(size * st["outline"]))
    margin_v = int(height * y_ratio) if top else int(height * (1 - y_ratio))  # top: y_ratio is the top edge
    highlight = palette["accent"]
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{st['font']},{size},{_ass_color('#FFFFFF')},{_ass_color(highlight)},{_ass_color('#000000', 0x10)},{_ass_color('#000000', 0x60)},{st['bold']},{st.get('italic', 0)},0,0,100,100,0,0,1,{outline},{max(1, outline // 2)},{8 if top else 2},{int(width * 0.08)},{int(width * 0.08)},{margin_v},1
Style: Title,Bahnschrift,{int(size * 1.35)},{_ass_color('#FFFFFF')},{_ass_color(highlight)},{_ass_color('#000000', 0x10)},{_ass_color('#000000', 0x70)},-1,0,0,0,100,100,0,0,1,{outline + 1},{outline // 2 + 1},8,{int(width * 0.07)},{int(width * 0.07)},{int(height * 0.12)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    hi = _ass_color(highlight).replace("&H00", "&H")
    chunks = chunk_words(words, st["words"])
    for ci, chunk in enumerate(chunks):
        start, end = chunk[0]["start"], max(chunk[-1]["end"], chunk[0]["start"] + 0.35)
        limit = chunks[ci + 1][0]["start"] - 0.02 if ci + 1 < len(chunks) else end + 0.6
        end = min(end, limit - 0.12) if limit - 0.12 > start else start + 0.2
        texts = [(w["text"].upper() if st["upper"] else w["text"]).replace("{", "(").replace("}", ")") for w in chunk]
        if style == "karaoke":
            body = karaoke_body(chunk, texts, start)
            lines.append(f"Dialogue: 0,{_ts(start)},{_ts(min(end + 0.15, limit))},Cap,,0,0,0,,{{\\1c&HFFFFFF&\\2c{hi}&}}{body}")
            continue
        # One event per word: the active word is highlighted (and pops) while the rest stay white.
        for i, w in enumerate(chunk):
            w_start = w["start"]
            w_end = chunk[i + 1]["start"] if i + 1 < len(chunk) else min(end + 0.12, limit)
            parts = []
            for j, t in enumerate(texts):
                if j == i:
                    pop = "\\t(0,90,\\fscx112\\fscy112)\\t(90,180,\\fscx100\\fscy100)" if st["pop"] else ""
                    parts.append(f"{{\\1c{hi}&{pop}}}{t}{{\\1c&HFFFFFF&\\fscx100\\fscy100}}")
                else:
                    parts.append(t)
            intro = "{\\fad(60,0)}" if i == 0 else ""
            lines.append(f"Dialogue: 0,{_ts(w_start)},{_ts(max(w_end, w_start + 0.05))},Cap,,0,0,0,,{intro}{' '.join(parts)}")
    for ov in overlays or []:
        lines.append(f"Dialogue: 1,{_ts(ov['start'])},{_ts(ov['end'])},Title,,0,0,0,,{{\\fad(150,150)}}{ov['text']}")
    dest.write_text(header + "\n".join(lines) + "\n", encoding="utf-8-sig")
    return dest


def build_srt(words: list[dict], dest: Path, per_chunk: int = 7) -> Path:
    out = []
    chunks = chunk_words(words, per_chunk, 0.8)
    for i, chunk in enumerate(chunks, 1):
        start = chunk[0]["start"]
        end = max(chunk[-1]["end"] + 0.1, start + 0.2)
        if i < len(chunks):
            end = min(end, chunks[i][0]["start"] - 0.01)
        end = max(end, start + 0.01)
        out.append(f"{i}\n{_srt_ts(start)} --> {_srt_ts(end)}\n{' '.join(w['text'] for w in chunk)}\n")
    dest.write_text("\n".join(out), encoding="utf-8")
    return dest


def build_vtt(srt: Path, dest: Path) -> Path:
    import re

    body = re.sub(r"(\d\d:\d\d:\d\d),(\d\d\d)", r"\1.\2", srt.read_text(encoding="utf-8"))
    dest.write_text("WEBVTT\n\n" + body, encoding="utf-8")
    return dest
