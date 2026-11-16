# Competitor research: open-source editors that ship a Windows exe

Date: 15 Nov 2026. Method: GitHub API (`gh api repos/...`, `gh api repos/.../releases`), READMEs, and the most-commented issues of each repo. Star counts and release dates were read on 15 Nov 2026. Where something comes from a README rather than our own test, it says so. None of these tools were installed or run for this pass.

**Scope rule.** A competitor counts only if it is open source on GitHub **and** a Windows `.exe`/installer (or a portable Windows archive with an exe inside) is attached to its GitHub Releases. Projects that fail the rule are listed in section 2 so nobody re-checks them.

---

## 1. Verified competitors (Windows binary on GitHub Releases)

### 1a. Direct rivals: "footage in, publish-ready video out"

| Project | Stars | Last release | Licence | Windows asset |
|---|---|---|---|---|
| [xixihhhh/hotclip](https://github.com/xixihhhh/hotclip) (HotClip) | 463 | v0.33.0, 2026-10-26 | AGPL-3.0 | `HotClip-0.33.0-win-x64.exe` + portable zip |
| [ColinGPT9/clips-studio](https://github.com/ColinGPT9/clips-studio) (Clips Kitty) | 253 | v2.1.0, 2026-10-21 | AGPL-3.0 | `ClipsKitty-Web-Setup-2.1.0.exe` |
| [0xsline/OpenChatCut](https://github.com/0xsline/OpenChatCut) | 3,122 | v0.2.17, 2026-10-15 | AGPL-3.0 | `OpenChatCut-0.2.17-x64.exe` |
| [Forward-Future/scarlet](https://github.com/Forward-Future/scarlet) | 253 | v0.1.100, 2026-11-15 | MIT | `Scarlet-0.1.100-Windows-x64-Setup.exe`, `.msi` |
| [WeftCut/WeftCut](https://github.com/WeftCut/WeftCut) | 335 | v0.7.1, 2026-11-11 | MIT | `WeftCut-win-x64.exe` |
| [harry0703/MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo) | 143,654 | v1.3.10, 2026-11-07 | MIT | `MoneyPrinterTurbo-Portable-Windows-1.3.10.7z` |
| [krillinai/OpenCreator](https://github.com/krillinai/OpenCreator) (formerly KrillinAI) | 14,362 | v3.3.1, 2026-11-08 | Apache-2.0 | `OpenCreator-3.3.1-win-x64.exe`, KrillinAI CLI/Server zips |
| [WyattBlue/auto-editor](https://github.com/WyattBlue/auto-editor) | 5,904 | 31.9.0, 2026-11-13 | Unlicense | `auto-editor-windows-x86_64.exe` (+ arm64) |

### 1b. Adjacent tools (one stage of our pipeline, or a manual NLE)

| Project | Stars | Last release | Licence | Windows asset | Role |
|---|---|---|---|---|---|
| [mifi/lossless-cut](https://github.com/mifi/lossless-cut) | 46,048 | v3.70.2, 2026-10-28 | GPL-2.0 | `LosslessCut-win-x64.7z` | lossless trim/segment |
| [mltframework/shotcut](https://github.com/mltframework/shotcut) | 15,685 | v26.10.30, 2026-10-31 | GPL-3.0 | `shotcut-win64-26.10.30.exe` | manual NLE |
| [OpenShot/openshot-qt](https://github.com/OpenShot/openshot-qt) | 6,786 | v4.1.1, 2026-11-02 | GPL-3.0 (API: NOASSERTION) | `OpenShot-v4.1.1-x86_64.exe` | manual NLE |
| [SubtitleEdit/subtitleedit](https://github.com/SubtitleEdit/subtitleedit) | 15,002 | v5.5.0-beta6, 2026-11-14 | MIT | `SubtitleEdit-Windows-x64-Setup.exe` | captions + Whisper |
| [chidiwilliams/buzz](https://github.com/chidiwilliams/buzz) | 22,802 | v1.4.5, 2026-08-23 | MIT | `Buzz-1.4.5-windows.exe` | offline transcription |
| [tmoroney/auto-subs](https://github.com/tmoroney/auto-subs) (AutoSubs) | 4,568 | v3.11.0, 2026-09-29 | MIT | `AutoSubs-windows-x86_64.exe` | captions into Resolve/Premiere |
| [WEIFENG2333/VideoCaptioner](https://github.com/WEIFENG2333/VideoCaptioner) | 16,527 | v1.4.2, 2026-05-24 (exe last attached to v1.3.3, 2025-03-02) | GPL-3.0 | `VideoCaptioner-Setup-win64-v1.3.3.exe` | LLM caption pipeline |
| [YaoFANGUK/video-subtitle-remover](https://github.com/YaoFANGUK/video-subtitle-remover) | 13,732 | 1.4.0, 2026-04-09 | Apache-2.0 | `VSR_v1.4.0_windows_x64_cpu_Setup.exe` | removes burned-in text |
| [olive-editor/olive](https://github.com/olive-editor/olive) | 9,157 | 0.2.0-nightly, 2024-12-05 | GPL-3.0 | `Olive-...-Windows-x86_64.exe` | NLE, stalled |
| [ozmartian/vidcutter](https://github.com/ozmartian/vidcutter) | 2,000 | 6.0.5.1, 2021-06-16 | GPL-3.0 | `VidCutter-6.0.5.1-setup-win64.exe` | trimmer, stale |
| [mean00/avidemux2](https://github.com/mean00/avidemux2) | 990 | 2.8.1, 2022-09-21 (on GitHub) | GPL | `Avidemux_2.8.1.VC++.64bits.exe` | trimmer/encoder |

**Borderline.** [jianchang512/pyvideotrans](https://github.com/jianchang512/pyvideotrans) (19,750 stars, GPL-3.0, v4.18 on 2026-11-05) has no files attached to the release. Its release notes link a Windows 7z, containing `sp.exe`, on Hugging Face and Baidu Pan. It fails the letter of the rule but not its spirit. Treat it as a competitor for dubbing and translation.

## 2. Checked and excluded (no Windows binary on GitHub Releases)

- **FunClip** ([modelscope/FunClip](https://github.com/modelscope/FunClip), 6,554 stars, MIT). The v2.2.1 assets are only source zips: "does not contain runtime dependencies or model weights".
- **AutoCut** ([mli/autocut](https://github.com/mli/autocut), 7,845 stars). No releases; last push Oct 2024.
- **VideoLingo** ([Huanshere/VideoLingo](https://github.com/Huanshere/VideoLingo), 19,259 stars). Releases have no assets; it is a source/Docker install.
- **NarratoAI** ([linyqh/NarratoAI](https://github.com/linyqh/NarratoAI), 11,829 stars). Its Windows "整合包" (all-in-one bundle) is distributed off GitHub.
- **Kdenlive** ([KDE/kdenlive](https://github.com/KDE/kdenlive)). The GitHub repo is a mirror with no releases; the Windows exe is on kde.org. It is still a strong manual NLE, with Whisper subtitles and object masking.
- **Flowblade.** Linux only; its releases have no Windows assets.
- **OpenCut** ([OpenCut-app/OpenCut](https://github.com/OpenCut-app/OpenCut), 98,382 stars) is a web app; its releases hold only FFmpeg builds. **Cutia** ([msgbyte/cutia](https://github.com/msgbyte/cutia), 900 stars) runs in the browser; no releases.
- **No releases at all:** [Anil-matcha/AI-Youtube-Shorts-Generator](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator) (6,084), [mutonby/openshorts](https://github.com/mutonby/openshorts) (8,332), [RayVentura/ShortGPT](https://github.com/RayVentura/ShortGPT) (8,171), [FujiwaraChoki/MoneyPrinter](https://github.com/FujiwaraChoki/MoneyPrinter) (14,201), [Shaarav4795/ClippedAI](https://github.com/Shaarav4795/ClippedAI), [fralapo/clippyme](https://github.com/fralapo/clippyme) and [ClipsAI/clipsai](https://github.com/ClipsAI/clipsai) (a library, dormant since Jan 2024).
- **Releases without binaries:** [MartinDelophy/ai-video-editor](https://github.com/MartinDelophy/ai-video-editor) (979) and [kurbaitaev/ghost-editor](https://github.com/kurbaitaev/ghost-editor) (538). [openreelio](https://github.com/openreelio/openreelio) ships only a macOS CLI and is pre-alpha.
- **Warning.** Searching GitHub for "video editor windows" returns many repos that are repacks of commercial editors, such as "PowerDirector-Video-Editor-Repack". Treat them as probable malware, not competitors.

---

## 3. What each verified rival does, and what it does better than us

### HotClip (the closest rival in depth)
A desktop app for clipping livestreams, podcasts and talking-head videos, aimed at Chinese platforms (Douyin, Bilibili, Xiaohongshu). It runs locally, needs no account and adds no watermark. Its README lists:
- **Cutting:** silence and breath jump-cuts, triple-checked (no word, acoustic silence, and VAD finds no voice) so soft tails and laughs survive; conservative filler and stutter removal; snapping to shot boundaries with TransNetV2; a second Paraformer alignment pass for word-accurate cuts.
- **Transcription:** three local engines (SenseVoice, Paraformer, FireRedASR2); a transcript you can correct by clicking; a **hot-word list** that fixes names across every future job; speaker diarisation (pyannote + 3D-Speaker).
- **Highlights:** nine evidence signals (text, loudness, shot density, motion, danmaku (viewer comment) density, YuNet+FER+ facial-expression peaks, an optional Ollama vision model). Each clip gets a virality score, a reason and a four-part sub-score. Your accepts and rejections feed a local preference profile, and imported view CSVs feed real performance back in.
- **Look:** "comfort-first" 9:16 framing that locks the shot and follows only when the subject would leave the frame; caption bubbles rendered in Chromium; Hormozi-style captions; speaker-coloured labels; bilingual subtitles; a cold-open hook and a 0.3–1 s flash-forward; SFX hits; BGM ducking; −14 LUFS loudness; an optional ~10 MB DPDFNet voice enhancer; HDR→SDR conversion; a conservative auto colour fix.
- **Workflow:** a review desk with draggable word-snapped handles; undo/redo saved with the project; platform safe-zone overlays (Douyin, TikTok, Reels, Shorts and others); per-platform publish packs (cover crops, captions, hashtags); a banned-word linter; brand presets; one-click landscape and portrait versions; EDL and CapCut/Jianying draft export; a watch folder that runs 24/7; a CLI, an MCP server and an Agent Skill; GPU encoding (NVENC/QSV/VideoToolbox) with x264 fallback; render cache and stream copy.

**Better than us:** almost everything on the workflow side (the review desk, hot words, diarisation, safe zones, publish packs, GPU encode, MCP, watch folder) and clipping long videos into many shorts. **Weaker:** its ASR engines target Chinese (SenseVoice covers five languages, none of them Hindi), so it does nothing for Hindi or Hinglish. It has no layered text-behind-subject style, no 3D, no script-to-video, and its tiny community (463 stars) means little real-world testing.

### Clips Kitty (clips-studio)
"The free, open-source Opus Clip alternative", on Windows 10/11.
- **Clip detection:** scores moments 0–100 by fusing speech, audio excitement, visual activity, on-screen reactions and hook strength.
- **Framing:** **speaker-aware reframing** (YOLOv8 pose + TalkNet active-speaker detection; it *cuts* to the new speaker rather than panning); podcast mode for multi-camera footage; vertical-live passthrough; sports modes that read the score box and follow the ball.
- **Captions and languages:** editable word-synced captions with speaker colours; 19 languages, **Hindi among them**; local AI dubbing.
- **Editing by chat:** an "AI edit chat" and an "Ask Clips Kitty" box ("clip this stream, include when I died to the boss, big yellow captions").
- **Publishing:** AI titles, descriptions and hashtags; Creator Profiles that learn each creator over time; branding and watermark profiles; publish straight to YouTube.

Requirements: 16 GB RAM, an RTX 2060 recommended (CPU works, slowly), Python, Node and Ollama, ~20 GB disk ([README §Requirements](https://github.com/ColinGPT9/clips-studio#requirements)).

**Better than us:** active-speaker reframing, many clips from one long video, chat-driven re-editing, Hindi dubbing, YouTube upload. **Weaker:** it needs a heavy GPU stack, and has no style pass comparable to ours (text behind subject, pop-out cards) and no script-to-video.

### OpenChatCut
An Electron, React and Remotion editor with "a professional multi-track timeline" and agents (the built-in agent, or Codex/Claude Code over MCP) that write real, editable clips.
- **Editing:** word-level transcript editing, pause handling, speakers, linked captions; transitions, effects, LUTs, keyframes.
- **Generation:** WebGL motion templates; image, video, speech and music generation; online media search.
- **Vision:** in-browser person segmentation and face-safe zones, so "captions avoid the speaker automatically… overlay graphics land in empty space".
- **Export:** MP4, captions and FCPXML.

**Better than us:** a real editable timeline after the AI pass, captions that avoid faces, FCPXML, MCP. **Weaker:** generation uses paid cloud APIs (the README is sponsored by API resellers), and the top open issue is "Any size local Whisper have error" ([#120](https://github.com/0xsline/OpenChatCut/issues/120), 18 comments). Users also report the agent saying "done" with an empty timeline ([#129](https://github.com/0xsline/OpenChatCut/issues/129)).

### Scarlet
An early-access Tauri app (MIT, macOS/Windows/Linux) for dialogue-heavy footage, driven through Claude Code or Codex by voice or text.
- **Agent edits:** "remove the false starts and repeated takes"; a `/dialogue-pass` command; it renders only the changed section so you hear the join; you approve or skip each flagged cut; press `\` to hear removed audio.
- **Transcription:** CrisperWhisper (verbatim, keeps fillers) or Whisper large-v3-turbo, plus acoustic filler detection from Desert Ant Labs.
- **Export:** MP4 or OTIO.

It sends anonymous telemetry to PostHog by default.

**Better than us:** per-cut review with audio auditioning of the join, and a verbatim ASR that catches fillers better than standard Whisper. **Weaker:** English-only word alignment ([#105](https://github.com/Forward-Future/scarlet/issues/105)), no styling, captions or reframing pass, needs a paid agent subscription, and is very early.

### WeftCut
An MIT editor built on PixiJS and WebCodecs with a full timeline: ripple delete, keyframes with bézier curves, role-based audio mixer, chroma key, shot detection, vision descriptions, pause removal, H.264/HEVC/AV1 with hardware encoders. An MCP agent edits the open project, and every batch can be undone. Transcription uses a cloud provider. **Better than us:** a manual editing surface and agent-safe undo. **Weaker:** no auto "brief to finished reel" pipeline, and cloud ASR.

### MoneyPrinterTurbo (biggest by stars)
Topic → LLM script → stock or AI footage (Pexels, Pixabay, Coverr, many paid text-to-video APIs) → TTS (Edge TTS free, Kokoro, VoxCPM voice cloning, ElevenLabs and others) → Whisper or TTS-timed captions with word pop animation → BGM → 9:16, 16:9 or 1:1. It also has batch generation and **auto-upload to TikTok, Instagram and YouTube Shorts** with LLM-written titles and hashtags. It ships a Windows portable 7z. **Better than us:** stock B-roll search, batch output, direct upload, wide TTS choice, and huge community reach. **Weaker:** it does not edit your own footage; it needs cloud LLM/TTS keys for good results; and its top issues are TTS failures (Edge TTS 403 errors: [#514](https://github.com/harry0703/MoneyPrinterTurbo/issues/514), [#570](https://github.com/harry0703/MoneyPrinterTurbo/issues/570), [#828](https://github.com/harry0703/MoneyPrinterTurbo/issues/828)) and slow encoding without NVENC ([#171](https://github.com/harry0703/MoneyPrinterTurbo/issues/171)).

### OpenCreator (KrillinAI)
A Codex-native desktop app: video translation and dubbing, a subtitle editor with waveform, portrait-mode conversion, a video downloader (yt-dlp), image and video generation, templates, 43 UI languages. **Better than us:** translation and dubbing, the subtitle editor, the downloader. **Weaker:** the agent needs Codex/ChatGPT credentials, and users hit FFmpeg and Edge TTS errors ([#106](https://github.com/krillinai/OpenCreator/issues/106), [#272](https://github.com/krillinai/OpenCreator/issues/272)).

### auto-editor
A CLI that cuts by audio loudness and/or **motion** (`--edit "(or audio:0.03 motion:0.06)"`), can speed up rather than cut, and exports to Premiere, Resolve, Final Cut and Shotcut timelines. It is a single exe and very fast. **Better than us:** motion-based cutting, NLE timeline export, raw speed, zero setup. **Weaker:** no transcription, captions, framing or style. Users complain that it "cuts words instead of silence" ([#582](https://github.com/WyattBlue/auto-editor/issues/582)) and that output files come out larger than the input ([#58](https://github.com/WyattBlue/auto-editor/issues/58)).

### Adjacent tools, briefly
- **LosslessCut:** instant lossless trims and segment export; detects silence and black scenes. Its #1 request, open since 2019, is "smart cut" ([#126](https://github.com/mifi/lossless-cut/issues/126), 143 comments), followed by batch processing ([#89](https://github.com/mifi/lossless-cut/issues/89)).
- **Shotcut and OpenShot:** mature manual NLEs with transitions, keyframes and titles. OpenShot's long-running complaints are audio crackle ([#1069](https://github.com/OpenShot/openshot-qt/issues/1069), 110 comments), A/V desync ([#1290](https://github.com/OpenShot/openshot-qt/issues/1290)) and crashes ([#13](https://github.com/OpenShot/openshot-qt/issues/13)).
- **Subtitle Edit, Buzz, AutoSubs:** best-in-class caption editing and offline Whisper GUIs. AutoSubs puts animated captions and speaker labels straight onto a Resolve timeline.
- **pyvideotrans:** ASR, translation, dubbing with voice cloning, and burned subtitles, in many languages. Its top issues are crashes and installation ([#193](https://github.com/jianchang512/pyvideotrans/issues/193), 67 comments; [#215](https://github.com/jianchang512/pyvideotrans/issues/215)) and poor F5-TTS dubbing ([#636](https://github.com/jianchang512/pyvideotrans/issues/636)).

---

## 4. Feature matrix

Legend: **Y** = has it · **P** = partial or weak · **–** = no · **?** = not confirmed. VS = Video Studio (from our README and code; a grep of `video_studio/` found no MCP, dubbing, diarisation, NVENC/QSV, FCPXML/EDL, stock-media or safe-zone code).

| Feature | VS | HotClip | Clips Kitty | OpenChatCut | Scarlet | WeftCut | MPT | OpenCreator | auto-editor |
|---|---|---|---|---|---|---|---|---|---|
| Edit own footage from a text brief | **Y** | P (preset modes) | P (Ask box) | Y (agent) | Y (agent) | Y (MCP) | – | P | – |
| Silence removal | Y | Y (VAD triple check) | ? | Y | Y | Y | – | – | **Y** |
| Filler removal | Y | Y | ? | P | **Y** (acoustic) | – | – | – | – |
| Retake / false-start removal | P (lexical) | P | – | P | **Y** | – | – | – | – |
| Motion-based cuts | – | P (motion signal) | Y (sports) | – | – | P | – | – | **Y** |
| Local transcription | Y (Whisper) | Y (3 engines) | Y | P (buggy) | Y | – (cloud) | Y | Y | – |
| Hindi captions | **Y** | – | Y | ? | – | ? | Y | Y | – |
| Hinglish Roman-script captions | **Y** | – | – | – | – | – | – | – | – |
| Custom vocabulary / hot words | P (built-in loanwords) | **Y** (user list) | – | – | – | – | – | – | – |
| Click-to-fix transcript UI | – | Y | Y | Y | Y | Y | – | Y | – |
| Speaker diarisation | – | Y | Y (TalkNet) | Y | ? | – | – | – | – |
| Hook selection | Y | **Y** (cold open, flash-forward) | Y | P | – | – | – | – | – |
| Many clips from one long video | – | **Y** | **Y** | P | – | – | – | – | – |
| Virality score with reasons | – | Y | Y | – | – | – | – | – | – |
| Face-tracked 9:16 reframe | Y | Y (comfort lock) | **Y** (active speaker) | Y | – | – | crop only | Y | – |
| Virtual camera angles / punch-ins | **Y** | P | P | P (keyframes) | – | P | – | – | – |
| Text behind subject (matte) | **Y** | – | – | P (segmentation) | – | – | – | – | – |
| Captions avoid face / platform safe zones | ? | **Y** (safe-zone overlays) | ? | **Y** (face-safe) | – | – | – | – | – |
| Animated caption styles | Y | Y (bubble, Hormozi) | Y | Y | – | P | Y | Y | – |
| Transitions / SFX hits | Y | Y | ? | Y | – | Y | Y | – | – |
| Motion graphics / 3D | **Y** (HyperFrames, Blender) | – | – | Y (WebGL templates) | – | Y (Motifs) | – | P | – |
| Music bed + ducking | Y | Y | ? | Y | – | Y (mixer) | Y | – | – |
| Loudness normalisation | Y | Y | ? | ? | ? | P | – | – | – |
| Voice clean-up | Y | Y (DPDFNet) | ? | ? | – | Y | – | – | – |
| TTS / script-to-video | **Y** (Kokoro, Blender) | – | – | Y (cloud) | – | Y | **Y** | Y | – |
| Voice cloning | – | – | – | ? | – | – | Y | Y | – |
| Dubbing / translation | – | P (bilingual subs) | **Y** (19 languages) | ? | – | – | P | **Y** | – |
| Stock B-roll search | – | – | – | Y | – | – | **Y** | – | – |
| Editable timeline after AI pass | – (JSON + resume) | P (review desk) | P | **Y** | **Y** | **Y** | – | P | – |
| Hear removed bits / per-cut approve | – | Y | – | – | **Y** | – | – | – | – |
| Chat re-edit after first render | P (AI CLI) | – | Y | Y | Y | Y | – | Y | – |
| Learns your taste | **Y** (ratings + notes) | Y (accept/reject + view CSV) | Y (Creator Profiles) | – | – | – | – | P (memory) | – |
| Automated QA | **Y** | **Y** (self-repair) | ? | – | – | Y (conformance tests) | – | – | – |
| Titles / hashtags / cover per platform | P (thumbnail) | **Y** | Y | – | – | – | Y | – | – |
| Direct upload to YouTube / IG / TikTok | – | – | Y (YouTube) | – | – | – | **Y** | – | – |
| NLE hand-off | P (OTIO) | Y (EDL, Jianying) | – | Y (FCPXML) | Y (OTIO) | – | – | – | **Y** (Premiere, Resolve, FCP) |
| GPU encode (NVENC / QSV) | – | **Y** | ? | ? | ? | Y | P | ? | ? |
| Batch / watch folder | – | **Y** | Y (queue) | – | – | – | Y | – | Y |
| MCP server for agents | – | Y | – | Y | P | Y | Y (agent) | Y | – |
| Fully offline core | **Y** | Y | Y (Ollama) | P | P (cloud agent) | – (cloud ASR) | P | P | **Y** |
| One-click install with dependencies bundled | P (doctor + separate installs) | **Y** | P (web setup) | Y | Y | Y | Y (portable) | Y | **Y** |
| Code-signed | – | ? | ? | ? | ? | ? | ? | ? | ? |
| Runs on 16 GB laptop without NVIDIA GPU | Y (slow) | Y | P (slow) | ? | Y | Y | Y | Y | Y |

**Where we are honestly ahead:**
- Hinglish Roman-script captions; no verified rival offers them.
- A combined layered style pass: text behind the subject, a pop-out hook card, virtual camera angles.
- Script-to-video with local Blender 3D and HyperFrames.
- A fully offline core that runs on an Intel iGPU.
- A taste model you can inspect and delete.
- Built-in QA.

**Where we are behind:** review and editability, many clips from one long video, speaker-aware framing, speed (GPU encode), install friction, publishing, and dubbing.

---

## 5. Gap list, ranked by user impact

Each gap names who does it best, then what "+10" (clearly better, not parity) would look like for us.

1. **Install friction.** HotClip, Scarlet and auto-editor are double-click installs with FFmpeg and models bundled. Ours needs `doctor` plus separate FFmpeg, Python, Whisper and Kokoro installs, and it is unsigned, so SmartScreen warns on first run. Every rival's top issue is installation (pyvideotrans #193, VideoLingo #233, FunClip #41, NarratoAI #21). **+10:** ship one offline bundle with an embedded Python runtime, FFmpeg, faster-whisper small/turbo, Kokoro and MediaPipe. On first launch, run a self-test video end to end and show green ticks. Fix failures with one click, without a terminal. Sign the installer (e.g. a free SignPath OSS plan) so SmartScreen does not warn.
2. **No review or editing surface after the AI pass.** OpenChatCut, WeftCut and Scarlet have real timelines; HotClip has a review desk. Ours means editing `edit_plan.json` and running `resume`. **+10:** a transcript-first review in the web UI. Every cut shows as struck-through words with its reason; click to restore; press a key to hear the join (Scarlet's best idea); re-render only the affected segment, in seconds, from the cache.
3. **One long video cannot become many shorts.** HotClip, Clips Kitty, FunClip and the shorts generators do this. **+10:** "Find N clips" mode. Rank candidates with a score and a one-line reason in Hinglish, using our taste model and hook rules. Batch-render all candidates in our full style pass, which no rival can match.
4. **Speaker-aware reframing.** Clips Kitty uses TalkNet active-speaker detection and cuts, never pans; HotClip locks framing for comfort. Ours tracks a single face. **+10:** active-speaker detection with lightweight CPU models, cuts on speaker change, comfort lock, a two-person split layout for podcasts, and a framing receipt in QA.
5. **Render speed and GPU encode.** HotClip auto-detects NVENC, QSV and VideoToolbox, stream-copies untouched segments and caches renders. MoneyPrinterTurbo users ask for NVENC (#171). Our target laptop has an Intel iGPU, so **QSV** (`h264_qsv`) is a free speed-up we do not use. **+10:** auto-detect QSV, NVENC and AMF with x264 fallback. Show a time estimate before rendering, and report actual vs estimate in QA.
6. **Chat re-editing after the first render.** Clips Kitty, OpenChatCut, Scarlet and WeftCut offer it. **+10:** a "change it" box on the result ("music lower, drop the 2nd retake, bigger hook") that maps to plan edits and re-renders only what changed. Expose the same operations as an **MCP server**, so Claude Code and Codex can drive it; HotClip, OpenChatCut and WeftCut already have MCP.
7. **Transcript correction and a user hot-word list.** HotClip has the best version: fix a name once and it is fixed in every future job. Ours has a built-in loanword list only. **+10:** click-to-fix captions in the UI, a per-user vocabulary (names, brands, Hinglish spellings) learned from those fixes, and fixes applied before captions are styled.
8. **Captions avoiding faces and platform UI.** OpenChatCut uses face-safe zones; HotClip has measured safe-zone overlays for each platform. **+10:** we already have the MediaPipe matte. Use it to place captions and cards automatically away from the face, check against Reels, Shorts and TikTok UI masks, and fail QA if text overlaps a face or the UI.
9. **Publishing kit.** HotClip builds per-platform packs; MoneyPrinterTurbo auto-uploads to TikTok, IG and YouTube; Clips Kitty publishes to YouTube. We export a thumbnail and captions. **+10:** a pack per platform with a Hinglish title, description and hashtags in the creator's own voice (from the taste memory), cover crops per aspect ratio, and an optional YouTube upload through the official API. Ask before uploading, per the owner's rule.
10. **Dubbing and translation.** Clips Kitty (19 languages, Hindi among them), OpenCreator, pyvideotrans and VideoLingo do this. **+10:** a one-click Hindi⇄English version of a finished reel (local Whisper → LLM translation → Kokoro or cloned TTS) with captions restyled to match. That would make us the only tool producing both a Hinglish original and an English cut from the same edit.
11. **Speaker diarisation.** HotClip, Clips Kitty and AutoSubs have it. **+10:** speaker-coloured captions, framing per speaker, and "keep only my lines" in the brief.
12. **Retake quality (our detection is lexical).** Scarlet uses CrisperWhisper plus acoustic filler detection and asks about doubtful cuts. **+10:** score each retake on fluency (rate, pauses, fillers, loudness), keep the best take, and show why in the review panel.
13. **NLE hand-off formats.** auto-editor exports Premiere, Resolve and FCP timelines; OpenChatCut exports FCPXML; HotClip exports EDL and Jianying drafts. We export OTIO only. **+10:** add FCPXML, Premiere XML and a CapCut draft, with captions and SFX on their own tracks.
14. **Stock B-roll.** MoneyPrinterTurbo and OpenChatCut have it. **+10:** optional Pexels and Pixabay search, keyed off the keywords our editor brain already picks. Cache downloads locally, fall back to Blender or HyperFrames when offline, and record licence info in QA.
15. **Batch and watch-folder automation.** HotClip has a 24/7 watch folder; MoneyPrinterTurbo batches. **+10:** a "drop folder" that turns each new recording into a draft reel overnight, held for the owner to approve.
16. **Motion and non-speech cutting.** auto-editor has motion thresholds; Clips Kitty has gaming and sports modes. **+10:** add a motion signal to the cut decision for B-roll and silent demos.
17. **Podcast audiograms.** HotClip turns audio-only sources into waveform videos. **+10:** an audiogram from HyperFrames templates in our style.
18. **Voice cloning for TTS.** MoneyPrinterTurbo (VoxCPM), pyvideotrans and OpenCreator have it. **+10:** an opt-in local clone of the owner's voice for pickups that fix a fumbled word inside footage. Mark the result as synthetic.
19. **Self-repair in QA.** HotClip re-fixes safe warnings and re-checks the result. **+10:** let QA auto-fix loudness, caption overflow and black frames, and re-run itself.
20. **Lossless fast path.** LosslessCut and HotClip stream-copy when nothing changes. **+10:** stream-copy rough cuts for previews; keep full re-encodes for finals only.

---

## 6. Recurring user pain points (opportunities)

1. **Install and dependency hell** is the top issue category in pyvideotrans ([#193](https://github.com/jianchang512/pyvideotrans/issues/193)), VideoLingo ([#233](https://github.com/Huanshere/VideoLingo/issues/233)), FunClip ([#41](https://github.com/modelscope/FunClip/issues/41), [#61](https://github.com/modelscope/FunClip/issues/61)), NarratoAI ([#77](https://github.com/linyqh/NarratoAI/issues/77), missing cuDNN DLL) and OpenCreator ([#106](https://github.com/krillinai/OpenCreator/issues/106)). Whoever is truly "double-click and it works offline" wins.
2. **Cloud TTS and API breakage.** Edge TTS 403 errors dominate MoneyPrinterTurbo's issues ([#514](https://github.com/harry0703/MoneyPrinterTurbo/issues/514), [#570](https://github.com/harry0703/MoneyPrinterTurbo/issues/570), [#828](https://github.com/harry0703/MoneyPrinterTurbo/issues/828)). NarratoAI fails on Google Cloud upload ([#29](https://github.com/linyqh/NarratoAI/issues/29)). Users ask for Ollama/local LLMs ([NarratoAI #85](https://github.com/linyqh/NarratoAI/issues/85)). Our local Kokoro and `--no-ai` path is a real selling point; say so loudly.
3. **Cuts that clip words.** Seen in auto-editor ([#582](https://github.com/WyattBlue/auto-editor/issues/582)), in VideoLingo's timing after long pauses ([#154](https://github.com/Huanshere/VideoLingo/issues/154)), and in clip boundaries that take lines out of context (HotClip's comparison table notes the complaint against SaaS tools). Word-boundary guards plus "hear the join" fix this.
4. **Slow transcription and rendering.** FunClip: "a 3-minute video still queued after an hour" ([#97](https://github.com/modelscope/FunClip/issues/97)). MoneyPrinterTurbo asks for NVENC ([#171](https://github.com/harry0703/MoneyPrinterTurbo/issues/171)). Clips Kitty needs 16 GB RAM and an RTX GPU. Our honest CPU figure (about 2 min per minute of speech on turbo) is a weakness too. QSV encoding and a faster default model would help.
5. **Agents that say "done" but did nothing.** OpenChatCut [#129](https://github.com/0xsline/OpenChatCut/issues/129) (empty timeline reported as success) and Scarlet [#106](https://github.com/Forward-Future/scarlet/issues/106) (tool-call timeouts). Our deterministic pipeline plus QA is the answer: never report success without QA passing.
6. **Captions lost after re-edit.** Clips Kitty [#120](https://github.com/ColinGPT9/clips-studio/issues/120): making a video longer with AI edit dropped its captions. Captions must be regenerated from the cut map on every re-render.
7. **NLE-grade reliability.** OpenShot's audio crackle and desync ([#1069](https://github.com/OpenShot/openshot-qt/issues/1069), [#1290](https://github.com/OpenShot/openshot-qt/issues/1290)) and LosslessCut's smart-cut request ([#126](https://github.com/mifi/lossless-cut/issues/126)) show that users notice A/V sync and audio artefacts above all. Keep our A/V sync and loudness checks in QA.
8. **Non-English gaps.** Scarlet is English-only ([#105](https://github.com/Forward-Future/scarlet/issues/105)); HotClip's engines are Chinese-first. Users volunteer translations for Clips Kitty (Hindi issue [#56](https://github.com/ColinGPT9/clips-studio/issues/56)). **No verified competitor targets Indian creators or Hinglish.** That is our clearest positioning.

---

## 7. Bottom line

- **Distribution scale.** MoneyPrinterTurbo (143k stars) and LosslessCut (46k) dwarf everyone; they have reach, not depth for our use case.
- **The real rivals.** HotClip is the most complete "footage → publishable short" tool, with by far the deepest workflow. Clips Kitty has the best speaker-aware framing. OpenChatCut and Scarlet have the best review and edit loop.
- **We do not beat them today** on review, speed, install or clipping long videos into many shorts.
- **We lead on** Hinglish, a combined layered style pass, offline script-to-video with 3D, and a taste model you can inspect.
- **Fastest route to "+10 overall":** gaps 1, 2, 3, 5 and 8, which together turn our pipeline quality into a product people can install and trust.
