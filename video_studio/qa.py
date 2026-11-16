"""Automated quality assurance on a rendered video (all local, FFmpeg-based)."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from video_studio.engines import ff
from video_studio.util import ToolError, console, run, write_json


def _cmd_stderr(args: list[str], timeout: float = 1800) -> str:
    res = run([ff.ffmpeg(), "-hide_banner", "-nostdin", *args], check=False, timeout=timeout)
    return res.stderr or ""


def check(video: Path, expect: dict, report_dir: Path, captions: Path | None = None) -> dict:
    """expect: {width, height, fps, duration?, loudness}. Returns a report dict with PASS/WARN/FAIL items."""
    report_dir.mkdir(parents=True, exist_ok=True)
    items: list[dict] = []

    def add(name: str, status: str, detail: str, value=None) -> None:
        items.append({"check": name, "status": status, "detail": detail, "value": value})

    if not video.exists() or video.stat().st_size < 1000:
        raise ToolError(f"No rendered video at {video}.", "Run `video-studio render` first.")
    info = ff.probe(video)
    v, a = ff.stream(info, "video"), ff.stream(info, "audio")
    dur = float(info.get("format", {}).get("duration", 0) or 0)

    # container / codecs
    ok_codec = v.get("codec_name") == "h264" and v.get("pix_fmt") == "yuv420p"
    add("video codec", "PASS" if ok_codec else "FAIL", f"{v.get('codec_name')} {v.get('pix_fmt')} {v.get('profile', '')}")
    add("audio codec", "PASS" if a.get("codec_name") == "aac" else ("FAIL" if not a else "WARN"),
        f"{a.get('codec_name', 'none')} {a.get('sample_rate', '')} Hz {a.get('channels', '')}ch")
    w, h = int(v.get("width", 0)), int(v.get("height", 0))
    add("resolution", "PASS" if (w, h) == (expect["width"], expect["height"]) else "FAIL",
        f"{w}x{h} (expected {expect['width']}x{expect['height']})", [w, h])
    aspect_ok = abs(w / max(h, 1) - expect["width"] / expect["height"]) < 0.01
    add("aspect ratio", "PASS" if aspect_ok else "FAIL", f"{w / max(h, 1):.4f}")
    fps = ff.fps_of(v)
    add("frame rate", "PASS" if abs(fps - expect["fps"]) < 0.05 else "FAIL", f"{fps:.3f} fps", fps)
    if expect.get("duration"):
        diff = abs(dur - expect["duration"])
        add("duration", "PASS" if diff < 0.6 else "WARN", f"{dur:.2f}s (planned {expect['duration']:.2f}s)", dur)
    else:
        add("duration", "PASS" if dur > 0.5 else "FAIL", f"{dur:.2f}s", dur)
    vdur, adur = float(v.get("duration", dur) or dur), float(a.get("duration", dur) or dur) if a else 0
    sync = abs(vdur - adur) if a else 0
    add("A/V sync (stream length)", "PASS" if sync < 0.1 else ("WARN" if sync < 0.3 else "FAIL"), f"video {vdur:.3f}s / audio {adur:.3f}s", sync)
    add("fast start (moov first)", "PASS" if _moov_first(video) else "WARN", "web-optimised MP4" if _moov_first(video) else "moov atom at end")

    # full decode + black/freeze/silence/loudness in one pass
    log = _cmd_stderr(["-i", str(video), "-vf", "blackdetect=d=0.4:pic_th=0.97,freezedetect=n=-55dB:d=3.5",
                       "-af", "silencedetect=n=-45dB:d=2.0,ebur128=peak=true,astats=metadata=0:measure_overall=Peak_level",
                       "-f", "null", "-"])
    errors = [l for l in log.splitlines() if re.search(r"error|corrupt|invalid", l, re.I) and "Error while" not in l][:5]
    add("full decode", "PASS" if not errors else "FAIL", "decoded every frame" if not errors else "; ".join(errors))
    blacks = [(float(s), float(e)) for s, e in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", log)]
    inner = [(s, e) for s, e in blacks if s > 0.3 and e < dur - 0.3]
    add("black frames", "PASS" if not inner else "FAIL", f"{len(inner)} black segment(s) inside the video" if inner else "none", inner)
    freezes = re.findall(r"freeze_start: ([\d.]+)", log)
    add("frozen video", "PASS" if not freezes else "WARN", f"{len(freezes)} still section(s) > 3.5 s" if freezes else "none")
    silences = [(float(s), float(e)) for s, e in zip(re.findall(r"silence_start: ([\d.]+)", log), re.findall(r"silence_end: ([\d.]+)", log))]
    inner_sil = [(s, e) for s, e in silences if s > 0.5 and e < dur - 0.5]
    add("dead air", "PASS" if not inner_sil else "WARN", f"{len(inner_sil)} silent gap(s) > 2 s" if inner_sil else "none", inner_sil)
    lufs = re.findall(r"I:\s+(-?[\d.]+) LUFS", log)
    tpk = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", log)
    if lufs:
        val = float(lufs[-1])
        target = expect.get("loudness", -14.0)
        add("loudness (EBU R128)", "PASS" if abs(val - target) <= 1.5 else "WARN", f"{val:.1f} LUFS (target {target})", val)
    if tpk:
        peak = float(tpk[-1])
        add("true peak / clipping", "PASS" if peak <= -0.5 else ("WARN" if peak < 0 else "FAIL"), f"{peak:.1f} dBTP", peak)
    else:
        peak_levels = [float(x) for x in re.findall(r"Peak level dB:\s+(-?[\d.]+|-inf)", log) if x != "-inf"]
        if peak_levels:
            add("clipping", "PASS" if max(peak_levels) < -0.1 else "FAIL", f"peak {max(peak_levels):.2f} dBFS")

    # frame inspection: sample frames, check for blank/overexposed images, build a contact sheet
    samples = [dur * f for f in (0.03, 0.15, 0.3, 0.45, 0.6, 0.75, 0.9, 0.98)]
    thumbs, flat, blown = [], [], []
    for i, t in enumerate(samples):
        p = report_dir / f"frame_{i:02d}.jpg"
        try:
            ff.frame_at(video, t, p, 320)
        except ToolError:
            continue
        img = Image.open(p).convert("L")
        arr = np.asarray(img, np.float32)
        if arr.std() < 4:
            flat.append(round(t, 2))
        if (arr > 250).mean() > 0.35:
            blown.append(round(t, 2))
        thumbs.append((t, Image.open(p).convert("RGB")))
    add("frame inspection", "PASS" if not flat else "WARN",
        f"{len(thumbs)} frames checked; blank-looking at {flat}" if flat else f"{len(thumbs)} frames checked, all have content")
    add("exposure", "PASS" if not blown else "WARN", f"overexposed at {blown}" if blown else "no blown-out frames")
    sheet = _contact_sheet(thumbs, report_dir / "contact_sheet.jpg")

    if captions and captions.exists():
        cues = re.findall(r"(\d\d):(\d\d):(\d\d),(\d\d\d) --> (\d\d):(\d\d):(\d\d),(\d\d\d)", captions.read_text(encoding="utf-8"))
        bad = 0
        last_end = 0.0
        for c in cues:
            s = int(c[0]) * 3600 + int(c[1]) * 60 + int(c[2]) + int(c[3]) / 1000
            e = int(c[4]) * 3600 + int(c[5]) * 60 + int(c[6]) + int(c[7]) / 1000
            if e <= s or e > dur + 0.5 or s < last_end - 0.25:
                bad += 1
            last_end = e
        add("captions timing", "PASS" if cues and not bad else ("WARN" if cues else "WARN"),
            f"{len(cues)} cues, {bad} out of range/overlapping")

    place = expect.get("caption_place")
    if place:
        ok = place["clear"] and place["y"] <= 0.801
        edge = "top edge" if place.get("top") else "bottom edge"
        face = f"face {place['face_top']:.0%}-{place['face_bottom']:.0%}" if place.get("face_bottom") is not None else "no face"
        add("captions vs face & app buttons", "PASS" if ok else "WARN",
            f"caption {edge} at {place['y']:.0%}, {face}, safe area 8%-80%: {place['why']}")

    fails = sum(1 for i in items if i["status"] == "FAIL")
    warns = sum(1 for i in items if i["status"] == "WARN")
    score = max(0, 100 - fails * 20 - warns * 5)
    report = {"video": str(video), "duration": dur, "size": video.stat().st_size, "items": items,
              "fails": fails, "warnings": warns, "score": score, "contact_sheet": str(sheet) if sheet else ""}
    write_json(report_dir / "qa_report.json", report)
    (report_dir / "qa_report.md").write_text(to_markdown(report), encoding="utf-8")
    return report


def _moov_first(path: Path) -> bool:
    with path.open("rb") as fh:
        head = fh.read(1 << 16)
    m, d = head.find(b"moov"), head.find(b"mdat")
    return m != -1 and (d == -1 or m < d)


def _contact_sheet(thumbs: list, dest: Path) -> Path | None:
    if not thumbs:
        return None
    tw, th = thumbs[0][1].size
    cols = 4
    rows = (len(thumbs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tw, rows * (th + 24)), (18, 18, 22))
    d = ImageDraw.Draw(sheet)
    for i, (t, img) in enumerate(thumbs):
        x, y = (i % cols) * tw, (i // cols) * (th + 24)
        sheet.paste(img.resize((tw, th)), (x, y + 24))
        d.text((x + 6, y + 5), f"{t:6.2f}s", fill=(220, 220, 220))
    sheet.save(dest, quality=88)
    return dest


def to_markdown(report: dict) -> str:
    icon = {"PASS": "PASS", "WARN": "WARN", "FAIL": "FAIL"}
    lines = [f"# QA report", "", f"- File: `{report['video']}`", f"- Duration: {report['duration']:.2f}s",
             f"- Score: **{report['score']}/100** ({report['fails']} fail, {report['warnings']} warn)", "",
             "| Check | Result | Detail |", "|---|---|---|"]
    for it in report["items"]:
        lines.append(f"| {it['check']} | {icon[it['status']]} | {str(it['detail']).replace('|', '/')} |")
    if report.get("contact_sheet"):
        lines += ["", f"Contact sheet: `{report['contact_sheet']}`"]
    return "\n".join(lines) + "\n"


def print_report(report: dict) -> None:
    for it in report["items"]:
        fn = {"PASS": console.ok, "WARN": console.warn, "FAIL": console.error}[it["status"]]
        fn(f"{it['check']}: {it['detail']}")
    console.info(f"QA score {report['score']}/100 - {report['fails']} fail(s), {report['warnings']} warning(s)")
