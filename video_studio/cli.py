"""video-studio command line."""

from __future__ import annotations

import argparse
import json
import shutil
import signal
import sys
import time
import traceback
from pathlib import Path

from video_studio import __version__, config, detect, memory, resources
from video_studio.project import Project, create, find, slug
from video_studio.util import ToolError, app_data_dir, confirm, console, fmt_bytes, kill_children, paint

STATUS_ICON = {True: paint("ok", "green"), False: paint("--", "yellow")}


# ----------------------------------------------------------------- helpers

def _proj(args) -> Project:
    return find(getattr(args, "project", None))


def _apply_overrides(proj: Project, args) -> None:
    changes = {}
    for key in ("aspect", "quality", "voice", "look", "pacing", "transition", "music", "captions", "motion", "style"):
        val = getattr(args, key, None)
        if val:
            changes[key] = val
    if getattr(args, "height", None):
        changes["height"] = int(args.height)
    if getattr(args, "fps", None):
        changes["fps"] = int(args.fps)
    if getattr(args, "music_file", None):
        changes["music_file"] = str(Path(args.music_file).resolve())
    if getattr(args, "no_music", False):
        changes["music_enabled"] = False
    if getattr(args, "no_captions", False):
        changes["captions_enabled"] = False
    if getattr(args, "no_3d", False):
        changes["use_blender"] = False
    if getattr(args, "engine", None):
        changes["motion_engine"] = args.engine
    if getattr(args, "ai", None):
        changes["ai_provider"] = args.ai
    if changes:
        proj.update_settings(**changes)


def _record_failure(proj: Project | None, stage: str, exc: Exception) -> None:
    if proj is None:
        return
    try:
        sig, fix = memory.record_failure(proj.id, stage, str(exc))
        proj.mark(stage, "failed", error=str(exc)[:500], signature=sig)
        if fix:
            console.info(f"Known fix for this failure on record: {fix}")
    except Exception:  # never let failure bookkeeping hide the real error
        pass


# ---------------------------------------------------------------- commands

def cmd_init(args) -> int:
    root = Path(args.name if Path(args.name).is_absolute() else Path(config.get("workspace") or ".") / slug(args.name)).resolve()
    proj = create(root, args.name, "script", aspect=args.aspect, height=args.height, fps=args.fps, quality=args.quality)
    if args.script:
        shutil.copy2(args.script, proj.root / "script.txt")
    else:
        (proj.root / "script.txt").write_text("# My video title\n\nWrite your narration here, one idea per paragraph.\n", encoding="utf-8")
    if args.instructions:
        (proj.root / "instructions.txt").write_text(args.instructions, encoding="utf-8")
    console.ok(f"Project created: {proj.root}")
    console.info("Next: edit script.txt, then run `video-studio make` inside that folder.")
    return 0


def cmd_doctor(args) -> int:
    console.step("Checking this computer")
    res = resources.summary()
    console.info(f"  CPU threads {res['cpu_threads']} (renders use {res['render_threads']}), RAM {res['ram_free_gb']}/{res['ram_total_gb']} GB free, "
                 f"GPU: {', '.join(res['gpus']) or 'unknown'}")
    console.info(f"  Disk free in workspace: {fmt_bytes(resources.disk_free(Path.cwd()))}")
    tools = detect.check_all(include_ai=not args.no_ai)
    from video_studio.engines import ml

    kokoro = ml.kokoro_ready()
    rows = []
    for t in tools:
        status = "ok" if t.ok else ("missing" if not t.found else "problem")
        rows.append({**t.to_dict(), "status": status})
    if args.json:
        print(json.dumps({"resources": res, "tools": rows, "kokoro": kokoro}, indent=2))
        return 0
    for t in tools:
        mark = paint("[ok]  ", "green") if t.ok else (paint("[req] ", "red") if t.required else paint("[opt] ", "yellow"))
        line = f"{mark}{t.name:<22} {t.version or '-':<16} {t.role}"
        console.info(line)
        if not t.ok:
            console.info(paint(f"        {t.problem}. Fix: {t.hint}", "dim"))
    console.info(f"{paint('[ok]  ', 'green') if kokoro else paint('[opt] ', 'yellow')}{'Kokoro voice':<22} {'ready' if kokoro else 'not set up':<16} "
                 f"natural offline voice (Apache-2.0)" + ("" if kokoro else "  Fix: video-studio install kokoro"))
    required_ok = all(t.ok for t in tools if t.required)
    if required_ok:
        console.ok("Ready to produce videos.")
    else:
        console.error("A required tool is missing; follow the Fix lines above.")
    recent = memory.failures(5)
    if recent:
        console.info("Recent failures on this machine (memory):")
        for f in recent:
            console.info(f"  {time.strftime('%m-%d %H:%M', time.localtime(f['created']))} {f['stage']}: {f['signature']}"
                         + (f"  -> fix {f['fix']}" + (" (verified)" if f["fixed"] else "") if f["fix"] else ""))
    return 0 if required_ok else 1


def cmd_install(args) -> int:
    if args.tool == "kokoro":
        return _install_kokoro(args.yes)
    return 0 if detect.install(args.tool, args.yes) else 1


def _install_kokoro(assume_yes: bool) -> int:
    from video_studio.engines import ml
    from video_studio.util import run_interactive

    py = detect.system_python()
    if not py:
        console.error("Kokoro needs Python 3.10+ (python.org).")
        return 1
    d = ml.kokoro_dir()
    files = {"kokoro-v1.0.int8.onnx": "88 MB", "voices-v1.0.bin": "27 MB"}
    console.info("Install plan for the Kokoro voice (weights Apache-2.0, runtime kokoro-onnx MIT):")
    console.info(f"  1. {py} -m pip install --user kokoro-onnx soundfile")
    for f, size in files.items():
        console.info(f"  2. download {f} ({size}) from github.com/thewh1teagle/kokoro-onnx releases into {d}")
    if not confirm("Proceed?", assume_yes):
        return 1
    if run_interactive([str(py), "-m", "pip", "install", "--user", "kokoro-onnx", "soundfile"]) != 0:
        console.error("pip install failed.")
        return 1
    import urllib.request

    d.mkdir(parents=True, exist_ok=True)
    for f in files:
        if not (d / f).exists():
            console.step(f"Downloading {f}")
            urllib.request.urlretrieve(f"https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/{f}", d / f)
    ok = ml.kokoro_ready()
    (console.ok if ok else console.error)("Kokoro voice ready." if ok else "Kokoro check failed; run `video-studio doctor`.")
    return 0 if ok else 1


def cmd_ai(args) -> int:
    from video_studio import ai

    if args.ai_cmd == "status":
        current = config.get("ai.provider")
        console.info(f"Preferred provider: {current}   fallback order: {', '.join(config.get('ai.fallback'))}")
        for p in ai.providers():
            st = p.status()
            mark = paint("[ready]", "green") if st.signed_in and not st.problem else (paint("[issue]", "yellow") if st.installed else paint("[none] ", "dim"))
            console.info(f"{mark} {p.label:<24} {st.version or '-':<12} {st.account or ''}")
            if st.problem:
                console.info(paint(f"         {st.problem}" + (f"  -> {st.hint}" if st.hint else ""), "dim"))
        return 0
    if args.ai_cmd == "login":
        return ai.get_provider(args.provider).login()
    if args.ai_cmd == "use":
        if args.provider not in ("auto", "none"):
            ai.get_provider(args.provider)
        config.set_value("ai.provider", args.provider)
        if args.model:
            models = dict(config.get("ai.models") or {})
            models[args.provider] = args.model
            config.set_value("ai.models", models)
        console.ok(f"AI provider set to {args.provider}" + (f" ({args.model})" if args.model else ""))
        return 0
    if args.ai_cmd == "fallback":
        config.set_value("ai.fallback", args.order)
        console.ok(f"Fallback order: {args.order}")
        return 0
    if args.ai_cmd == "test":
        director = ai.Director(args.provider or None, budget=1)
        data, who = director.ask_json("connection test", 'Reply with only this JSON: {"ok": true}', use_cache=False)
        console.ok(f"{who} answered: {data}")
        return 0
    return 1


def cmd_analyze(args) -> int:
    from video_studio import pipeline

    proj = _proj(args)
    if args.script:
        shutil.copy2(args.script, proj.root / "script.txt")
    if args.instructions:
        (proj.root / "instructions.txt").write_text(args.instructions, encoding="utf-8")
    _apply_overrides(proj, args)
    pipeline.analyze(proj, use_ai=not args.no_ai, seed=args.seed)
    console.info(f"Review {proj.root / 'storyboard.md'} (edit build/plan.json to change anything), then `video-studio storyboard`.")
    return 0


def cmd_storyboard(args) -> int:
    from video_studio import pipeline

    pipeline.storyboard(_proj(args), frames=not args.no_frames)
    return 0


def cmd_produce(args) -> int:
    from video_studio import pipeline

    proj = _proj(args)
    _apply_overrides(proj, args)
    pipeline.produce(proj)
    return 0


def cmd_render(args) -> int:
    from video_studio import editor, pipeline

    proj = _proj(args)
    if proj.kind == "edit":
        editor.render(proj)
    else:
        pipeline.render(proj)
    return 0


def cmd_validate(args) -> int:
    from video_studio import pipeline

    proj = _proj(args)
    report = pipeline.validate(proj, Path(args.file) if args.file else None)
    if args.ai_review:
        _ai_review(proj, report)
    return 0 if report["fails"] == 0 else 2


def _ai_review(proj: Project, report: dict) -> None:
    from video_studio.ai import Director

    director = Director(proj.settings.get("ai_provider"), usage_file=proj.build / "ai_usage.json")
    sheet = Path(report["contact_sheet"]) if report.get("contact_sheet") else None
    plan = proj.read("plan.json", {}) or proj.read("edit_plan.json", {}) or {}
    prompt = ("You are a senior video editor reviewing a finished short video. Contact-sheet frames are attached. "
              "Given the automated QA results and plan, return ONLY JSON: {\"score\": 1-10, \"strengths\": [..], "
              "\"fixes\": [{\"issue\": str, \"setting\": one of [look,pacing,transition,captions,music,motion], \"suggest\": str}]}\n"
              f"QA: {json.dumps([{k: i[k] for k in ('check', 'status', 'detail')} for i in report['items']])}\n"
              f"PLAN: {json.dumps(plan)[:3000]}")
    data, who = director.ask_json("creative review", prompt, images=[sheet] if sheet and sheet.exists() else None)
    proj.write("ai_review.json", data)
    console.info(f"AI review ({who}): {data.get('score')}/10")
    for s in data.get("strengths", [])[:4]:
        console.ok(s)
    for f in data.get("fixes", [])[:5]:
        console.warn(f"{f.get('issue')} -> {f.get('setting')}: {f.get('suggest')}")


def cmd_export(args) -> int:
    from video_studio import export

    proj = _proj(args)
    platforms = list(export.PLATFORMS) if "all" in args.platform else args.platform
    files = export.export(proj, platforms)
    console.ok(f"{len(files)} file(s) in {proj.exports}")
    return 0


def cmd_make(args) -> int:
    """Everything in one go: init (if needed) + all stages."""
    from video_studio import export, pipeline

    if args.script:
        script = Path(args.script).resolve()
        name = args.name or script.stem
        root = Path(args.out or (Path(config.get("workspace") or ".") / slug(name))).resolve()
        proj = Project(root) if (root / "project.json").exists() else create(root, name, "script", aspect=args.aspect,
                                                                               height=args.height, fps=args.fps, quality=args.quality)
        shutil.copy2(script, root / "script.txt")
        console.attach_log(proj.logs / "run.log")
    else:
        proj = _proj(args)
    if args.instructions:
        (proj.root / "instructions.txt").write_text(args.instructions, encoding="utf-8")
    _apply_overrides(proj, args)
    started = time.time()
    stage = "analyze"
    try:
        if not proj.done("analyze") or args.replan:
            pipeline.analyze(proj, use_ai=not args.no_ai, seed=args.seed)
        stage = "storyboard"
        if not proj.done("storyboard"):
            pipeline.storyboard(proj, frames=args.storyboard_frames)
        if args.approve_storyboard and not confirm(f"Storyboard ready at {proj.root / 'storyboard.md'}. Produce the video?"):
            return 0
        stage = "produce"
        pipeline.produce(proj)
        stage = "render"
        pipeline.render(proj)
        stage = "validate"
        report = pipeline.validate(proj)
        stage = "export"
        files = export.export(proj, args.platform)
    except ToolError:
        raise
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        _record_failure(proj, stage, exc)
        raise
    console.ok(f"Done in {time.time() - started:.0f}s. Master: {proj.master}")
    console.info(f"Exports: {', '.join(p.name for p in files)}")
    console.info("Rate it so the studio learns your taste:  video-studio feedback 5 --notes \"loved the 3D, slightly too fast\"")
    return 0 if report["fails"] == 0 else 2


def _cmd_vocab(args) -> int:
    from video_studio import vocab

    cur = vocab.add(args.words) if args.action == "add" else vocab.remove(args.words) if args.action == "remove" else vocab.words()
    console.info("Your words: " + (", ".join(cur) if cur else "(none yet)  e.g. video-studio vocab add Sahkarmi Dhrubo"))
    return 0


def _cmd_styles(args) -> int:
    from video_studio.engines import styledna

    names = styledna.available()
    if not names:
        console.info("No styles yet. Teach one:  video-studio learn-style \"C:\\path\\to\\reels\" --name my-style")
    for n in names:
        p = styledna.load(n) or {}
        console.info(f"  {n:<22} {p.get('count', 0)} video(s): {styledna.describe(p) if p.get('shot_len') else '-'}")
    return 0


def _cmd_shorts(args) -> int:
    from video_studio import shorts

    src = Path(args.video).resolve()
    out = Path(args.out or src.with_name(src.stem + "_shorts")).resolve()
    extra = ["--fx", args.fx, "--platform", *args.platform] + (["--like", args.like] if args.like else [])
    return shorts.run(src, out, max(1, args.count), args.min_len, args.max_len, args.brief, extra)


def _cmd_learn(args) -> int:
    from video_studio import learn
    from video_studio.studio import workspace

    roots = [Path(f) for f in args.folders] or [workspace()]
    return 0 if learn.train(roots).get("ok") else 1


def cmd_edit(args) -> int:
    from video_studio import editor

    return editor.run_cli(args)


def cmd_resume(args) -> int:
    from video_studio import editor, export, pipeline

    proj = _proj(args)
    if args.from_stage:
        proj.invalidate_from(args.from_stage)
        if args.from_stage in ("plan", "cut") and proj.kind == "edit":
            import shutil as _sh

            _sh.rmtree(proj.build / "pieces", ignore_errors=True)
    nxt = proj.next_stage()
    if not nxt:
        console.ok("Everything is done already. Use --from <stage> to redo a stage.")
        return 0
    console.info(f"Resuming at stage '{nxt}'")
    if proj.kind == "edit":
        return editor.resume(proj)
    steps = {"analyze": lambda: pipeline.analyze(proj, use_ai=True), "storyboard": lambda: pipeline.storyboard(proj, False),
             "produce": lambda: pipeline.produce(proj), "render": lambda: pipeline.render(proj),
             "validate": lambda: pipeline.validate(proj), "export": lambda: export.export(proj, ["shorts"])}
    order = list(steps)
    for st in order[order.index(nxt):]:
        steps[st]()
    return 0


def cmd_status(args) -> int:
    proj = _proj(args)
    data = proj.data
    console.info(f"{data['name']}  ({proj.kind})  {proj.root}")
    s = data["settings"]
    console.info(f"  {s['aspect']} {proj.size[0]}x{proj.size[1]} @ {s['fps']} fps, quality {s['quality']}, voice {s['voice']}")
    st = proj.state()
    from video_studio.project import EDIT_STAGES, STAGES

    for stage in (EDIT_STAGES if proj.kind == "edit" else STAGES):
        e = st.get(stage)
        mark = paint("done   ", "green") if e and e["status"] == "done" else (paint(e["status"].ljust(7), "red") if e else paint("pending", "dim"))
        extra = ""
        if e:
            extra = " ".join(f"{k}={v}" for k, v in e.items() if k not in ("status", "at", "files", "scene_errors") and not isinstance(v, (list, dict)))
        console.info(f"  {mark} {stage:<11} {extra[:90]}")
    if proj.master.exists():
        console.info(f"  master: {proj.master} ({fmt_bytes(proj.master.stat().st_size)})")
    return 0


def cmd_feedback(args) -> int:
    proj = _proj(args)
    result = memory.add_feedback(proj.id, args.rating, args.notes or "")
    console.ok(f"Saved {args.rating}/5 for {proj.data['name']}. Taste model updated ({result['bucket']}):")
    for item in result["learned"]:
        console.info(f"    {item}")
    if args.rating >= 4:
        plan = proj.read("plan.json") or proj.read("edit_plan.json")
        if plan and plan.get("scenes"):
            structure = [{"purpose": s.get("purpose"), "type": s["visual"]["type"]} for s in plan["scenes"]]
            memory.save_template(f"{plan.get('category', 'general')}-{proj.id}", plan.get("category", "general"), structure, args.rating)
            console.info("    scene structure saved as a reusable template")
    return 0


def cmd_memory(args) -> int:
    sub = args.mem_cmd
    if sub == "list":
        for p in memory.list_projects():
            console.info(f"{p['id']:<34} {p['status'] or '-':<10} rating={p['rating'] or '-'} qa={p['qa_score'] or '-'} {p['path']}")
    elif sub == "taste":
        rows = memory.taste_table()
        if not rows:
            console.info("No ratings yet. Rate a finished video: video-studio feedback 5 --notes \"...\"")
        for r in rows:
            if args.all or r["bucket"] == "global":
                console.info(f"{r['dimension']:<11} {r['arm']:<11} {r['bucket']:<22} liked {r['mean']:.0%}  (a={r['alpha']:.1f} b={r['beta']:.1f})")
    elif sub == "prefs":
        for k, v in memory.prefs().items():
            console.info(f"{k} = {v}")
    elif sub == "set":
        memory.set_pref(args.key, args.value)
        console.ok(f"Preference saved: {args.key} = {args.value}")
    elif sub == "note":
        memory.add_note(args.text)
        console.ok("Note stored; the AI director will see it when relevant.")
    elif sub == "search":
        for hit in memory.recall(args.query, 10):
            console.info(f"[{hit['kind']}] {hit['ref']}: {hit['text'][:120]}")
    elif sub == "failures":
        for f in memory.failures(30):
            console.info(f"#{f['id']} {f['stage']:<10} {f['signature']:<28} fix={f['fix'] or '-'} fixed={bool(f['fixed'])}")
    elif sub == "templates":
        for t in memory.templates():
            console.info(f"{t['name']:<40} {t['category']:<10} score={t['score']} uses={t['uses']}")
    elif sub == "delete":
        n = memory.delete(args.kind, args.ref)
        console.ok(f"Deleted {n} {args.kind} record(s).")
    elif sub == "reset-taste":
        n = memory.reset_taste(args.dimension)
        console.ok(f"Cleared {n} taste record(s).")
    elif sub == "export":
        Path(args.file).write_text(json.dumps(memory.export_all(), indent=2, default=str), encoding="utf-8")
        console.ok(f"Memory exported to {args.file}")
    elif sub == "wipe":
        if confirm("Delete ALL Video Studio memory (projects, ratings, taste, failures)?", args.yes):
            memory.wipe()
            console.ok("Memory wiped.")
    return 0


def cmd_config(args) -> int:
    if args.cfg_cmd == "list":
        for k, v in config.load().items():
            console.info(f"{k:<32} {json.dumps(v)}")
    elif args.cfg_cmd == "get":
        print(json.dumps(config.get(args.key)))
    elif args.cfg_cmd == "set":
        config.set_value(args.key, args.value)
        console.ok(f"{args.key} = {json.dumps(config.get(args.key))}")
    elif args.cfg_cmd == "reset":
        config.reset(args.key)
        console.ok("Reset.")
    return 0


def cmd_clean(args) -> int:
    proj = _proj(args)
    freed = 0
    for sub in ("blender", "storyboard", "hf"):
        d = proj.build / sub
        if d.exists():
            freed += sum(p.stat().st_size for p in d.rglob("*") if p.is_file())
            shutil.rmtree(d, ignore_errors=True)
    if args.all:
        for d in (proj.build / "scenes", proj.build / "voice", proj.build / "audio"):
            if d.exists():
                freed += sum(p.stat().st_size for p in d.rglob("*") if p.is_file())
                shutil.rmtree(d, ignore_errors=True)
        proj.invalidate_from("produce" if proj.kind == "script" else "cut")
    console.ok(f"Freed {fmt_bytes(freed)}.")
    return 0


def cmd_studio(args) -> int:
    from video_studio import studio

    return studio.serve(args.port, open_browser=not args.no_browser, workspace_dir=args.workspace)


def cmd_version(args) -> int:
    print(f"video-studio {__version__}  (data: {app_data_dir()})")
    return 0


# ------------------------------------------------------------------ parser

def _add_style_options(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("creative overrides (otherwise the taste model decides)")
    g.add_argument("--look", choices=list(memory.DIMENSIONS["look"]))
    g.add_argument("--pacing", choices=memory.DIMENSIONS["pacing"])
    g.add_argument("--transition", choices=memory.DIMENSIONS["transition"])
    g.add_argument("--music", choices=memory.DIMENSIONS["music"])
    g.add_argument("--captions", choices=memory.DIMENSIONS["captions"])
    g.add_argument("--motion", choices=memory.DIMENSIONS["motion"])
    g.add_argument("--style", choices=["cinematic", "toon"], help="3D render style (toon = cel-shaded anime look)")
    g.add_argument("--voice", help="auto | male | female | male_uk | female_uk | hindi_female | hindi_male | sapi | none | Kokoro voice id")
    g.add_argument("--music-file", help="use your own music track instead of the generated bed")
    g.add_argument("--no-music", action="store_true")
    g.add_argument("--no-captions", action="store_true")
    g.add_argument("--no-3d", action="store_true", help="skip Blender (faster)")
    g.add_argument("--engine", choices=["canvas", "hyperframes"], help="motion-graphics engine")
    g.add_argument("--ai", help="AI provider for this project (opencode = free models, claude, codex, gemini, qwen, ollama, auto, none)")
    g.add_argument("--broll", help="folder of your own B-roll clips to cut away to (also %%LOCALAPPDATA%%/VideoStudio/broll)")


def _add_format_options(p: argparse.ArgumentParser, defaults: bool = True) -> None:
    p.add_argument("--aspect", choices=["9:16", "16:9", "1:1", "4:5"], default=None)
    p.add_argument("--height", type=int, default=None, help="long side for 9:16, short side otherwise (e.g. 1920 or 1080)")
    p.add_argument("--fps", type=int, default=None)
    p.add_argument("--quality", choices=["draft", "standard", "high"], default=None)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="video-studio", description="Local-first AI video production studio. "
                                "AI directs; Blender, FFmpeg and local engines produce; you approve.")
    p.add_argument("--project", help="project folder (default: current folder)")
    p.add_argument("-q", "--quiet", action="store_true")
    sub = p.add_subparsers(dest="cmd", metavar="<command>")

    s = sub.add_parser("init", help="create a new project")
    s.add_argument("name")
    s.add_argument("--script", help="copy this script into the project")
    s.add_argument("--instructions", help="production instructions, e.g. 'energetic, for Instagram, Hindi-English audience'")
    _add_format_options(s)
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("doctor", help="check installed tools, versions, hardware and AI logins")
    s.add_argument("--json", action="store_true")
    s.add_argument("--no-ai", action="store_true", help="skip AI CLI checks (faster)")
    s.set_defaults(func=cmd_doctor)

    s = sub.add_parser("shorts", help="one long video -> the best N shorts, each fully edited (local, no AI needed)")
    s.add_argument("video")
    s.add_argument("brief", nargs="?", default="", help="style for every short, e.g. 'viral Hinglish reel'")
    s.add_argument("--count", type=int, default=3)
    s.add_argument("--min", dest="min_len", type=float, default=20.0, help="shortest clip in seconds")
    s.add_argument("--max", dest="max_len", type=float, default=58.0, help="longest clip in seconds")
    s.add_argument("--out", help="output folder")
    s.add_argument("--fx", choices=["auto", "off", "clean", "bold", "viral"], default="auto")
    s.add_argument("--like")
    s.add_argument("--platform", nargs="+", default=["shorts", "reels"])
    s.set_defaults(func=_cmd_shorts)

    s = sub.add_parser("learn-style", help="study reference videos YOU choose (any genre) and learn their editing style")
    s.add_argument("folder", help="a video file or a folder of reference videos")
    s.add_argument("--name", required=True, help="a name for this style, e.g. travel-cinematic")
    s.set_defaults(func=lambda a: 0 if __import__("video_studio.engines.styledna", fromlist=["learn"]).learn(
        Path(a.folder), a.name) else 1)

    s = sub.add_parser("dub", help="English version of a finished edit: translated, re-voiced, English captions (local)")
    s.add_argument("project", help="the edited project folder")
    s.add_argument("--to", default="en", choices=["en"])
    s.add_argument("--voice", default="", help="Kokoro voice, e.g. am_michael (default) or af_heart")
    s.set_defaults(func=lambda a: (__import__("video_studio.dub", fromlist=["dub"]).dub(Path(a.project), a.to, a.voice), 0)[1])

    s = sub.add_parser("watch", help="watch a folder: every new clip dropped in becomes a finished draft in <folder>/edited")
    s.add_argument("folder")
    s.add_argument("brief", nargs="?", default="", help="how every clip should be edited")
    s.add_argument("--fx", choices=["auto", "off", "clean", "bold", "viral"], default="auto")
    s.add_argument("--like")
    s.add_argument("--platform", nargs="+", default=["shorts", "reels"])
    s.add_argument("--once", action="store_true", help="process what is there now, then exit")
    s.set_defaults(func=lambda a: __import__("video_studio.watch", fromlist=["run"]).run(
        Path(a.folder), a.brief, ["--fx", a.fx, "--platform", *a.platform] + (["--like", a.like] if a.like else []), once=a.once))

    s = sub.add_parser("vocab", help="your names/brands/terms: always heard and spelled exactly")
    s.add_argument("action", choices=["add", "remove", "list"])
    s.add_argument("words", nargs="*")
    s.set_defaults(func=_cmd_vocab)

    s = sub.add_parser("styles", help="list learned styles")
    s.set_defaults(func=_cmd_styles)

    s = sub.add_parser("learn", help="learn keep/cut decisions from your finished edits (tested on unseen videos first)")
    s.add_argument("folders", nargs="*", help="folders with past edits (default: Video Studio projects)")
    s.set_defaults(func=_cmd_learn)

    s = sub.add_parser("setup", help="first run: scan this PC, install everything (asks once), explain what it can edit")
    s.add_argument("--yes", action="store_true", help="approve the install without asking")
    s.add_argument("--report", action="store_true", help="only scan and explain; install nothing")
    s.set_defaults(func=lambda a: __import__("video_studio.setup", fromlist=["run_setup"]).run_setup(a.yes, a.report))

    s = sub.add_parser("install", help="install a missing tool (shows the plan and asks first)")
    s.add_argument("tool", choices=[*detect.INSTALL_RECIPES, "kokoro"])
    s.add_argument("--yes", action="store_true", help="approve without asking")
    s.set_defaults(func=cmd_install)

    s = sub.add_parser("ai", help="AI providers: status, login, use, fallback, test")
    aisub = s.add_subparsers(dest="ai_cmd", required=True)
    aisub.add_parser("status")
    x = aisub.add_parser("login")
    x.add_argument("provider")
    x = aisub.add_parser("use")
    x.add_argument("provider")
    x.add_argument("--model")
    x = aisub.add_parser("fallback")
    x.add_argument("order", nargs="+")
    x = aisub.add_parser("test")
    x.add_argument("provider", nargs="?")
    s.set_defaults(func=cmd_ai)

    s = sub.add_parser("analyze", help="understand the script and plan scenes (1 AI call, or local rules)")
    s.add_argument("script", nargs="?")
    s.add_argument("--instructions")
    s.add_argument("--no-ai", action="store_true")
    s.add_argument("--seed", type=int)
    _add_format_options(s)
    _add_style_options(s)
    s.set_defaults(func=cmd_analyze)

    s = sub.add_parser("storyboard", help="draw storyboard frames for every scene")
    s.add_argument("--no-frames", action="store_true")
    s.set_defaults(func=cmd_storyboard)

    s = sub.add_parser("produce", help="generate voice, scenes (3D + motion graphics), music, SFX, captions")
    _add_format_options(s)
    _add_style_options(s)
    s.set_defaults(func=cmd_produce)

    s = sub.add_parser("render", help="assemble, grade, mix and encode the master")
    s.set_defaults(func=cmd_render)

    s = sub.add_parser("validate", help="automated QA (sync, black frames, clipping, loudness, format)")
    s.add_argument("file", nargs="?")
    s.add_argument("--ai-review", action="store_true", help="also ask the AI director for a creative review (1 call)")
    s.set_defaults(func=cmd_validate)

    s = sub.add_parser("export", help="platform versions, thumbnail, captions, OpenTimelineIO")
    s.add_argument("--platform", nargs="+", default=["shorts"], choices=["shorts", "reels", "tiktok", "youtube", "square", "feed45", "all"])
    s.set_defaults(func=cmd_export)

    s = sub.add_parser("make", help="script -> finished video in one command")
    s.add_argument("script", nargs="?", help="script .txt (creates the project if needed)")
    s.add_argument("--name")
    s.add_argument("--out", help="project folder")
    s.add_argument("--instructions")
    s.add_argument("--no-ai", action="store_true")
    s.add_argument("--replan", action="store_true")
    s.add_argument("--seed", type=int)
    s.add_argument("--storyboard-frames", action="store_true", help="also draw storyboard frames")
    s.add_argument("--approve-storyboard", action="store_true", help="pause for approval after the storyboard")
    s.add_argument("--platform", nargs="+", default=["shorts"], choices=["shorts", "reels", "tiktok", "youtube", "square", "feed45"])
    _add_format_options(s)
    _add_style_options(s)
    s.set_defaults(func=cmd_make)

    s = sub.add_parser("edit", help="edit YOUR footage: describe the result, get a finished video")
    s.add_argument("footage", nargs="+", help="video file(s), in order")
    s.add_argument("brief", nargs="?", default="", help='what you want, e.g. "60s reel, punchy, remove mistakes"')
    s.add_argument("--brief-file")
    s.add_argument("--name")
    s.add_argument("--out")
    s.add_argument("--target", type=float, help="target length in seconds")
    s.add_argument("--no-ai", action="store_true")
    s.add_argument("--keep-silences", action="store_true")
    s.add_argument("--no-reframe", action="store_true", help="never crop; pad instead")
    s.add_argument("--no-enhance", action="store_true", help="skip voice cleanup and colour correction")
    s.add_argument("--language", default="", help="speech language code (auto-detect if empty)")
    s.add_argument("--like", help="edit in a style learned with `video-studio learn-style` (see `video-studio styles`)")
    s.add_argument("--fx", choices=["auto", "off", "clean", "bold", "viral"], default="auto",
                   help="layered style pass: camera angles, transitions, words behind you, hits (auto = from the brief)")
    s.add_argument("--caption-script", choices=["auto", "native", "roman"], default="auto",
                   help="roman = write Hindi/Bengali/etc. captions in Latin letters (Hinglish style)")
    s.add_argument("--whisper-model", default="auto", help="auto (best model already downloaded) | tiny | base | small | medium | large-v3-turbo")
    s.add_argument("--platform", nargs="+", default=["shorts"], choices=["shorts", "reels", "tiktok", "youtube", "square", "feed45"])
    _add_format_options(s)
    _add_style_options(s)
    s.set_defaults(func=cmd_edit)

    s = sub.add_parser("resume", help="continue an interrupted job from the first unfinished stage")
    s.add_argument("--from", dest="from_stage", help="redo from this stage (e.g. plan, cut, produce, render)")
    s.set_defaults(func=cmd_resume)

    s = sub.add_parser("status", help="show project stages and outputs")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("feedback", help="rate the result (1-5) so the studio learns your taste")
    s.add_argument("rating", type=int)
    s.add_argument("--notes", default="")
    s.set_defaults(func=cmd_feedback)

    s = sub.add_parser("memory", help="review, correct and delete what the studio has learned")
    msub = s.add_subparsers(dest="mem_cmd", required=True)
    msub.add_parser("list")
    x = msub.add_parser("taste")
    x.add_argument("--all", action="store_true")
    msub.add_parser("prefs")
    x = msub.add_parser("set")
    x.add_argument("key")
    x.add_argument("value")
    x = msub.add_parser("note")
    x.add_argument("text")
    x = msub.add_parser("search")
    x.add_argument("query")
    msub.add_parser("failures")
    msub.add_parser("templates")
    x = msub.add_parser("delete")
    x.add_argument("kind", choices=["project", "feedback", "pref", "failure", "template"])
    x.add_argument("ref")
    x = msub.add_parser("reset-taste")
    x.add_argument("dimension", nargs="?")
    x = msub.add_parser("export")
    x.add_argument("file")
    x = msub.add_parser("wipe")
    x.add_argument("--yes", action="store_true")
    s.set_defaults(func=cmd_memory)

    s = sub.add_parser("config", help="settings: list | get | set | reset")
    csub = s.add_subparsers(dest="cfg_cmd", required=True)
    csub.add_parser("list")
    x = csub.add_parser("get")
    x.add_argument("key")
    x = csub.add_parser("set")
    x.add_argument("key")
    x.add_argument("value")
    x = csub.add_parser("reset")
    x.add_argument("key", nargs="?")
    s.set_defaults(func=cmd_config)

    s = sub.add_parser("clean", help="delete render caches (keeps outputs)")
    s.add_argument("--all", action="store_true", help="also delete scene/voice/audio caches")
    s.set_defaults(func=cmd_clean)

    s = sub.add_parser("studio", help="open the visual Studio in your browser (local only)")
    s.add_argument("--port", type=int, default=7860)
    s.add_argument("--workspace", help="folder for Studio projects")
    s.add_argument("--no-browser", action="store_true")
    s.set_defaults(func=cmd_studio)

    s = sub.add_parser("version", help="show version")
    s.set_defaults(func=cmd_version)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    # global options are accepted anywhere on the line (video-studio render --project x)
    for flag in ("--project", "-q", "--quiet"):
        if flag in argv[1:]:
            i = argv.index(flag)
            take = 2 if flag == "--project" and i + 1 < len(argv) else 1
            moved = argv[i:i + take]
            del argv[i:i + take]
            argv = moved + argv
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    console.quiet = bool(args.quiet)

    def _sigint(signum, frame):  # stop child renders immediately, state stays resumable
        kill_children()
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _sigint)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        kill_children()
        console.warn("Stopped. Everything finished so far is kept; run `video-studio resume` to continue.")
        return 130
    except ToolError as exc:
        kill_children()
        console.error(str(exc))
        if exc.hint:
            console.info(paint("How to fix: ", "cyan") + exc.hint)
        return 1
    except Exception as exc:  # unexpected: show a short message, full trace in the log
        kill_children()
        console.error(f"Unexpected error: {exc}")
        console.debug(traceback.format_exc())
        if console.log_file:
            console.info(f"Details: {console.log_file}  (set VIDEO_STUDIO_DEBUG=1 to print the trace)")
        else:
            console.info(traceback.format_exc().splitlines()[-3])
        return 1


if __name__ == "__main__":
    sys.exit(main())
