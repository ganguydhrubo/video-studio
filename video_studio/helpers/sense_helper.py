"""Video sense: look at the footage itself (runs in the system Python with OpenCV, MediaPipe, librosa).

Per sampled frame (~6 fps at 320 px): sharpness, exposure, colourfulness, motion (optical flow), camera-vs-subject motion,
person area and position (MediaPipe selfie matte), shot cuts (colour-histogram jumps). Audio: speech/music energy, beats.
Output: shots with quality scores, footage type, and a timeline the editor uses to choose cuts, effects and style.
Usage: python sense_helper.py <video> <out.json> [model.tflite] [ffmpeg] [speech_ratio] [workers]
With workers > 1 the frames are analysed in time ranges by parallel processes (MediaPipe uses one core each) and the
audio in one more process at the same time; the result is the same as one process.
"""

from __future__ import annotations

import json
import subprocess
import sys

import cv2
import numpy as np

SAMPLE_FPS = 6
SW = 320


def frames(path, ffmpeg, first=0):
    """Samples from sample number `first` on (a seek half a millisecond early gives exactly the same samples)."""
    probe = cv2.VideoCapture(path)
    w, h = probe.get(cv2.CAP_PROP_FRAME_WIDTH), probe.get(cv2.CAP_PROP_FRAME_HEIGHT)
    probe.release()
    sh = int(round(SW * h / max(1, w) / 2) * 2) or 180
    seek = ["-ss", f"{max(0.0, first / SAMPLE_FPS - 0.0005):.6f}"] if first else []
    proc = subprocess.Popen([ffmpeg, "-v", "error", *seek, "-i", path, "-vf", f"fps={SAMPLE_FPS},scale={SW}:{sh}", "-f", "rawvideo",
                             "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE,
                            creationflags=0x08000000 if sys.platform == "win32" else 0)
    n = SW * sh * 3
    while True:
        buf = proc.stdout.read(n)
        if len(buf) < n:
            break
        yield np.frombuffer(buf, np.uint8).reshape(sh, SW, 3)
    proc.stdout.close()
    proc.wait()


_RADIAL = {}


def radial(hh, ww):
    """Offsets from the frame centre (same numbers as before, built once per frame size)."""
    if (hh, ww) not in _RADIAL:
        ry, rx = np.mgrid[0:hh, 0:ww].astype(np.float32)
        rx, ry = rx - ww / 2, ry - hh / 2
        _RADIAL[(hh, ww)] = (rx, ry, np.sqrt(rx ** 2 + ry ** 2) + 1e-3)
    return _RADIAL[(hh, ww)]


class Person:
    def __init__(self, model):
        self.seg = None
        if model:
            try:
                import mediapipe as mp
                from mediapipe.tasks.python import BaseOptions, vision

                self.mp = mp
                self.seg = vision.ImageSegmenter.create_from_options(vision.ImageSegmenterOptions(
                    base_options=BaseOptions(model_asset_path=model), running_mode=vision.RunningMode.IMAGE,
                    output_confidence_masks=True))
            except Exception:  # noqa: BLE001 - optional signal
                self.seg = None

    def __call__(self, bgr):
        if self.seg is None:
            return 0.0, None
        rgb = np.ascontiguousarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        m = np.squeeze(1 - self.seg.segment(self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb)).confidence_masks[0].numpy_view())
        mask = m > 0.5
        area = float(mask.mean())
        if area < 0.01:
            return area, None
        ys, xs = np.nonzero(mask)
        return area, [float(xs.mean() / m.shape[1]), float(ys.min() / m.shape[0])]


def colourfulness(bgr):
    b, g, r = [c.astype(np.float32) for c in cv2.split(bgr)]
    rg, yb = r - g, 0.5 * (r + g) - b
    return float(np.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2))


def analyse(path, model, ffmpeg, start=0, end=None):
    """Rows for samples [start, end). A part that starts later also reads the sample before `start`, so motion, cuts
    and the every-other-sample person reading continue exactly as in one pass; that extra row is dropped."""
    person = Person(model)
    rows, prev_gray, prev_hist = [], None, None
    first = max(0, start - 1)
    for i, f in enumerate(frames(path, ffmpeg, first), first):
        if end is not None and i >= end:
            break
        gray = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(f, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [16, 8], [0, 180, 0, 256])
        cv2.normalize(hist, hist)
        cut = float(cv2.compareHist(prev_hist, hist, cv2.HISTCMP_BHATTACHARYYA)) if prev_hist is not None else 0.0
        motion = cam = zoom = 0.0
        if prev_gray is not None:
            flow = cv2.calcOpticalFlowFarneback(prev_gray, gray, None, 0.5, 2, 13, 2, 5, 1.1, 0)
            mag = np.linalg.norm(flow, axis=2)
            motion = float(mag.mean())
            cam = float(np.linalg.norm(np.median(flow.reshape(-1, 2), axis=0)))  # global (camera) motion
            rx, ry, rn = radial(*gray.shape)
            zoom = float(np.mean((flow[..., 0] * rx + flow[..., 1] * ry) / rn))  # >0 push in, <0 pull out
        lab = cv2.cvtColor(f, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)
        sat = cv2.cvtColor(f, cv2.COLOR_BGR2HSV)[..., 1]
        if i < start:  # the extra sample before this part: only its picture is needed (person is read on even samples)
            area, pos = person(f) if i % 2 == 0 else (0.0, None)
        else:
            area, pos = person(f) if i % 2 == 0 else (rows[-1]["person"], rows[-1]["pos"]) if rows else (0.0, None)
        rows.append({"t": round(i / SAMPLE_FPS, 3), "sharp": float(cv2.Laplacian(gray, cv2.CV_32F).var()),
                     "bright": float(gray.mean() / 255), "contrast": float(gray.std() / 255), "colour": colourfulness(f),
                     "zoom": zoom, "lab": [round(float(v), 2) for v in lab.mean(0)], "lab_sd": [round(float(v), 2) for v in lab.std(0)],
                     "sat": float(sat.mean() / 255),
                     "motion": motion, "cam": cam, "cut": cut, "person": area, "pos": pos})
        prev_gray, prev_hist = gray, hist
    return rows[1:] if start > 0 else rows


def shots(rows, thr=0.42):
    bounds = [0]
    for i, r in enumerate(rows):
        if i and r["cut"] > thr and i - bounds[-1] >= 3:
            bounds.append(i)
    bounds.append(len(rows))
    sharp_ref = np.percentile([r["sharp"] for r in rows], 90) + 1e-6 if rows else 1
    out = []
    for a, b in zip(bounds, bounds[1:]):
        seg = rows[a:b]
        if not seg:
            continue
        sharp = float(np.clip(np.median([r["sharp"] for r in seg]) / sharp_ref, 0, 1))
        bright = float(np.median([r["bright"] for r in seg]))
        expo = float(1 - min(1, abs(bright - 0.5) / 0.42))
        motion = float(np.mean([r["motion"] for r in seg]))
        shake = float(np.std([r["cam"] for r in seg]))
        person = float(np.mean([r["person"] for r in seg]))
        colour = float(np.mean([r["colour"] for r in seg]))
        quality = 0.35 * sharp + 0.25 * expo + 0.15 * min(1, colour / 60) + 0.15 * (1 - min(1, shake / 3)) + 0.10 * min(1, person * 4)
        out.append({"start": seg[0]["t"], "end": round(seg[-1]["t"] + 1 / SAMPLE_FPS, 3), "sharp": round(sharp, 3),
                    "exposure": round(expo, 3), "motion": round(motion, 3), "shake": round(shake, 3),
                    "person": round(person, 3), "colour": round(colour, 1), "quality": round(quality, 3)})
    return out


def audio(path, ffmpeg):
    try:
        import librosa
    except ImportError:
        return {}
    raw = subprocess.run([ffmpeg, "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", "22050", "-f", "f32le", "-"],
                         capture_output=True, creationflags=0x08000000 if sys.platform == "win32" else 0).stdout
    y = np.frombuffer(raw, np.float32)
    if len(y) < 22050:
        return {"has_audio": bool(len(y))}
    tempo, beats = librosa.beat.beat_track(y=y, sr=22050, units="time")
    flat = float(np.mean(librosa.feature.spectral_flatness(y=y)))
    rms = librosa.feature.rms(y=y)[0]
    zcr = float(np.mean(librosa.feature.zero_crossing_rate(y)))
    # speech: strong syllable-rate modulation of loudness (3-8 Hz); music: steady energy + regular beats
    env = rms - rms.mean()
    spec = np.abs(np.fft.rfft(env))
    freqs = np.fft.rfftfreq(len(env), d=512 / 22050)
    band = spec[(freqs >= 3) & (freqs <= 8)].sum() / (spec[(freqs > 0.3) & (freqs <= 20)].sum() + 1e-9)
    return {"has_audio": True, "tempo": float(np.atleast_1d(tempo)[0]), "beats": [round(float(b), 3) for b in beats],
            "speechiness": round(float(band), 3), "flatness": round(flat, 4), "zcr": round(zcr, 4),
            "loudness_db": round(float(20 * np.log10(np.sqrt(np.mean(y ** 2)) + 1e-9)), 1)}


def classify(rows, shot_list, aud, speech_ratio=None):
    """Footage type from cheap signals (first match wins)."""
    if not rows:
        return "unknown", {}
    person = float(np.mean([r["person"] for r in rows]))
    motion = float(np.mean([r["motion"] for r in rows]))
    cam = float(np.mean([r["cam"] for r in rows]))
    dur = rows[-1]["t"] + 1 / SAMPLE_FPS
    shot_len = dur / max(1, len(shot_list))
    speech = speech_ratio if speech_ratio is not None else (1.0 if aud.get("speechiness", 0) > 0.3 else 0.0)
    colour = float(np.mean([r["colour"] for r in rows]))
    sig = {"person_area": round(person, 3), "motion": round(motion, 3), "camera_motion": round(cam, 3), "avg_shot_s": round(shot_len, 2),
           "speech": round(speech, 2), "colourfulness": round(colour, 1), "shots": len(shot_list)}
    if person > 0.12 and speech > 0.5 and shot_len > 4:
        kind = "talking_head"
    elif speech > 0.5 and person > 0.05:
        kind = "vlog"
    elif motion > 2.5 or cam > 2.0:
        kind = "action"
    elif person > 0.15:
        kind = "people_lifestyle"
    elif motion < 0.6 and colour > 35:
        kind = "scenic_or_product"
    else:
        kind = "b_roll"
    return kind, sig


def parallel(path, model, ffmpeg, workers, out):
    """Frame rows in `workers` time ranges plus the audio, all at once in separate processes.
    Returns (rows, audio), or None if any part failed (the caller then does it all in one process)."""
    import os

    probe = cv2.VideoCapture(path)
    fps, count = probe.get(cv2.CAP_PROP_FPS) or 0, probe.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    probe.release()
    n = int(count / fps * SAMPLE_FPS) if fps > 0 and count > 0 else 0
    workers = max(1, min(workers, n // 60))  # at least 10 seconds of footage per part
    bounds = [round(n * i / workers) for i in range(workers)] + [-1]  # the last part reads to the end
    cf = 0x08000000 | 0x00004000 if sys.platform == "win32" else 0
    me = [sys.executable, "-E", "-P", os.path.abspath(__file__)]
    files = [f"{out}.rows{i}.json" for i in range(workers)] + [f"{out}.audio.json"]
    procs = [subprocess.Popen([*me, "--rows", path, model or "", ffmpeg, str(bounds[i]), str(bounds[i + 1]), files[i]],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=cf) for i in range(workers)]
    procs.append(subprocess.Popen([*me, "--audio", path, ffmpeg, files[-1]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  creationflags=cf))
    ok = all(pr.wait() == 0 for pr in procs)
    try:
        if not ok:
            return None
        rows = []
        for f in files[:-1]:
            with open(f, encoding="utf-8") as fh:
                rows += json.load(fh)
        with open(files[-1], encoding="utf-8") as fh:
            return rows, json.load(fh)
    except (OSError, ValueError):
        return None
    finally:
        for f in files:
            try:
                os.remove(f)
            except OSError:
                pass


def main():
    if sys.argv[1] == "--rows":  # worker: --rows video model ffmpeg start end(-1 = to the end) out.json
        _, _, path, model, ffmpeg, a, b, dest = sys.argv[:8]
        rows = analyse(path, model or None, ffmpeg, int(a), None if int(b) < 0 else int(b))
        with open(dest, "w", encoding="utf-8") as fh:
            json.dump(rows, fh)
        return
    if sys.argv[1] == "--audio":  # worker: --audio video ffmpeg out.json
        with open(sys.argv[4], "w", encoding="utf-8") as fh:
            json.dump(audio(sys.argv[2], sys.argv[3]), fh)
        return
    path, out = sys.argv[1], sys.argv[2]
    model = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else None
    ffmpeg = sys.argv[4] if len(sys.argv) > 4 else "ffmpeg"
    speech_ratio = float(sys.argv[5]) if len(sys.argv) > 5 and sys.argv[5] != "" else None
    workers = int(sys.argv[6]) if len(sys.argv) > 6 and sys.argv[6] else 1
    both = parallel(path, model, ffmpeg, workers, out) if workers > 1 else None
    if both:
        rows, aud = both
    else:
        rows = analyse(path, model, ffmpeg)
        aud = audio(path, ffmpeg)
    shot_list = shots(rows)
    kind, sig = classify(rows, shot_list, aud, speech_ratio)
    best = sorted(shot_list, key=lambda s: -s["quality"])[:8]
    issues = []
    if rows:
        if np.mean([r["bright"] for r in rows]) < 0.28:
            issues.append("footage is dark")
        if np.mean([r["bright"] for r in rows]) > 0.78:
            issues.append("footage is very bright")
        if np.mean([s["shake"] for s in shot_list] or [0]) > 2.5:
            issues.append("camera is shaky")
        if np.median([r["sharp"] for r in rows]) < 40:  # Laplacian variance at 320 px: soft focus / motion blur
            issues.append("some shots are soft or blurry")
    result = {"type": kind, "signals": sig, "shots": shot_list, "best_shots": best, "audio": aud, "issues": issues,
              "timeline": [{k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()} for r in rows]}
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(result, fh)
    print(json.dumps({"type": kind, "signals": sig, "shots": len(shot_list), "issues": issues,
                      "tempo": aud.get("tempo"), "speechiness": aud.get("speechiness")}))


if __name__ == "__main__":
    main()
