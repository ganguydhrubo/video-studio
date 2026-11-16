# Takebloom research report, November 2026

Checked by web search on 16 Nov 2026. Many sources are blogs or roundups, not the project's own page. Where sources disagree, both are given. Anything I could not confirm says **not verified**. Laptop timings are **estimates** unless marked **measured** (measured figures come from `docs/KNOWN_LIMITATIONS.md`).

Target laptop: i7-13620H (10 cores, 16 threads), 16 GB RAM, Intel UHD graphics, no NVIDIA GPU.

---

## 1. Open-source building blocks

### 1a. Engines, timelines and editors

| Tool | Licence | What it does | CPU-only? | Verdict for Takebloom |
|---|---|---|---|---|
| FFmpeg `xfade` | LGPL core (the gyan.dev full build is GPL-3.0) | About 50 built-in transitions plus custom expressions | Yes, near real time at 1080p (estimate) | **Use now.** Main transition engine. |
| FFmpeg `lut3d` | as above | Applies `.cube` colour LUTs | Yes, fast | **Use now.** Basis of a "looks" library. |
| FFmpeg `zoompan` | as above | Ken Burns pans and zooms | Yes | **Use, with care.** It moves in whole pixels, so it shakes. Scale the image up 2–4x first, then zoom. |
| FFmpeg `chromakey` / `colorkey` | as above | Green-screen keying | Yes, fast | **Use.** Good when the user really has a green screen. |
| FFmpeg `vidstab` | GPL (needs `--enable-gpl --enable-libvidstab`; the gyan full build includes it, not verified for FFmpeg 9 builds) | Two-pass stabilisation | Yes, about 0.5–2x real time at 1080p (estimate) | **Use now** for phone footage and shorts. |
| FFmpeg `minterpolate` | as above | Motion-compensated frame rate change | Yes, but very slow and leaves warping | **Avoid** for long clips. RIFE is better. |
| GStreamer | LGPL-2.1 | Pipeline media framework | Yes | **Skip.** It duplicates FFmpeg. Only useful for a live preview pipeline later. |
| MLT | LGPL-2.1 | Engine behind Shotcut and Kdenlive; has the `melt` CLI and an XML project format | Yes | **Export target.** Write MLT XML so users can open an edit in Shotcut or Kdenlive. |
| OpenTimelineIO | Apache-2.0 | Neutral timeline format with adapters | n/a | **Extend.** Takebloom already writes OTIO JSON. Add FCP XML and other adapters so Resolve and Premiere users can finish there. Resolve 21 OTIO support: not verified. |
| Blender 5.2 | GPL | 3D titles and scenes | Yes, but slow on iGPU (**measured** 25–35 s per EEVEE frame) | **Keep** for short 3D titles. Draft mode for previews. |
| Natron | GPL-2.0 | Node compositor | Yes | **Skip.** Development has been stalled for years, and I found no 2026 release ([forum](https://discuss.pixls.us/t/natron-development-status/9505/28)). |
| Shotcut / Kdenlive | GPL | Full desktop editors on MLT | Yes | **Do not embed.** Use them as hand-off targets via MLT XML. |
| Olive | GPL-3.0 | Node-based editor | Yes | **Skip.** Still 0.2 alpha/nightly ([update](https://scribe.disroot.org/post/2429686)). |

### 1b. Background removal and matting

| Model | Licence | Strength | Hardware | Verdict |
|---|---|---|---|---|
| [rembg](https://github.com/danielgatis/rembg) (U2Net and others) | MIT code; U2Net Apache-2.0 (not verified this session) | Good still-image cut-outs | CPU OK, about 1–3 s per 1080p frame (estimate) | **Stills and thumbnails only.** Too slow and flickery for video. |
| MODNet | **CC BY-NC-SA 4.0** per its README ([mirror](https://gitee.com/openbayes/MODNet)); a promised licence change is not verified | Portrait matting | CPU OK | **Do not ship.** Non-commercial. |
| MediaPipe selfie segmenter | Apache-2.0 | Fast person mask | CPU, real time at preview size | **Use now.** The old `solutions.selfie_segmentation` API is legacy. Move to the Tasks [Image Segmenter](https://developers.google.com/edge/mediapipe/solutions/vision/image_segmenter). Edges are rough. |
| [RobustVideoMatting](https://github.com/PeterL1n/RobustVideoMatting) | **GPL-3.0** | Best temporally stable person matte for video | CPU possible at reduced size, about 2–8 fps at 1080p (estimate) | **Optional separate download.** Takebloom is MIT, so call RVM as a separate process and do not copy its code into the repo. |
| [BiRefNet](https://github.com/ZhengPeng7/BiRefNet) | MIT (repo). Weights licence is not stated on the page. ONNX and lite versions exist | Highest-quality edges (hair) | Heavy. On CPU, several seconds per frame (estimate) | **Stills and key frames.** Note: BRIA **RMBG-2.0** is built on BiRefNet but is non-commercial. Do not ship it. |

### 1c. Frame interpolation and upscaling

| Tool | Licence | Hardware | Verdict |
|---|---|---|---|
| RIFE via [rife-ncnn-vulkan](https://github.com/nihui/rife-ncnn-vulkan) | MIT | Vulkan, so it should run on Intel UHD. Not verified on this laptop | **Optional "smooth slow-mo / 60 fps" pass** for short clips. |
| Real-ESRGAN via [Real-ESRGAN-ncnn-vulkan](https://github.com/xinntao/Real-ESRGAN-ncnn-vulkan) | MIT upstream; one packager lists BSD-3-Clause ([FreshPorts](https://freshports.org/graphics/realesrgan-ncnn-vulkan)) | Vulkan; slow on an iGPU | **Stills, thumbnails and a few seconds of video only.** |

### 1d. Speech recognition

| Tool | Licence | Hardware | Verdict |
|---|---|---|---|
| faster-whisper | MIT | CPU int8. **Measured** about 2 min per min of speech with large-v3-turbo | **Keep.** |
| [whisper.cpp](https://github.com/ggml-org/whisper.cpp) | MIT | Version 1.8.3 added Vulkan for integrated GPUs. PR tests on an Intel Arc iGPU gave **3–4x better speed than CPU**; the "12x" headline is from the PR title ([Phoronix](https://phoronix.com/news/whisper-cpp-1.8.3-12x-perf)). An OpenVINO path speeds up the encoder only | **Add as a second backend.** Benchmark on Intel UHD first; the tests used a newer Arc iGPU. |

### 1e. Text to speech

| Tool | Licence | Verdict |
|---|---|---|
| Piper | Original rhasspy repo (MIT) was archived in Oct 2025. Work moved to [OHF-Voice/piper1-gpl](https://github.com/OHF-Voice/piper1-gpl/releases), **GPL-3.0** ([Debian packaging note](https://www.mail-archive.com/debian-wnpp@lists.debian.org/msg274337.html)). Each voice has its own licence | **Keep as an optional external install.** Do not bundle. Check each voice. |
| [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) | Apache-2.0 code and weights | **Keep as default.** Hindi voices are listed ([tts.ai](https://tts.ai/voices/kokoro/?lang=hi)). No voice cloning. CPU speed on this laptop not measured. |
| [XTTS-v2](https://huggingface.co/coqui/XTTS-v2) | **Coqui Public Model Licence**, non-commercial. The paid route is unclear ([summary](https://localaimaster.com/blog/xtts-coqui-commercial-license)) | **Do not ship.** At most a "bring your own model, personal use" switch. |

### 1f. Music generation

| Tool | Licence | Verdict |
|---|---|---|
| MusicGen ([audiocraft](https://github.com/facebookresearch/audiocraft)) | Code MIT, **weights CC-BY-NC 4.0** | **Do not ship.** Non-commercial weights. |
| [ACE-Step 1.5](https://comfyui-wiki.com/en/models/ace-step/ace-step-v1-5) | Apache-2.0 per secondary sources; one checkpoint listed as MIT. Check each checkpoint | **Best candidate** for an optional generated-music adapter on a remote GPU. CPU speed: not verified. |
| Stable Audio 3.0 | Community licence, "free for commercial use" per news reports ([report](https://aigenart.beehiiv.com/p/stable-audio-s-open-music-model-drops)). Not verified | **Watch.** Not Apache; read the licence before use. |

Keep the procedural music beds as default. They have zero licence risk.

### 1g. Open video generation models

| Model | Licence | Real VRAM need | Notes |
|---|---|---|---|
| Wan 2.1 (1.3B, 14B) | Apache-2.0 | 1.3B about 8 GB (README figure as I recall it, not verified) | 1.3B quality is "prototype" level ([comparison](https://loremotion.pages.dev/blog/ltx-video-2-3-vs-wan-2-1)). |
| [Wan 2.2 TI2V-5B](https://huggingface.co/Wan-AI/Wan2.2-TI2V-5B) | Apache-2.0 ([The Decoder](https://the-decoder.com/alibaba-launches-wan2-2-its-improved-open-source-video-generation-model/)) | About 24 GB unquantised; about 8 GB with GGUF and offloading ([guide](https://computingforgeeks.com/run-wan-video-generation-locally/)) | 720p, 24 fps, text and image to video. **Best permissive pick.** |
| Wan 2.2 A14B | Apache-2.0 | 80 GB for one GPU (official); quantised versions run on 24 GB cards | High quality, heavy. |
| Wan 2.5 / 2.6 / 3.0 | API only | n/a | No open weights. Wan 3.0 is an API beta; open weights were announced, not released ([preview](https://wan27.org/blog/wan-3-0-everything-we-know), [news](https://www.cliprise.app/news/alibaba-wan-2-2-2-6)). |
| [LTX-2](https://huggingface.co/Lightricks/LTX-2) / 2.3 / 2.5 | **LTX-2 Community Licence** ([PDF](https://static.lightricks.com/legal/ltx-2-open-weights-license-0.X.pdf)); free for commercial use under US$10M revenue per secondary sources (not verified) | 19B (LTX-2), 22B (2.3). Figures range from 16 GB to 32 GB+ ([InVideo summary](https://invideo.io/blog/ltx-ai-video-generator/)) | Makes sound with the video. LTX-2.5 released Aug 2026 ([DataNorth](https://datanorth.ai/news/ltx-releases-ltx-2-5-open-weights-video-world-model)). Not OSI open source. |
| LTX-Video (older 2B) on OpenVINO | Older LTX licence (not verified) | Runs on Intel CPU or GPU via [OpenVINO GenAI](https://openvinotoolkit.github.io/openvino.genai/docs/samples/python/video_generation) | **Only local test path for this laptop.** Speed on Intel UHD: not verified, likely minutes per second of video. |
| HunyuanVideo 1.5 | Tencent Hunyuan Community Licence. **Excludes the EU, UK and South Korea** | About 14 GB with offloading ([Hardwarepedia](https://hardwarepedia.com/ai/hunyuanvideo-1-5), [review](https://airmore.ai/ai-review/hunyuan-video-review)) | Region limit makes it a poor default for a public app. |
| CogVideoX | 2B Apache-2.0; 5B under the CogVideoX Licence | 2B about 12–16 GB; 5B 16–24 GB ([comparison](https://docs.clore.ai/guides/comparisons/video-gen-comparison)) | Older. Wan 2.2 5B is better. |
| Mochi 1 | Apache-2.0 | About 60 GB official; under 20–24 GB in ComfyUI ([Comfy blog](https://blog.comfy.org/mochi-1/)) | Good licence, heavy. |
| Kandinsky 5.0 Video Lite (2B) | MIT per most sources; some say Apache-2.0 ([paper](https://arxiv.org/abs/2511.14993)) | 12–16 GB per one source ([guide](https://apatero.com/blog/kandinsky-5-0-complete-guide-t2i-t2v-i2v-2025)) | Up to 10 s at 768×512. Permissive either way. |
| MiniMax H3 | Licence reportedly excludes US, EU, UK, South Korea ([roundup](https://magichour.ai/blog/best-open-source-ai-video-generation-models)) | not verified | **Not verified.** Avoid until checked. |

**Verdict:** none of these is usable on the owner's laptop for real work. Build one adapter that sends jobs to ComfyUI on a GPU the user controls. Default to Wan 2.2 TI2V-5B (Apache-2.0). Make LTX and Hunyuan opt-in with their licence shown.

---

## 2. Honest capability table

Market note: OpenAI closed the Sora app on 26 Apr 2026 and the Sora 2 API on 24 Sept 2026 ([Axios](https://www.axios.com/2026/03/24/openai-discontinue-sora-video-app), [Neowin](https://www.neowin.net/news/openai-to-shut-down-sora-app-and-apis-as-it-shifts-focus-to-coding-ai/)). Model versions below come from 2026 roundups ([Pinggy](https://pinggy.io/blog/best_video_generation_ai_models/)) and are partly **not verified**.

| Product | What it does best | Where Takebloom can be better | Where Takebloom cannot win |
|---|---|---|---|
| CapCut | Huge template and effect library, auto captions, trends | No account, footage stays on the PC. CapCut's terms give it a broad, perpetual licence over uploads ([Fluxnote](https://fluxnote.io/guides/capcut-questions-answered)). Paid tiers and AI points since 2026 ([CapCut](https://www.capcut.com/help/pricing-change)) | Template volume and trend speed; cloud AI effects |
| VN | Simple free mobile and desktop timeline | Brief-to-edit automation, batch work | Mobile editing |
| Premiere Pro | Pro NLE, team workflows, Firefly generative tools | Fully automated first cut from a typed brief; no subscription | Deep manual editing, plug-in ecosystem, generative extend |
| After Effects | Keyframed motion design and compositing | Templated motion graphics from text (HyperFrames), repeatable | Hand-crafted compositing, tracking, roto at pro level |
| DaVinci Resolve 21 | Grading, Fairlight audio, free tier; most AI tools need Studio, US$295 ([PetaPixel](https://petapixel.com/2026/06/03/davinci-resolve-21-officially-released-with-new-photo-editing-ai-tools-and-much-more/)) | Automation and story decisions; runs on a weak iGPU laptop | Colour science, audio post, heavy GPU AI |
| Final Cut Pro | Fast Mac editing | Runs on Windows; automation | Apple Silicon speed |
| Filmora | Easy effects, stock, AI add-ons | No credits or subscription | Asset store size |
| Runway (Gen-4.5) | Generative video and AI editing | Cost, privacy, edits real footage end to end | Generative quality |
| Kling (3.0; 4.0 reports conflict) | Realistic generated motion | Cost, privacy | Generative quality |
| Google Veo 3.1 (Gemini Omni may replace it in the Gemini app, single source) | Top-tier text-to-video with sound | Cost, privacy | Generative quality |
| OpenAI Sora | Closed in 2026 | n/a | n/a |
| Pika (2.x) | Short stylised effects | Cost | Viral effects |
| Luma (Ray3.2) | Cinematic and HDR generation | Cost | Generative quality |
| Descript | Edit video by editing text, filler removal | Same idea offline, plus Hinglish captions and dubbing on the user's PC | Polish, collaboration, voice cloning |
| Canva | Brand kits, design templates | Real video editing of long footage; offline | Template and stock library |
| InVideo | Script to video with stock and AI | Uses the user's own footage; no stock licence risk | Stock library breadth |
| VEED | Browser editor, subtitles | Offline, no upload, no watermark | Collaboration |
| Clipchamp | Free on Windows, 1080p export free ([Windows Central](https://www.windowscentral.com/microsoft-increases-free-plan-video-editor-clipchamp-include-1080p-exports)) | Automation, captions in Indian languages, 3D titles | Simple manual timeline UX |
| HitFilm | VFX plus editing (current product status not verified) | Automation | Manual VFX tools |
| Blender | Full 3D, VSE, compositing | Takebloom drives Blender for the user; no 3D skill needed | Anything hand-made in 3D |

**Where a local-first automated editor can truly win:**
- **Automation:** brief in, finished cut out. Batch shorts, captions, dubbing and exports with no clicking.
- **Privacy:** footage never leaves the PC unless the user picks a cloud AI.
- **Cost:** no subscription, no AI credits, free CLIs by default.
- **Repeatability:** the same brief gives the same edit; projects are files.
- **Weak hardware:** most AI-first tools assume a cloud or a big GPU.

**Where it cannot win now:** generated footage quality (needs 16–80 GB GPUs or paid cloud), large template and stock libraries, real-time preview of heavy effects, team collaboration.

---

## 3. What this laptop can run

All throughput figures are **estimates** for 1080p unless marked **measured**. "Real time" means 1 minute of video takes 1 minute.

| Task | Local? | Rough speed | Practical limit |
|---|---|---|---|
| Cut, trim, join with stream copy | Yes | Many times real time | Any length, up to 4K |
| H.264 encode, x264 on CPU | Yes | 1–3x real time (medium preset) | 1080p30 comfortable; 4K about 4x slower |
| H.264/HEVC encode on Intel Quick Sync | Yes | 3–8x real time | Good for proxies and drafts. AV1 encode on this iGPU: not verified (likely not supported) |
| Full Takebloom effects pass | Yes | **Measured** about 15 min per minute of footage | Keep finished videos under about 10 min, or use `--fx clean` |
| xfade, lut3d, chromakey | Yes | Near real time | Fine |
| vidstab (two passes) | Yes | 0.5–2x real time | Fine for shorts |
| minterpolate | Yes | 0.05–0.2x real time | Avoid for anything over 30 s |
| RIFE 2x via Vulkan | Probably | 1–5 fps output | 1 min clip ≈ 10–30 min |
| Real-ESRGAN 4x | Probably | Under 1 fps at 720p input | Stills and a few seconds of video |
| faster-whisper large-v3-turbo | Yes | **Measured** about 2 min per min | Fine overnight; whisper.cpp Vulkan may cut this |
| Kokoro TTS | Yes | Faster than real time | Fine |
| MediaPipe person mask | Yes | 20–60 fps at preview size | Fine for previews and shorts |
| RobustVideoMatting | Yes, slowly | 2–8 fps | 1 min ≈ 4–15 min |
| rembg / BiRefNet per frame | Stills only | 1–10 s per frame | Thumbnails, key frames |
| Blender EEVEE | Yes, slowly | **Measured** 25–35 s per frame | A 5 s title at 30 fps ≈ 1–1.5 hours; use draft mode |
| HyperFrames (headless browser) | Yes | 2–10 fps | Fine for titles and lower thirds |
| Ollama local LLM | Yes | 3B–8B quantised models at about 5–15 tokens/s | 14B possible but tight on 16 GB RAM |
| Open video generation | **No** | Wan 2.2 5B needs 8 GB+ VRAM even quantised | Cloud or remote GPU only. LTX-Video via OpenVINO is an experiment, not a feature |
| Generated music (ACE-Step) | Probably not | not verified | Remote GPU |

**Rules of thumb for this laptop:**
- Work at 1080p30 or below for any effect pass. Edit 4K with stream copy or proxies only.
- Run heavy steps one at a time. Blender, a local LLM and Whisper together will exceed 16 GB RAM.
- Use Quick Sync for previews and proxies; x264 for the final export.

**Cloud or remote GPU only:** text-to-video and image-to-video (Wan, LTX, Hunyuan, Kling, Veo, Runway), generated music at good quality, voice cloning, BiRefNet on full-length video, Blender Cycles at length.

---

## 4. Free sources of templates, transitions, LUTs, fonts, SFX and music

**Safe rule:** bundle only CC0, public domain, MIT, Apache, ISC or SIL OFL items. Keep a `THIRD_PARTY_ASSETS.md` with the source and licence of every file. Avoid CC BY-SA, NC and ND.

### Safe to bundle

| Source | Type | Licence | Notes |
|---|---|---|---|
| [gl-transitions](https://unpkg.com/gl-transitions@1.76.1/README.md) | GLSL transitions | MIT, set per transition in a `license` field | Check each entry's field. Render through HyperFrames/WebGL, or port the simple ones to `xfade` expressions. |
| Self-made `.cube` LUTs | Colour looks | Your own (MIT) | Generate LUTs in code (teal-orange, warm, mono, film fade). No licence risk at all. |
| [Google Fonts](https://github.com/google/fonts) | Fonts | Mostly SIL OFL, some Apache | Noto Sans Bengali and Devanagari cover Indian scripts. Ship the OFL text with each font. |
| [Kenney audio packs](https://kenney.nl/assets/ui-audio) | UI sounds, jingles | CC0 | Good for clicks, pops and whooshes. |
| [FreePD](https://freepd.com) | Music | CC0 per its legal notice ([mirror of notice](https://a.ocv.me/pub/stuff/music/freepd/README.md)) | By Kevin MacLeod. Do not confuse with Incompetech. |
| [Freesound](https://freesound.org), CC0 filter only | Sound effects | CC0 per item | Licence is per sound. Record each one. |
| [OpenGameArt](https://opengameart.org), CC0 only | SFX, music | Per item | Many items are CC BY-SA or GPL; take CC0 only. |
| Procedural music beds | Music | Your own | Already in Takebloom. Keep as default. |

### Do NOT bundle (link or let the user download instead)

| Source | Why |
|---|---|
| Pixabay | Terms ban distributing content "on a standalone basis" ([terms](https://pixabay.com/service/terms/)). |
| Mixkit | Items cannot be passed on in their original form ([Mixkit](https://mixkit.co/llm-info/), [terms](https://mixkit.co/terms/)). |
| Pexels | Similar stock licence; redistribution as-is not allowed (not verified this session). |
| Incompetech (Kevin MacLeod) | CC BY 4.0. Bundling is legal with credit, but every user video would then need credit. Use FreePD instead. |
| YouTube Audio Library | Meant for use in your videos, not for redistribution (not verified this session). |
| Zapsplat, Uppbeat, Sonniss GDC bundles | Royalty-free use, not redistribution. Sonniss terms not verified. |
| Free LUT packs from vendors | Usually "use, do not redistribute". |
| MusicGen output, MODNet, RMBG-2.0, XTTS voices | Non-commercial licences. |
| CapCut, Canva or Filmora templates | Proprietary. Never copy them. |

---

## 5. Top 10 next additions, ranked

| # | Addition | Effort | Why |
|---|---|---|---|
| 1 | **Edit-by-text transcript panel** in the browser UI: delete words to cut, drag to reorder, saves to `edit_plan.json` | M | Descript's core idea, done offline. Turns the automated cut into something users trust and fix in seconds. Skip if the UI already has it. |
| 2 | **whisper.cpp Vulkan backend** with auto benchmark against faster-whisper | S | Captions are on every job. A 2–4x speed-up on the iGPU saves the most waiting time. Test on Intel UHD first. |
| 3 | **Subject cut-out pipeline:** MediaPipe Tasks for previews, optional RVM (separate GPL download) for final | M | Unlocks background swap, text behind the speaker and blur — the most used CapCut-style effects — with no green screen. |
| 4 | **NLE round trip:** OTIO adapters, FCP XML and MLT XML export | M | Pros can finish in Resolve, Premiere, Kdenlive or Shotcut. Takebloom becomes the automatic first-cut tool for all of them, not a rival they must leave. |
| 5 | **Bundled look pack:** gl-transitions (MIT), code-generated LUTs, Noto fonts, Kenney and FreePD audio, with an asset licence manifest | M | Closes the "too few templates" gap with zero licence risk and no downloads. |
| 6 | **Proxy and draft path:** Quick Sync proxies, low-res draft render, then final x264 | M | The full effects pass takes about 15 min per minute. Fast drafts make the brief-review-fix loop usable. |
| 7 | **Stabilise and smart reframe for shorts:** vidstab plus the existing face tracking for 9:16 crops | S | Phone footage is shaky. This is cheap and visible in every short. |
| 8 | **Enhance pass:** RIFE slow-mo/60 fps and Real-ESRGAN for stills and thumbnails (both MIT, Vulkan) | S | Easy wins users notice. Keep it to short clips on this laptop. |
| 9 | **Remote GPU adapter** for ComfyUI: Wan 2.2 TI2V-5B default (Apache-2.0), LTX and others opt-in with licence shown | L | The only fair way to offer generated B-roll without paid APIs. Runs on a GPU the user rents or owns. |
| 10 | **Optional generated music** via ACE-Step 1.5 on the same remote adapter; procedural beds stay default | M | Better music on request, still no paid API. Do after #9, since it reuses that adapter. |

**Do not build now:** local video generation on this laptop, MusicGen, XTTS, MODNet, GStreamer, Natron or Olive integration.
