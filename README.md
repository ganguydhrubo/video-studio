# Video Studio

Local-first AI video production for Windows. Free, offline-capable, no paid APIs.

- **Works with no AI service at all.** A local editor brain (voice stress, key words, hook strength) and a
  video-sense pass (shots, sharpness, light, motion, people, music beats) decide the edit. No AI service is needed;
  a free AI editor reviews the story by default (transcript text only) and can be switched off with `--no-ai`.
- **Edit my footage**: drop your clips and describe the result ("45-second Hinglish reel, remove mistakes"). The studio:
  - watches the footage first and says what it understood (talking head, vlog, action, scenic/product, lifestyle)
  - talking videos: cuts mistakes, picks the hook, virtual camera angles, words behind you, pop-out hook card
  - no talking (travel, product, cars, events): a beat-synced montage of the best moments, on your own music if the clip has it
  - transcribes speech locally and removes silences, filler words and fumbled retakes
  - **builds a story**: cuts the self-intro, sign-off and repeats, opens cold on the strongest self-contained claim,
    keeps concrete examples (names, numbers) and ends on the payoff. Works in any spoken language: each line is
    translated to English on this PC to judge its meaning. The Studio shows "How the story was built", line by line.
  - **an AI senior editor reviews the story** (free AI by default, `--no-ai` to switch off): it reads every line and the
    draft, proposes a better cut with a reason for each change, and says what footage would show each line. Its cut is
    checked against editing rules before it is used; if it fails, the local draft stands.
  - **paces it**: holds a beat after strong lines and questions, runs weak lines tight
  - **B-roll** (`--broll <folder>`, or put clips in `%LOCALAPPDATA%\VideoStudio\broll`): matches what each kept line
    means to a moment in your own clips and cuts away for up to 2.8 s while your voice continues (never on the cold
    open, the payoff or a giant-text slam). Each clip is indexed once and cached.
  - picks the hook and the order, and trims to your target length
  - reframes to 9:16, 16:9, 1:1 or 4:5 with face tracking, and adds punch-in zooms
  - cleans the voice and colour-corrects the footage. It never replaces your background, and never brightens an already-bright face.
  - adds word-highlighted captions (native or Roman script) placed off your face and clear of the Reels/Shorts buttons,
    a title, keyword graphics and SFX
  - music: your own tracks first (put files you have rights to in `%LOCALAPPDATA%\VideoStudio\music`, optionally in
    mood folders like `uplifting\`), with the track's drop lined up to the hook; otherwise an original arranged bed in
    one of six genres (lo-fi, trap, house, cinematic, desi tabla + tanpura, pop) whose drop lands as the hook ends and
    which cuts out for a beat before each punch-word. A different key, tempo and progression every video.
  - every export comes with a ready-to-paste post (title, caption, hashtags), a cover frame and a Premiere/Resolve timeline
- **Your words** (`video-studio vocab add Sahkarmi Dhrubo`): names and brands are listened for and always spelled right.
- **Watch folder** (`video-studio watch <folder> "<brief>"`): every clip dropped in becomes a finished draft.
  - normalises loudness, QA-checks the result and exports for each platform
- **Script to video**: paste a script. The studio:
  - plans scenes (an AI director, or local rules)
  - narrates with Kokoro (or the Windows voice)
  - renders 3D scenes in Blender (cinematic or toon), motion graphics (built-in canvas or HyperFrames), music and SFX
  - assembles, QA-checks and exports
- **One long video -> many shorts** (`video-studio shorts`): finds the best complete stretches and fully edits each one,
  with a plain-English reason per clip.
- **Learns any style you show it** (`video-studio learn-style <folder> --name ...`): give it reels you love, of any genre;
  it measures their cut rhythm, beat sync, punch-ins and colour on this PC and edits in that style (`--like name`).
  Only numbers are kept; the videos are never copied or uploaded.
- **Review before you publish**: the Studio opens each project in a workspace - player, a timeline of the edit (your lines, B-roll, big text, cuts, and where each moment sits in the recording), and tabs for the story notes, every spoken line, look and sound, quality checks, exports and rating. Click a line on the timeline or tick it in the list to keep or cut it, then re-edit.
- **Learns your taste**: rate each video 1–5 stars and add notes. A Bayesian preference model, plus searchable project memory, steers the next video. You can review or delete everything (`video-studio memory ...`). No AI model is trained or fine-tuned.

## Install

1. Run `VideoStudio-Setup-1.3.0.exe`. It installs per user, needs no admin rights, adds itself to PATH and creates a Start-menu entry called "Video Studio". Uninstall from Settings › Apps; your projects and memory are kept.
2. Run `video-studio setup` (or open the Studio's System page). It scans this PC, explains in plain English what it can
   and cannot edit, and installs everything it needs after one confirmation (~1.5 GB, all free and open source).
   `video-studio setup --report` only scans. `video-studio doctor` lists every tool with the exact fix:
   - **Required:** FFmpeg (`video-studio install ffmpeg`, via winget).
   - **Recommended:** Blender for 3D (`install blender`), Python 3.11+ with the ML extras for transcription, voice cleanup and face tracking (`install whisper`), and Kokoro natural voices (`install kokoro`).
   - **Optional:** Node.js plus HyperFrames (`install hyperframes`), and an AI brain (see `docs/AI_CLI.md`). The default
     is **OpenCode's free models** (`install opencode`, no account). Ollama runs free and offline. Gemini and Qwen are
     free with an account. Claude and Codex need a paid plan. The Studio's System page has an "AI brain" card with an
     Install button, a Sign-in button and a free-model picker for each one. Sign-in always opens the provider's own login;
     Video Studio never sees your password or tokens.

   Every install shows the plan and the licence first, then asks before it runs.

A portable zip (`VideoStudio-1.3.0-portable.zip`) is also built. To run from source, use `pip install -e .` and then `video-studio ...`.

## Use

```text
video-studio studio                                   # the visual app (local web UI at 127.0.0.1)
video-studio edit clip1.mp4 clip2.mov "60s reel, remove mistakes, bold captions"
video-studio edit talk.mov "Hinglish reel" --caption-script roman --target 45 --platform reels shorts
video-studio edit trip.mp4 "Goa trip, 20 seconds" --like travel-cinematic     # no talking: beat montage
video-studio shorts podcast.mp4 "viral Hinglish reel" --count 5 --min 20 --max 58
video-studio learn-style "C:\Videos\reels I love" --name travel-cinematic && video-studio styles
video-studio learn                                    # keep/cut model from your finished edits (tested first)
video-studio setup [--report]
video-studio make script.txt --instructions "warm explainer, 3D hook" --aspect 9:16
video-studio init my-video && video-studio analyze && video-studio storyboard && video-studio produce \
  && video-studio render && video-studio validate && video-studio export --platform shorts youtube
video-studio resume [--from plan]                     # every stage is resumable and cached
video-studio feedback 5 --notes "loved the captions, music a bit loud"
video-studio edit talk.mov "viral Hinglish reel" --fx viral --broll "C:\Videos\my b-roll"
video-studio ai status | ai login claude | ai use opencode | ai test
```

The script format is plain paragraphs (one scene each), with optional tags: `# Title`, `[3D: rocket]`, `[STAT: 70% | label]`, `[QUOTE: text - Author]`, bullet lists, `[IMAGE: path]`, `[CHART: ...]`, `[CTA: ...]` and `[HYPERFRAMES]`.

## Outputs (per project)

| File | What it is |
|---|---|
| `output/master.mp4` | The finished video |
| `exports/` | Platform cuts, thumbnail, SRT/VTT captions, and an `.otio` timeline for finishing in DaVinci Resolve or Kdenlive |
| `output/qa/qa_report.md`, `contact_sheet.jpg` | Automated quality report |
| `storyboard.md` / `edit_plan.md`, `transcript.txt` | Human-readable plan |
| `project.json`, `build/` | Reproducible project file and cached intermediates (`video-studio clean` frees space) |

## Docs

- [Architecture](docs/ARCHITECTURE.md)
- [AI CLI integration](docs/AI_CLI.md)
- [Licences](docs/LICENSES.md)
- [Known limitations and recovery](docs/KNOWN_LIMITATIONS.md)
- [Test report](docs/TEST_REPORT.md)
- [Research notes](docs/research/raw_findings.md), [editing grammar](docs/research/editing_grammar.md),
  [edit styles catalog](docs/research/edit_styles_catalog.md), [competitors](docs/research/competitors.md)
