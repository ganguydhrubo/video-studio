# Architecture

```
video-studio (CLI, argparse)          video-studio studio (local web UI, http.server, 127.0.0.1 + token)
        │                                         │ runs the CLI as subprocesses, polls progress
        ▼                                         ▼
  pipeline.py  (script → video)          editor.py  (footage → video)
  analyze → storyboard → produce →       ingest → transcribe → plan → cut →
  render → validate → export             render → validate → export
        │                                         │
        ├── planner.py   script parsing, local rules, AI plan (one JSON call), creative decisions
        ├── ai.py        Director: Claude / Codex / Gemini / Qwen / Ollama CLIs, fallback chain, cache, budget
        ├── memory.py    SQLite: taste model, FTS5 recall, failure memory + learned fixes, templates, metrics
        ├── engines/
        │    canvas.py      Pillow motion graphics → raw frames → FFmpeg
        │    blender.py     runs blender/scene_builder.py headless (EEVEE → Cycles fallback), resumable frames
        │    hyperframes.py HTML/GSAP compositions → `npx hyperframes render` (offline, vendored GSAP)
        │    tts.py         Kokoro (ONNX) or Windows SAPI with word timings; Whisper alignment
        │    ml.py          bridge to helpers/ml_helper.py in system Python (whisper, kokoro, denoise, faces, beats)
        │    music.py       NumPy music beds + SFX (no samples, no licences to worry about)
        │    captions.py    ASS word-highlight / karaoke, SRT, VTT
        │    compose.py     xfade assembly, grade, grain, burn-in, audio mix (sidechain duck, 2-pass loudnorm)
        │    ff.py          FFmpeg/ffprobe wrappers, quality presets
        ├── qa.py        technical QA in one decode pass + frame sampling + contact sheet
        ├── export.py    platform reframes, thumbnail, captions, OpenTimelineIO JSON
        └── project.py   project folder, settings, stage state (each stage idempotent → resumable)
```

## Design decisions

- **The core stays light.** The frozen app needs only NumPy and Pillow (about 27 MB setup). Heavy ML (torch, faster-whisper, mediapipe, kokoro-onnx) runs in the user's system Python via `ml_helper.py` with `-E -P` isolation, so it is optional and never bundled.
- **Local software first.** Cutting, captions, mixing, QA and reframing use FFmpeg and local models. AI CLIs are used only for judgment calls: scene plans, edit decisions, transliteration and optional visual review. A budget defaults to 4 calls per project, and results are cached.
- **Logins belong to the official CLIs.** The studio runs each CLI's own status and login commands. It never reads, copies or transmits tokens; for Gemini and Qwen it checks only that the credentials file exists.
- **Resumable and low resource.**
  - Every stage writes its outputs and state. Clips are cached by a hash of their spec.
  - Blender renders frame-by-frame with `use_overwrite=False`, so an interrupted render continues where it stopped.
  - Renders run at below-normal priority on cores − 2 threads, one heavy job at a time.
- **Self-learning without claiming model training.**
  - A Beta-Bernoulli Thompson-sampling taste model over 7 creative dimensions, per (tone, aspect) bucket with global backoff.
  - Feedback keywords map notes to dimensions ("music too loud" lowers music).
  - FTS5 recall of past briefs and feedback is fed to the AI director.
  - Failure memory: a recognised error plus a fix that worked gets applied automatically next time, for example EEVEE failing and Cycles succeeding.
- **Footage editing.**
  - Utterances come from Whisper word timings, split on sentence ends, commas and pauses.
  - A retake is a later utterance (within 3) that restarts the same words; the last take is kept.
  - Fillers are removed. Pauses are shortened according to the pacing setting (0.16, 0.28 or 0.45 s).
  - Face-tracked crop uses the median face x per piece. Punch-ins alternate 1.0/1.09, and emphasis words get 1.18.
  - Colour: signalstats picks a gentle `eq`. Dark footage is lifted; hot or bright faces are toned down, never brightened.
