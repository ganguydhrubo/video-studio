"""Final assembly: transitions, grade, captions, audio mix, loudness, mux."""

from __future__ import annotations

import shutil
from pathlib import Path

from video_studio.engines import ff
from video_studio.util import console


def assemble_video(clips: list[Path], durations: list[float], transitions: list[str], t_dur: float, fps: int,
                   w: int, h: int, grade: str, ass: Path | None, quality: str, dest: Path, overlays: list[dict] | None = None,
                   total: float | None = None) -> Path:
    """Join scene clips with xfade transitions, grade, burn captions, encode once."""
    args: list[str] = []
    for c in clips:
        args += ["-i", str(c)]
    ov_index = len(clips)
    for ov in overlays or []:
        args += ["-loop", "1", "-t", f"{ov['end'] + 0.5:.3f}", "-i", str(ov["png"])]
    parts = []
    for i in range(len(clips)):
        parts.append(f"[{i}:v]fps={fps},scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,"
                     f"format=yuv420p,settb=AVTB[c{i}]")
    cur = "c0"
    for i in range(1, len(clips)):
        # Every transition overlaps the clips by t_dur, so clip i starts at sum(d[:i]) - i * t_dur.
        offset = sum(durations[:i]) - i * t_dur
        tr = transitions[i] if i < len(transitions) and transitions[i] != "cut" else "fade"
        parts.append(f"[{cur}][c{i}]xfade=transition={tr}:duration={t_dur:.3f}:offset={offset:.3f}[x{i}]")
        cur = f"x{i}"
    for k, ov in enumerate(overlays or []):
        idx = ov_index + k
        fade = f"fade=t=in:st={ov['start']:.2f}:d=0.25:alpha=1,fade=t=out:st={max(ov['start'], ov['end'] - 0.25):.2f}:d=0.25:alpha=1"
        parts.append(f"[{idx}:v]format=rgba,{fade}[ov{k}]")
        parts.append(f"[{cur}][ov{k}]overlay=0:0:enable='between(t,{ov['start']:.2f},{ov['end']:.2f})'[o{k}]")
        cur = f"o{k}"
    post = [grade] if grade else []
    post.append("noise=alls=5:allf=t+u")
    if ass is not None:
        post.append(f"subtitles={ass.name}" + (":fontsdir=fonts" if (ass.parent / "fonts").is_dir() else ""))
    if total:
        post.append(f"tpad=stop_mode=clone:stop_duration=1,trim=duration={total:.3f},setpts=PTS-STARTPTS")
    post.append("format=yuv420p")
    parts.append(f"[{cur}]{','.join(post)}[vout]")
    graph = ";".join(parts)
    script = dest.with_suffix(".filter.txt")
    script.write_text(graph, encoding="utf-8")
    tmp = dest.with_suffix(".part.mp4")
    from video_studio import encoders  # fastest working encoder on this PC (x264 fallback), see encoders.py

    encoders.encode([*args, "-/filter_complex", str(script), "-map", "[vout]"], quality, ["-r", str(fps), "-an", str(tmp)],
                    cwd=ass.parent if ass else None)
    tmp.replace(dest)
    return dest


def mix_audio(voice: list[tuple[Path, float]], music: Path | None, sfx: list[tuple[Path, float, float]], total: float,
              dest: Path, loudness: float, music_gain: float = 0.32, duck: bool = True) -> Path:
    """voice: [(wav, start_s)], sfx: [(wav, start_s, gain)]. Produces a loudness-normalised 48 kHz stereo wav."""
    args: list[str] = []
    parts: list[str] = []
    idx = 0
    vo_labels = []
    for path, start in voice:
        args += ["-i", str(path)]
        ms = int(max(0.0, start) * 1000)
        parts.append(f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,adelay={ms}|{ms}[v{idx}]")
        vo_labels.append(f"[v{idx}]")
        idx += 1
    fx_labels = []
    for path, start, gain in sfx:
        args += ["-i", str(path)]
        ms = int(max(0.0, start) * 1000)
        parts.append(f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,volume={gain:.2f},adelay={ms}|{ms}[f{idx}]")
        fx_labels.append(f"[f{idx}]")
        idx += 1
    music_label = None
    if music:
        args += ["-stream_loop", "-1", "-i", str(music)]
        parts.append(f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{total:.3f},"
                     f"afade=t=out:st={max(0.0, total - 2.0):.3f}:d=2,volume={music_gain:.2f}[mus]")
        music_label = "[mus]"
        idx += 1
    if vo_labels:
        parts.append(f"{''.join(vo_labels)}amix=inputs={len(vo_labels)}:normalize=0:duration=longest[vo]")
    final = []
    if vo_labels and music_label and duck:
        parts.append("[vo]asplit=2[vo1][vokey]")
        parts.append(f"{music_label}[vokey]sidechaincompress=threshold=0.04:ratio=8:attack=15:release=350:makeup=1[duck]")
        final += ["[vo1]", "[duck]"]
    else:
        if vo_labels:
            final.append("[vo]")
        if music_label:
            final.append(music_label)
    final += fx_labels
    if not final:
        args += ["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo"]
        final.append(f"[{idx}:a]")
    parts.append(f"{''.join(final)}amix=inputs={len(final)}:normalize=0:duration=longest,"
                 f"apad,atrim=0:{total:.3f},alimiter=limit=0.95[mix]")
    raw = dest.with_suffix(".raw.wav")
    script = dest.with_suffix(".afilter.txt")
    script.write_text(";".join(parts), encoding="utf-8")
    ff.ff([*args, "-/filter_complex", str(script), "-map", "[mix]", "-ar", "48000", "-c:a", "pcm_s24le", str(raw)])
    measured = ff.loudness_measure(raw, loudness)
    ff.ff(["-i", str(raw), "-af", ff.loudnorm_filter(measured, loudness) + ",alimiter=limit=0.79:attack=1:release=60:level=false",
           "-ar", "48000", "-c:a", "pcm_s24le", str(dest)])
    raw.unlink(missing_ok=True)
    return dest


def mux(video: Path, audio: Path, dest: Path, quality: str = "standard") -> Path:
    tmp = dest.with_suffix(".part.mp4")
    ff.ff(["-i", str(video), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac",
           "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(tmp)])
    tmp.replace(dest)
    return dest


def copy_if_changed(src: Path, dest: Path) -> None:
    if not dest.exists() or dest.stat().st_size != src.stat().st_size:
        shutil.copy2(src, dest)
    console.debug(f"copied {src} -> {dest}")
