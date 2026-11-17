"""Local background removal: cut the person out of a video and put something else behind them.

Two steps, so the app never imports heavy ML itself:
  1. helpers/ml_helper.py `matte` (system Python, MediaPipe selfie-multiclass, rembg fallback) writes a greyscale
     mask video (255 = person) the same size and frame rate as the input. It is cached, so trying another
     background later is an FFmpeg-only job.
  2. FFmpeg puts the source through `alphamerge` with the mask and overlays it on the chosen background.

Backgrounds: blur, colour, image, video (another clip, looped or trimmed to length), transparent
(.webm VP9 with alpha, or .mov ProRes 4444).

Quality: "fast" (default) is the MediaPipe matte above. "best" (optional, 1.9.0) uses BiRefNet lite on keyframes with
the edge carried between them (video_studio/matting.py + helpers/matte_helper.py); its model is downloaded only
when the person allows it.
"""

from __future__ import annotations

import hashlib
import re
import time
import urllib.request
from pathlib import Path

from video_studio import detect, resources
from video_studio.engines import ff, ml
from video_studio.util import ToolError, cache_dir, read_json, write_json

BACKGROUNDS = ("blur", "colour", "image", "video", "transparent")
MODEL_NAME = "selfie_multiclass.tflite"
MAX_SIDE = 1280  # the matte is worked out at most this big; the mask is scaled back to full size
STRIDE = 2  # the model runs on every 2nd frame (about twice as fast); the edge filter still follows every frame
MASK_VERSION = 1  # bump when the matte recipe changes so old cached masks are not reused


# ---------------------------------------------------------------- model / checks

def _model_entry() -> tuple[str, str]:
    from video_studio import setup  # same download URL and folder that `video-studio setup` uses

    url, sub, _size, _desc = setup.MODELS[MODEL_NAME]
    return url, sub


def model_path() -> Path:
    return ml.models_dir() / _model_entry()[1] / MODEL_NAME


def model_url() -> str:
    return _model_entry()[0]


def ensure_model(download: bool = False) -> Path:
    """The cut-out model on disk; with download=True it is fetched the same way setup fetches it (16 MB)."""
    path = model_path()
    if path.exists():
        return path
    if download:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        try:
            urllib.request.urlretrieve(model_url(), tmp)
            tmp.replace(path)
            return path
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise ToolError(f"Could not download the cut-out model ({exc}).",
                            f"Check the internet connection and try again, or save {model_url()} as {path}.") from exc
    raise ToolError("The cut-out model is not on this PC, so the background cannot be removed.",
                    f"Run `video-studio setup` to download it (16 MB), or save {model_url()} as {path}.")


def _python() -> Path:
    py = detect.system_python()
    if not py:
        raise ToolError("Background removal needs Python on this PC, and none was found.",
                        "Install Python 3.10 or newer from python.org, then run `video-studio setup` "
                        "to add MediaPipe and OpenCV.")
    return py


def available() -> dict:
    """Can cutout run on this PC? {'ok', 'engine', 'reasons', ...} - reasons are plain-English fixes."""
    from video_studio import setup

    reasons: list[str] = []
    py = detect.system_python()
    ffmpeg = detect.ffmpeg_path()
    model = model_path()
    mods = setup._modules(py, ("cv2", "numpy", "mediapipe", "rembg")) if py else {}
    if not py:
        reasons.append("No Python found. Install Python 3.10 or newer from python.org, then run `video-studio setup`.")
    elif not (mods.get("cv2") and mods.get("numpy")):
        reasons.append("OpenCV is missing. Run `video-studio setup` (or `py -m pip install --user opencv-python`).")
    if not ffmpeg:
        reasons.append("FFmpeg not found. Run `video-studio install ffmpeg`.")
    if not model.exists():
        reasons.append(f"The cut-out model is missing. Run `video-studio setup` to download it ({model_url()}).")
    engine = ""
    if mods.get("mediapipe"):
        engine = "mediapipe-selfie-multiclass"
    elif mods.get("rembg"):
        engine = "rembg (slower; install mediapipe for speed)"
    elif py:
        reasons.append("Neither MediaPipe nor rembg is installed. Run `video-studio setup` "
                       "(or `py -m pip install --user mediapipe`).")
    return {"ok": not reasons, "engine": engine, "reasons": reasons, "python": str(py or ""),
            "ffmpeg": str(ffmpeg or ""), "model": str(model), "model_present": model.exists(), "model_url": model_url(),
            "backgrounds": list(BACKGROUNDS)}


# ---------------------------------------------------------------- pure FFmpeg graph builders

def parse_colour(colour: str) -> str:
    """'#00B140', '00b140', '#0b4', '0x00B140' or a plain colour name -> an FFmpeg colour ('0x00B140' / 'white')."""
    text = (colour or "").strip()
    hexpart = re.sub(r"^(#|0x)", "", text, flags=re.I)
    if re.fullmatch(r"[0-9a-fA-F]{3}", hexpart):
        hexpart = "".join(c * 2 for c in hexpart)
    if re.fullmatch(r"[0-9a-fA-F]{6}", hexpart):
        return "0x" + hexpart.upper()
    if re.fullmatch(r"[A-Za-z]{3,20}", text):
        return text.lower()
    raise ToolError(f"'{colour}' is not a colour I understand.", "Use a hex colour like #00B140, or a name like white.")


def _even(n: float) -> int:
    return max(2, int(n) // 2 * 2)


def build_graph(background: str, width: int, height: int, fps: str, *, colour: str = "#00B140",
                alpha_format: str = "yuva420p") -> str:
    """The -filter_complex graph. Inputs: 0 = source video, 1 = mask video, 2 = background image/video.
    Output label: [v]."""
    if background not in BACKGROUNDS:
        raise ToolError(f"Unknown background '{background}'.", "Choose one of: " + ", ".join(BACKGROUNDS) + ".")
    w, h = int(width), int(height)
    cover = (f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=bicubic,crop={w}:{h},setsar=1,"
             f"fps={fps},format=yuv420p")
    mask = f"[1:v]setpts=PTS-STARTPTS,scale={w}:{h},format=gray[m]"
    person = "[fg][m]alphamerge[person]"
    over = "[bg][person]overlay=0:0:shortest=1:format=auto,format=yuv420p[v]"
    if background == "blur":
        return ";".join([
            "[0:v]setpts=PTS-STARTPTS,split=2[src][back]",
            "[src]format=yuva420p[fg]",
            mask,
            person,
            # shrink, blur, grow back: a very heavy blur for little CPU
            f"[back]scale={_even(w / 8)}:{_even(h / 8)}:flags=area,gblur=sigma=6,"
            f"scale={w}:{h}:flags=bicubic,setsar=1,format=yuv420p[bg]",
            over,
        ])
    fg = "[0:v]setpts=PTS-STARTPTS,format=yuva420p[fg]"
    if background == "transparent":
        return ";".join([fg, mask, person, f"[person]format={alpha_format}[v]"])
    if background == "colour":
        bg = f"color=c={parse_colour(colour)}:s={w}x{h}:r={fps},format=yuv420p[bg]"
    elif background == "image":
        bg = f"[2:v]{cover}[bg]"
    else:  # video
        bg = f"[2:v]setpts=PTS-STARTPTS,{cover}[bg]"
    return ";".join([fg, mask, person, bg, over])


def output_plan(background: str, out: Path) -> tuple[Path, list[str], list[str], str]:
    """(final output path, video codec args, audio codec args, alpha pixel format) for this background + suffix."""
    suffix = out.suffix.lower()
    if background == "transparent":
        if suffix == ".mov":
            return (out, ["-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", "-vendor", "apl0"],
                    ["-c:a", "pcm_s16le"], "yuva444p10le")
        # anything else cannot hold alpha (mp4, mkv with x264...): VP9 WebM can
        return (out.with_suffix(".webm"),
                ["-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "30", "-row-mt", "1",
                 "-deadline", "good", "-cpu-used", "4", "-auto-alt-ref", "0"],
                ["-c:a", "libopus", "-b:a", "128k"], "yuva420p")
    if suffix == ".webm":
        return (out, ["-c:v", "libvpx-vp9", "-pix_fmt", "yuv420p", "-b:v", "0", "-crf", "30", "-row-mt", "1",
                      "-deadline", "good", "-cpu-used", "4"], ["-c:a", "libopus", "-b:a", "128k"], "yuva420p")
    if suffix not in (".mp4", ".mov", ".mkv", ".m4v"):
        out = out.with_suffix(".mp4")
    extra = ["-movflags", "+faststart"] if out.suffix.lower() in (".mp4", ".mov", ".m4v") else []
    return out, [*ff.x264_args("standard"), *extra], ["-c:a", "aac", "-b:a", "192k"], "yuva420p"


def background_inputs(background: str, media: Path | None, fps: str) -> list[str]:
    if background == "image":
        return ["-loop", "1", "-framerate", fps, "-i", str(media)]
    if background == "video":
        return ["-stream_loop", "-1", "-i", str(media)]
    return []


# ---------------------------------------------------------------- source facts and the mask

def _video_facts(video: Path) -> dict:
    info = ff.probe(video)
    vs = ff.stream(info, "video")
    if not vs:
        raise ToolError(f"{video.name} has no video stream.", "Pick a video file.")
    w, h = int(vs.get("width") or 0), int(vs.get("height") or 0)
    rot = 0
    for sd in vs.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(float(sd["rotation"]))
    rot = rot or int(float((vs.get("tags") or {}).get("rotate", 0) or 0))
    if abs(rot) % 180 == 90:  # FFmpeg and OpenCV both turn the picture upright
        w, h = h, w
    rate = vs.get("avg_frame_rate") or vs.get("r_frame_rate") or "30/1"
    if ff.fps_of({"avg_frame_rate": rate}) <= 0:
        rate = vs.get("r_frame_rate") or "30/1"
    try:
        duration = float(info.get("format", {}).get("duration") or vs.get("duration") or 0)
    except ValueError:
        duration = 0.0
    return {"width": w, "height": h, "fps": rate, "fps_value": ff.fps_of({"avg_frame_rate": rate}) or 30.0,
            "duration": duration, "has_audio": bool(ff.stream(info, "audio"))}


def _mask_key(video: Path, smooth: float, quality: str = "fast") -> str:
    st = video.stat()
    raw = f"{video.resolve()}|{st.st_size}|{st.st_mtime_ns}|{smooth:.3f}|{MAX_SIDE}|{STRIDE}|{MASK_VERSION}"
    if quality != "fast":  # fast keys stay exactly as before, so masks cached by 1.8 are still used
        from video_studio import matting

        raw += "|" + matting.cache_part()
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def _best_matte(video: Path, part: Path, facts: dict, smooth: float, ffmpeg: str) -> dict:
    from video_studio import matting

    model = matting.ensure(allow_download=False)
    mp_model = model_path()
    resources.ensure_memory(1.2, "best-quality background removal")
    frames_guess = max(1.0, facts["duration"] * facts["fps_value"])
    est = matting.estimate(facts["duration"], facts["fps_value"])
    timeout = max(1800.0, (est.get("seconds") or frames_guess * 10.0) * 3.0)
    data = matting.call("best", timeout=timeout, video=video, dest=part, model=model,
                        mp_model=mp_model if mp_model.exists() else "", ffmpeg=ffmpeg, fps=facts["fps"],
                        smooth=min(0.5, smooth * 0.5), max_side=matting.BEST_MAX_SIDE,
                        keys_per_second=matting.KEYS_PER_SECOND, threads=matting.default_threads())
    matting.record_speed("best", int(data.get("frames") or 0), float(data.get("seconds") or 0),
                         key_seconds=float(data.get("key_seconds") or 0) or None,
                         keyframes=int(data.get("keyframes") or 0))
    return data


def make_mask(video: Path, smooth: float = 0.6, facts: dict | None = None, quality: str = "fast") -> dict:
    """Run the helper (or reuse a cached mask). Returns the helper's JSON plus 'mask' and 'cached'."""
    _python()
    if quality == "fast":
        model = ensure_model()
    ffmpeg = ff.ffmpeg()
    facts = facts or _video_facts(video)
    folder = cache_dir("cutout")
    key = _mask_key(video, smooth, quality)
    mask, meta = folder / f"{key}.mkv", folder / f"{key}.json"
    cached = read_json(meta, None)
    if cached and mask.exists() and mask.stat().st_size > 0:
        return {**cached, "mask": str(mask), "cached": True}
    part = folder / f"{key}.part.mkv"
    part.unlink(missing_ok=True)
    try:
        if quality == "best":
            data = _best_matte(video, part, facts, smooth, ffmpeg)
        else:
            resources.ensure_memory(0.6, "background removal")
            frames_guess = max(1.0, facts["duration"] * facts["fps_value"])
            data = ml.call("matte", timeout=max(900.0, frames_guess / 2.0), video=video, dest=part, model=model,
                           ffmpeg=ffmpeg, fps=facts["fps"], smooth=smooth, max_side=MAX_SIDE, stride=STRIDE)
            from video_studio import matting

            matting.record_speed("fast", int(data.get("frames") or 0), float(data.get("seconds") or 0))
        if not part.exists() or part.stat().st_size == 0:
            raise ToolError("The cut-out helper finished but wrote no mask video.",
                            "Run `video-studio doctor` and try again.")
        part.replace(mask)
    finally:
        part.unlink(missing_ok=True)
    data = {k: v for k, v in data.items() if k not in ("ok", "dest")}
    write_json(meta, data)
    return {**data, "mask": str(mask), "cached": False}


# ---------------------------------------------------------------- the job

def run(video: Path, out: Path, background: str = "blur", *, image: Path | None = None, colour: str = "#00B140",
        smooth: float = 0.6, keep_audio: bool = True, quality: str = "fast", allow_download: bool = False,
        progress=None) -> dict:
    """Remove the background of `video` and write `out`.

    background: blur | colour | image | video | transparent. For 'image' and 'video', `image` is the background
    file (a still, or another clip that is looped or trimmed to length). 'transparent' writes .webm (VP9 + alpha)
    unless `out` ends in .mov (ProRes 4444). quality: fast (MediaPipe) | best (BiRefNet lite keyframes; its 214 MB
    model is fetched only with allow_download=True). Returns output path, seconds, engine, frames and speed."""
    started = time.time()
    video, out = Path(video), Path(out)
    if background not in BACKGROUNDS:
        raise ToolError(f"Unknown background '{background}'.", "Choose one of: " + ", ".join(BACKGROUNDS) + ".")
    if not video.is_file():
        raise ToolError(f"Video not found: {video}", "Check the file path.")
    if background in ("image", "video"):
        if image is None or not Path(image).is_file():
            raise ToolError(f"A '{background}' background needs a {background} file, and none was found"
                            + (f" at {image}." if image else "."),
                            f"Pass the {background} to put behind you (image=...).")
        image = Path(image)
    if not 0.0 <= float(smooth) < 1.0:
        raise ToolError(f"smooth must be from 0 to 0.95 (got {smooth}).", "0.6 suits talking heads; 0 turns it off.")
    if background == "colour":
        parse_colour(colour)  # fail before the long matte step
    from video_studio import matting

    if quality not in matting.QUALITIES:
        raise ToolError(f"Unknown cut-out quality '{quality}'.", "Choose fast or best.")
    downloaded = False
    if quality == "best" and not matting.model_present():
        matting.ensure(allow_download=allow_download, progress=progress)  # refuses without permission
        downloaded = True

    facts = _video_facts(video)
    matte = make_mask(video, float(smooth), facts, quality)
    final, vcodec, acodec, alpha_fmt = output_plan(background, out)
    graph = build_graph(background, facts["width"], facts["height"], facts["fps"], colour=colour,
                        alpha_format=alpha_fmt)
    audio = ["-map", "0:a:0?", *acodec] if keep_audio and facts["has_audio"] else ["-an"]
    final.parent.mkdir(parents=True, exist_ok=True)
    part = final.with_name(final.stem + ".part" + final.suffix)
    t0 = time.time()
    try:
        ff.ff(["-i", str(video), "-i", matte["mask"], *background_inputs(background, image, facts["fps"]),
               "-filter_complex", graph, "-map", "[v]", *audio, *vcodec,
               *(["-t", f"{facts['duration']:.3f}"] if facts["duration"] else []), str(part)],
              timeout=max(900.0, facts["duration"] * 20))
        part.replace(final)
    finally:
        part.unlink(missing_ok=True)
    compose = time.time() - t0
    seconds = time.time() - started
    frames = int(matte.get("frames") or round(facts["duration"] * facts["fps_value"]))
    return {"output": str(final), "background": background, "seconds": round(seconds, 2),
            "mask_seconds": 0.0 if matte["cached"] else float(matte.get("seconds") or 0.0),
            "compose_seconds": round(compose, 2), "engine": matte.get("engine", ""),
            "fallback": matte.get("fallback", ""), "frames": frames, "fps": round(facts["fps_value"], 3),
            "speed_fps": round(frames / seconds, 2) if seconds else 0.0, "mask_cached": matte["cached"],
            "mask": matte["mask"], "width": facts["width"], "height": facts["height"],
            "audio": bool(keep_audio and facts["has_audio"]), "quality": quality,
            "keyframes": int(matte.get("keyframes") or 0), "key_seconds": float(matte.get("key_seconds") or 0.0),
            "model_downloaded": downloaded}
