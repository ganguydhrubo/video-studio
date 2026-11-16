"""`video-studio dub <project> --to en`: an English version of a finished edit, made entirely on this PC.

1. Meaning: Whisper's translate task on the edit's own clean voice track (any of its ~99 languages -> English),
   so every line is timed to the finished edit.
2. Voice: Kokoro speaks each line; a line longer than its slot is sped up (max 1.3x) or allowed to run into the
   following silence, never cut off.
3. Captions: re-made in English with the same style and the same face-safe position as the original.
4. Same picture (style pass, hook, effects), same music and SFX; then the usual QA.
The original voice is not kept. Synthetic voice: the result is labelled as a dub in its file name and QA report.
"""

from __future__ import annotations

import wave
from pathlib import Path

from video_studio.project import Project
from video_studio.util import ToolError, console, read_json, write_json

VOICES = {"en": ("am_michael", "a"), "en-f": ("af_heart", "a")}


def _read_wav(path: Path):
    import numpy as np

    with wave.open(str(path), "rb") as wf:
        rate, ch = wf.getframerate(), wf.getnchannels()
        data = np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2").astype("float32") / 32768.0
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    return data, rate


def _write_wav(path: Path, data, rate: int) -> None:
    import numpy as np

    pcm = (np.clip(data, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm.tobytes())


def dub(folder: Path, to: str = "en", voice: str = "") -> Path:
    import numpy as np

    from video_studio import qa
    from video_studio.engines import captions, compose, ff, ml, music, style

    proj = Project(folder)
    m = proj.read("edit_manifest.json")
    if not m or not proj.master.exists():
        raise ToolError("Edit the video first.", "Run `video-studio edit ...`, then dub the project folder.")
    if to != "en":
        raise ToolError(f"Dubbing into '{to}' is not available yet.", "Dubbing into English works from any spoken language.")
    total = m["total"]
    work = proj.build / f"dub_{to}"
    work.mkdir(exist_ok=True)
    console.step("Dub: understanding every line (local Whisper translation)")
    transcript = proj.read("transcript.json") or {}
    if transcript.get("language") == "en":
        raise ToolError("This video is already in English.")
    # the translation made from the WHOLE recording (full context = accurate); re-translating the short cut invents
    # text, so map each translated sentence onto the edit instead and drop sentences that were cut out
    src_lines = transcript.get("translation") or []
    if not src_lines:
        raise ToolError("This project has no translation yet.", "Re-run the edit with version 1.2.2 or newer, then dub.")
    lines = []
    for t in src_lines:
        parts = [(max(t["start"], pc["src_start"]), min(t["end"], pc["src_end"]), pc) for pc in m["pieces"]
                 if pc["src_start"] < t["end"] and pc["src_end"] > t["start"]]
        kept = sum(b - a for a, b, _ in parts)
        if not parts or kept < 0.6 * (t["end"] - t["start"]):
            continue  # mostly cut from the edit (retake, filler): its meaning is not in the video
        a, _, pc = parts[0]
        out_start = pc["out_start"] + (a - pc["src_start"])
        lines.append({"start": round(out_start, 3), "end": round(out_start + kept, 3), "text": t["text"]})
    if not lines:
        raise ToolError("No translatable speech found in the edit.")
    write_json(work / "lines.json", lines)
    console.step(f"Dub: speaking {len(lines)} lines in English (Kokoro, local)")
    v, lang = VOICES.get(voice or to, (voice, "a")) if voice else VOICES[to]
    items = [{"id": f"l{i:03d}", "text": ln["text"], "out": str(work / f"l{i:03d}.wav")} for i, ln in enumerate(lines)]
    ml.kokoro(items, v, 1.0, lang)
    rate = 24000
    track = np.zeros(int((total + 1) * rate), dtype="float32")
    cap_words, cursor = [], 0.0
    for i, (ln, it) in enumerate(zip(lines, items)):
        data, r = _read_wav(Path(it["out"]))
        if r != rate:
            data = np.interp(np.arange(int(len(data) * rate / r)) * r / rate, np.arange(len(data)), data)
        nxt = lines[i + 1]["start"] if i + 1 < len(lines) else total
        start = max(ln["start"], cursor)
        slot = max(0.3, nxt - start)
        dur = len(data) / rate
        if dur > slot:  # speed up (pitch-preserving via ffmpeg atempo) up to 1.3x, then let it run on
            factor = min(1.3, dur / slot)
            src, dst = work / f"{it['id']}_in.wav", work / f"{it['id']}_fit.wav"
            _write_wav(src, data, rate)
            ff.ff(["-i", str(src), "-af", f"atempo={factor:.3f}", "-ar", str(rate), str(dst)])
            data, _ = _read_wav(dst)
            dur = len(data) / rate
        a = int(start * rate)
        b = min(len(track), a + len(data))
        track[a:b] += data[: b - a]
        # captions: spread the line's words over its spoken time, weighted by length
        ws = ln["text"].split()
        weights = np.array([max(2, len(x)) for x in ws], dtype=float)
        edges = start + np.concatenate([[0], np.cumsum(weights)]) / weights.sum() * dur
        cap_words += [{"text": x, "start": round(float(edges[k]), 3), "end": round(float(edges[k + 1]), 3)} for k, x in enumerate(ws)]
        cursor = start + dur + 0.05
    dub_voice = work / "voice_dub.wav"
    _write_wav(dub_voice, track[: int(total * rate)], rate)

    console.step("Dub: English captions, same look and face-safe position")
    w, h = m["size"]
    splan = proj.read("style_plan.json") or {}
    d = proj.read("creative.json")["decisions"]
    fx_on = bool(splan.get("shots"))
    base_video = proj.build / "styled.mp4" if fx_on and (proj.build / "styled.mp4").exists() else Path(m["cut"])
    if fx_on:
        spans = [(x["t0"] - 0.05, x["t1"] + 0.15) for x in splan.get("slams", [])]
        cap_words = [x for x in cap_words if not any(a <= x["start"] < b for a, b in spans)]
    place = m.get("caption_place") or {}
    y = place.get("y", 0.72 if h > w else 0.9)
    ass = captions.build_ass(cap_words, w, h, "reel" if fx_on and d["captions"] != "none" else d["captions"], d["look"],
                             work / "captions_en.ass", y_ratio=y, top=bool(place.get("top")))
    srt = captions.build_srt(cap_words, work / "captions_en.srt")
    video_only = work / "video_en.mp4"
    fonts = work / "fonts"
    if (proj.build / "fonts").is_dir() and not fonts.exists():
        import shutil

        shutil.copytree(proj.build / "fonts", fonts)
    compose.assemble_video([base_video], [total], [], 0.0, m["fps"], w, h, "", ass, proj.settings["quality"], video_only,
                           overlays=[], total=total)
    console.step("Dub: mixing the English voice with the same music and effects")
    audio_dir = proj.build / "audio"
    music_files = sorted(audio_dir.glob("music_*.wav"), key=lambda f: f.stat().st_mtime)
    sfx = []
    for kind, at, gain in (style.sfx_events(splan) if fx_on else []):
        path = audio_dir / f"sfx_{kind}.wav"
        if not path.exists():
            music.write_wav(path, music.sfx(kind))
        sfx.append((path, at, gain))
    final_audio = work / "audio_en.wav"
    compose.mix_audio([(dub_voice, 0.0)], music_files[-1] if music_files else None, sfx, total, final_audio,
                      float(proj.settings["loudness"]), music_gain=0.14)
    dest = proj.output / f"master_dub_{to}.mp4"
    compose.mux(video_only, final_audio, dest)
    report = qa.check(dest, {"width": w, "height": h, "fps": int(proj.settings["fps"]), "duration": total,
                             "loudness": float(proj.settings["loudness"])}, proj.output / f"qa_dub_{to}", srt)
    qa.print_report(report)
    write_json(work / "dub.json", {"to": to, "voice": v, "lines": len(lines), "file": str(dest), "qa": report["score"],
                                   "synthetic_voice": True, "fonts": str(fonts)})
    console.ok(f"English dub (synthetic voice): {dest}")
    return dest
