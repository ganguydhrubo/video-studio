"""Video to video, all local: give an existing video a new look, format, speed or background.

One pass of FFmpeg (two when steadying): colour look (generated LUT), new frame shape with a blurred fill or a
crop, steadier camera, speed change with pitch-correct audio, smooth slow motion, clean loud-enough sound, an
optional music bed, and an optional background cut-out (see cutout.py).
"""
from __future__ import annotations

from pathlib import Path

from video_studio import library
from video_studio.engines import compose, ff, music
from video_studio.photos import size_for
from video_studio.util import ToolError, console, write_json


def _atempo(speed: float) -> str:
    """atempo only takes 0.5..2 per stage; chain stages for bigger changes."""
    parts, s = [], speed
    while s > 2.0:
        parts.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        parts.append("atempo=0.5")
        s /= 0.5
    parts.append(f"atempo={s:.4f}")
    return ",".join(parts)


def video_filter(*, src_w: int, src_h: int, w: int | None, h: int | None, fit: str = "fill", grade: str | None = None,
                 speed: float = 1.0, smooth_slow: bool = False, fps: int = 30, stab: Path | None = None,
                 denoise: bool = False, sharpen: bool = False) -> str:
    steps = []
    if stab:
        steps.append(f"vidstabtransform=input='{ff.filter_escape(stab)}':smoothing=20:zoom=1:optzoom=1:interpol=bicubic")
    if denoise:
        steps.append("hqdn3d=2:1.5:3:3")
    if speed != 1.0:
        steps.append(f"setpts=PTS/{speed:.4f}")
    if smooth_slow and speed < 1.0:
        steps.append(f"minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:vsbmc=1")
    else:
        steps.append(f"fps={fps}")
    pre = ",".join(steps)
    lut = library.grade_filter(grade)
    post = ",".join(x for x in (lut, "unsharp=5:5:0.6" if sharpen else "", "format=yuv420p") if x)
    if not w or not h or (abs(w / h - src_w / src_h) < 0.01):
        size = f"scale={w}:{h}," if w and h else ""
        return f"[0:v]{pre},{size}setsar=1,{post}[v]"
    if fit == "crop":
        return f"[0:v]{pre},scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,{post}[v]"
    return (f"[0:v]{pre},split[a][b];[a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=30:2,"
            f"eq=brightness=-0.08[bg];[b]scale={w}:{h}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,{post}[v]")


def audio_filter(speed: float = 1.0, loudness: float = -14.0, clean: bool = True) -> str:
    steps = [_atempo(speed)] if speed != 1.0 else []
    if clean:
        steps += ["highpass=f=70", "afftdn=nr=10:nf=-40"]
    steps.append(f"loudnorm=I={loudness}:TP=-1.5:LRA=11")
    return ",".join(steps)


def run(video: str | Path, out_root: Path, *, aspect: str | None = None, short_side: int | None = None, fit: str = "fill",
        grade: str | None = None, speed: float = 1.0, smooth_slow: bool = False, steady: bool = False, denoise: bool = False,
        sharpen: bool = False, fps: int | None = None, mood: str | None = None, music_file: str | None = None,
        background: str | None = None, background_image: str | None = None, quality: str = "standard",
        loudness: float = -14.0, seed: int = 7) -> dict:
    src = Path(video)
    if not src.is_file():
        raise ToolError(f"Video not found: {src}")
    if not 0.25 <= speed <= 4.0:
        raise ToolError("Speed must be between 0.25 (four times slower) and 4 (four times faster).")
    out_root = Path(out_root)
    build, output = out_root / "build", out_root / "output"
    build.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    info = ff.probe(src)
    vs = ff.stream(info, "video")
    if not vs:
        raise ToolError("That file has no video picture.")
    has_audio = bool(ff.stream(info, "audio"))
    src_w, src_h = int(vs["width"]), int(vs["height"])
    out_fps = int(fps or min(60, round(ff.fps_of(vs)) or 30))
    w, h = size_for(aspect, short_side) if aspect else (None, None)
    if not aspect and short_side:
        k = short_side / min(src_w, src_h)
        w, h = int(round(src_w * k / 2) * 2), int(round(src_h * k / 2) * 2)
    work = src
    if background:
        from video_studio import cutout
        console.step(f"Cutting out the person (background: {background})")
        cut = build / ("cutout.mov" if background == "transparent" else "cutout.mp4")
        cutout.run(src, cut, background, image=Path(background_image) if background_image else None)
        work = cut
    stab = None
    if steady:
        console.step("Steadying: measuring camera shake")
        stab = build / "shake.trf"
        ff.ff(["-y", "-i", str(work), "-vf", f"vidstabdetect=shakiness=6:accuracy=12:result='{ff.filter_escape(stab)}'", "-f", "null", "-"])
    graph = video_filter(src_w=src_w, src_h=src_h, w=w, h=h, fit=fit, grade=grade, speed=speed, smooth_slow=smooth_slow,
                         fps=out_fps, stab=stab, denoise=denoise, sharpen=sharpen)
    duration = ff.duration(src) / speed
    video_only = build / "restyled_video.mp4"
    console.step("Restyling: " + ", ".join(x for x in (
        f"look {library.GRADES[grade]['name']}" if grade in library.GRADES else "", f"{aspect} frame" if aspect else "",
        f"speed x{speed:g}" if speed != 1 else "", "smooth slow motion" if smooth_slow and speed < 1 else "",
        "steadier" if steady else "") if x) or "Restyling")
    ff.ff(["-y", "-i", str(work), "-filter_complex", graph, "-map", "[v]", *ff.x264_args(quality), "-an", str(video_only)])
    master = output / "master.mp4"
    voice = None
    if has_audio:
        voice = build / "voice.wav"
        ff.ff(["-y", "-i", str(src), "-vn", "-af", audio_filter(speed, loudness), "-ar", "48000", "-ac", "2", str(voice)])
    music_path = Path(music_file) if music_file else None
    if not music_path and mood:
        music_path = music.write_wav(build / "music.wav", music.bed(mood, duration + 1.0, seed)[0])
    if voice or music_path:
        audio = compose.mix_audio([(voice, 0.0)] if voice else [], music_path, [], duration, build / "audio_master.wav", loudness,
                                  music_gain=0.28 if voice else 0.9, duck=bool(voice))
        compose.mux(video_only, audio, master, quality)
    else:
        video_only.replace(master)
    result = {"output": str(master), "seconds": round(duration, 2), "size": [w or src_w, h or src_h], "fps": out_fps,
              "grade": grade, "speed": speed, "steady": steady, "background": background, "music": bool(music_path)}
    write_json(build / "restyle_manifest.json", result)
    console.ok(f"Restyled video ready: {master}")
    return result
