"""Photos to video: pan and zoom on each picture, transitions, colour look, titles, voice and music. All local.

Each picture is placed on a softly blurred copy of itself (so tall and wide photos both fill the frame), then a
slow camera move (zoom in, zoom out or a pan, never the same twice in a row) runs over it. Pictures are joined
with the chosen transition family, graded with a generated colour look, titled, scored with a built-in music bed
cut to its beat, and optionally narrated with the offline voice.
"""
from __future__ import annotations

import math
import random
from pathlib import Path

from video_studio import library
from video_studio.engines import captions, compose, ff, music
from video_studio.util import ToolError, console, write_json

IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif", ".heic", ".heif", ".avif"}
SIZES = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080), "4:5": (1080, 1350)}
MOVES = ["zoom_in", "pan_right", "zoom_out", "pan_up", "zoom_in_left", "pan_left", "zoom_out_right", "pan_down"]


def size_for(aspect: str, short_side: int | None = None) -> tuple[int, int]:
    w, h = SIZES.get(aspect, SIZES["9:16"])
    if short_side and short_side != min(w, h):
        k = short_side / min(w, h)
        w, h = int(round(w * k / 2) * 2), int(round(h * k / 2) * 2)
    return w, h


def collect(paths: list[str | Path]) -> list[Path]:
    """Images from files and folders, in the given order (folders sorted by name)."""
    out: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            out += sorted((f for f in p.iterdir() if f.suffix.lower() in IMAGE_TYPES), key=lambda f: f.name.lower())
        elif p.is_file() and p.suffix.lower() in IMAGE_TYPES:
            out.append(p)
    return out


def move_expr(move: str, frames: int, strength: float = 0.14) -> tuple[str, str, str]:
    """zoompan z, x, y expressions for one camera move over `frames` frames (smooth ease in-out)."""
    t = f"(0.5-0.5*cos(PI*on/{max(1, frames - 1)}))"  # eased 0..1
    zin, zout = f"(1+{strength}*{t})", f"(1+{strength}-{strength}*{t})"
    cx, cy = "(iw/2-iw/zoom/2)", "(ih/2-ih/zoom/2)"
    span_x, span_y = "(iw-iw/zoom)", "(ih-ih/zoom)"
    table = {
        "zoom_in": (zin, cx, cy), "zoom_out": (zout, cx, cy),
        "zoom_in_left": (zin, f"{span_x}*(1-{t})*0.5", cy), "zoom_out_right": (zout, f"{span_x}*(0.5+0.5*{t})", cy),
        "pan_right": (f"{1 + strength * 0.8}", f"{span_x}*{t}", cy), "pan_left": (f"{1 + strength * 0.8}", f"{span_x}*(1-{t})", cy),
        "pan_up": (f"{1 + strength * 0.8}", cx, f"{span_y}*(1-{t})"), "pan_down": (f"{1 + strength * 0.8}", cx, f"{span_y}*{t}"),
    }
    return table.get(move, table["zoom_in"])


def still_filter(w: int, h: int, scale: int = 2) -> str:
    """Picture centred on a blurred, darkened copy of itself, rendered at `scale`x for smooth camera moves."""
    W, H = w * scale, h * scale
    return (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=40:2,eq=brightness=-0.08:saturation=0.9[bg];"
            f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[v]")


def clip_filter(w: int, h: int, fps: int, frames: int, move: str) -> str:
    z, x, y = move_expr(move, frames)
    return f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps={fps},setsar=1,format=yuv420p"


def plan_durations(n: int, seconds: float | None, bpm: float, total: float | None, t_dur: float) -> list[float]:
    """Seconds per picture. Without a choice, snap to whole bars of the music (4 beats) so cuts land on the beat."""
    if total and n:
        seconds = (total + t_dur * (n - 1)) / n
    if not seconds:
        bar = 4 * 60.0 / bpm
        seconds = bar * max(1, round(3.0 / bar))
    return [max(1.2, float(seconds))] * n


def run(images: list[str | Path], out_root: Path, *, aspect: str = "9:16", short_side: int | None = None, fps: int = 30,
        seconds: float | None = None, total: float | None = None, transition: str = "smooth", grade: str | None = "natural",
        mood: str = "uplifting", music_file: str | None = None, no_music: bool = False, titles: list[str] | None = None,
        narrate: bool = False, voice: str = "auto", caption_style: str = "bold_pop", look: str = "midnight",
        quality: str = "standard", seed: int = 7, loudness: float = -14.0) -> dict:
    pics = collect(images)
    if not pics:
        raise ToolError("No pictures found.", "Give image files (JPG, PNG, WEBP, HEIC) or a folder of them.")
    if len(pics) > 400:
        raise ToolError(f"{len(pics)} pictures is too many for one video.", "Split them into videos of 400 or fewer.")
    out_root = Path(out_root)
    build, output = out_root / "build", out_root / "output"
    (build / "photos").mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    w, h = size_for(aspect, short_side)
    t_dur = library.transition_seconds(transition)
    bpm = music.bpm_for(mood, seed) if not no_music else 100.0
    titles = [t.strip() for t in (titles or [])]
    narration: dict[str, dict] = {}
    if narrate and any(titles):
        from video_studio.engines import tts
        lines = [{"id": f"p{i:03d}", "text": t} for i, t in enumerate(titles[:len(pics)]) if t]
        console.step(f"Voiceover for {len(lines)} picture(s)")
        narration = tts.synthesize(lines, build / "voice", voice, 0, 1.0)
    durs = plan_durations(len(pics), seconds, bpm, total, t_dur)
    for i in range(len(pics)):  # a picture with narration stays up until its line is spoken
        said = narration.get(f"p{i:03d}")
        if said:
            durs[i] = max(durs[i], said["duration"] + 0.6 + t_dur)
    rng = random.Random(seed)
    moves, last = [], ""
    for _ in pics:
        move = rng.choice([m for m in MOVES if m != last])
        moves.append(move)
        last = move
    clips = []
    console.step(f"Animating {len(pics)} picture(s) at {w}x{h}")
    for i, (pic, dur, move) in enumerate(zip(pics, durs, moves)):
        still = build / "photos" / f"still_{i:03d}.png"
        clip = build / "photos" / f"clip_{i:03d}.mp4"
        try:
            ff.ff(["-y", "-i", str(pic), "-frames:v", "1", "-filter_complex", still_filter(w, h), "-map", "[v]", str(still)])
        except ToolError as exc:
            raise ToolError(f"Could not read picture {pic.name}.", "Convert it to JPG or PNG and try again.") from exc
        frames = int(math.ceil(dur * fps))
        ff.ff(["-y", "-loop", "1", "-i", str(still), "-vf", clip_filter(w, h, fps, frames, move), "-frames:v", str(frames),
               *ff.x264_args(quality, intermediate=True), str(clip)])
        clips.append(clip)
        console.info(f"  picture {i + 1}/{len(pics)}: {pic.name} ({move.replace('_', ' ')}, {dur:.1f}s)")
    fam = library.transitions(transition)
    trans = [fam[i % len(fam)] for i in range(len(clips))]
    total_len = sum(durs) - t_dur * (len(clips) - 1)
    starts = [sum(durs[:i]) - i * t_dur for i in range(len(clips))]
    ass = None
    words = []
    for i, start in enumerate(starts):
        said = narration.get(f"p{i:03d}")
        if said:
            words += [{"text": wd["text"], "start": round(start + 0.3 + wd["start"], 3), "end": round(start + 0.3 + wd["end"], 3)}
                      for wd in said["words"]]
        elif i < len(titles) and titles[i]:
            ws = titles[i].split()
            span = max(1.0, durs[i] - t_dur - 0.6)
            words += [{"text": wd, "start": round(start + 0.3 + span * k / len(ws), 3),
                       "end": round(start + 0.3 + span * (k + 1) / len(ws), 3)} for k, wd in enumerate(ws)]
    if words and caption_style != "none":
        ass = captions.build_ass(words, w, h, caption_style, look, build / "captions.ass", y_ratio=0.80 if h > w else 0.86)
    video_only = build / "video_master.mp4"
    console.step("Joining pictures: transitions, colour, titles")
    compose.assemble_video(clips, durs, trans, t_dur, fps, w, h, library.grade_filter(grade), ass, quality, video_only,
                           total=total_len)
    voice_parts = [(Path(narration[f"p{i:03d}"]["path"]), starts[i] + 0.3) for i in range(len(clips)) if f"p{i:03d}" in narration]
    music_path = None
    if music_file:
        music_path = Path(music_file)
    elif not no_music:
        console.step(f"Music: {mood} bed at about {bpm:.0f} beats per minute")
        music_path = music.write_wav(build / "music.wav", music.bed(mood, total_len + 1.0, seed)[0])
    master = output / "master.mp4"
    if music_path or voice_parts:
        audio = compose.mix_audio(voice_parts, music_path, [], total_len, build / "audio_master.wav", loudness,
                                  music_gain=0.32 if voice_parts else 0.9, duck=bool(voice_parts))
        compose.mux(video_only, audio, master, quality)
    else:
        video_only.replace(master)
    result = {"output": str(master), "pictures": len(pics), "seconds": round(total_len, 2), "size": [w, h], "fps": fps,
              "transition": transition, "grade": grade, "music": "file" if music_file else ("none" if no_music else mood),
              "narrated": len(voice_parts), "moves": moves}
    write_json(build / "photos_manifest.json", result)
    console.ok(f"Photo video ready: {master} ({total_len:.1f}s, {len(pics)} pictures)")
    return result
