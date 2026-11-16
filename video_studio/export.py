"""Platform exports, thumbnail, caption sidecars, OpenTimelineIO hand-off."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from video_studio.engines import captions, ff
from video_studio.project import Project
from video_studio.util import console, read_json, write_json

PLATFORMS = {
    # name: aspect, width, height, max seconds, video bitrate cap
    "shorts": {"aspect": "9:16", "size": (1080, 1920), "max": 180, "label": "YouTube Shorts"},
    "reels": {"aspect": "9:16", "size": (1080, 1920), "max": 180, "label": "Instagram Reels"},
    "tiktok": {"aspect": "9:16", "size": (1080, 1920), "max": 600, "label": "TikTok"},
    "youtube": {"aspect": "16:9", "size": (1920, 1080), "max": 43200, "label": "YouTube"},
    "square": {"aspect": "1:1", "size": (1080, 1080), "max": 3600, "label": "Feed (square)"},
    "feed45": {"aspect": "4:5", "size": (1080, 1350), "max": 3600, "label": "Instagram feed 4:5"},
}


def reframe_filter(src_w: int, src_h: int, dst_w: int, dst_h: int) -> str:
    """Fit without cropping important content: blurred fill behind a scaled copy when aspects differ."""
    src_ratio, dst_ratio = src_w / src_h, dst_w / dst_h
    if abs(src_ratio - dst_ratio) < 0.01:
        return f"scale={dst_w}:{dst_h}:flags=lanczos,setsar=1"
    return (f"split=2[bgsrc][fg];[bgsrc]scale={dst_w}:{dst_h}:force_original_aspect_ratio=increase,crop={dst_w}:{dst_h},"
            f"gblur=sigma=40,eq=brightness=-0.08:saturation=1.1[bg];[fg]scale={dst_w}:{dst_h}:force_original_aspect_ratio=decrease:flags=lanczos[fgs];"
            f"[bg][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1")


def export(proj: Project, platforms: list[str], video: Path | None = None) -> list[Path]:
    video = video or proj.master
    if not video.exists():
        from video_studio.util import ToolError

        raise ToolError("Nothing to export yet.", "Run `video-studio render` first.")
    info = ff.probe(video)
    v = ff.stream(info, "video")
    sw, sh = int(v["width"]), int(v["height"])
    dur = float(info["format"]["duration"])
    proj.exports.mkdir(parents=True, exist_ok=True)
    base = Path(proj.data["name"]).stem.replace(" ", "_") or "video"
    out: list[Path] = []
    for name in platforms:
        p = PLATFORMS[name]
        dw, dh = p["size"]
        dest = proj.exports / f"{base}_{name}.mp4"
        if dur > p["max"]:
            console.warn(f"{p['label']}: video is {dur:.0f}s, platform limit {p['max']}s - exported anyway, trim before upload.")
        if (sw, sh) == (dw, dh):
            shutil.copy2(video, dest)
        else:
            console.step(f"Exporting {p['label']} {dw}x{dh}")
            ff.ff(["-i", str(video), "-filter_complex", reframe_filter(sw, sh, dw, dh), "-map", "0:a?", "-c:a", "copy",
                   *ff.x264_args(proj.settings.get("quality", "standard")), "-movflags", "+faststart", str(dest)])
        out.append(dest)
        console.ok(f"{p['label']}: {dest}")
    # cover: the hook frame (big hook words over the pop-out card) when there is a hook, else 30% in
    thumb = proj.exports / f"{base}_thumbnail.jpg"
    plan = read_json(proj.build / "edit_plan.json", {}) or {}
    ff.frame_at(video, 1.1 if plan.get("hook_text") else min(dur * 0.3, 4.0), thumb)
    out.append(thumb)
    post = publish_pack(proj, plan, dur, platforms)
    if post:
        dest_post = proj.exports / f"{base}_post.txt"
        dest_post.write_text(post, encoding="utf-8")
        out.append(dest_post)
        console.ok(f"Ready-to-paste post (title, caption, hashtags): {dest_post}")
    manifest = read_json(proj.build / "manifest.json", {}) or read_json(proj.build / "edit_manifest.json", {}) or {}
    srt = Path(manifest.get("srt", "")) if manifest.get("srt") else None
    if srt and srt.exists():
        shutil.copy2(srt, proj.exports / f"{base}.srt")
        captions.build_vtt(srt, proj.exports / f"{base}.vtt")
        out += [proj.exports / f"{base}.srt", proj.exports / f"{base}.vtt"]
    xml = write_xmeml(proj, proj.exports / f"{base}_premiere_resolve.xml")
    if xml:
        out.append(xml)
    otio = write_otio(proj, proj.exports / f"{base}.otio")
    if otio:
        out.append(otio)
    proj.mark("export", "done", files=[str(p) for p in out])
    return out


CATEGORY_TAGS = {
    "business": ["business", "entrepreneur", "startup", "marketing", "growth"],
    "tech": ["tech", "ai", "technology", "tools", "productivity"],
    "education": ["learn", "education", "tips", "knowledge", "skills"],
    "motivation": ["motivation", "mindset", "success", "discipline", "goals"],
    "travel": ["travel", "travelgram", "wanderlust", "explore", "trip"],
    "lifestyle": ["lifestyle", "vlog", "dailyvlog", "life", "aesthetic"],
}


def publish_pack(proj: Project, plan: dict, duration: float, platforms: list[str]) -> str:
    """Title, caption and hashtags per platform, made locally from the edit's hook and key words."""
    from video_studio import brain

    LOAN = set(brain.LOANWORDS)
    hook = (plan.get("hook_text") or plan.get("title") or proj.data.get("name", "")).strip()
    if not hook:
        return ""
    tr = read_json(proj.build / "transcript.json", {}) or {}
    lang = tr.get("language", "")
    emph = [e for e in plan.get("emphasis") or [] if len(e) > 2]
    keys = []
    for e in emph + [s.get("text", "").lower() for s in plan.get("slams") or []]:
        r = brain.transliterate(e).lower().strip(".,!?")
        if r in LOAN and r not in keys:  # hashtags people search: real English terms only, never Hindi fillers
            keys.append(r)
    text = (" ".join(brain.transliterate(t) for u in plan.get("utterances") or [] for t in u.get("text", "").split())
            + " " + proj.data.get("brief", "")).lower()
    if len(keys) < 3:  # top up from the English terms actually spoken
        for t in text.split():
            t = t.strip(".,!?")
            if t in LOAN and t not in keys and t not in brain.EN_STOP:
                keys.append(t)
    cat = next((c for c, kw in (("business", "business startup product revenue market sales brand customer"),
                                ("tech", "ai app software tool code website automation"),
                                ("travel", "travel trip goa paris flight hotel beach mountain"),
                                ("motivation", "success mindset motivation discipline dream goal"),
                                ("education", "learn tips course exam study skill"))
                if any(k in text.split() or k in text for k in kw.split())), "lifestyle")
    tags = [*keys[:4], *CATEGORY_TAGS[cat]]
    if lang in ("hi", "ur") or "hinglish" in text:
        tags += ["hinglish", "hindi"]
    tags = list(dict.fromkeys(t for t in tags if t))[:10]
    title = hook.title()
    first = next((u.get("text") for u in plan.get("utterances") or [] if u.get("id") in (plan.get("keep") or [])), "")
    first_roman = " ".join(brain.transliterate(t) for t in first.split()[:14])
    hashtags = " ".join("#" + t for t in tags)
    lines = [f"Video: {proj.data.get('name', '')}  ({duration:.0f} s)", ""]
    if any(p in platforms for p in ("shorts", "youtube")):
        yt = f"{title} #shorts" if duration <= 60 else title
        lines += ["== YouTube ==", f"Title ({len(yt)}/100): {yt[:100]}",
                  f"Description: {first_roman}{'...' if first_roman else ''}", f"{hashtags}", ""]
    if any(p in platforms for p in ("reels", "feed45", "square")):
        lines += ["== Instagram ==", f"{title} 👇", "", f"{first_roman}{'...' if first_roman else ''}", "",
                  "Save this for later and share it with a friend who needs it.", "", hashtags, ""]
    if "tiktok" in platforms:
        lines += ["== TikTok ==", f"{title} {' '.join('#' + t for t in tags[:5])}", ""]
    lines += ["Cover image: the *_thumbnail.jpg next to this file (hook frame).",
              "Made locally from your video's hook and key words - edit freely before posting."]
    return "\n".join(lines)


def write_xmeml(proj: Project, dest: Path) -> Path | None:
    """Final Cut 7 XML (xmeml v4) of a footage edit: every kept piece as a cut on V1/A1 against your source footage.
    Imports in Adobe Premiere Pro (File > Import), DaVinci Resolve (File > Import > Timeline) and Final Cut (via XtoCC)."""
    from xml.sax.saxutils import escape

    edit = read_json(proj.build / "edit_manifest.json") or {}
    pieces = edit.get("pieces") or []
    src = Path(edit.get("source") or "")
    if not pieces or not src.exists():
        return None
    fps = int(round(float(edit.get("fps") or 30)))
    w, h = edit.get("size") or [1080, 1920]
    src_frames = int(round(ff.duration(src) * fps))
    rate = f"<rate><timebase>{fps}</timebase><ntsc>FALSE</ntsc></rate>"
    file_xml = (f'<file id="src1"><name>{escape(src.name)}</name><pathurl>{escape(src.resolve().as_uri())}</pathurl>{rate}'
                f"<duration>{src_frames}</duration><media><video/><audio/></media></file>")
    v_items, a_items, t = [], [], 0
    for i, pc in enumerate(pieces):
        a, b = int(round(pc["src_start"] * fps)), int(round(pc["src_end"] * fps))
        if b <= a:
            continue
        common = (f"<name>piece {i + 1}</name><enabled>TRUE</enabled><duration>{src_frames}</duration>{rate}"
                  f"<start>{t}</start><end>{t + b - a}</end><in>{a}</in><out>{b}</out>")
        file_ref = file_xml if i == 0 else '<file id="src1"/>'
        v_items.append(f'<clipitem id="v{i}">{common}{file_ref}</clipitem>')
        a_items.append(f'<clipitem id="a{i}">{common}<file id="src1"/><sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack></clipitem>')
        t += b - a
    name = escape(proj.data.get("name", "Video Studio edit"))
    xml = (f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4"><sequence id="seq1"><name>{name}</name>'
           f"<duration>{t}</duration>{rate}<media><video><format><samplecharacteristics>{rate}<width>{w}</width><height>{h}</height>"
           f"<pixelaspectratio>square</pixelaspectratio></samplecharacteristics></format><track>{''.join(v_items)}</track></video>"
           f"<audio><track>{''.join(a_items)}</track></audio></media></sequence></xmeml>")
    dest.write_text(xml, encoding="utf-8")
    return dest


def write_otio(proj: Project, dest: Path) -> Path | None:
    """OpenTimelineIO JSON so the edit can be opened/finished in Kdenlive, Resolve, Premiere (via adapters)."""
    m = read_json(proj.build / "manifest.json") or {}
    edit = read_json(proj.build / "edit_manifest.json") or {}
    rate = float(m.get("fps") or edit.get("fps") or 30)

    def rt(seconds: float) -> dict:
        return {"OTIO_SCHEMA": "RationalTime.1", "rate": rate, "value": round(seconds * rate)}

    def clip(name: str, path: str, start: float, dur: float) -> dict:
        return {"OTIO_SCHEMA": "Clip.2", "name": name, "metadata": {}, "enabled": True,
                "source_range": {"OTIO_SCHEMA": "TimeRange.1", "start_time": rt(start), "duration": rt(dur)},
                "media_references": {"DEFAULT_MEDIA": {"OTIO_SCHEMA": "ExternalReference.1", "target_url": Path(path).as_uri(),
                                                       "available_range": None, "metadata": {}, "name": Path(path).name}},
                "active_media_reference_key": "DEFAULT_MEDIA", "effects": [], "markers": []}

    video_items, audio_items = [], []
    if m.get("clips"):
        t_dur = m.get("transition_seconds", 0.4)
        for i, (c, d) in enumerate(zip(m["clips"], m["durations"])):
            if i:
                video_items.append({"OTIO_SCHEMA": "Transition.1", "name": m["transitions"][i], "transition_type": "SMPTE_Dissolve",
                                    "in_offset": rt(t_dur / 2), "out_offset": rt(t_dur / 2), "metadata": {}})
            video_items.append(clip(f"scene {i + 1}", c, 0, d))
        cursor = 0.0
        for vo in m.get("voice", []):
            gap = vo["start"] - cursor
            if gap > 0:
                audio_items.append({"OTIO_SCHEMA": "Gap.1", "name": "", "source_range": {"OTIO_SCHEMA": "TimeRange.1", "start_time": rt(0), "duration": rt(gap)},
                                    "effects": [], "markers": [], "metadata": {}, "enabled": True})
            vdur = ff.duration(Path(vo["path"]))
            audio_items.append(clip("voice", vo["path"], 0, vdur))
            cursor = vo["start"] + vdur
    elif edit.get("segments"):
        for i, seg in enumerate(edit["segments"]):
            video_items.append(clip(f"cut {i + 1}", edit["source"], seg["start"], seg["end"] - seg["start"]))
            audio_items.append(clip(f"cut {i + 1}", edit["source"], seg["start"], seg["end"] - seg["start"]))
    else:
        return None

    def track(name: str, kind: str, children: list) -> dict:
        return {"OTIO_SCHEMA": "Track.1", "name": name, "kind": kind, "children": children, "source_range": None,
                "effects": [], "markers": [], "metadata": {}, "enabled": True}

    timeline = {"OTIO_SCHEMA": "Timeline.1", "name": proj.data["name"], "metadata": {"generator": "Video Studio"},
                "global_start_time": None,
                "tracks": {"OTIO_SCHEMA": "Stack.1", "name": "tracks", "children": [track("V1", "Video", video_items), track("A1", "Audio", audio_items)],
                           "source_range": None, "effects": [], "markers": [], "metadata": {}, "enabled": True}}
    dest.write_text(json.dumps(timeline, indent=1), encoding="utf-8")
    return dest
