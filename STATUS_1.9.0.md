# Status: 1.9.0 portable one-click setup (branch wt/setup)

Finished on 17 Nov 2026. Nothing is running. Everything is committed.

## Done and verified
- `video_studio/toolbox.py` (new): pinned downloads with SHA-256, resumable `.part`, retries and mirrors, safe unzip to a temp folder then rename, `.takebloom.json` stamp, process-only `activate()`, winget fallback (non-interactive), `install_python()` (uv -> Python 3.12 -> private venv -> `uv pip install`), `install_hyperframes()`, portable Ollama. `hashlib`, `urllib.request` and `zipfile` load only when something is downloaded (import cost of the module went from about 110 ms to about 30 ms; every command imports it through detect.py).
- `setup.py` (shared): the default plan is the editing engine only: FFmpeg, private Python + helpers, Kokoro files (exact sizes checked), cut-out model, Node.js, HyperFrames, speech model 'small'. Optional extras: `--with blender ollama speech-large`. Nothing for generation (PyTorch, CUDA, Wan) in any plan; the report no longer talks about text-to-video. The report names the speech model really on disk and suggests `--with speech-large` on PCs with 12 GB+ memory. Blender is listed as optional, not as a warning.
- `engines/ml.py` (shared, small): `hf_hub()` honours HF_HUB_CACHE / HF_HOME; `model_on_disk()` needs `model.bin` (a half-downloaded speech model does not count). `best_whisper_model()` and `setup.whisper_cached()` both use them.
- `detect.py` (shared, small): `VIDEO_STUDIO_PORTABLE_ONLY=1` hides every system FFmpeg/Node/Blender/Python (fresh-PC test switch; off by default). Python hint now says `video-studio setup`.
- `util.py` (shared, 2 lines): `which()` tries `.exe/.cmd/.bat` before the bare name on Windows. Without this, AI CLIs found in `%APPDATA%\npm` (stripped PATH, or an app started before the CLI was installed) failed with "WinError 193".
- `cli.py` (shared): `setup --with blender ollama speech-large`, `--skip`.
- `studio.py`, `index.html` (shared, small, from earlier commits): Setup button shows the plan (items, sizes, licences, total) and asks once.
- Tests: `tests/test_setup_toolbox.py` (21) + `tests/test_setup_oneclick.py` (16), offline, under 2 s. Full suite: 195 tests OK; `node tests/test_file_input.cjs` passes.

## Real hidden-tools run on this laptop (17 Nov 2026)
Runner: `%TEMP%\tb-setup` scratch home/tools/models/HF_HOME, PATH = `C:\Windows\System32;C:\Windows;...\WindowsPowerShell\v1.0`, `VIDEO_STUDIO_PORTABLE_ONLY=1`, all queued through `tb_heavy.py`.
- Before: `setup --report` showed FFmpeg, Node, HyperFrames, Python helpers all "not found". Plan: 8 items, 1.4 GB shown.
- `setup --yes`: 292.7 s in all. FFmpeg 14 s (SHA-256 ok), Python 3.12.16 + 8 helpers 70 s (uv; Python 21 MB, wheels about 330 MB; uv cache of 925 MB cleaned afterwards), Kokoro 8 s + 2 s, cut-out model 3 s, Node 6 s (SHA-256 ok), HyperFrames + chrome-headless-shell 85 s (went into the tools folder: `HYPERFRAMES_CACHE_DIR` is honoured), speech model 'small' 92 s. Disk afterwards: about 2.4 GB (tools 1.8 GB, models 131 MB, speech model 464 MB).
- After: re-scan in the same process and `doctor` show FFmpeg 9.0.2 essentials, Node 24.22.3, HyperFrames 0.9.52, faster-whisper 1.2.1 all "portable". The FFmpeg essentials build passes `check_ffmpeg`.
- Real edit, 10 s synthetic clip (testsrc2 + SAPI speech): `edit clip10.mp4 "short reel, remove mistakes and filler words, add captions" --no-ai` -> 2.6 s master, QA 95/100 (one loudness warning), 21 words transcribed. Clean run: 3 min 10 s (transcribe 28 s). The first run took 13 min 55 s because it ran at the same time as the HyperFrames test (see the queue note below).
- HyperFrames 2 s title through `engines/hyperframes.render_scene` with the portable Node: 115 s, 90 KB MP4. (The first try timed out at 300 s while the edit ran beside it; a direct run took 46 s.)

## Not done
- No truly clean PC or VM test (Windows 11 Home has no Windows Sandbox). The owner should run the packaged app on a fresh PC once before release.
- Ollama (1.6 GB + 1.4 GB model) and Blender (406 MB) were not downloaded for real; pins checked against publisher files only. `--hidethinking` on 0.40.16 is untested.
- No UI button for the optional extras (CLI only). Winget fallback unit-tested only.

## Note for the coordinator: queue race in tb_heavy.py
Two waiters can both take over a stale lock: each reads the dead PID, one unlinks and creates its lock, the other then unlinks that new lock and creates its own. My edit and my HyperFrames run both started at 11:44 this way. Fix: take over a stale lock with an atomic rename (or re-read the file and check it still holds the same PID before unlinking). Not changed by me (outside this worktree).

## Pinned downloads (SHA-256 checked against each publisher's checksum file on 17 Nov 2026)
| Tool | URL | Bytes | SHA-256 | Checksum source |
|---|---|---|---|---|
| FFmpeg 9.0.2 essentials (GPL-3.0) | https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.zip (mirror https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-9.0.2-essentials_build.zip) | 114768076 | 60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba | gyan.dev .../ffmpeg-9.0.2-essentials_build.zip.sha256 |
| uv 0.13.8 (MIT/Apache-2.0) | https://github.com/astral-sh/uv/releases/download/0.13.8/uv-x86_64-pc-windows-msvc.zip | 15529884 | 529e78a9cec6cd7e3e014f7ecd8bda0c113969d4ed0f2efeedb85c30943325b8 | the .zip.sha256 beside it |
| Blender 5.2.4 LTS (GPL-3.0-or-later, optional) | https://download.blender.org/release/Blender5.2/blender-5.2.4-windows-x64.zip | 405779142 | bbd78f8931dc940444da4ff2983374930f9f5c092ae0e330588e9760bb9d967c | blender-5.2.4.sha256 |
| Node.js 24.22.3 LTS (MIT) | https://nodejs.org/dist/v24.22.3/node-v24.22.3-win-x64.zip | 37762649 | e612af99f626ab415ea225af6bce5c971feb448cb5cae250bb1c3fa50c5f7d9f | SHASUMS256.txt |
| Ollama 0.40.16 (MIT, optional) | https://github.com/ollama/ollama/releases/download/v0.40.16/ollama-windows-amd64.zip | 1618244995 | 50fcf5ad54277f2fa5d173ebfc93bcb017adbc0c9ef2314daabc96ca9e6aa64e | sha256sum.txt |

Not pinned by hash: Kokoro model files (exact sizes 92361271 / 28214398 checked; the publisher gives no checksum), MediaPipe selfie model ('latest' URL), faster-whisper models (Hugging Face), PyPI wheels (uv), npm packages (npm integrity).
