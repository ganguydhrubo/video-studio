"""Footage editor: the creator uploads their own clips and describes the result.

Stages (each resumable): ingest -> transcribe -> plan -> cut -> render -> validate -> export

What it does automatically, like a human editor would:
- transcribes speech with word timings (faster-whisper, local)
- removes silences, filler words and fumbled retakes (keeps the last, best take)
- AI director (optional, one call) picks the hook, the order, what to drop to hit
  the target length, the title and on-screen graphics; local rules otherwise
- smart reframe to the target aspect with face tracking (MediaPipe/OpenCV),
  alternating punch-in zoom on cuts to hide jump cuts
- voice cleanup (spectral denoise + broadcast EQ/compression), auto colour
  correction that never brightens an already-bright face
- word-highlighted captions, title card, keyword pops, music bed ducked under
  the voice, whooshes on graphics, loudness normalised to the platform target
- QA, platform exports, OpenTimelineIO hand-off for manual finishing
"""

from __future__ import annotations

import json
import re
import shutil
import statistics
import time
from difflib import SequenceMatcher
from pathlib import Path

from video_studio import config, memory, planner, qa
from video_studio.ai import Director
from video_studio.engines import canvas, captions, compose, ff, ml, music, tts
from video_studio.looks import LOOKS
from video_studio.project import Project, create, dims, find, slug
from video_studio.util import ToolError, app_data_dir, console, fmt_seconds, read_json, resource_path, run, sha, write_json

FILLERS = {"um", "uh", "umm", "uhh", "erm", "er", "ah", "hmm", "mm", "uhm"}
SOFT_FILLERS = {("you", "know"), ("i", "mean"), ("sort", "of"), ("kind", "of")}


# ---------------------------------------------------------------- CLI entry

def run_cli(args) -> int:
    brief = args.brief
    items = list(args.footage)
    media = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mts", ".mpg", ".mpeg", ".wmv"}
    # `edit a.mp4 b.mov "make a 40s reel"`: the trailing non-media argument is the brief
    while items and not brief and Path(items[-1]).suffix.lower() not in media and not Path(items[-1]).is_file():
        brief = items.pop()
    if args.brief_file:
        brief = Path(args.brief_file).read_text(encoding="utf-8")
    from video_studio.cli import apply_template
    brief = apply_template(args, brief or "")
    # clips, pictures and a music track can be given together: pictures become cutaways, the track the music
    from video_studio import pictures

    dropped = pictures.split_media(items)
    footage = [f.resolve() for f in dropped["videos"]]
    if dropped["music"] and not getattr(args, "music_file", None):
        args.music_file = str(dropped["music"][0])
    if not footage:
        raise ToolError("No video clip given." if dropped["pictures"] else "No footage given.",
                        "Pictures alone make a photo film: video-studio photos <pictures>" if dropped["pictures"]
                        else 'video-studio edit my_clip.mp4 "60 second reel, remove mistakes"')
    for f in footage:
        if not f.exists():
            raise ToolError(f"Footage not found: {f}")
    name = args.name or footage[0].stem
    root = Path(args.out or (Path(config.get("workspace") or ".") / slug(name))).resolve()
    if (root / "project.json").exists():
        proj = Project(root)
    else:
        proj = create(root, name, "edit", aspect=args.aspect or _guess_aspect(brief) or config.get("defaults.aspect"),
                      height=args.height, fps=args.fps, quality=args.quality)
    console.attach_log(proj.logs / "run.log")
    data = proj.data
    data["footage"] = [str(f) for f in footage]
    data["brief"] = brief
    from video_studio.briefing import local_intent
    inferred=local_intent(brief)
    data["edit_options"] = {"target": args.target or inferred['target_seconds'],
                            "target_source":"explicit" if args.target is not None else "brief",
                            "hook_seconds":inferred['hook_seconds'], "use_ai": not args.no_ai and getattr(args, "ai", None) != "none" and config.get("ai.provider") != "none",
                            "keep_silences": args.keep_silences, "reframe": not args.no_reframe,
                            "enhance": not args.no_enhance, "language": args.language, "whisper_model": args.whisper_model,
                            "caption_script": args.caption_script,
                            "fx": getattr(args, "fx", None) or "auto", "like": getattr(args, "like", None),
                            "broll": getattr(args, "broll", None),
                            "pictures": [str(p.resolve()) for p in dropped["pictures"] if p.is_file()],
                            "platforms": args.platform}
    proj.save(data)
    from video_studio.cli import _apply_overrides

    _apply_overrides(proj, args)
    return resume(proj)


def _guess_aspect(brief: str) -> str | None:
    low = brief.lower()
    if any(k in low for k in ("reel", "short", "tiktok", "vertical", "9:16", "story")):
        return "9:16"
    if any(k in low for k in ("youtube video", "landscape", "16:9", "widescreen")):
        return "16:9"
    if "square" in low or "1:1" in low:
        return "1:1"
    return None


def _guess_target(brief: str) -> float | None:
    from video_studio.briefing import local_intent
    return local_intent(brief)['target_seconds']


def _guess_language(brief: str) -> str:
    low = brief.lower()
    for key, code in (("hinglish", "hi"), ("hindi", "hi"), ("bengali", "bn"), ("bangla", "bn"), ("tamil", "ta"),
                      ("telugu", "te"), ("marathi", "mr"), ("english", "en"), ("spanish", "es")):
        if key in low:
            return code
    return ""


def resume(proj: Project) -> int:
    started = time.time()
    steps = [("ingest", ingest), ("transcribe", transcribe), ("sense", sense_stage), ("plan", plan), ("cut", cut), ("render", render),
             ("validate", validate), ("export", export_stage)]
    report = None
    ran_all = not any(proj.done(name) for name, _ in steps)
    for name, fn in steps:
        if proj.done(name):
            continue
        if name == "transcribe" and ran_all:
            _predict_time(proj)
        t0 = time.time()
        try:
            result = fn(proj)
        except ToolError as exc:
            sig, fix = memory.record_failure(proj.id, name, str(exc))
            proj.mark(name, "failed", error=str(exc)[:400], signature=sig)
            raise
        if name == "validate":
            report = result
        memory.add_metric(proj.id, f"edit:{name}", time.time() - t0, {"output_seconds": (proj.read("edit_manifest.json") or {}).get("total")})
    if ran_all:
        _learn_speed(proj, time.time() - started)
    console.ok(f"Edit finished in {fmt_seconds(time.time() - started)}. Master: {proj.master}")
    console.info("Rate it so the studio learns your taste:  video-studio feedback 5 --notes \"...\"")
    return 0 if not report or report["fails"] == 0 else 2


def _speed_file() -> Path:
    from video_studio.util import app_data_dir

    return app_data_dir() / "speed.json"


def _predict_time(proj: Project) -> None:
    """Say how long this edit will take, from how long edits actually took on THIS computer."""
    import math

    from video_studio.util import read_json

    runs = (read_json(_speed_file(), {}) or {}).get("history") or []
    footage = float((proj.state().get("ingest") or {}).get("seconds_of_footage") or 0)
    if not runs or footage <= 0:
        return
    near = min(runs, key=lambda r: abs(math.log(footage / r["footage"])))  # the past edit most like this one
    est = near["seconds"] * (0.5 + 0.5 * footage / near["footage"])       # about half the work scales with length
    console.info(f"    Estimated time: about {fmt_seconds(est)} (from your last {len(runs)} edit(s) on this PC)")


def _learn_speed(proj: Project, seconds: float) -> None:
    from video_studio.util import read_json, write_json

    footage = float((proj.state().get("ingest") or {}).get("seconds_of_footage") or 0)
    if footage <= 0:
        return
    runs = (read_json(_speed_file(), {}) or {}).get("history") or []
    write_json(_speed_file(), {"history": [*runs, {"footage": round(footage, 1), "seconds": round(seconds)}][-8:]})


# ------------------------------------------------------------------ ingest

def ingest(proj: Project) -> None:
    data = proj.data
    files = [Path(f) for f in data["footage"]]
    s = proj.settings
    fps = int(s["fps"])
    infos = []
    for f in files:
        info = ff.probe(f)
        v = ff.stream(info, "video")
        if not v:
            raise ToolError(f"{f.name} has no video stream.")
        rot = 0
        for side in v.get("side_data_list", []) or []:
            if "rotation" in side:
                rot = int(side["rotation"])
        w, h = int(v["width"]), int(v["height"])
        if abs(rot) in (90, 270):
            w, h = h, w
        infos.append({"file": str(f), "width": w, "height": h, "fps": ff.fps_of(v), "duration": float(info["format"]["duration"]),
                      "has_audio": bool(ff.stream(info, "audio"))})
    total = sum(i["duration"] for i in infos)
    console.step(f"Ingesting {len(files)} clip(s), {fmt_seconds(total)} of footage")
    src = proj.build / "source.mp4"
    # Normalise to one constant-frame-rate file: phone footage is often variable-rate, which breaks cut accuracy.
    ref = infos[0]
    tw, th = ref["width"] // 2 * 2, ref["height"] // 2 * 2
    if tw * th > 3840 * 2160:
        tw, th = tw // 2 // 2 * 2, th // 2 // 2 * 2
    args = []
    parts = []
    for i, inf in enumerate(infos):
        args += ["-i", inf["file"]]
        parts.append(f"[{i}:v]fps={fps},scale={tw}:{th}:force_original_aspect_ratio=decrease,pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p[v{i}]")
        if inf["has_audio"]:
            parts.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo[a{i}]")
        else:
            parts.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{inf['duration']:.3f}[a{i}]")
    parts.append("".join(f"[v{i}][a{i}]" for i in range(len(infos))) + f"concat=n={len(infos)}:v=1:a=1[v][a]")
    script = proj.build / "ingest_filter.txt"
    script.write_text(";".join(parts), encoding="utf-8")
    ff.ff([*args, "-/filter_complex", str(script), "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast",
           "-crf", "15", "-c:a", "pcm_s16le", "-ar", "48000", str(src.with_suffix(".mov"))])
    shutil.move(str(src.with_suffix(".mov")), str(proj.build / "source.mov"))
    proj.write("ingest.json", {"clips": infos, "source": str(proj.build / "source.mov"), "width": tw, "height": th,
                               "fps": fps, "duration": total})
    proj.mark("ingest", "done", clips=len(files), seconds_of_footage=round(total, 1))


def sense_stage(proj: Project) -> dict:
    """Look at the footage itself: shots, sharpness, light, motion, people, music -> footage type and edit recipe."""
    from video_studio.engines import sense

    console.step("Watching the footage (shots, light, motion, people, music)")
    ing = proj.read("ingest.json")
    tr = proj.read("transcript.json") or {}
    from video_studio import speed  # 1.9.0: the editor brain's voice analysis runs while the footage is watched

    if tr.get("words"):
        speed.start_prosody(proj.build, _source(proj), tr["words"])
    spoken = sum(w["end"] - w["start"] for w in tr.get("words", []))
    ratio = min(1.0, spoken / max(1.0, ing["duration"]) * 1.6)
    try:
        result = sense.analyse(_source(proj), proj.build / "sense.json", ratio)
    except ToolError as exc:
        console.warn(f"Footage analysis skipped ({exc}); editing from speech only.")
        result = {"type": "talking_head" if ratio > 0.4 else "unknown", "shots": [], "issues": [], "audio": {}, "signals": {}}
        proj.write("sense.json", result)
    sense.report(result)
    proj.mark("sense", "done", type=result.get("type"), shots=len(result.get("shots", [])))
    return result


def _is_montage(proj: Project) -> bool:
    tr = proj.read("transcript.json") or {}
    words = tr.get("words", [])
    dur = (proj.read("ingest.json") or {}).get("duration", 1.0)
    spoken = sum(w["end"] - w["start"] for w in words)
    brief = proj.data.get("brief", "").lower()
    if any(k in brief for k in ("montage", "beat sync", "music edit", "no talking", "b-roll edit", "broll edit")):
        return True
    sense = proj.read("sense.json") or {}
    if sense.get("type") not in (None, "talking_head", "vlog") and (sense.get("audio") or {}).get("speechiness", 1) < 0.25:
        return True  # music or ambience only: Whisper's "words" there are usually song lyrics or noise
    return len(words) < 6 or spoken / max(1.0, dur) < 0.15


def _hook_from_brief(brief: str) -> str:
    """Topic words only: instructions about the edit itself (style, length, platform) never become on-screen text."""
    stop = set("a an the and or for of to in on with my me make edit edits editing video videos reel reels short shorts instagram "
               "youtube tiktok yt ig please viral punchy cinematic top best it this that from into beat beats sync synced "
               "montage music song seconds second sec secs s min mins minute minutes long vertical horizontal square fast slow "
               "smooth clean bold style aesthetic transitions transition effects fx hook captions caption subtitles no without "
               "like using use make create want need 4k hd 1080p vibe vibes energetic trending trend story stories post ad ads advert "
               "promo commercial product hinglish hindi english bengali bangla tamil telugu marathi gujarati language our your".split())
    out = []
    for chunk in re.split(r"[,.;:!?\n]+", brief):
        words = [w.strip("\"'()") for w in chunk.split()]
        keep = [w for w in words if w and w.lower() not in stop and not re.fullmatch(r"[\d%x.]+", w.lower())]
        if keep:
            out += keep
        if len(out) >= 3:
            break
    return " ".join(out[:3]).upper()


def _style_params(proj: Project) -> dict:
    from video_studio.engines import styledna

    name = proj.data["edit_options"].get("like")
    prof = styledna.load(name)
    if name and not prof:
        console.warn(f"Style '{name}' not found (video-studio styles). Using the automatic style.")
    return styledna.apply(prof)


def _montage_plan(proj: Project) -> dict:
    """No (or little) speech: pick the best moments and cut them on the music beat."""
    from video_studio.engines import music as music_engine

    sense = proj.read("sense.json") or {}
    ing = proj.read("ingest.json")
    opts = proj.data["edit_options"]
    brief = proj.data.get("brief", "")
    s = proj.settings
    tone = planner.detect_tone(brief or "energetic")
    creative = planner.decide({"tone": tone}, {**s, "blender_ok": False}, brief)
    aud = sense.get("audio", {})
    own_music = bool(aud.get("has_audio") and aud.get("speechiness", 1) < 0.25 and aud.get("loudness_db", -99) > -32
                     and aud.get("beats") and not s.get("music_file"))
    if own_music:
        beats = [b for b in aud["beats"] if b > 0.05]
        beat = (beats[-1] - beats[0]) / max(1, len(beats) - 1) if len(beats) > 1 else 0.5
    else:
        beat = 60.0 / music_engine.bpm_for(creative["decisions"]["music"], _music_seed(proj))
        beats = [i * beat for i in range(1, 2000)]
    fx = _fx(proj)
    per_cut = {"viral": 2, "bold": 2, "clean": 4}.get(fx, 2) if fx != "off" else 4
    per_cut = _style_params(proj).get("beats_per_cut", per_cut)
    while per_cut * beat < 0.45:
        per_cut *= 2
    total_src = ing["duration"]
    target = opts.get("target") or min(30.0, max(6.0, total_src * 0.6))
    target = min(target, total_src)
    grid = [0.0] + [b for b in beats if b <= target + 0.01]
    cuts = grid[::per_cut]
    if cuts[-1] < target - 0.3:
        cuts.append(round(target, 3))
    slots = [(a, b) for a, b in zip(cuts, cuts[1:]) if b - a >= 0.3]
    # candidate windows from the best shots (skip soft/dark/shaky frames)
    rows = sense.get("timeline", [])
    shots = sense.get("shots") or [{"start": 0.0, "end": total_src, "quality": 0.5}]

    def window_score(a, b):
        seg = [r for r in rows if a <= r["t"] < b]
        if not seg:
            return 0.4
        sharp_ref = max(1e-6, max(r["sharp"] for r in rows))
        sharp = sum(r["sharp"] for r in seg) / len(seg) / sharp_ref
        bright = sum(r["bright"] for r in seg) / len(seg)
        motion = sum(r["motion"] for r in seg) / len(seg)
        return 0.45 * min(1, sharp * 1.6) + 0.3 * (1 - min(1, abs(bright - 0.5) / 0.42)) + 0.25 * min(1, motion / 3)

    chosen, used = [], []
    order = sorted(shots, key=lambda x: -x.get("quality", 0.5))
    for i, (a, b) in enumerate(slots):
        need = b - a
        best = None
        for sh in order:
            st, en = sh["start"] + 0.15, sh["end"] - 0.15
            t = st
            while t + need <= en:
                if not any(t < ue and t + need > us for us, ue in used):
                    sc = (window_score(t, t + need) - 0.18 * sum(1 for c in chosen if c["shot"] == sh["start"])
                          - (0.5 if chosen and chosen[-1]["shot"] == sh["start"] else 0.0))
                    if best is None or sc > best[0]:
                        best = (sc, t, sh["start"])
                t += max(0.25, need / 2)
        if best is None:  # footage shorter than the edit: reuse the best moment with a fresh window
            st = (i * 1.37) % max(0.01, total_src - need)
            best = (0.0, st, -1)
        chosen.append({"start": round(best[1], 3), "end": round(best[1] + need, 3), "shot": best[2], "slot": [a, b]})
        used.append((best[1], best[1] + need))
    # hook first: best-scoring window moves to the opening slot (same length rules)
    if len(chosen) > 2:
        top = max(range(len(chosen)), key=lambda k: window_score(chosen[k]["start"], chosen[k]["end"]))
        chosen[0], chosen[top] = {**chosen[top], "slot": chosen[0]["slot"]}, {**chosen[0], "slot": chosen[top]["slot"]}
        for c in chosen:
            c["end"] = round(c["start"] + (c["slot"][1] - c["slot"][0]), 3)
    title = _hook_from_brief(brief)
    out = {"mode": "montage", "planner": "local-brain", "title": title.title(), "hook_text": title, "slams": [], "brands": [],
           "tone": tone, "keep": [], "retakes": [], "dropped": [], "emphasis": [], "graphics": [], "utterances": [],
           "montage": [{k: c[k] for k in ("start", "end")} for c in chosen], "beat": round(beat, 4), "per_cut": per_cut,
           "own_music": own_music, "scenes": [], "category": sense.get("type", "montage")}
    proj.write("edit_plan.json", out)
    proj.write("creative.json", creative)
    proj.mark("plan", "done", planner="local-brain", mode="montage", cuts=len(chosen))
    console.ok(f"Montage plan: {len(chosen)} cuts on the beat ({per_cut} beats each, {60 / beat:.0f} BPM"
               f"{', your own music' if own_music else ''}) -> {fmt_seconds(target)}")
    return out


def _source(proj: Project) -> Path:
    return Path(proj.read("ingest.json")["source"])


# -------------------------------------------------------------- transcribe

def transcribe(proj: Project) -> dict:
    opts = proj.data["edit_options"]
    src = _source(proj)
    if not ml.available("faster_whisper"):
        raise ToolError("Speech recognition needs faster-whisper.", "video-studio install whisper")
    from video_studio import speed  # 1.9.0: the voice clean-up runs in the background from here to the cut

    speed.start_voice_cleanup(proj.build, src, enhance=opts.get("enhance", True))
    model = ml.best_whisper_model(opts.get("whisper_model", "auto"))
    lang = opts.get("language") or _guess_language(proj.data.get("brief", ""))
    console.step(f"Transcribing speech locally (faster-whisper {model}{', ' + lang if lang else ''})")
    t0 = time.time()
    tr = ml.transcribe(src, model=model, language=lang)
    words = [w for w in tr["words"] if w["end"] > w["start"]]
    proj.write("transcript.json", {"language": tr["language"], "segments": tr["segments"], "words": words,
                                   "translation": tr.get("translation") or []})
    (proj.root / "transcript.txt").write_text("\n".join(f"[{s['start']:7.2f}] {s['text']}" for s in tr["segments"]), encoding="utf-8")
    proj.mark("transcribe", "done", words=len(words), language=tr["language"], seconds=round(time.time() - t0, 1))
    console.ok(f"{len(words)} words ({tr['language']}) in {fmt_seconds(time.time() - t0)}")
    return tr


# -------------------------------------------------------------------- plan

def _utterances(words: list[dict], max_gap: float = 0.7) -> list[dict]:
    """Group words into sentence-like utterances (punctuation or pauses)."""
    utts, cur = [], []
    for w in words:
        if cur:
            gap = w["start"] - cur[-1]["end"]
            last = cur[-1]["text"].rstrip()
            span = cur[-1]["end"] - cur[0]["start"]
            # Sentence ends; long pauses; and, for languages/speakers without full stops (Hinglish), commas and
            # medium pauses once a line is long enough - so the director can pick and drop individual lines.
            if (gap > max_gap or last.endswith((".", "?", "!", "।"))
                    or (last.endswith(",") and len(cur) >= 7)
                    or (gap > 0.32 and len(cur) >= 6)
                    or span > 7.0):
                cut = len(cur)
                if span > 7.0 and gap <= 0.32 and len(cur) >= 6:
                    # no pause right here: break at the biggest pause inside the line instead of mid-phrase
                    j = max(range(3, len(cur)), key=lambda i: cur[i]["start"] - cur[i - 1]["end"])
                    if cur[j]["start"] - cur[j - 1]["end"] >= 0.12:
                        cut = j
                utts.append(cur[:cut])
                cur = cur[cut:]
        cur.append(w)
    if cur:
        utts.append(cur)
    return [{"id": f"u{i + 1:03d}", "words": u, "start": u[0]["start"], "end": u[-1]["end"],
             "text": " ".join(x["text"] for x in u).strip()} for i, u in enumerate(utts)]


def _norm(text: str) -> list[str]:
    # whitespace split keeps Indic words whole (their vowel signs are not \w characters)
    tokens = (t.strip(".,!?;:\"'()[]।…-") for t in text.lower().split())
    return [t for t in tokens if t]


def find_retakes(utts: list[dict]) -> set[str]:
    """An utterance is a fumble if a later one (within 3) restarts the same sentence."""
    drop = set()
    for i, u in enumerate(utts):
        a = _norm(u["text"])
        if len(a) < 3:
            continue
        for j in range(i + 1, min(i + 4, len(utts))):
            b = _norm(utts[j]["text"])
            if len(b) < 3:
                continue
            k = min(len(a), len(b), 6)
            head = SequenceMatcher(None, a[:k], b[:k]).ratio()
            whole = SequenceMatcher(None, a, b[: len(a) + 3]).ratio()
            if head >= 0.7 or whole >= 0.75:
                drop.add(u["id"])
                break
        if len(a) <= 4 and u["words"][-1]["end"] - u["start"] < 2.5 and i + 1 < len(utts) and \
                _norm(utts[i + 1]["text"])[:2] == a[:2]:
            drop.add(u["id"])
    return drop


def _is_filler(words: list[dict], i: int) -> bool:
    t = _norm(words[i]["text"])
    if not t:
        return False
    if t[0] in FILLERS:
        return True
    if i + 1 < len(words):
        pair = (t[0], (_norm(words[i + 1]["text"]) or [""])[0])
        if pair in SOFT_FILLERS and words[i].get("p", 1) < 0.9:
            return True
    return False


def plan(proj: Project) -> dict:
    if _is_montage(proj):
        return _montage_plan(proj)
    data = proj.data
    opts = data["edit_options"]
    tr = proj.read("transcript.json")
    words = tr["words"]
    utts = _utterances(words)
    retakes = find_retakes(utts)
    brief = data.get("brief", "")
    target = opts.get("target")
    s = proj.settings
    from video_studio import briefing
    intent = proj.read('brief_intent.json')
    if not intent or intent.get('original_brief') != brief:
        console.step('Understanding your editing instructions')
        intent=briefing.interpret(brief,s.get('ai_provider'),bool(opts.get('use_ai')),proj.build/'ai_usage.json')
        proj.write('brief_intent.json',intent)
    hook_seconds=opts.get('hook_seconds') or intent.get('hook_seconds')
    if target and opts.get('target_source') != 'explicit' and intent.get('target_seconds') is None and hook_seconds == target:
        target=None
        opts.update(target=None,target_source='corrected-legacy-hook')
        data['edit_options']=opts
        proj.save(data)
        intent['warnings'].append('A previous version mistook the hook duration for the whole video duration. That inferred limit was removed.')
        proj.write('brief_intent.json', intent)
    # Use corrected wording for creative decisions, while retaining the creator's original verbatim.
    brief = intent.get('normalized_brief') or brief
    speech_seconds=sum(w['end']-w['start'] for w in words)
    preserve_content=bool(intent.get('preserve_content')) or bool(target and target >= speech_seconds * .95)
    if opts.get('target_source') == 'explicit' and target and target < speech_seconds * .95:
        preserve_content=False
    if preserve_content:
        # A repeated prefix alone is not proof that a complete statement is a bad take.
        retakes={k for k in retakes if any(u['id']==k and any(v['start']>u['start'] and
                  SequenceMatcher(None,_norm(u['text']),_norm(v['text'])).ratio()>=.92 for v in utts) for u in utts)}
    br = _brain(proj, words, utts)
    from video_studio import story

    # 1. the rule-based story editor always drafts the cut on this PC (works offline, in any language)
    translation = story.english(tr)
    w_, h_ = proj.size
    reel = h_ > w_
    draft, story_notes = story.build(utts, set(retakes), translation, br["strength"], target, brief, reel=reel,
                                     preserve_content=preserve_content)
    en = story.meanings(utts, translation)
    aim = target or (55.0 if not preserve_content and reel and sum(u["end"] - u["start"] for u in utts if u["id"] not in retakes) > 62 else None)
    # 2. an AI senior editor (free provider by default) reviews the draft; its cut is accepted only if it passes the
    #    checks in story.accept_review, otherwise the draft stands
    ai_plan = None
    sig = sha([[[u["id"], u["text"]] for u in utts], brief, target, preserve_content, s.get('ai_provider')], 16)
    saved = proj.read("ai_review.json") or {}
    if opts.get("use_ai") and saved.get("sig") == sig:
        ai_plan = saved["answer"]  # a re-edit (your review, another opening, shorter...) reuses the review: instant, and the story stays put
    elif opts.get("use_ai") and utts:
        director = Director(s.get("ai_provider"), usage_file=proj.build / "ai_usage.json")
        if director.available():
            hints = memory.recall(brief + " " + " ".join(u["text"] for u in utts[:6]), 4)
            prefs = memory.prefs()
            mem = []
            if prefs:
                mem.append("CREATIVE DIRECTOR'S SAVED PREFERENCES: " + "; ".join(f"{k}={v}" for k, v in prefs.items()))
            if hints:
                mem.append("PAST FEEDBACK: " + " | ".join(h["text"][:160] for h in hints))
            prompt = story.review_prompt(utts, set(retakes), en, br["strength"], draft, aim, brief, "\n".join(mem),
                                         json.dumps(list(LOOKS)), json.dumps(memory.DIMENSIONS["music"]))
            console.info("    AI editor: the transcript text (no video, no audio) goes to the AI provider; --no-ai keeps everything on this PC")
            try:
                ai_plan, who = director.ask_json("story review", prompt)
                if not isinstance(ai_plan, dict):
                    raise ToolError("the reply was not a JSON object")
                ai_plan["planner"] = f"ai:{who}"
                proj.write("ai_review.json", {"sig": sig, "answer": ai_plan})
            except ToolError as exc:
                ai_plan = None
                console.warn(f"AI editor unavailable ({str(exc).splitlines()[0][:120]}); using the rule-based story.")
    keep = None
    if ai_plan:
        keep, review_notes = story.accept_review(ai_plan, utts, set(retakes), en, br["strength"], draft, aim,
                                                 preserve_content=preserve_content)
        if keep:
            ai_plan["story"] = [f"AI editor ({ai_plan['planner'][3:]}) reviewed the rule-based draft", *review_notes]
            ai_plan["visuals"] = story.visuals(ai_plan, keep)
        else:
            story_notes = review_notes + story_notes
            ai_plan = None
    if not keep:
        keep = draft
        ai_plan = {"planner": "local-brain", "story": story_notes}
    for n in ai_plan["story"]:
        console.info("Story: " + n)
    manual = [k for k in opts.get("manual_keep") or [] if k in {u["id"] for u in utts}]
    if manual:  # the creator's own review wins over every automatic choice
        opener = keep[0] if keep and keep[0] in manual else None  # the opening the edit already had stays the opening
        keep = [u["id"] for u in utts if u["id"] in manual]
        if opener:
            keep = [opener] + [k for k in keep if k != opener]
        ai_plan = {**ai_plan, "planner": ai_plan["planner"] + " + your review"}
    opening = opts.get("opening")
    if opening in {u["id"] for u in utts}:  # the creator chose the opening line
        keep = [opening] + [u["id"] for u in utts if u["id"] in keep and u["id"] != opening]
        if "your opening" not in ai_plan["planner"]:
            ai_plan = {**ai_plan, "planner": ai_plan["planner"] + " + your opening"}
    by_id = {u["id"]: u for u in utts}
    title = ai_plan.get("title") or br["hook"]["text"].title() or (planner.headline_for(by_id[keep[0]]["text"], 6) if keep else "")
    tone = planner.detect_tone(" ".join(by_id[k]["text"] for k in keep) + " " + brief)
    pseudo = {"tone": tone}
    settings = {**s, "aspect": s["aspect"], "blender_ok": False}
    creative = planner.decide(pseudo, settings, brief)
    for dim in ("look", "music", "captions", "pacing"):
        val = ai_plan.get(dim)
        if val in memory.DIMENSIONS.get(dim, []) and creative["reasons"][dim] != "your instruction/preference":
            creative["decisions"][dim], creative["reasons"][dim] = val, f"AI director ({ai_plan['planner']})"
    graphics = []
    for g in ai_plan.get("graphics") or []:
        if isinstance(g, dict) and g.get("at") in keep and g.get("type") in ("keyword", "lower_third") and g.get("text"):
            graphics.append({"at": g["at"], "type": g["type"], "text": str(g["text"])[:30], "sub": str(g.get("sub") or "")[:40]})
    if not graphics and ai_plan["planner"] == "local-brain":
        for k in keep[1:]:
            stat = re.search(r"\d[\d,.]*\s?(%|x|k|lakh|crore|hours?|minutes?|days?|rupees?)", by_id[k]["text"], re.I)
            if stat and len(graphics) < 3:
                graphics.append({"at": k, "type": "keyword", "text": stat.group(0)[:20], "sub": ""})
    emphasis = [str(e).lower().strip(".,!?") for e in (ai_plan.get("emphasis") or [])][:8] or br["emphasis"]
    kept_spans = [(by_id[k]["start"], by_id[k]["end"]) for k in keep]
    local_slams = [x for x in br["slams"] if any(a - 0.01 <= x["t"] <= b + 0.01 for a, b in kept_spans)]
    ai_slams = [x for x in ai_plan.get("slams") or [] if isinstance(x, dict) and x.get("at") in keep and x.get("text")]
    hook_override = opts.get("hook_override")
    extras = {"hook_text": (hook_override if hook_override is not None else str(ai_plan.get("hook_text") or br["hook"]["text"] or ""))[:40],
              "slams": (ai_slams or local_slams)[:6],
              "brands": [x for x in ai_plan.get("brands") or [] if isinstance(x, dict) and x.get("name")][:4]}
    choices = {"openings": story.openings(utts, set(retakes), en, br["strength"], keep),
               "forecast": story.forecast(utts, keep, en, br["strength"], ai_plan.get("visuals") or {})}
    out = {**extras, "story": ai_plan.get("story") or [], "visuals": ai_plan.get("visuals") or {}, "assist": choices, "planner": ai_plan["planner"], "title": title, "tone": tone, "keep": keep, "retakes": sorted(retakes),
           "dropped": [u["id"] for u in utts if u["id"] not in keep], "emphasis": emphasis, "graphics": graphics,
           "utterances": [{k: u[k] for k in ("id", "start", "end", "text")} for u in utts],
           "scenes": [{"purpose": "hook" if i == 0 else "body", "visual": {"type": "footage"}} for i, _ in enumerate(keep)],
           "category": planner._category(" ".join(by_id[k]["text"] for k in keep))}
    out['hook_seconds']=hook_seconds
    out['intent']=intent
    from video_studio import learn
    probabilities = learn.keep_probabilities(words)
    out['learned_advice'] = {'available': probabilities is not None, 'role': 'advisory_only', 'lines': []}
    if probabilities is not None:
        for u in utts:
            values = [prob for w, prob in zip(words, probabilities) if u['start'] <= w['start'] and w['end'] <= u['end']]
            if values:
                out['learned_advice']['lines'].append({'id': u['id'], 'keep_probability': round(sum(values) / len(values), 3)})
    out['coverage']={'source_speech_seconds':round(sum(u['end']-u['start'] for u in utts),2),
                     'kept_speech_seconds':round(sum(by_id[k]['end']-by_id[k]['start'] for k in keep),2),
                     'kept_lines':len(keep),'total_lines':len(utts),'preserve_content':preserve_content}
    proj.write("edit_plan.json", out)
    proj.write("creative.json", creative)
    _write_edit_storyboard(proj, out, creative)
    kept = sum(by_id[k]["end"] - by_id[k]["start"] for k in keep)
    memory.upsert_project(proj.id, name=proj.data["name"], path=str(proj.root), status="planned", aspect=s["aspect"], tone=tone,
                          decisions=creative["decisions"], summary=f"Edit: {title}. Brief: {brief}. {tone}")
    proj.mark("plan", "done", planner=out["planner"], kept=len(keep), dropped=len(out["dropped"]), retakes=len(retakes))
    console.ok(f"Edit plan ({out['planner']}): keep {len(keep)}/{len(utts)} lines (~{fmt_seconds(kept)}), "
               f"{len(retakes)} fumbled take(s) removed, title '{title}'")
    for dim, arm in creative["decisions"].items():
        console.info(f"    {dim:<10} {arm:<11} ({creative['reasons'][dim]})")
    return out


def _brain(proj: Project, words: list[dict], utts: list[dict]) -> dict:
    """Local analysis: prosody per word (system Python, librosa) + lexical importance -> hook, slams, line strength."""
    from video_studio import brain

    cached = proj.read("brain.json")
    if cached:
        return cached
    from video_studio import speed

    prosody = speed.wait_prosody(proj.build, words)  # started in the background while the footage was watched
    if prosody is None:
        try:
            wav = proj.build / "voice16k.wav"
            if not wav.exists():
                ff.ff(["-i", str(_source(proj)), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)])
            wfile = proj.build / "words_for_prosody.json"
            wfile.write_text(json.dumps([{"start": w["start"], "end": w["end"]} for w in words]), encoding="utf-8")
            prosody = ml.call("prosody", audio=wav, words=wfile, timeout=600)["words"]
        except ToolError as exc:
            console.warn(f"Prosody analysis unavailable ({str(exc)[:100]}); using word importance only.")
    console.step("Editor brain: reading stress, pitch and key words")
    translation = (proj.read("transcript.json") or {}).get("translation") or []
    result = brain.analyze(words, prosody, utts, translation=translation)
    slim = {k: result[k] for k in ("strength", "hook", "hook_end", "slams", "emphasis")}
    proj.write("brain.json", slim)
    brain.save_debug(proj.build / "brain_words.json", result["scored"])
    console.ok(f"Hook: '{result['hook']['text']}'  |  slams: {', '.join(x['text'] for x in result['slams']) or '-'}")
    return slim


def _fit_target(utts: list[dict], keep: list[str], target: float, strength: dict | None = None) -> list[str]:
    """Keep the opening line, then drop the weakest lines (brain strength, else content density) until it fits."""
    by_id = {u["id"]: u for u in utts}
    dur = lambda ids: sum(by_id[i]["end"] - by_id[i]["start"] for i in ids)  # noqa: E731
    kept = list(keep)

    def score(u):
        if strength and u["id"] in strength:
            return strength[u["id"]]
        content = [w for w in _norm(u["text"]) if w not in planner.STOP]
        return len(content) / max(0.5, u["end"] - u["start"])
    while dur(kept) > target * 1.05 and len(kept) > 2:
        victim = min(kept[1:-1] or kept[1:], key=lambda i: score(by_id[i]))
        kept.remove(victim)
    return kept


def _write_edit_storyboard(proj: Project, p: dict, creative: dict) -> None:
    by_id = {u["id"]: u for u in p["utterances"]}
    lines = [f"# Edit plan - {p['title']}", "", f"- Planner: `{p['planner']}` | tone {p['tone']}",
             "- Creative: " + ", ".join(f"{k}=**{v}**" for k, v in creative["decisions"].items()),
             f"- Emphasis (punch-ins): {', '.join(p['emphasis']) or '-'}",
             f"- Graphics: {', '.join(g['type'] + ':' + g['text'] for g in p['graphics']) or '-'}", "",
             "| Order | Line | Source time | Text |", "|---|---|---|---|"]
    for i, k in enumerate(p["keep"], 1):
        u = by_id[k]
        lines.append(f"| {i} | {k} | {u['start']:.1f}-{u['end']:.1f}s | {u['text'].replace('|', '/')} |")
    lines += ["", "Dropped: " + (", ".join(f"{k}{' (retake)' if k in p['retakes'] else ''}" for k in p["dropped"]) or "none"),
              "", "Edit build/edit_plan.json (`keep` order) and run `video-studio resume` after `video-studio status` to re-cut."]
    (proj.root / "edit_plan.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------- cut

def _pieces(proj: Project, p: dict, words: list[dict]) -> list[dict]:
    """Source-time pieces to keep, after removing fillers and long pauses inside kept lines."""
    if p.get("mode") == "montage":
        return [{"start": c["start"], "end": c["end"], "utt": f"m{i}", "words": []} for i, c in enumerate(p["montage"])]
    opts = proj.data["edit_options"]
    pace = {"relaxed": 0.45, "balanced": 0.28, "fast": 0.16}[(proj.read("creative.json") or {}).get("decisions", {}).get("pacing", "balanced")]
    by_id = {u["id"]: u for u in p["utterances"]}
    # rhythm like a human editor: let strong lines land (a beat after), run weak connecting lines tight
    strength = (proj.read("brain.json") or {}).get("strength") or {}
    vals = sorted(strength.get(k, 0.0) for k in p["keep"]) or [0.0]
    hi_cut, lo_cut = vals[int(len(vals) * 0.75)], vals[int(len(vals) * 0.35)]
    from video_studio import story

    en = story.meanings(p["utterances"], (proj.read("transcript.json") or {}).get("translation") or [])
    tails, pace_of, held, tightened = {}, {}, 0, 0
    for i, k in enumerate(p["keep"]):
        st, text = strength.get(k, 0.0), en.get(k, "")
        if "?" in text or by_id[k]["text"].strip().endswith("?"):
            tails[k], pace_of[k] = 0.45, pace * 1.2
        elif st >= hi_cut or i == 0 or i == len(p["keep"]) - 1:
            tails[k], pace_of[k] = 0.36, pace * 1.2
        elif st <= lo_cut:
            tails[k], pace_of[k] = 0.05, pace * 0.6
        else:
            tails[k], pace_of[k] = 0.11, pace
        held += tails[k] >= 0.36
        tightened += tails[k] <= 0.05
    if opts.get("keep_silences") is not True and strength and p.get("mode") != "montage":
        console.info(f"Pacing: holds a beat after {held} strong line(s)/questions, runs {tightened} weaker line(s) tight")
    pieces = []
    for k in p["keep"]:
        u = by_id[k]
        uw = [w for w in words if w["start"] >= u["start"] - 0.01 and w["end"] <= u["end"] + 0.01]
        if not uw:
            continue
        if opts.get("keep_silences"):
            pieces.append({"start": max(0.0, uw[0]["start"] - 0.08), "end": uw[-1]["end"] + 0.12, "utt": k,
                           "words": uw})
            continue
        cur = None
        for i, w in enumerate(uw):
            if not p.get('coverage', {}).get('preserve_content') and _is_filler(uw, i):
                continue
            if cur and w["start"] - cur["words"][-1]["end"] <= pace_of.get(k, pace):
                cur["words"].append(w)
            else:
                if cur:
                    pieces.append(cur)
                cur = {"utt": k, "words": [w]}
        if cur:
            pieces.append(cur)
    last_of = {}
    for i, pc in enumerate(pieces):
        last_of[pc["utt"]] = i
    for i, pc in enumerate(pieces):
        tail = tails.get(pc["utt"], 0.11) if last_of.get(pc["utt"]) == i else 0.11  # the beat goes after the line
        nxt_word = next((w["start"] for w in words if w["start"] > pc["words"][-1]["end"] + 0.01), None)
        if nxt_word is not None:  # never let the held beat show the start of the next (cut) word
            tail = min(tail, max(0.05, nxt_word - pc["words"][-1]["end"] - 0.04))
        pc.setdefault("start", max(0.0, pc["words"][0]["start"] - 0.07))
        pc.setdefault("end", pc["words"][-1]["end"] + tail)
    # merge pieces that are contiguous in source time (fewer cuts, smoother)
    merged = []
    for pc in pieces:
        if merged and merged[-1]["utt"] == pc["utt"] and pc["start"] - merged[-1]["end"] < 0.06:
            merged[-1]["end"] = pc["end"]
            merged[-1]["words"] += pc["words"]
        else:
            merged.append(pc)
    # Adjacent lines can share padded boundaries: never play the same source instant twice.
    for left, right in zip(merged, merged[1:]):
        if left['words'][-1]['end'] <= right['words'][0]['start'] and left['end'] > right['start']:
            boundary = (left['words'][-1]['end'] + right['words'][0]['start']) / 2
            left['end'] = min(left['end'], boundary)
            right['start'] = max(right['start'], boundary)
    return [pc for pc in merged if pc["end"] - pc["start"] >= 0.25]


def _faces(proj: Project, src: Path) -> list[dict]:
    cache = proj.build / "faces.json"
    data = read_json(cache)
    if data is None:
        if not (ml.available("cv2") or ml.available("mediapipe")):
            return []
        console.step("Tracking faces for smart reframing")
        try:
            data = ml.call("faces", video=src, samples_per_second=3, model=_matte() or "")
        except ToolError as exc:
            console.warn(f"Face tracking unavailable ({exc}); centring the frame instead.")
            data = {"track": []}
        write_json(cache, data)
    return data.get("track", [])


def _matte() -> str:
    from video_studio.engines import style

    m = style.matte_model()
    return str(m) if m else ""


def _word_info(proj: Project, pieces: list[dict]) -> list[dict | None]:
    """Brain scores (voice stress, question, contrast, number, clean) for each output word, via its source time."""
    scored = read_json(proj.build / "brain_words.json", []) or []
    by_start = sorted(scored, key=lambda x: x["start"])
    starts = [x["start"] for x in by_start]
    import bisect

    out = []
    for pc in pieces:
        for wd in pc["words"]:
            src_t = wd["start"] - pc["out_start"] + pc["src_start"]
            k = bisect.bisect_left(starts, src_t - 0.08)
            hit = by_start[k] if k < len(by_start) and abs(by_start[k]["start"] - src_t) < 0.12 else None
            if hit is not None:
                from video_studio import brain

                hit = {**hit, "clean": brain.punchable(hit)}  # effects only ever follow meaningful, verified words
            out.append(hit)
    return out


def _broll_pass(proj: Project, m: dict, p: dict, video: Path, splan: dict, w: int, h: int, total: float) -> Path:
    """Cut away to matching footage from your B-roll library while your voice continues (see broll.py)."""
    from video_studio import broll, story

    opts = proj.data["edit_options"]
    if "broll" in (m.get("timeline") or {}):  # timeline: the cutaways the person kept, where they put them
        cuts = [c for c in m["timeline"]["broll"] if Path(c["path"]).is_file()]
        m["broll"] = cuts
        proj.write("edit_manifest.json", m)
        return broll.apply(video, cuts, w, h, m["fps"], proj.build / "with_broll.mp4", proj.settings["quality"]) if cuts else video
    if p.get("mode") == "montage" or not broll.library_dirs(opts.get("broll")):
        return video
    index = broll.build_index(opts.get("broll"))
    en = story.assign(p["utterances"], story.english(proj.read("transcript.json") or {}))
    vis = p.get("visuals") or {}
    lines: list[dict] = []
    for pc in m["pieces"]:
        end = pc["out_start"] + (pc["src_end"] - pc["src_start"])
        if lines and lines[-1]["id"] == pc["utt"]:
            lines[-1]["t1"] = end
        else:
            lines.append({"id": pc["utt"], "t0": pc["out_start"], "t1": end, "en": en.get(pc["utt"], ""),
                          "visual": vis.get(pc["utt"], "")})
    busy = [(s["t0"] - 0.2, s["t1"] + 0.2) for s in splan.get("slams", [])]
    cuts = broll.plan(lines, index, busy, total)
    m["broll"] = cuts
    proj.write("edit_manifest.json", m)
    if not cuts:
        console.info("B-roll: nothing in your library matches what you say closely enough; staying on you")
        return video
    for c in cuts:
        console.info(f"B-roll {c['t0']:.1f}-{c['t1']:.1f}s: {c['why']}")
    dest = proj.build / "with_broll.mp4"
    return broll.apply(video, cuts, w, h, m["fps"], dest, proj.settings["quality"])


def _music_seed(proj: Project) -> int:
    import zlib

    key = proj.data.get("name", "") + proj.data.get("brief", "") + str(proj.data.get("created", ""))
    return zlib.crc32(key.encode()) % 100000


MUSIC_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}


def library_pick(mood: str, seconds: float, seed: int) -> dict | None:
    """A track from your music folder (%LOCALAPPDATA%/VideoStudio/music, optionally in <mood> subfolders), skipping
    the last few used. Returns its biggest energy rise (the drop) so the edit can land it on the hook."""
    import numpy as np

    lib = app_data_dir() / "music"
    if not lib.exists():
        return None
    tracks = [f for f in lib.rglob("*") if f.suffix.lower() in MUSIC_EXT]
    if not tracks:
        return None
    mood_tracks = [f for f in tracks if f.parent.name.lower() == mood.lower()]
    pool = mood_tracks or [f for f in tracks if f.parent == lib] or tracks
    used_f = app_data_dir() / "music_used.json"
    used = (read_json(used_f, {}) or {}).get("recent", [])
    fresh = [f for f in pool if str(f) not in used] or pool
    pick = sorted(fresh)[seed % len(fresh)]
    write_json(used_f, {"recent": ([str(pick)] + used)[: max(1, min(5, len(pool) - 1))]})
    tmp = app_data_dir() / "music_probe.raw"
    ff.ff(["-i", str(pick), "-ac", "1", "-ar", "4000", "-f", "s16le", str(tmp)])
    pcm = np.fromfile(tmp, dtype="<i2").astype("float32")
    tmp.unlink(missing_ok=True)
    if pcm.size < 4000 * 5:
        return {"path": str(pick), "drop": 0.0}
    rms = np.sqrt(np.convolve(pcm ** 2, np.ones(2000) / 2000, mode="valid")[::1000] + 1e-9)  # 4 Hz energy curve
    rise = rms[8:] - rms[:-8]  # biggest 2-second jump in energy = the drop
    limit = max(1, len(rise) - int((seconds - 2) * 4))
    drop = float(np.argmax(rise[:limit]) + 8) / 4.0 if limit > 1 else 0.0
    return {"path": str(pick), "drop": drop}


SAFE_TOP = 0.08     # status bar / account name on Reels & Shorts
SAFE_BOTTOM = 0.80  # Reels/Shorts/TikTok put caption text and buttons over the bottom ~20% of a vertical video
CAP_BLOCK = 0.11    # one caption cue (up to ~1.3 lines of reel-size text) as a share of frame height


def _caption_place(proj: Project, video: Path, default: float) -> dict:
    """Where the caption bottom edge goes (share of height): clear of the face (measured on the cut) and of the UI."""
    cache = proj.build / "faces_cut.json"
    data = read_json(cache)
    if data is None:
        try:
            data = ml.call("faces", video=video, samples_per_second=2, model=_matte() or "")
        except ToolError:
            data = {"track": []}
        write_json(cache, data)
    pts = [p for p in data.get("track", []) if p.get("y1") is not None]
    if len(pts) < 3:
        return {"y": default, "top": False, "face_bottom": None, "face_top": None, "clear": True,
                "why": "no face in the frame; standard position"}
    bottoms, tops = sorted(p["y1"] for p in pts), sorted(p["y0"] for p in pts)
    chin, brow = bottoms[int(len(bottoms) * 0.9)], tops[int(len(tops) * 0.1)]  # the face's usual lowest / highest point
    y = min(SAFE_BOTTOM, max(default, chin + 0.02 + CAP_BLOCK))
    if y - CAP_BLOCK >= chin:
        why = "moved down to clear your face" if y > default + 0.005 else "below your face"
        return {"y": round(y, 3), "top": False, "face_bottom": round(chin, 3), "face_top": round(brow, 3), "clear": True, "why": why}
    if brow - SAFE_TOP >= CAP_BLOCK + 0.01:  # close-up: no room below, so go above the face (over hair/background)
        y_top = max(SAFE_TOP, brow - 0.01 - CAP_BLOCK)
        return {"y": round(y_top, 3), "top": True, "face_bottom": round(chin, 3), "face_top": round(brow, 3), "clear": True,
                "why": "close-up: your face fills the lower frame, so captions go above it"}
    return {"y": SAFE_BOTTOM, "top": False, "face_bottom": round(chin, 3), "face_top": round(brow, 3), "clear": False,
            "why": "your face fills the whole frame; captions sit low, over the chin, clear of the app buttons"}


def _colour_correction(src: Path, duration: float) -> tuple[str, dict]:
    """Measure luma/saturation and return a gentle eq filter. Never brightens an already-bright image."""
    res = run([ff.ffmpeg(), "-hide_banner", "-nostdin", "-i", str(src), "-vf",
               f"fps=1/{max(1.0, duration / 12):.2f},signalstats,metadata=print:file=-", "-an", "-f", "null", "-"],
              check=False, timeout=900)
    text = (res.stdout or "") + (res.stderr or "")
    yavg = [float(x) for x in re.findall(r"signalstats\.YAVG=([\d.]+)", text)]
    sat = [float(x) for x in re.findall(r"signalstats\.SATAVG=([\d.]+)", text)]
    ymax = [float(x) for x in re.findall(r"signalstats\.YHIGH=([\d.]+)", text)]
    if not yavg:
        return "", {}
    y = statistics.median(yavg)
    s = statistics.median(sat) if sat else 40
    hi = statistics.median(ymax) if ymax else 200
    brightness = 0.0
    if y < 85:
        brightness = min(0.08, (100 - y) / 900)  # lift dark footage a little
    elif y > 150 or hi > 238:
        brightness = -min(0.05, (y - 130) / 1200)  # tame hot/over-bright faces
    contrast = 1.05 if 70 < y < 160 else 1.0
    saturation = 1.15 if s < 30 else (1.05 if s < 45 else 1.0)
    gamma = 1.0 if y > 85 else 1.06
    filt = f"eq=contrast={contrast:.3f}:brightness={brightness:.3f}:saturation={saturation:.3f}:gamma={gamma:.3f}"
    return filt, {"yavg": round(y, 1), "satavg": round(s, 1), "yhigh": round(hi, 1)}


def _fx(proj: Project) -> str:
    from video_studio.engines import style

    fx = (proj.data.get("edit_options") or {}).get("fx", "auto")
    if fx == "auto":
        fx = style.guess_intensity(proj.data.get("brief", ""))
    return fx


def cut(proj: Project) -> dict:
    ing = proj.read("ingest.json")
    p = proj.read("edit_plan.json")
    tr = proj.read("transcript.json")
    opts = proj.data["edit_options"]
    s = proj.settings
    creative = proj.read("creative.json")
    d = creative["decisions"]
    src = Path(ing["source"])
    fps = int(s["fps"])
    w, h = proj.size
    sw, sh = ing["width"], ing["height"]
    pieces = _pieces(proj, p, tr["words"])
    if not pieces:
        raise ToolError("Nothing left to keep after cleaning the transcript.", "Check transcript.txt, or use --keep-silences.")
    total = sum(pc["end"] - pc["start"] for pc in pieces)
    console.step(f"Cutting {len(pieces)} piece(s) -> {fmt_seconds(total)} (from {fmt_seconds(ing['duration'])})")

    # audio: optional denoise of the whole source once, then broadcast polish
    audio_src = proj.build / "voice_clean.wav"
    from video_studio import speed

    early = speed.wait_voice_cleanup(proj.build)  # started in the background during transcription (same command)
    if not audio_src.exists():
        raw = proj.build / "voice_raw.wav"
        if early is None or not raw.exists():
            ff.ff(["-i", str(src), "-vn", "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(raw)])
        stage = raw
        if opts.get("enhance", True) and p.get("mode") != "montage" and ml.available("noisereduce"):
            console.step("Cleaning up the voice (spectral noise reduction)")
            dn = proj.build / "voice_dn.wav"
            try:
                if early is None:
                    ml.call("denoise", audio=raw, dest=dn, strength=0.8)
                stage = dn
            except ToolError as exc:
                console.warn(f"Noise reduction skipped: {exc}")
        if opts.get("enhance", True) and p.get("mode") != "montage":
            tts.polish(stage, audio_src)
        else:
            shutil.copy2(stage, audio_src)

    # framing
    target_ratio, src_ratio = w / h, sw / sh
    track = _faces(proj, src) if opts.get("reframe", True) and abs(target_ratio - src_ratio) > 0.02 else []
    grade, stats = _colour_correction(src, ing["duration"]) if opts.get("enhance", True) else ("", {})
    look_grade = LOOKS[d["look"]]["grade"] if d["look"] else ""
    emphasis = set(p.get("emphasis") or [])
    pieces_dir = proj.build / "pieces"
    pieces_dir.mkdir(exist_ok=True)
    out_pieces, t_out, encodes, made = [], 0.0, [], []
    for i, pc in enumerate(pieces):
        dur = pc["end"] - pc["start"]
        words_here = {_norm(wd["text"])[0] for wd in pc["words"] if _norm(wd["text"])}
        punch = 1.0 if _fx(proj) != "off" else (1.18 if words_here & emphasis else (1.0 if i % 2 == 0 else 1.09))
        if src_ratio > target_ratio + 0.02 and opts.get("reframe", True):
            # crop a vertical/narrower window around the speaker
            cw = sh * target_ratio / punch
            ch = sh / punch
            xs = [t["x"] for t in track if t["x"] is not None and pc["start"] - 0.5 <= t["t"] <= pc["end"] + 0.5]
            cx = (statistics.median(xs) if xs else 0.5) * sw
            x0 = min(max(0.0, cx - cw / 2), sw - cw)
            y0 = (sh - ch) * 0.35
            vf = f"crop={int(cw) // 2 * 2}:{int(ch) // 2 * 2}:{int(x0)}:{int(y0)},scale={w}:{h}:flags=lanczos"
        elif src_ratio < target_ratio - 0.02 or not opts.get("reframe", True):
            vf = (f"split=2[a][b];[a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},gblur=sigma=30[bg];"
                  f"[b]scale={w}:{h}:force_original_aspect_ratio=decrease[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2")
            if punch > 1.0:
                vf += f",scale=iw*{punch}:ih*{punch},crop={w}:{h}"
        else:
            cw, ch = sw / punch, sh / punch
            vf = f"crop={int(cw) // 2 * 2}:{int(ch) // 2 * 2}:{int((sw - cw) / 2)}:{int((sh - ch) * 0.4)},scale={w}:{h}:flags=lanczos"
        if "camera is shaky" in ((proj.read("sense.json") or {}).get("issues") or []):
            vf = "deshake," + vf
        vf += ",setsar=1" + (f",{grade}" if grade else "")
        dest = pieces_dir / f"p{i:04d}.mov"
        if not dest.exists():
            encodes.append((dest, ["-ss", f"{pc['start']:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
                   "-ss", f"{pc['start']:.3f}", "-t", f"{dur:.3f}", "-i", str(audio_src),
                   "-filter_complex", f"[0:v]{vf},fps={fps},format=yuv420p[v];[1:a]afade=t=in:d=0.012,afade=t=out:st={max(0.0, dur - 0.015):.3f}:d=0.015,aresample=48000[a]",
                   "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "14",
                   "-c:a", "pcm_s16le", "-t", f"{dur:.3f}", str(dest.with_suffix(".part.mov"))]))
        made.append((pc, dest, punch))

    # 1.9.0: the pieces are independent short encodes, so a few run at once (identical files, less waiting)
    def encode_piece(job):
        piece, args = job
        ff.ff(args)
        piece.with_suffix(".part.mov").replace(piece)

    speed.run_all(encode_piece, encodes, speed.piece_workers(), lambda n: console.progress("cutting", n, len(encodes)))
    for pc, dest, punch in made:
        out_pieces.append({"file": str(dest), "src_start": pc["start"], "src_end": pc["end"], "out_start": round(t_out, 3),
                           "utt": pc["utt"], "punch": punch, "vf": vf,  # vf: the timeline can re-cut a longer range
                           "words": [{"text": wd["text"], "start": round(t_out + wd["start"] - pc["start"], 3),
                                      "end": round(t_out + wd["end"] - pc["start"], 3)} for wd in pc["words"]]})
        t_out += ff.duration(dest)
    listing = proj.build / "pieces.txt"
    listing.write_text("\n".join(f"file '{Path(x['file']).as_posix()}'" for x in out_pieces), encoding="utf-8")
    cut_file = proj.build / "cut.mov"
    ff.ff(["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(cut_file)])
    total = ff.duration(cut_file)
    manifest = {"source": str(src), "fps": fps, "size": [w, h], "total": round(total, 3), "pieces": out_pieces,
                "segments": [{"start": x["src_start"], "end": x["src_end"]} for x in out_pieces],
                "colour": stats, "grade": grade, "look_grade": look_grade, "cut": str(cut_file)}
    proj.write("edit_manifest.json", manifest)
    proj.mark("cut", "done", pieces=len(out_pieces), seconds=round(total, 1))
    console.ok(f"Cut to {fmt_seconds(total)} ({len(out_pieces)} pieces, colour {stats or 'unchanged'})")
    return manifest


# ------------------------------------------------------------------ render

def render(proj: Project) -> Path:
    m = proj.read("edit_manifest.json")
    p = proj.read("edit_plan.json")
    s = proj.settings
    d = proj.read("creative.json")["decisions"]
    w, h = m["size"]
    look = d["look"]
    total = m["total"]
    gfx_dir = proj.build / "graphics"
    gfx_dir.mkdir(exist_ok=True)
    overlays, sfx_events = [], []
    by_utt: dict[str, float] = {}
    for pc in m["pieces"]:
        by_utt.setdefault(pc["utt"], pc["out_start"])
    raw_words = [dict(wd) for pc in m["pieces"] for wd in pc["words"]]
    words = list(raw_words)
    script = proj.data["edit_options"].get("caption_script", "auto")
    lang = (proj.read("transcript.json") or {}).get("language", "")
    brief_low = proj.data.get("brief", "").lower()
    if script == "auto" and lang in ("hi", "ur") and not any(k in brief_low for k in ("devanagari", "hindi captions", "hindi script")):
        script = "roman"  # Hinglish creators write captions in Latin letters
    tl = m.get("timeline") or {}   # timeline.py: the person's own captions, pictures, B-roll and music level win
    if tl.get("words") is None and words and script == "roman" and any(ord(c) > 0x2FF for wd in words for c in wd["text"]):
        if proj.data["edit_options"].get("use_ai"):
            words = _romanize(proj, words)
        else:
            from video_studio import brain

            words = brain.romanize_words(words)
    from video_studio import vocab

    words = [dict(x) for x in tl["words"]] if tl.get("words") is not None else vocab.correct(words)
    m["caption_words"] = [{"text": x["text"], "start": x["start"], "end": x["end"]} for x in words]
    word_info = _word_info(proj, m["pieces"])
    fx = _fx(proj)
    cut_video = Path(m["cut"])
    if fx != "off":
        from video_studio.engines import style

        director = {**p, "chips": [{"t": by_utt[g["at"]] + 0.3, "text": g["text"]} for g in p.get("graphics") or []
                                   if g.get("at") in by_utt]}
        director["style_params"] = _style_params(proj)
        director["beat"] = p.get("beat")
        director["word_info"] = word_info
        splan = style.build_plan([{"out_start": pc["out_start"]} for pc in m["pieces"]], raw_words, total, director, fx,
                                 seed=len(p.get("title", "")) + len(m["pieces"]), display_words=words)
        proj.write("style_plan.json", splan)
        if splan.get("reasons"):
            console.info("Effects chosen from what you said and how you said it:\n  " + "\n  ".join(splan["reasons"][:12]))
        console.step(f"Style pass ({fx}): {len(splan['shots'])} camera angles, {len(splan['transitions'])} transitions, "
                     f"{len(splan['slams'])} slams, {len(splan['hits'])} hits")
        styled = proj.build / "styled.mp4"
        if not styled.exists() or styled.stat().st_mtime < (proj.build / "style_plan.json").stat().st_mtime:
            style.render(cut_video, styled, splan, w, h, m["fps"], total, look, s["quality"], proj.build / "style")
        cut_video = styled
        sfx_events += style.sfx_events(splan)
    elif p.get("title"):
        png = canvas.overlay_png("title", p["title"], "", w, h, look, gfx_dir / "title.png")
        overlays.append({"png": png, "start": 0.15, "end": min(3.0, total * 0.4)})
        sfx_events.append(("hit", 0.12, 0.35))
    for i, g in enumerate((p.get("graphics") or []) if fx == "off" else []):
        at = by_utt.get(g["at"])
        if at is None or at < 3.2:
            continue
        png = canvas.overlay_png(g["type"], g["text"], g.get("sub", ""), w, h, look, gfx_dir / f"g{i}.png")
        overlays.append({"png": png, "start": at + 0.2, "end": min(total - 0.2, at + (2.2 if g["type"] == "keyword" else 3.5))})
        sfx_events.append(("whoosh" if g["type"] == "lower_third" else "pop", max(0.0, at), 0.3))
    ass = srt = None
    if words:
        srt = captions.build_srt(words, proj.build / "captions.srt")
        if d["captions"] != "none" and s.get("captions_enabled", True):
            cap_words = words
            if fx != "off":
                spans = [(x["t0"] - 0.05, x["t1"] + 0.15) for x in splan["slams"]]
                cap_words = [x for x in words if not any(a <= x["start"] < b for a, b in spans)]
                fonts = proj.build / "fonts"
                if not fonts.exists():
                    shutil.copytree(resource_path("assets", "fonts"), fonts)
            y_cap = (0.72 if fx != "off" else 0.78) if h > w else 0.9
            if h > w:
                place = _caption_place(proj, cut_video, y_cap)
                y_cap = place["y"]
                m["caption_place"] = place
                console.info(f"Captions {'from' if place['top'] else 'at'} {y_cap:.0%} of the height: {place['why']}")
                if place["top"] and fx != "off":  # top captions would collide with chips; the chip already says it
                    busy = [(c["t0"] - 0.05, c["t1"] + 0.05) for c in splan.get("chips", [])]
                    cap_words = [x for x in cap_words if not any(a <= x["start"] < b for a, b in busy)]
            ass = captions.build_ass(cap_words, w, h, "reel" if fx != "off" and d["captions"] != "none" else d["captions"],
                                     look, proj.build / "captions.ass", y_ratio=y_cap,
                                     top=bool(m.get("caption_place", {}).get("top")) if h > w else False)
    # cutaways go in after the caption position was measured on YOUR face; captions are then burned on top
    cut_video = _broll_pass(proj, m, p, cut_video, splan if fx != "off" else {}, w, h, total)
    from video_studio import pictures

    cut_video = pictures.apply_pass(proj, m, cut_video, splan if fx != "off" else {}, w, h, total)
    m["srt"] = str(srt) if srt else ""
    proj.write("edit_manifest.json", m)
    console.step("Finishing: graphics, captions, grade")
    video_only = proj.build / "video_master.mp4"
    from video_studio import library
    grade = ",".join(x for x in [m.get("look_grade") if s.get("look") and fx == "off" else "", library.grade_filter(s.get("grade"))] if x)
    compose.assemble_video([cut_video], [total], [], 0.0, m["fps"], w, h, grade, ass, s["quality"], video_only,
                           overlays=overlays, total=total)
    console.step("Mixing voice, music and effects")
    audio_dir = proj.build / "audio"
    audio_dir.mkdir(exist_ok=True)
    voice = audio_dir / "voice_cut.wav"
    ff.ff(["-i", m["cut"], "-vn", "-c:a", "pcm_s16le", "-ar", "48000", str(voice)])
    music_path = None
    tl_music = tl.get("music") or {}
    if s.get("music_enabled", True) and tl_music.get("on", True):
        if tl_music.get("reuse") and Path(tl_music["reuse"]).is_file():  # timeline: keep the same library track
            music_path = Path(tl_music["reuse"])
            console.info(f"Music: the same track as before ({(m.get('music') or {}).get('file', music_path.name)})")
        elif p.get("own_music"):
            music_path = audio_dir / "own_music.wav"
            if not music_path.exists():
                ff.ff(["-i", m["source"], "-vn", "-ac", "2", "-ar", "48000", "-t", f"{total + 0.3:.3f}", "-c:a", "pcm_s16le", str(music_path)])
        elif s.get("music_file"):
            music_path = Path(s["music_file"])
            m["music"] = {"source": "your song", "file": music_path.name.split("_", 1)[-1] if re.match(r"[0-9a-f]{32}_", music_path.name) else music_path.name}
            console.info(f"Music: your song {m['music']['file']}")
        else:
            hook_end = float(splan["slams"][0]["t1"]) if splan.get("slams") and p.get("mode") != "montage" else 0.0
            pick = library_pick(d["music"], total, _music_seed(proj))
            if pick:
                music_path = audio_dir / "music_library.wav"
                start = max(0.0, pick["drop"] - hook_end)
                ff.ff(["-ss", f"{start:.3f}", "-i", pick["path"], "-vn", "-ac", "2", "-ar", "48000", "-t", f"{total + 0.5:.3f}",
                       "-af", f"afade=t=out:st={max(0.0, total - 1.5):.3f}:d=1.5", "-c:a", "pcm_s16le", str(music_path)])
                m["music"] = {"source": "your library", "file": Path(pick["path"]).name, "drop_on": round(hook_end, 2)}
                console.info(f"Music: {Path(pick['path']).name} from your library, its drop lined up with the hook end")
            else:
                stops = [x["t0"] for x in splan.get("slams", [])[1:]] if p.get("mode") != "montage" else []
                arr, info = music.bed(d["music"], total + 0.5, seed=_music_seed(proj), drop_at=hook_end, stops=stops)
                music_path = audio_dir / f"music_{info['genre']}_{_music_seed(proj)}.wav"
                music.write_wav(music_path, arr)
                m["music"] = {"source": "generated", **info, "stops": len(stops)}
                console.info(f"Music: original {info['genre']} bed at {info['bpm']:.0f} BPM, drop at {info['drop_at']:.1f}s"
                             + (f", drops out before {len(stops)} punch-words" if stops else ""))
    fx = []
    for kind, at, gain in sfx_events:
        path = audio_dir / f"sfx_{kind}.wav"
        if not path.exists():
            music.write_wav(path, music.sfx(kind))
        fx.append((path, at, gain))
    final_audio = proj.build / "audio_master.wav"
    if p.get("mode") == "montage":
        amb = audio_dir / "ambient.wav"
        ff.ff(["-i", str(voice), "-af", "volume=0.0" if p.get("own_music") else "volume=0.25", str(amb)])
        compose.mix_audio([(amb, 0.0)], music_path, fx, total, final_audio, float(s["loudness"]),
                          music_gain=float(tl_music.get("volume", 1.0)), duck=False)
    else:
        compose.mix_audio([(voice, 0.0)], music_path, fx, total, final_audio, float(s["loudness"]),
                          music_gain=float(tl_music.get("volume", 0.14)))
    compose.mux(video_only, final_audio, proj.master)
    proj.mark("render", "done", file=str(proj.master))
    console.ok(f"Rendered {proj.master} ({fmt_seconds(total)})")
    return proj.master


def _romanize(proj: Project, words: list[dict]) -> list[dict]:
    """Transliterate caption words to Roman script (e.g. Hinglish) with one cached AI call; timings untouched."""
    from video_studio import brain

    director = Director(proj.settings.get("ai_provider"), usage_file=proj.build / "ai_usage.json")
    if not director.available():
        return brain.romanize_words(words)
    src = [w["text"] for w in words]
    prompt = ("Transliterate each item of this JSON array of spoken words into casual Roman script as Indian creators write "
              "Hinglish on social media (e.g. 'आज' -> 'aaj', 'प्रोडक्ट' -> 'product'). Fix obvious speech-recognition "
              "misspellings of English loanwords. Return ONLY a JSON array of exactly the same length, one string per item.\n"
              + json.dumps(src, ensure_ascii=False))
    try:
        out, _ = director.ask_json("roman captions", prompt)
    except ToolError as exc:
        console.warn(f"AI Roman captions unavailable ({str(exc)[:100]}); using the built-in transliterator.")
        return brain.romanize_words(words)
    if not isinstance(out, list) or len(out) != len(words):
        console.warn("AI Roman captions came back the wrong length; using the built-in transliterator.")
        return brain.romanize_words(words)
    for w, t in zip(words, out):
        w["text"] = str(t).strip() or w["text"]
    return words


def validate(proj: Project) -> dict:
    s = proj.settings
    m = proj.read("edit_manifest.json")
    w, h = proj.size
    report = qa.check(proj.master, {"width": w, "height": h, "fps": int(s["fps"]), "duration": m["total"],
                                    "loudness": float(s["loudness"]), "caption_place": m.get("caption_place")}, proj.output / "qa", Path(m["srt"]) if m.get("srt") else None)
    qa.print_report(report)
    proj.mark("validate", "done" if report["fails"] == 0 else "failed", score=report["score"])
    memory.upsert_project(proj.id, qa_score=report["score"], duration=report["duration"], status="validated")
    return report


def export_stage(proj: Project) -> list[Path]:
    from video_studio import export

    files = export.export(proj, proj.data["edit_options"].get("platforms") or ["shorts"])
    return files
