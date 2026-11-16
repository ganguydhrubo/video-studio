# Status: 1.9.0 portable one-click setup (branch wt/setup)

Stopped on request on 17 Nov 2026. Nothing is running. Nothing has been downloaded for real yet.

## Done and verified
- `video_studio/toolbox.py` (new): pinned downloads with SHA-256, resumable `.part`, retries and mirrors, safe unzip to a temp folder then rename, `.takebloom.json` stamp (a tool without it is ignored), process-only `activate()` (portable node/ffmpeg put on this process's PATH; `npm_config_cache` and `HYPERFRAMES_CACHE_DIR` set inside tools), `refresh_path_from_registry()` after winget, winget fallback with `--accept-source-agreements --accept-package-agreements --silent --disable-interactivity`, `install_python()` (uv -> Python 3.12 -> private venv -> `uv pip install`; falls back to an existing/winget Python + `python -m venv`), `pip_install()`, `install_hyperframes()`, `ensure_ollama_server()` / `install_ollama()` (qwen3:1.7b, Apache-2.0).
- `detect.py` (shared): portable tools found first (`ffmpeg_path`, `blender_path`, `node_path`); `system_python()` returns the private venv first (only when `.takebloom-ready` exists), then old behaviour, then a winget Python under `%LOCALAPPDATA%\Programs\Python\Python3*`; `source()` labels portable/custom/system, stored in `ToolInfo.extra["source"]`; winget recipes carry the flags; new `install_summary()`, `install()` routes ffmpeg/blender/node/ollama/python/whisper/hyperframes through the toolbox; new `python` recipe.
- `setup.py` (shared): `plan()` (ffmpeg, private Python + helpers, models, node, HyperFrames, Blender, speech model; Ollama only with `--with ollama`), each item has size in bytes and licence; `plan_summary()`, `print_plan()`, `install_item()` (never raises), `install_all()` (ONE confirmation, step timing, failures do not stop the rest), `verify()` / `print_tools()` (where each tool came from), `run_setup(assume_yes, report_only, skip, extra)` re-scans in-process.
- `cli.py` (shared): `setup --skip blender --with ollama`; doctor shows a source column; Kokoro install uses `toolbox.pip_install` and `toolbox.download`.
- `studio.py` (shared): `install_info` uses `install_summary`; new GET `/api/setup/plan`; the System page "missing" flag uses `setup.plan()`.
- `index.html` (shared, small): Setup button fetches the plan and asks once (items, sizes, licences, total) before POST `/api/setup`.
- `ai.py` (shared): Ollama provider finds the portable `ollama.exe`, starts `ollama serve` for it, passes `--hidethinking` only to the portable copy.
- `engines/hyperframes.py` (shared, 2 lines): looks for vendored GSAP in `npm_config_cache` too.
- `packaging/video_studio.spec`: hidden import `video_studio.toolbox`.
- `tests/test_setup_toolbox.py`: 21 tests pass offline in about 1 s (checksum mismatch refused, resume with Range, server without Range, retry then mirror, offline error, atomic unpack + skip, half-unpacked ignored, unsafe zip refused, winget flags, winget fallback, no winget, private python preferred, portable detection + labels, activate, Ollama path, plan contents/skip/extra/ready PC, one confirmation, decline, never raises).

## Not done yet (exact next steps)
1. Run the full suite: `python -m unittest discover -s tests` and `node tests/test_file_input.cjs` (not run since these changes).
2. REAL fresh-PC run (check `curl -s http://127.0.0.1:7860/api/jobs` first): scratch `VIDEO_STUDIO_HOME`, `VIDEO_STUDIO_TOOLS`, `VIDEO_STUDIO_MODELS`, and PATH = `C:\Windows\System32;C:\Windows` only; run `python -m video_studio setup --yes`; record download size and time; then `video-studio doctor` (sources should say portable) and `video-studio photos` on 3 synthetic pictures, plus `npx hyperframes@0.9.52 --version` and one tiny render with the portable Node.
   Unverified until then: `uv venv --allow-existing`, `UV_PYTHON_INSTALL_BIN=0` / `UV_PYTHON_INSTALL_REGISTRY=0`, that mediapipe installs on uv's Python 3.12, that `hyperframes browser ensure` honours `HYPERFRAMES_CACHE_DIR`, the real HyperFrames size (estimate 260 MB in `setup.HYPERFRAMES_SIZE`), and whether the ffmpeg essentials build passes `check_ffmpeg` (needs xfade, subtitles, loudnorm, sidechaincompress, blackdetect, ebur128, libx264).
3. Ollama: the 1.6 GB zip and `--hidethinking` on 0.40.16 are not tested. Test it or say so in the limits.
4. Optional: Install buttons on the System page's tools table (today it shows the `video-studio install <tool>` text; the AI card's Install already uses the toolbox through `install ollama`).
5. Docs: `## Setup 1.9.0` at the END of `docs/KNOWN_LIMITATIONS.md` (it must also say a true clean-VM test was not possible: Windows 11 Home has no Windows Sandbox); add uv, portable FFmpeg/Blender/Node/Ollama and qwen3:1.7b rows to `docs/LICENSES.md` (the FFmpeg row still says "installed via winget").
6. Final report to the coordinator.

## Pinned downloads (SHA-256 checked against each publisher's checksum file on 17 Nov 2026)
| Tool | URL | Bytes | SHA-256 | Checksum source |
|---|---|---|---|---|
| FFmpeg 9.0.2 essentials (GPL-3.0) | https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.zip (mirror https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-9.0.2-essentials_build.zip) | 114768076 | 60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba | gyan.dev .../ffmpeg-9.0.2-essentials_build.zip.sha256 |
| uv 0.13.8 (MIT/Apache-2.0) | https://github.com/astral-sh/uv/releases/download/0.13.8/uv-x86_64-pc-windows-msvc.zip | 15529884 | 529e78a9cec6cd7e3e014f7ecd8bda0c113969d4ed0f2efeedb85c30943325b8 | the .zip.sha256 beside it |
| Blender 5.2.4 LTS (GPL-3.0-or-later) | https://download.blender.org/release/Blender5.2/blender-5.2.4-windows-x64.zip | 405779142 | bbd78f8931dc940444da4ff2983374930f9f5c092ae0e330588e9760bb9d967c | blender-5.2.4.sha256 |
| Node.js 24.22.3 LTS (MIT) | https://nodejs.org/dist/v24.22.3/node-v24.22.3-win-x64.zip | 37762649 | e612af99f626ab415ea225af6bce5c971feb448cb5cae250bb1c3fa50c5f7d9f | SHASUMS256.txt |
| Ollama 0.40.16 (MIT, optional) | https://github.com/ollama/ollama/releases/download/v0.40.16/ollama-windows-amd64.zip | 1618244995 | 50fcf5ad54277f2fa5d173ebfc93bcb017adbc0c9ef2314daabc96ca9e6aa64e | sha256sum.txt |

Notes: Blender 5.2 is the current LTS (5.3 is not LTS); the owner's PC already runs 5.2. Node 26 is also LTS now, but 24 was chosen because HyperFrames 0.9.52 is proven on Node 24. Qwen2.5-3B was avoided (its licence is non-commercial); qwen3:1.7b is Apache-2.0. Python 3.12 comes from uv's python-build-standalone download, which uv checks against its own built-in hashes.
