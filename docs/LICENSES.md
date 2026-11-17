# Licence report

Provider logos are from [LobeHub Icons](https://github.com/lobehub/lobe-icons), MIT. The licence is kept in `video_studio/assets/provider-icons/LICENSE` and bundled in the packaged app. Provider names and marks belong to their respective owners.

Video Studio's own code is MIT. It **does not bundle** third-party tools except the Python runtime, NumPy and Pillow inside the frozen build. Everything else is detected or installed separately, with your approval, under its own licence.

| Component | Use | Licence | Notes |
|---|---|---|---|
| Python runtime (PyInstaller build) | app runtime | PSF | PyInstaller bootloader: GPL-2.0 with an exception that allows any licence for the bundled app |
| NumPy | audio/image math | BSD-3-Clause | bundled |
| Pillow | motion graphics | MIT-CMU (HPND) | bundled |
| FFmpeg 9.0.2 (gyan.dev essentials build) | all encoding/editing | **GPL-3.0** (build includes GPL libs such as x264) | not bundled. Setup downloads the portable zip (115 MB) from github.com/GyanD/codexffmpeg (mirror gyan.dev), checks its SHA-256 and unpacks it into Takebloom's tools folder as a separate program; winget is only a fallback. Shipping it inside a closed product would trigger the GPL. |
| Blender 5.2.4 LTS | 3D (optional) | GPL-3.0-or-later | separate program, called as a subprocess; scene_builder.py runs inside it. Optional in setup (`setup --with blender`): portable zip 406 MB from download.blender.org, SHA-256 checked |
| HyperFrames 0.9.52 | optional HTML motion engine | Apache-2.0 | via npx; uses its own vendored GSAP (GSAP licence: free "no charge" standard licence since 2025) |
| Node.js 24.22.3 LTS | runtime for HyperFrames / AI CLIs | MIT | setup downloads the portable zip (38 MB) from nodejs.org, SHA-256 checked against SHASUMS256.txt; npm cache kept inside the tools folder |
| faster-whisper / CTranslate2 | transcription | MIT | Whisper model weights: MIT |
| Kokoro-82M (kokoro-onnx) | narration | Apache-2.0 (code + weights) | |
| Windows SAPI voices | fallback narration | Windows licence | |
| noisereduce | voice cleanup | MIT | |
| MediaPipe / OpenCV | face tracking | Apache-2.0 | |
| MediaPipe selfie-multiclass model | fast background cut-out | Apache-2.0 | downloaded by setup (16 MB) |
| BiRefNet lite (swin-v1-tiny, general) ONNX, [ZhengPeng7/BiRefNet](https://github.com/ZhengPeng7/BiRefNet) | optional best-quality cut-out (1.9.0) | MIT | 214 MB, downloaded only when you choose Best quality and allow it, from the publisher's GitHub release; SHA-256 `5600024376f572a557870a5eb0afb1e5961636bef4e1e22132025467d0f03333` is checked (the publisher gives none; computed from the release file) |
| onnxruntime | runs BiRefNet lite | MIT | already installed with kokoro-onnx in the helper Python |
| librosa | beat detection | ISC | |
| OpenTimelineIO (format only) | timeline export | Apache-2.0 | the studio writes the JSON itself; the library is not required |
| Claude Code, Codex, Gemini CLI, Qwen Code | AI director | proprietary / Apache-2.0 (Gemini, Qwen, Codex CLI code) | you use your own accounts; the providers' terms apply |
| Ollama 0.40.16 | local LLM (optional) | MIT | model weights carry their own licences. Optional in setup (`setup --with ollama`): portable zip 1.6 GB from github.com/ollama/ollama releases, SHA-256 checked against sha256sum.txt |
| qwen3:1.7b (via Ollama) | offline AI editor model (optional) | Apache-2.0 | about 1.4 GB, pulled by Ollama only with `setup --with ollama`. Qwen2.5-3B was avoided: its licence is non-commercial |
| uv 0.13.8 (Astral) | installs Takebloom's private Python 3.12 | MIT or Apache-2.0 | setup downloads the zip (16 MB) from github.com/astral-sh/uv, SHA-256 checked; uv checks the Python download against its own built-in hashes |
| Python 3.12 (python-build-standalone, via uv) | private Python for the AI helpers | PSF (plus the licences of the libraries it is built with) | separate program in the tools folder; never linked into Takebloom |
| faster-whisper 'small' model (Systran) | default speech model | MIT | 486 MB from Hugging Face on first setup; 'large-v3-turbo' (1.6 GB, MIT) only with `setup --with speech-large` |
| MediaPipe selfie multiclass model | cut-out for words behind you | Apache-2.0 | 16 MB from storage.googleapis.com |

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
- **Cut-out models not used (1.9.0):** MODNet, RMBG-2.0 and MatAnyone are on the excluded list (non-commercial terms on the weights); the larger BiRefNet files (940 MB to 1.1 GB) are too heavy for the target laptop.
- **Music:** generated procedurally, so there are no sample or licence issues. A user-supplied `--music-file` is the user's responsibility.
