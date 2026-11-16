"""Script-to-video pipeline: analyze -> storyboard -> produce -> render -> validate -> export."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from video_studio import detect, memory, planner, qa
from video_studio.ai import Director
from video_studio.engines import canvas, captions, compose, ff, music, tts
from video_studio.looks import LOOKS, PACING, TRANSITION_SECONDS, TRANSITIONS
from video_studio.project import Project
from video_studio.util import ToolError, console, fmt_seconds, sha, write_json

LEAD = 0.25  # seconds between a scene appearing and its first spoken word


def capabilities(settings: dict) -> dict:
    blender_ok = bool(settings.get("use_blender")) and detect.blender_path() is not None
    hf_ok = settings.get("motion_engine") == "hyperframes" and detect.check_hyperframes().ok
    return {"blender_ok": blender_ok, "hyperframes_ok": hf_ok}


# ------------------------------------------------------------------ analyze


def analyze(proj: Project, use_ai: bool, seed: int | None = None) -> dict:
    t0 = time.time()
    s = proj.settings
    script = proj.script_text()
    instructions = proj.instructions()
    caps = capabilities(s)
    settings = {**s, **caps}
    director = Director(s.get("ai_provider"), usage_file=proj.build / "ai_usage.json") if use_ai else None
    console.step("Reading the script and planning scenes")
    plan = planner.analyze(script, instructions, settings, director, use_ai)
    creative = planner.decide(plan, settings, instructions, seed)
    proj.write("plan.json", plan)
    proj.write("creative.json", creative)
    (proj.root / "storyboard.md").write_text(planner.storyboard_markdown(plan, creative), encoding="utf-8")
    proj.invalidate_from("storyboard")
    proj.mark("analyze", "done", planner=plan["planner"], scenes=len(plan["scenes"]), seconds=round(time.time() - t0, 1))
    memory.upsert_project(proj.id, name=proj.data["name"], path=str(proj.root), status="planned", aspect=s["aspect"],
                          tone=plan["tone"], decisions=creative["decisions"],
                          summary=f"{plan['title']}. {plan.get('logline') or ''} tone {plan['tone']} category {plan.get('category')}")
    console.ok(f"Plan ready: {len(plan['scenes'])} scenes, tone '{plan['tone']}', planner {plan['planner']}")
    for dim, arm in creative["decisions"].items():
        console.info(f"    {dim:<10} {arm:<11} ({creative['reasons'][dim]})")
    return plan


def _require(proj: Project, stage: str) -> None:
    if not proj.done(stage):
        raise ToolError(f"Stage '{stage}' has not run yet.", f"Run `video-studio {stage}` (or `video-studio make` to run everything).")


# --------------------------------------------------------------- storyboard


def storyboard(proj: Project, frames: bool = True) -> Path:
    _require(proj, "analyze")
    plan, creative = proj.read("plan.json"), proj.read("creative.json")
    look = creative["decisions"]["look"]
    w, h = proj.size
    tw, th = (w // 4) // 2 * 2, (h // 4) // 2 * 2
    sheet_path = proj.root / "storyboard.png"
    if frames:
        console.step(f"Drawing storyboard frames ({len(plan['scenes'])} panels)")
        from PIL import Image, ImageDraw

        panels = []
        for i, sc in enumerate(plan["scenes"]):
            v = sc["visual"]
            path = proj.build / "storyboard" / f"{sc['id']}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                if v["type"] in ("title3d", "object3d", "chart3d"):
                    from video_studio.engines import blender

                    blender.preview_frame(v, path, tw, th, look, sc["camera"], proj.build / "storyboard" / f"w_{sc['id']}",
                                          proj.settings.get("style", "cinematic"))
                else:
                    scene = canvas.SCENES.get(v["type"], canvas.Kinetic)(v, tw, th, 30, 3.0, look, creative["decisions"]["motion"], i)
                    scene.render(int(30 * 1.8)).save(path)
            except ToolError as exc:
                console.warn(f"Panel {sc['id']} preview failed: {exc}")
                Image.new("RGB", (tw, th), (40, 40, 40)).save(path)
            panels.append((sc, Image.open(path).convert("RGB")))
            console.progress("storyboard", i + 1, len(plan["scenes"]))
        cols = min(4, len(panels))
        rows = (len(panels) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * (tw + 16) + 16, rows * (th + 70) + 16), (16, 16, 20))
        d = ImageDraw.Draw(sheet)
        for i, (sc, img) in enumerate(panels):
            x, y = 16 + (i % cols) * (tw + 16), 16 + (i // cols) * (th + 70)
            sheet.paste(img, (x, y))
            d.text((x, y + th + 6), f"{sc['id']}  {sc['visual']['type']}  {sc['camera']}", fill=(230, 230, 230))
            d.text((x, y + th + 24), sc["narration"][:48] + ("..." if len(sc["narration"]) > 48 else ""), fill=(160, 160, 170))
        sheet.save(sheet_path)
        console.ok(f"Storyboard: {sheet_path}")
    proj.mark("storyboard", "done", frames=frames)
    return sheet_path


# ------------------------------------------------------------------ produce


def produce(proj: Project) -> dict:
    _require(proj, "analyze")
    t0 = time.time()
    s = proj.settings
    plan, creative = proj.read("plan.json"), proj.read("creative.json")
    d = creative["decisions"]
    w, h = proj.size
    fps, quality, look = int(s["fps"]), s["quality"], d["look"]
    pace = PACING[d["pacing"]]
    t_dur = TRANSITION_SECONDS[d["transition"]]

    # 1. voice
    voice_dir = proj.build / "voice"
    lines = [{"id": sc["id"], "text": sc["narration"]} for sc in plan["scenes"] if sc["narration"].strip()]
    voices: dict = {}
    if s.get("voice") != "none" and lines:
        try:
            voices = tts.synthesize(lines, voice_dir, s.get("voice", "auto"), int(s.get("voice_rate", 0)) + pace["rate"], pace["speed"])
        except ToolError as exc:
            sig, fix = memory.record_failure(proj.id, "voice", str(exc))
            console.warn(f"Voiceover failed ({exc}); continuing without voice.")
    for vid, info in voices.items():
        polished = Path(info["path"]).with_suffix(".polished.wav")
        if not polished.exists():
            tts.polish(Path(info["path"]), polished)
        info["polished"] = str(polished)

    # 2. timeline
    timeline, start = [], 0.0
    for i, sc in enumerate(plan["scenes"]):
        v = voices.get(sc["id"])
        speech = v["duration"] if v else max(2.0, len(sc["narration"].split()) / 2.6)
        head = (t_dur if i > 0 else 0.0) + LEAD
        dur = max(2.4, head + speech + pace["pad"])
        if i == len(plan["scenes"]) - 1:
            dur += 0.8  # let the call to action breathe
        timeline.append({"id": sc["id"], "start": round(start, 3), "end": round(start + dur, 3), "duration": round(dur, 3),
                         "voice_start": round(start + head, 3), "speech": round(speech, 3)})
        start += dur - t_dur
    total = timeline[-1]["end"]
    proj.write("timeline.json", timeline)
    (proj.root / "storyboard.md").write_text(planner.storyboard_markdown(plan, creative, timeline), encoding="utf-8")

    # 3. scene clips (Blender jobs in a background thread, motion graphics in the foreground)
    clips_dir = proj.build / "scenes"
    clips_dir.mkdir(parents=True, exist_ok=True)
    jobs = []
    for i, (sc, tl) in enumerate(zip(plan["scenes"], timeline)):
        spec = {"v": sc["visual"], "w": w, "h": h, "fps": fps, "d": tl["duration"], "look": look, "cam": sc["camera"],
                "q": quality, "motion": d["motion"], "style": s.get("style"), "engine_rev": 3}
        clip = clips_dir / f"{sc['id']}_{sha(spec, 10)}.mp4"
        jobs.append((i, sc, tl, clip))
    three_d = [j for j in jobs if j[1]["visual"]["type"] in ("title3d", "object3d", "chart3d") and not j[3].exists()]
    flat = [j for j in jobs if j not in three_d and not j[3].exists()]
    errors: list[str] = []

    def run_3d() -> None:
        from video_studio.engines import blender

        for i, sc, tl, clip in three_d:
            try:
                console.step(f"3D scene {sc['id']} ({sc['visual']['type']}, {tl['duration']:.1f}s)")
                blender.render_clip(sc["visual"], clip, w, h, fps, tl["duration"], look, sc["camera"], quality,
                                    proj.build / "blender" / sc["id"], s.get("style", "cinematic"))
            except ToolError as exc:
                errors.append(f"{sc['id']}: {exc}")
                memory.record_failure(proj.id, "blender", str(exc))
                console.warn(f"3D scene {sc['id']} failed; using a 2D version instead.")
                canvas.render_scene({**sc["visual"], "type": "title" if sc["visual"]["type"] == "title3d" else "kinetic",
                                     "headline": sc["visual"].get("label") or sc["visual"].get("text") or sc["visual"].get("title", "")},
                                    clip, w, h, fps, tl["duration"], look, d["motion"], quality, i)

    worker = threading.Thread(target=run_3d, daemon=True) if three_d else None
    if worker:
        worker.start()
    for i, sc, tl, clip in flat:
        console.step(f"Scene {sc['id']} ({sc['visual']['type']}, {tl['duration']:.1f}s)")
        if sc["visual"]["type"] == "hyperframes":
            try:
                from video_studio.engines import hyperframes

                hyperframes.render_scene(sc["visual"], clip, w, h, fps, tl["duration"], look, proj.build / "hf" / sc["id"], quality)
                continue
            except ToolError as exc:
                memory.record_failure(proj.id, "hyperframes", str(exc))
                console.warn(f"HyperFrames failed ({str(exc)[:100]}); using the built-in engine.")
        canvas.render_scene(sc["visual"], clip, w, h, fps, tl["duration"], look, d["motion"], quality, i)
    if worker:
        while worker.is_alive():
            worker.join(0.5)

    # 4. music + SFX
    audio_dir = proj.build / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    music_path = None
    if s.get("music_enabled", True):
        if s.get("music_file"):
            music_path = Path(s["music_file"])
        else:
            music_path = audio_dir / f"music_{d['music']}_{int(total)}.wav"
            if not music_path.exists():
                console.step(f"Composing a {d['music']} music bed ({fmt_seconds(total)})")
                music.write_wav(music_path, music.compose(d["music"], total + 0.5, seed=len(plan["title"])))
    sfx_events = []
    for i, (sc, tl) in enumerate(zip(plan["scenes"], timeline)):
        kind = sc.get("sfx", "none")
        if kind == "none":
            continue
        fx = audio_dir / f"sfx_{kind}.wav"
        if not fx.exists():
            music.write_wav(fx, music.sfx(kind))
        at = max(0.0, tl["start"] - (0.25 if kind == "whoosh" else 1.2 if kind == "riser" else 0.0))
        if i == 0 and kind == "riser":
            at = max(0.0, timeline[0]["end"] - t_dur - 1.3)
        sfx_events.append({"path": str(fx), "start": round(at, 3), "gain": 0.35 if kind in ("whoosh", "riser") else 0.45})

    # 5. captions (absolute word timings)
    words = []
    for sc, tl in zip(plan["scenes"], timeline):
        v = voices.get(sc["id"])
        if v:
            words += [{"text": wd["text"], "start": round(tl["voice_start"] + wd["start"], 3),
                       "end": round(tl["voice_start"] + wd["end"], 3)} for wd in v["words"]]
    cap_style = d["captions"]
    ass = srt = None
    if words:
        srt = captions.build_srt(words, proj.build / "captions.srt")
        if cap_style != "none" and s.get("captions_enabled", True):
            ass = captions.build_ass(words, w, h, cap_style, look, proj.build / "captions.ass",
                                     y_ratio=0.80 if h > w else 0.88)
    manifest = {
        "size": [w, h], "fps": fps, "quality": quality, "total": round(total, 3), "transition_seconds": t_dur,
        "transitions": [TRANSITIONS[d["transition"]][i % len(TRANSITIONS[d["transition"]])] for i in range(len(jobs))],
        "clips": [str(j[3]) for j in jobs], "durations": [j[2]["duration"] for j in jobs],
        "voice": [{"path": voices[sc["id"]]["polished"], "start": tl["voice_start"]} for sc, tl in zip(plan["scenes"], timeline) if sc["id"] in voices],
        "music": str(music_path) if music_path else "", "sfx": sfx_events,
        "ass": str(ass) if ass else "", "srt": str(srt) if srt else "", "grade": LOOKS[look]["grade"],
        "words": len(words), "voice_engine": next(iter(voices.values()))["engine"] if voices else "none",
    }
    proj.write("manifest.json", manifest)
    secs = time.time() - t0
    proj.mark("produce", "done", seconds=round(secs, 1), scene_errors=errors)
    memory.add_metric(proj.id, "produce", secs, {"output_seconds": total, "scenes": len(jobs), "three_d": len(three_d)})
    console.ok(f"Produced {len(jobs)} scenes, voice, music and captions in {fmt_seconds(secs)} (video length {fmt_seconds(total)})")
    return manifest


# ------------------------------------------------------------------- render


def render(proj: Project) -> Path:
    _require(proj, "produce")
    t0 = time.time()
    m = proj.read("manifest.json")
    s = proj.settings
    w, h = m["size"]
    proj.output.mkdir(parents=True, exist_ok=True)
    video_only = proj.build / "video_master.mp4"
    audio = proj.build / "audio_master.wav"
    console.step("Assembling scenes, transitions, grade and captions")
    compose.assemble_video([Path(c) for c in m["clips"]], m["durations"], m["transitions"], m["transition_seconds"],
                           m["fps"], w, h, m["grade"], Path(m["ass"]) if m["ass"] else None, m["quality"], video_only,
                           total=m["total"])
    console.step("Mixing voice, music (ducked) and sound effects to broadcast loudness")
    compose.mix_audio([(Path(v["path"]), v["start"]) for v in m["voice"]], Path(m["music"]) if m["music"] else None,
                      [(Path(x["path"]), x["start"], x["gain"]) for x in m["sfx"]], m["total"], audio, float(s["loudness"]))
    compose.mux(video_only, audio, proj.master, m["quality"])
    secs = time.time() - t0
    proj.mark("render", "done", seconds=round(secs, 1), file=str(proj.master))
    memory.add_metric(proj.id, "render", secs, {"output_seconds": m["total"]})
    console.ok(f"Rendered {proj.master} ({fmt_seconds(m['total'])}) in {fmt_seconds(secs)}")
    return proj.master


# ------------------------------------------------------------------ validate


def validate(proj: Project, video: Path | None = None) -> dict:
    video = video or proj.master
    s = proj.settings
    m = proj.read("manifest.json", {}) or {}
    w, h = proj.size
    console.step(f"Quality checks on {video.name}")
    report = qa.check(video, {"width": w, "height": h, "fps": int(s["fps"]), "duration": m.get("total"),
                              "loudness": float(s["loudness"])}, proj.output / "qa",
                      Path(m["srt"]) if m.get("srt") else None)
    qa.print_report(report)
    proj.mark("validate", "done" if report["fails"] == 0 else "failed", score=report["score"])
    memory.upsert_project(proj.id, qa_score=report["score"], duration=report["duration"], status="validated")
    return report
