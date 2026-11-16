"""Optional ML worker, run with the user's own Python (never imported by the app).

The packaged app stays small; heavy open-source ML libraries are used only if
the user has them installed. Every subcommand reads arguments, writes a JSON
result to --out and exits. Missing libraries produce {"ok": false, "missing": ...}.

  transcribe  faster-whisper word timestamps (+ Silero VAD filter)
  kokoro      Kokoro-82M TTS (Apache-2.0) -> wav per item
  denoise     noisereduce spectral gating on a wav
  faces       MediaPipe / OpenCV face track -> per-second horizontal centre
  beats       librosa beat + onset times
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
    model = WhisperModel(args.model, device="cpu", compute_type="int8", cpu_threads=args.threads)
    opts = dict(language=args.language or None, word_timestamps=True, vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 300}, condition_on_previous_text=False)
    vocab = [v.strip() for v in (args.vocab or "").split("|") if v.strip()]
    if vocab:  # the creator's own names/brands: biased decoding (hotwords) or a context prompt on older versions
        try:
            segments, info = model.transcribe(pcm, hotwords=" ".join(vocab), **opts)
        except TypeError:
            segments, info = model.transcribe(pcm, initial_prompt=", ".join(vocab), **opts)
    else:
        segments, info = model.transcribe(pcm, **opts)
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
        segs.append({"start": seg.start, "end": seg.end, "text": "".join(w.word for w in kept).strip()})
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
                                          "model": args.model}), encoding="utf-8")


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
    for sp in (t, k, d, f, b, pr):
        sp.add_argument("--out", required=True)
    args = p.parse_args()
    {"transcribe": transcribe, "kokoro": kokoro, "denoise": denoise, "faces": faces, "beats": beats, "prosody": prosody}[args.cmd](args)


if __name__ == "__main__":
    main()
