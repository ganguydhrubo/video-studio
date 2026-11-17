# Licence report

Provider logos are from [LobeHub Icons](https://github.com/lobehub/lobe-icons), MIT. The licence is kept in `video_studio/assets/provider-icons/LICENSE` and bundled in the packaged app. Provider names and marks belong to their respective owners.

Video Studio's own code is MIT. It **does not bundle** third-party tools except the Python runtime, NumPy and Pillow inside the frozen build. Everything else is detected or installed separately, with your approval, under its own licence.

| Component | Use | Licence | Notes |
|---|---|---|---|
| Python runtime (PyInstaller build) | app runtime | PSF | PyInstaller bootloader: GPL-2.0 with an exception that allows any licence for the bundled app |
| NumPy | audio/image math | BSD-3-Clause | bundled |
| Pillow | motion graphics | MIT-CMU (HPND) | bundled |
| FFmpeg (gyan.dev full build) | all encoding/editing | **GPL-3.0** (build includes GPL libs such as x264) | not bundled; installed via winget. Shipping it inside a closed product would trigger the GPL. |
| Blender | 3D | GPL-3.0 | separate program, called as a subprocess; scene_builder.py runs inside it |
| HyperFrames 0.9.52 | optional HTML motion engine | Apache-2.0 | via npx; uses its own vendored GSAP (GSAP licence: free "no charge" standard licence since 2025) |
| Node.js | runtime for HyperFrames / AI CLIs | MIT | |
| faster-whisper / CTranslate2 | transcription | MIT | Whisper model weights: MIT |
| Kokoro-82M (kokoro-onnx) | narration | Apache-2.0 (code + weights) | |
| Windows SAPI voices | fallback narration | Windows licence | |
| noisereduce | voice cleanup | MIT | |
| MediaPipe / OpenCV | face tracking | Apache-2.0 | |
| librosa | beat detection | ISC | |
| OpenTimelineIO (format only) | timeline export | Apache-2.0 | the studio writes the JSON itself; the library is not required |
| Claude Code, Codex, Gemini CLI, Qwen Code | AI director | proprietary / Apache-2.0 (Gemini, Qwen, Codex CLI code) | you use your own accounts; the providers' terms apply |
| Ollama | local LLM | MIT | model weights carry their own licences |

## Free stock footage (optional, 1.9.0)

Takebloom bundles no stock footage and no stock code. With the person's own free key (`stock.pexels_key`,
`stock.pixabay_key`, or the `PEXELS_API_KEY` / `PIXABAY_API_KEY` environment variables) an edit can search and download
a few clips at the person's request. Only `urllib` from Python's standard library is used.

| Source | Content licence | What Takebloom does to respect the terms |
|---|---|---|
| Pexels API (`api.pexels.com/v1/videos/search`) | [Pexels Licence](https://www.pexels.com/license/): free to use and modify, including commercially; no attribution required. Not allowed: selling unaltered copies, or offering the clips as a stock or wallpaper service. | Clips are used only inside the person's own film. Every clip used is listed in `exports/credits.txt` as "Video by NAME on Pexels" with the clip page, as the [API guidelines](https://www.pexels.com/api/documentation/) ask ("credit when possible" and show where content comes from). Default limits: 200 requests an hour, 20,000 a month. |
| Pixabay API (`pixabay.com/api/videos/`) | [Pixabay Content Licence](https://pixabay.com/service/license-summary/): free to use, including commercially; no attribution required. Not allowed: selling or distributing the content on a standalone basis. | Search results are cached for 24 hours as the [API docs](https://pixabay.com/api/docs/) ask; clips are downloaded (not hotlinked) in a small size (never the 4K "large" copy); the source is named in the log and in `exports/credits.txt`. Default limit: 100 requests a minute. |

The keys are free and need no payment. The person gets them on the sites themselves; Takebloom never asks for an
account. Keys are kept in the app settings file, never in code or logs (`video-studio config list` shows them only as
"(set, hidden)").

## Rejected or flagged during research (see research/raw_findings.md)

- **Remotion:** not open source (company licence above 3 employees).
- **XTTS / Coqui weights:** CPML, non-commercial.
- **Piper:** the MIT repo is archived, piper1-gpl is GPL, and voice licences vary per voice.
- **edge-tts:** uses an unofficial Microsoft endpoint, so it is not used by default.
- **Olive / Natron:** dormant.
- **Generative video (Wan 2.2, LTX-Video, HunyuanVideo):** open weights, but they need a 12–24 GB+ GPU and can't run on the target laptop (16 GB RAM, Intel iGPU). Not integrated yet; see known limitations.
- **Music:** generated procedurally, so there are no sample or licence issues. A user-supplied `--music-file` is the user's responsibility.
