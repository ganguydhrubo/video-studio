"""Optional ML worker, run with the user's own Python (never imported by the app).

The packaged app stays small; heavy open-source ML libraries are used only if
the user has them installed. Every subcommand reads arguments, writes a JSON
result to --out and exits. Missing libraries produce {"ok": false, "missing": ...}.

  transcribe  faster-whisper word timestamps (+ Silero VAD filter)
  kokoro      Kokoro-82M TTS (Apache-2.0) -> wav per item
  denoise     noisereduce spectral gating on a wav
  faces       MediaPipe / OpenCV face track -> per-second horizontal centre
  beats       librosa beat + onset times
  matte       MediaPipe selfie person matte (rembg fallback) -> greyscale mask video via FFmpeg
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _fail(out: Path, missing: str, err: str = "") -> None:
    out.write_text(json.dumps({"ok": False, "missing": missing, "error": err}), encoding="utf-8")
    sys.exit(3)


def _word_end(w) -> float:
    """Whisper stretches a word across silence - or across speech it skipped - when it loses its place. No spoken
    word lasts that long: end it where a word plausibly ends, so the hole after it can be seen and listened to again."""
    return min(w.end, w.start + max(1.2, 0.18 * len(w.word.strip())))


def _sentences(words: list, max_words: int = 30, max_gap: float = 0.8) -> list[list]:
    """Split one long (batched, up to 30 s) segment's words into sentence-sized pieces: at full stops, at long
    pauses, or every `max_words` words."""
    out, cur = [], []
    for w in words:
        if cur and (w.start - cur[-1].end > max_gap or len(cur) >= max_words):
            out.append(cur)
            cur = []
        cur.append(w)
        if w.word.rstrip().endswith((".", "?", "!", "।")):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def transcribe(args: argparse.Namespace) -> None:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        _fail(Path(args.out), "faster-whisper", str(exc))
    import wave

    import numpy as np

    # The app hands us 16 kHz mono PCM, decoded by FFmpeg; reading it here avoids PyAV version issues.
    with wave.open(args.audio, "rb") as wf:
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2").astype("float32") / 32768.0
    import time

    started = time.time()
    model = WhisperModel(args.model, device="cpu", compute_type="int8", cpu_threads=args.threads)
    loaded = time.time()
    opts = dict(language=args.language or None, word_timestamps=True, vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 300}, condition_on_previous_text=False)
    # Batched decoding: the speech found by VAD is packed into <=30 s pieces and decoded `batch` pieces at a time.
    # Same model, same word timestamps; much better use of the CPU cores than one window after another.
    runner, batch = model, max(0, int(getattr(args, "batch", 0) or 0))
    if batch > 0:
        try:
            from faster_whisper import BatchedInferencePipeline

            runner = BatchedInferencePipeline(model=model)
            opts["batch_size"] = batch
            # (with timestamp tokens on, faster-whisper 1.2 drops the unfinished last sentence of each 30 s piece;
            # without them each piece is one segment, split back into sentences below from the word timings)
        except ImportError:  # faster-whisper older than 1.1
            runner, batch = model, 0
    vocab = [v.strip() for v in (args.vocab or "").split("|") if v.strip()]
    if vocab:  # the creator's own names/brands: biased decoding (hotwords) or a context prompt on older versions
        try:
            segments, info = runner.transcribe(pcm, hotwords=" ".join(vocab), **opts)
        except TypeError:
            segments, info = runner.transcribe(pcm, initial_prompt=", ".join(vocab), **opts)
    else:
        segments, info = runner.transcribe(pcm, **opts)
    import re

    words, segs, dropped = [], [], 0
    for seg in segments:
        # Whisper hallucination guards: non-speech, highly repetitive text, garbage runs of one letter
        if seg.no_speech_prob > 0.6 or seg.compression_ratio > 2.4 or re.search(r"(.)\1{5,}", seg.text):
            dropped += 1
            continue
        kept = [w for w in (seg.words or []) if w.probability >= 0.2 and not re.search(r"(.)\1{4,}", w.word)]
        if not kept:
            continue
        for part in _sentences(kept) if batch else [kept]:
            segs.append({"start": seg.start if not batch else part[0].start, "end": seg.end if not batch else part[-1].end,
                         "text": "".join(w.word for w in part).strip()})
        for w in kept:
            words.append({"text": w.word.strip(), "start": round(w.start, 3), "end": round(_word_end(w), 3), "p": round(w.probability, 3)})
    translation = []
    if args.translate and info.language != "en" and words:
        # what was MEANT, in English, whatever the language: used to check on-screen words and write the hook
        t_opts = {k: v for k, v in opts.items() if k not in ("word_timestamps",)}
        t_opts["language"] = info.language
        try:
            tmodel = model if "turbo" not in args.model else WhisperModel(  # turbo was trained without translation
                args.translate_model, device="cpu", compute_type="int8", cpu_threads=args.threads)
            # one window after another, as before: the meaning has no word timings to split 30 s batched pieces by
            t_opts = {k: v for k, v in t_opts.items() if k != "batch_size"}
            for seg in tmodel.transcribe(pcm, task="translate", **t_opts)[0]:
                if seg.no_speech_prob <= 0.6 and seg.compression_ratio <= 2.4:
                    translation.append({"start": round(seg.start, 3), "end": round(seg.end, 3), "text": seg.text.strip()})
            # whisper sometimes skips a whole stretch (loops, then drops it): translate each uncovered stretch on its own
            def covered(s):
                return sum(max(0.0, min(s["end"], t["end"]) - max(s["start"], t["start"])) for t in translation) >= 0.4 * (s["end"] - s["start"])
            gaps = []
            for s in segs:
                if not covered(s):
                    if gaps and s["start"] - gaps[-1][1] < 1.5 and s["end"] - gaps[-1][0] < 28:
                        gaps[-1][1] = s["end"]
                    else:
                        gaps.append([s["start"], s["end"]])
            g_opts = {k: v for k, v in t_opts.items() if k not in ("vad_filter", "vad_parameters", "clip_timestamps")}
            g_opts["condition_on_previous_text"] = False
            for a, b in gaps:
                for seg in tmodel.transcribe(pcm, task="translate", clip_timestamps=[max(0.0, a - 0.2), b + 0.2], **g_opts)[0]:
                    if seg.compression_ratio <= 2.4 and seg.text.strip():
                        translation.append({"start": round(seg.start, 3), "end": round(seg.end, 3), "text": seg.text.strip(), "refill": True})
            translation.sort(key=lambda t: t["start"])
        except Exception as exc:  # noqa: BLE001 - meaning check is an extra; never fail the transcript
            translation = [*translation, {"error": str(exc)[:200]}]
    relistened = 0
    try:
        # The translation heard meaning in a stretch where the transcript kept no words (low-confidence words were
        # dropped, or whisper skipped ahead): listen to just that stretch again, so its captions and cuts exist.
        heard = [t for t in translation if "text" in t]
        edges = [0.0] + [x for w in words for x in (w["start"], w["end"])] + [float(info.duration)]
        for a, b in zip(edges[0::2], edges[1::2]):  # the silences between words: (0, first), (end, next start), ...
            if b - a < 2.0 or sum(max(0.0, min(b, t["end"]) - max(a, t["start"])) for t in heard) < 1.5:
                continue
            r_opts = dict(language=info.language, word_timestamps=True, condition_on_previous_text=False)
            for seg in model.transcribe(pcm, clip_timestamps=[max(0.0, a + 0.05), b - 0.05], **r_opts)[0]:
                if seg.no_speech_prob > 0.6 or seg.compression_ratio > 2.4 or re.search(r"(.)\1{5,}", seg.text):
                    continue
                kept = [w for w in (seg.words or []) if w.probability >= 0.3 and a <= w.start and w.end <= b
                        and not re.search(r"(.)\1{4,}", w.word)]
                if not kept:
                    continue
                segs.append({"start": kept[0].start, "end": kept[-1].end, "text": "".join(w.word for w in kept).strip(), "relisten": True})
                for w in kept:
                    words.append({"text": w.word.strip(), "start": round(w.start, 3), "end": round(_word_end(w), 3), "p": round(w.probability, 3)})
                relistened += len(kept)
        if relistened:
            words.sort(key=lambda w: w["start"])
            segs.sort(key=lambda s: s["start"])
    except Exception:  # noqa: BLE001 - a second listen is an extra; the first transcript stands
        pass
    Path(args.out).write_text(json.dumps({"ok": True, "language": info.language, "duration": info.duration,
                                          "segments": segs, "words": words, "dropped_segments": dropped,
                                          "relistened_words": relistened,
                                          "translation": [t for t in translation if "text" in t],
                                          "model": args.model, "batch": batch,
                                          "seconds": {"load": round(loaded - started, 2),
                                                      "total": round(time.time() - started, 2)}}), encoding="utf-8")


def kokoro(args: argparse.Namespace) -> None:
    """Kokoro-82M via kokoro-onnx (works on Python 3.13; weights Apache-2.0)."""
    try:
        import soundfile as sf
        from kokoro_onnx import Kokoro
    except ImportError as exc:
        _fail(Path(args.out), "kokoro-onnx soundfile", str(exc))
    items = json.loads(Path(args.items).read_text(encoding="utf-8"))
    model_dir = Path(args.model_dir)
    model = next((model_dir / n for n in ("kokoro-v1.0.int8.onnx", "kokoro-v1.0.onnx") if (model_dir / n).exists()), None)
    if model is None or not (model_dir / "voices-v1.0.bin").exists():
        _fail(Path(args.out), "kokoro model files", f"missing in {model_dir}")
    tts = Kokoro(str(model), str(model_dir / "voices-v1.0.bin"))
    lang = {"a": "en-us", "b": "en-gb", "h": "hi", "e": "es", "f": "fr-fr", "i": "it", "p": "pt-br", "j": "ja", "z": "cmn"}.get(args.lang, "en-us")
    results = []
    for item in items:
        audio, rate = tts.create(item["text"], voice=args.voice, speed=args.speed, lang=lang)
        sf.write(item["out"], audio, rate, subtype="PCM_16")
        results.append({"id": item["id"], "duration": len(audio) / rate, "words": []})
    Path(args.out).write_text(json.dumps({"ok": True, "items": results}), encoding="utf-8")


def denoise(args: argparse.Namespace) -> None:
    try:
        import noisereduce as nr
        import soundfile as sf
    except ImportError as exc:
        _fail(Path(args.out), "noisereduce soundfile", str(exc))
    data, rate = sf.read(args.audio)
    cleaned = nr.reduce_noise(y=data.T if data.ndim > 1 else data, sr=rate, stationary=False, prop_decrease=args.strength)
    sf.write(args.dest, cleaned.T if data.ndim > 1 else cleaned, rate)
    Path(args.out).write_text(json.dumps({"ok": True, "dest": args.dest}), encoding="utf-8")


def faces(args: argparse.Namespace) -> None:
    import numpy as np
    try:
        import cv2
    except ImportError as exc:
        _fail(Path(args.out), "opencv-python", str(exc))
    cap = cv2.VideoCapture(args.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    width = cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1
    height = cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1
    step = max(1, int(round(fps / args.samples_per_second)))
    detector = seg = cascade = None
    engine = "none"
    if getattr(args, "model", "") and Path(args.model).exists():
        try:  # MediaPipe Tasks selfie-multiclass: class 3 = face skin -> face box (works on mediapipe >= 0.10 and 1.x)
            import mediapipe as mp
            from mediapipe.tasks.python import BaseOptions, vision

            seg = vision.ImageSegmenter.create_from_options(vision.ImageSegmenterOptions(
                base_options=BaseOptions(model_asset_path=args.model), running_mode=vision.RunningMode.IMAGE,
                output_category_mask=True))
            engine = "mediapipe-tasks-selfie-multiclass"
        except Exception:  # noqa: BLE001
            seg = None
    if seg is None:
        try:
            import mediapipe as mp
            detector = mp.solutions.face_detection.FaceDetection(model_selection=1, min_detection_confidence=0.5)
            engine = "mediapipe"
        except Exception:  # noqa: BLE001 - legacy API removed in new mediapipe
            if hasattr(cv2, "CascadeClassifier"):
                cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
                engine = "opencv-haar"
    if engine == "none":
        Path(args.out).write_text(json.dumps({"ok": True, "engine": "none", "track": [],
                                              "error": "no face detector available"}), encoding="utf-8")
        return
    track, idx = [], 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if idx % step == 0:
            _, frame = cap.retrieve()
            centre = y0 = y1 = None
            if seg is not None:
                small = cv2.resize(frame, (int(width) // 4 or 1, int(height) // 4 or 1), interpolation=cv2.INTER_AREA)
                rgb = np.ascontiguousarray(cv2.cvtColor(small, cv2.COLOR_BGR2RGB))
                cat = np.squeeze(seg.segment(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)).category_mask.numpy_view())
                ys, xs = np.nonzero(cat == 3)
                if len(xs) > cat.size * 0.003:
                    x0, x1 = np.percentile(xs, [3, 97]); fy0, fy1 = np.percentile(ys, [3, 97])
                    centre = (x0 + x1) / 2 / cat.shape[1]
                    y0, y1 = fy0 / cat.shape[0], fy1 / cat.shape[0]
            elif detector is not None:
                res = detector.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                if res.detections:
                    best = max(res.detections, key=lambda d: d.location_data.relative_bounding_box.width)
                    box = best.location_data.relative_bounding_box
                    centre = box.xmin + box.width / 2
                    y0, y1 = box.ymin, box.ymin + box.height
            else:
                gray = cv2.cvtColor(cv2.resize(frame, None, fx=0.5, fy=0.5), cv2.COLOR_BGR2GRAY)
                found = cascade.detectMultiScale(gray, 1.15, 5, minSize=(40, 40))
                if len(found):
                    x, y, w, h = max(found, key=lambda f: f[2] * f[3])
                    centre = (x + w / 2) * 2 / width
                    y0, y1 = y * 2 / height, (y + h) * 2 / height
            track.append({"t": round(idx / fps, 3), "x": None if centre is None else round(float(centre), 4),
                          "y0": None if y0 is None else round(float(y0), 4), "y1": None if y1 is None else round(float(y1), 4)})
        idx += 1
    Path(args.out).write_text(json.dumps({"ok": True, "engine": engine, "track": track}), encoding="utf-8")


def beats(args: argparse.Namespace) -> None:
    try:
        import librosa
    except ImportError as exc:
        _fail(Path(args.out), "librosa", str(exc))
    y, sr = librosa.load(args.audio, sr=22050, mono=True)
    tempo, frames = librosa.beat.beat_track(y=y, sr=sr)
    onsets = librosa.onset.onset_detect(y=y, sr=sr, units="time")
    Path(args.out).write_text(json.dumps({"ok": True, "tempo": float(tempo if not hasattr(tempo, "__len__") else tempo[0]),
                                          "beats": [round(float(t), 3) for t in librosa.frames_to_time(frames, sr=sr)],
                                          "onsets": [round(float(t), 3) for t in onsets]}), encoding="utf-8")


def prosody(args: argparse.Namespace) -> None:
    """Per-word acoustic prominence: loudness (dB), pitch (Hz, YIN), duration, plus speaker baselines."""
    import wave

    import numpy as np

    with wave.open(args.audio, "rb") as wf:
        sr = wf.getframerate()
        y = np.frombuffer(wf.readframes(wf.getnframes()), np.int16).astype(np.float32) / 32768
        if wf.getnchannels() > 1:
            y = y.reshape(-1, wf.getnchannels()).mean(1)
    words = json.loads(Path(args.words).read_text(encoding="utf-8"))
    try:
        import librosa

        hop = 160 * sr // 16000
        f0 = librosa.yin(y, fmin=70, fmax=420, sr=sr, frame_length=4 * hop * 4, hop_length=hop)
        voiced = librosa.feature.rms(y=y, frame_length=hop * 4, hop_length=hop)[0]
        f_t = np.arange(len(f0)) * hop / sr
    except Exception:  # pitch is optional; loudness and duration still work
        f0, voiced, f_t = None, None, None
    out = []
    for w in words:
        a, b = int(w["start"] * sr), max(int(w["end"] * sr), int(w["start"] * sr) + 80)
        seg = y[a:b]
        rms = float(np.sqrt(np.mean(seg ** 2)) + 1e-7)
        item = {"db": round(20 * np.log10(rms), 2), "dur": round(w["end"] - w["start"], 3), "f0": None}
        if f0 is not None:
            m = (f_t >= w["start"]) & (f_t <= w["end"])
            if m.any():
                fv, vv = f0[m], voiced[: len(f0)][m]
                good = fv[(vv > np.percentile(voiced, 40)) & (fv > 75) & (fv < 400)]
                if len(good) >= 2:
                    item["f0"] = round(float(np.median(good)), 1)
        out.append(item)
    Path(args.out).write_text(json.dumps({"ok": True, "words": out}), encoding="utf-8")


def _guided(guide, src, radius: int, eps: float, sub: int = 2):
    """Fast guided filter (He & Sun 2015, grey guide): the soft model mask snaps to the real edges in the frame
    (hair, shoulders). Coefficients are found at 1/sub size and applied at full size."""
    import cv2

    h, w = guide.shape[:2]
    gs = cv2.resize(guide, (max(1, w // sub), max(1, h // sub)), interpolation=cv2.INTER_AREA) if sub > 1 else guide
    ps = cv2.resize(src, (gs.shape[1], gs.shape[0]), interpolation=cv2.INTER_AREA) if sub > 1 else src
    r = max(1, radius // sub)
    k = (2 * r + 1, 2 * r + 1)

    def box(x):
        return cv2.boxFilter(x, -1, k, borderType=cv2.BORDER_REFLECT)

    mean_i, mean_p = box(gs), box(ps)
    cov = box(gs * ps) - mean_i * mean_p
    var = box(gs * gs) - mean_i * mean_i
    a = cov / (var + eps)
    b = mean_p - a * mean_i
    mean_a, mean_b = box(a), box(b)
    if sub > 1:
        mean_a = cv2.resize(mean_a, (w, h), interpolation=cv2.INTER_LINEAR)
        mean_b = cv2.resize(mean_b, (w, h), interpolation=cv2.INTER_LINEAR)
    return mean_a * guide + mean_b


class _Segmenter:
    """Person probability (0..1, float32) for one BGR frame. MediaPipe selfie-multiclass first, rembg if that fails."""

    def __init__(self, model: str, fps: float) -> None:
        self.engine, self.fallback = "", ""
        self.mp_seg = self.rembg = None
        self.fps, self.index = fps, 0
        try:
            import mediapipe as mp
            from mediapipe.tasks.python import BaseOptions, vision

            if not model or not Path(model).exists():
                raise FileNotFoundError(f"model not found: {model}")
            self.mp = mp
            self.mp_seg = vision.ImageSegmenter.create_from_options(vision.ImageSegmenterOptions(
                base_options=BaseOptions(model_asset_path=model), running_mode=vision.RunningMode.VIDEO,
                output_confidence_masks=True, output_category_mask=False))
            self.engine = "mediapipe-selfie-multiclass"
        except Exception as exc:  # noqa: BLE001 - any MediaPipe problem -> try rembg
            self._to_rembg(f"MediaPipe unavailable ({str(exc)[:200]})")

    def _to_rembg(self, why: str) -> None:
        self.mp_seg = None
        self.fallback = why + "; used rembg instead"
        try:
            from rembg import new_session

            for name in ("u2netp", "isnet-general-use"):
                try:
                    self.rembg = new_session(name)
                    self.engine = f"rembg-{name}"
                    return
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            pass
        self.engine = ""

    def __call__(self, bgr):
        import cv2
        import numpy as np

        h, w = bgr.shape[:2]
        if self.mp_seg is not None:
            try:  # the model sees 256x256 anyway: a ~512 px input keeps the returned masks small and fast
                s = min(1.0, 512 / max(h, w))
                small = cv2.resize(bgr, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA)
                rgb = np.ascontiguousarray(cv2.cvtColor(small, cv2.COLOR_BGR2RGB))
                res = self.mp_seg.segment_for_video(self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb),
                                                    int(self.index * 1000 / (self.fps or 30)))
                self.index += 1
                background = np.squeeze(res.confidence_masks[0].numpy_view()).astype(np.float32)
                return 1.0 - background  # person = hair + skin + clothes + accessories
            except Exception as exc:  # noqa: BLE001 - switch engines mid-way rather than fail the whole video
                self._to_rembg(f"MediaPipe failed at frame {self.index} ({str(exc)[:200]})")
                if self.rembg is None:
                    raise
        from PIL import Image
        from rembg import remove

        s = min(1.0, 320 / max(h, w))
        small = cv2.resize(bgr, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA)
        mask = remove(Image.fromarray(cv2.cvtColor(small, cv2.COLOR_BGR2RGB)), session=self.rembg, only_mask=True)
        return np.asarray(mask, dtype=np.float32) / 255.0


def _mask_encoder(ffmpeg: str, dest: Path) -> list[str]:
    import subprocess

    try:
        enc = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=60,
                             creationflags=0x08000000 if sys.platform == "win32" else 0).stdout
    except (OSError, subprocess.SubprocessError):
        enc = ""
    if dest.suffix.lower() == ".mkv" and " ffv1 " in enc:
        return ["-c:v", "ffv1", "-level", "3", "-slices", "4", "-g", "1"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "12", "-pix_fmt", "gray"]


def matte(args: argparse.Namespace) -> None:
    """Greyscale person matte (255 = person) at the input's size and frame rate, frames piped raw into FFmpeg."""
    import subprocess
    import tempfile
    import time

    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        _fail(Path(args.out), "opencv-python numpy", str(exc))
    started = time.time()
    cap = cv2.VideoCapture(args.video)
    ok, frame = cap.read()
    if not ok:
        raise RuntimeError(f"OpenCV could not read a frame from {args.video}")
    fps_text = args.fps or f"{cap.get(cv2.CAP_PROP_FPS) or 30:.6f}"
    num, _, den = fps_text.partition("/")
    fps = float(num) / float(den or 1)
    height, width = frame.shape[:2]  # first decoded frame: already rotated like FFmpeg rotates it
    scale = min(1.0, args.max_side / max(width, height))
    ww, wh = max(2, round(width * scale)), max(2, round(height * scale))
    radius = max(3, round(max(ww, wh) / 160))
    segment = _Segmenter(args.model, fps)
    if not segment.engine:
        _fail(Path(args.out), "mediapipe rembg", segment.fallback)

    import queue
    import threading

    dest = Path(args.dest)
    log = tempfile.TemporaryFile()
    cmd = [args.ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "gray",
           "-s", f"{width}x{height}", "-r", fps_text, "-i", "-", *_mask_encoder(args.ffmpeg, dest), str(dest)]
    flags = (0x08000000 | 0x00004000) if sys.platform == "win32" else 0  # no window, below-normal priority
    enc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log, creationflags=flags)

    # The model is the slow part (~100-200 ms a frame on a laptop CPU). Edge refinement and writing run on a
    # second thread (OpenCV/NumPy release the GIL) while the model works on the next frame.
    jobs: queue.Queue = queue.Queue(maxsize=4)
    failure: list[BaseException] = []

    def refine_and_write() -> None:
        while True:
            item = jobs.get()
            if item is None:
                return
            if failure:
                continue  # drain the queue so the producer never blocks
            work, prob = item
            try:
                soft = cv2.resize(prob, (ww, wh), interpolation=cv2.INTER_LINEAR)
                guide = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
                m = _guided(guide, soft, radius, 1e-2)
                m = np.clip((m - 0.15) / 0.7, 0.0, 1.0)  # solid inside, no faint grey veil outside
                m = cv2.GaussianBlur(m, (0, 0), 0.8)  # light edge feather
                if scale < 1.0:
                    m = cv2.resize(m, (width, height), interpolation=cv2.INTER_LINEAR)
                enc.stdin.write((m * 255.0 + 0.5).astype(np.uint8).tobytes())
            except BaseException as exc:  # noqa: BLE001 - reported by the main thread
                failure.append(exc)

    writer = threading.Thread(target=refine_and_write, daemon=True)
    writer.start()
    stride = max(1, args.stride)
    prev = raw = None
    frames = 0
    try:
        while ok and not failure:
            work = frame if scale >= 1.0 else cv2.resize(frame, (ww, wh), interpolation=cv2.INTER_AREA)
            if raw is None or frames % stride == 0:  # in between, the guided filter moves the edges with the frame
                raw = segment(work)
            prob = raw
            # temporal smoothing (EMA) that backs off where the matte changes a lot, so fast moves do not ghost
            if prev is not None and prev.shape == prob.shape and args.smooth > 0:
                weight = args.smooth * np.clip(1.0 - np.abs(prob - prev) * 2.0, 0.0, 1.0)
                prob = weight * prev + (1.0 - weight) * prob
            prev = prob
            jobs.put((work, prob))
            frames += 1
            ok, frame = cap.read()
        jobs.put(None)
        writer.join()
        if failure:
            raise failure[0]
        enc.stdin.close()
        code = enc.wait()
    except (BrokenPipeError, OSError):
        enc.kill()
        code = enc.wait()
    finally:
        cap.release()
        if writer.is_alive():
            failure.append(RuntimeError("stopped"))
            try:
                jobs.put_nowait(None)
            except queue.Full:
                pass
        if enc.poll() is None:  # the segmenter raised: do not leave FFmpeg waiting on the pipe
            enc.kill()
    if code != 0:
        log.seek(0)
        raise RuntimeError("FFmpeg could not write the mask video: " + log.read().decode("utf-8", "replace")[-800:])
    seconds = time.time() - started
    Path(args.out).write_text(json.dumps({
        "ok": True, "dest": str(dest), "engine": segment.engine, "fallback": segment.fallback, "frames": frames,
        "fps": round(fps, 3), "width": width, "height": height, "work_size": [ww, wh], "seconds": round(seconds, 2),
        "speed_fps": round(frames / seconds, 2) if seconds else 0.0, "encoder": cmd[cmd.index("-c:v") + 1],
        "stride": stride}),
        encoding="utf-8")


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("transcribe")
    t.add_argument("--audio", required=True)
    t.add_argument("--model", default="small")
    t.add_argument("--language", default="")
    t.add_argument("--threads", type=int, default=4)
    t.add_argument("--vocab", default="")
    t.add_argument("--translate", type=int, default=1)
    t.add_argument("--translate-model", default="small")
    t.add_argument("--batch", type=int, default=0)  # 0 = one window after another; N = batched decoding
    k = sub.add_parser("kokoro")
    k.add_argument("--items", required=True)
    k.add_argument("--voice", default="af_heart")
    k.add_argument("--lang", default="a")
    k.add_argument("--speed", type=float, default=1.0)
    k.add_argument("--model-dir", default="")
    d = sub.add_parser("denoise")
    d.add_argument("--audio", required=True)
    d.add_argument("--dest", required=True)
    d.add_argument("--strength", type=float, default=0.85)
    f = sub.add_parser("faces")
    f.add_argument("--model", default="")
    f.add_argument("--video", required=True)
    f.add_argument("--samples-per-second", type=float, default=4)
    b = sub.add_parser("beats")
    b.add_argument("--audio", required=True)
    pr = sub.add_parser("prosody")
    pr.add_argument("--audio", required=True)
    pr.add_argument("--words", required=True)
    mt = sub.add_parser("matte")
    mt.add_argument("--video", required=True)
    mt.add_argument("--dest", required=True)
    mt.add_argument("--model", default="")
    mt.add_argument("--ffmpeg", default="ffmpeg")
    mt.add_argument("--fps", default="")  # exact rate from ffprobe, e.g. 30000/1001
    mt.add_argument("--smooth", type=float, default=0.6)
    mt.add_argument("--max-side", type=int, default=1280)
    mt.add_argument("--stride", type=int, default=1)  # run the model on every Nth frame (2 = about twice as fast)
    for sp in (t, k, d, f, b, pr, mt):
        sp.add_argument("--out", required=True)
    args = p.parse_args()
    {"transcribe": transcribe, "kokoro": kokoro, "denoise": denoise, "faces": faces, "beats": beats, "prosody": prosody,
     "matte": matte}[args.cmd](args)


if __name__ == "__main__":
    main()
