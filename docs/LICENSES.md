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
| librosa | beat detection | ISC | |
| OpenTimelineIO (format only) | timeline export | Apache-2.0 | the studio writes the JSON itself; the library is not required |
| Claude Code, Codex, Gemini CLI, Qwen Code | AI director | proprietary / Apache-2.0 (Gemini, Qwen, Codex CLI code) | you use your own accounts; the providers' terms apply |
| Ollama 0.40.16 | local LLM (optional) | MIT | model weights carry their own licences. Optional in setup (`setup --with ollama`): portable zip 1.6 GB from github.com/ollama/ollama releases, SHA-256 checked against sha256sum.txt |
| qwen3:1.7b (via Ollama) | offline AI editor model (optional) | Apache-2.0 | about 1.4 GB, pulled by Ollama only with `setup --with ollama`. Qwen2.5-3B was avoided: its licence is non-commercial |
| uv 0.13.8 (Astral) | installs Takebloom's private Python 3.12 | MIT or Apache-2.0 | setup downloads the zip (16 MB) from github.com/astral-sh/uv, SHA-256 checked; uv checks the Python download against its own built-in hashes |
| Python 3.12 (python-build-standalone, via uv) | private Python for the AI helpers | PSF (plus the licences of the libraries it is built with) | separate program in the tools folder; never linked into Takebloom |
| faster-whisper 'small' model (Systran) | default speech model | MIT | 486 MB from Hugging Face on first setup; 'large-v3-turbo' (1.6 GB, MIT) only with `setup --with speech-large` |
| MediaPipe selfie multiclass model | cut-out for words behind you | Apache-2.0 | 16 MB from storage.googleapis.com |

## Rejected or flagged during research (see research/raw_findings.md)

- **Remotion:** not open source (company licence above 3 employees).
- **XTTS / Coqui weights:** CPML, non-commercial.
- **Piper:** the MIT repo is archived, piper1-gpl is GPL, and voice licences vary per voice.
- **edge-tts:** uses an unofficial Microsoft endpoint, so it is not used by default.
- **Olive / Natron:** dormant.
- **Generative video (Wan 2.2, LTX-Video, HunyuanVideo):** open weights, but they need a 12–24 GB+ GPU and can't run on the target laptop (16 GB RAM, Intel iGPU). Not integrated yet; see known limitations.
- **Music:** generated procedurally, so there are no sample or licence issues. A user-supplied `--music-file` is the user's responsibility.
