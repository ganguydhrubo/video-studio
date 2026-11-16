"""`video-studio shorts`: one long video -> the N best self-contained shorts, each fully edited and styled.

Local only: one transcription of the long video, the editor brain scores every line (stress, key words, hook strength),
every window of complete lines between --min and --max seconds is scored, the best non-overlapping ones are cut out
(frame-accurate) and each goes through the full footage editor. Every clip gets a plain-English reason.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from video_studio import brain, editor
from video_studio.engines import ff, ml
from video_studio.project import slug
from video_studio.util import ToolError, console, fmt_seconds, write_json


def _complete_end(u: dict, nxt: dict | None) -> bool:
    return bool(re.search(r"[.?!।]$", u["text"].strip())) or nxt is None or nxt["start"] - u["end"] > 0.5


def _complete_start(u: dict, prev: dict | None) -> bool:
    return prev is None or u["start"] - prev["end"] > 0.4 or bool(re.search(r"[.?!।]$", prev["text"].strip()))


def pick(words: list[dict], prosody: list[dict] | None, count: int, lo: float, hi: float) -> list[dict]:
    utts = editor._utterances(words)
    if not utts:
        return []
    res = brain.analyze(words, prosody, utts)
    strength = res["strength"]
    scored = res["scored"]
    cands = []
    for i in range(len(utts)):
        for j in range(i, len(utts)):
            dur = utts[j]["end"] - utts[i]["start"]
            if dur > hi:
                break
            if dur < lo:
                continue
            span = utts[i:j + 1]
            mean = sum(strength.get(u["id"], 0) for u in span) / len(span)
            hook = strength.get(utts[i]["id"], 0)
            score = 1.5 * hook + mean
            score += 0.8 if _complete_start(utts[i], utts[i - 1] if i else None) else -1.0
            score += 0.8 if _complete_end(utts[j], utts[j + 1] if j + 1 < len(utts) else None) else -1.0
            cands.append({"start": utts[i]["start"], "end": utts[j]["end"], "score": round(score, 3), "first": utts[i],
                          "lines": len(span)})
    chosen = []
    for c in sorted(cands, key=lambda c: -c["score"]):
        if len(chosen) >= count:
            break
        if any(c["start"] < x["end"] + 1.0 and c["end"] > x["start"] - 1.0 for x in chosen):
            continue
        ws = [s for s in scored if c["first"]["start"] - 0.01 <= s["start"] <= c["first"]["end"] + 0.01 and not s["features"]["stop"]]
        key = " ".join(brain._tok(s["display"]) for s in sorted(sorted(ws, key=lambda s: -s["score"])[:3], key=lambda s: s["start"]))
        opening = " ".join(brain.transliterate(t) for t in c["first"]["text"].split()[:9])
        c["reason"] = f"opens with a strong line (\"{opening}...\"), {c['lines']} complete line(s), key words: {key or '-'}"
        chosen.append(c)
    return sorted(chosen, key=lambda c: c["start"])


def run(src: Path, out: Path, count: int, lo: float, hi: float, brief: str, passthrough: list[str]) -> int:
    from video_studio import cli

    if not src.exists():
        raise ToolError(f"Video not found: {src}")
    out.mkdir(parents=True, exist_ok=True)
    work = out / "_long"
    work.mkdir(exist_ok=True)
    tr_file = work / "transcript.json"
    if tr_file.exists():
        tr = json.loads(tr_file.read_text(encoding="utf-8"))
    else:
        if not ml.available("faster_whisper"):
            raise ToolError("Speech recognition needs faster-whisper.", "video-studio setup")
        model = ml.best_whisper_model("auto")
        console.step(f"Listening to the whole video once ({fmt_seconds(ff.duration(src))}, {model})")
        tr = ml.transcribe(src, model=model)
        write_json(tr_file, tr)
    words = [w for w in tr["words"] if w["end"] > w["start"]]
    prosody = None
    try:
        wav = work / "voice16k.wav"
        if not wav.exists():
            ff.ff(["-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)])
        wf = work / "words.json"
        wf.write_text(json.dumps([{"start": w["start"], "end": w["end"]} for w in words]), encoding="utf-8")
        prosody = ml.call("prosody", audio=wav, words=wf, timeout=1800)["words"]
    except ToolError as exc:
        console.warn(f"Voice stress analysis skipped ({str(exc)[:80]}).")
    clips = pick(words, prosody, count, lo, hi)
    if not clips:
        raise ToolError(f"No complete {lo:.0f}-{hi:.0f} s stretch of speech found.", "Try --min 15 --max 90.")
    console.ok(f"Found {len(clips)} short(s):")
    for i, c in enumerate(clips, 1):
        console.info(f"  {i}. {fmt_seconds(c['start'])}-{fmt_seconds(c['end'])} ({c['end'] - c['start']:.0f} s): {c['reason']}")
    write_json(out / "shorts.json", clips)
    results = []
    for i, c in enumerate(clips, 1):
        clip = work / f"clip_{i:02d}.mov"
        a, b = max(0.0, c["start"] - 0.25), c["end"] + 0.35
        if not clip.exists():
            ff.ff(["-ss", f"{a:.3f}", "-i", str(src), "-t", f"{b - a:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "14",
                   "-c:a", "pcm_s16le", str(clip)])
        console.step(f"Editing short {i}/{len(clips)}")
        dest = out / f"short_{i:02d}"
        code = cli.main(["edit", str(clip), brief or "viral short, punchy", "--out", str(dest), "--name",
                         f"{slug(src.stem)}-short-{i:02d}", *passthrough])
        results.append({"short": i, "folder": str(dest), "ok": code == 0, **{k: c[k] for k in ("start", "end", "reason")}})
    write_json(out / "shorts.json", results)
    good = sum(1 for r in results if r["ok"])
    console.ok(f"{good}/{len(results)} shorts finished in {out}")
    return 0 if good == len(results) else 2
