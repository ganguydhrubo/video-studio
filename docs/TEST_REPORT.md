# Test report — 15 Nov 2026, v1.0.0

**Machine:** Windows 11, i7-13620H, 16 GB RAM, Intel UHD iGPU. Tools: FFmpeg 9.0.1, Blender 5.2.3, Node 24.19, Python 3.13.

## Automated

| Suite | Result |
|---|---|
| `python -m unittest discover -s tests` | **14 / 14 pass**: captions (ordering, no SRT overlap, ASS events), planner, editor rules (brief parsing, retakes, Indic tokens, target fit), memory (taste update, failure memory), JSON extraction, music/SFX synthesis |

## End-to-end (real renders, verified output)

| Run | Result |
|---|---|
| **Footage edit**: owner's real 48 s iPhone HEVC clip (Hinglish), `--ai claude --caption-script roman --platform reels` | 42.3 s reel, QA **100/100**, -14.0 LUFS, -1.3 dBTP. Claude kept 8 of 10 lines. Roman captions checked on the contact sheet. About 6 min (transcription with large-v3-turbo took 1 m 48 s). |
| **Script → video**: `samples/local_first_studio.txt`, Claude plan, Kokoro voice, 2 Blender 3D scenes, draft quality, 3 platform exports | 36.9 s, QA **100/100**, 13 m 15 s total, 10 m of it Blender + Kokoro. **Visual issue:** the 3D title label and the captions overlap in the 3D scenes (see known limitations). |
| **Script → video**: no AI, no 3D, SAPI, run from the *installed* exe | 42.8 s in 28.5 s, QA 95 (1 caption-timing warning, since fixed and covered by a unit test) |
| **HyperFrames scene** (offline, vendored GSAP) | 4 s 1080×1920 clip rendered; frame checked |
| **Installer**: `VideoStudio-Setup-1.0.0.exe /Q`, then run, then uninstall | install: files, user PATH, Start menu and Apps entry all OK. Uninstall: everything removed, user PATH byte-identical, user data kept |
| **Studio UI** | create and result pages render (headless Chrome screenshots). Foreign Host header → 403. POST without token → 403. File outside workspace → 403. Range request → 206 |
| **AI CLIs** | Claude OK, Codex OK. Gemini **fails** (Google: personal login ineligible) and is correctly reported with its fix. Qwen not signed in. Ollama not installed |

## Not tested

- A real install on a second, clean PC.
- SmartScreen on a downloaded copy.
- Blender at `high` quality for a full video. It would take about 1 h+ per 3D scene on this iGPU.
- Ollama, Qwen.
- The ComfyUI adapter (not built).
