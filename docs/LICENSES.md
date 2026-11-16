# Licence report

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

## Rejected or flagged during research (see research/raw_findings.md)

- **Remotion:** not open source (company licence above 3 employees).
- **XTTS / Coqui weights:** CPML, non-commercial.
- **Piper:** the MIT repo is archived, piper1-gpl is GPL, and voice licences vary per voice.
- **edge-tts:** uses an unofficial Microsoft endpoint, so it is not used by default.
- **Olive / Natron:** dormant.
- **Generative video (Wan 2.2, LTX-Video, HunyuanVideo):** open weights, but they need a 12–24 GB+ GPU and can't run on the target laptop (16 GB RAM, Intel iGPU). Not integrated yet; see known limitations.
- **Music:** generated procedurally, so there are no sample or licence issues. A user-supplied `--music-file` is the user's responsibility.
