# Edit styles catalog for non-talking-head footage

Scope: a local, CPU-only (16 GB RAM, Intel iGPU) automatic editor built on Python, OpenCV, MediaPipe, FFmpeg and Blender. Decisions come from rules and small local models, not an LLM. Talking-head grammar (silence trimming, punch-in alternation, captions, loudness, J/L cuts) is in `editing_grammar.md` and is not repeated here.

Conventions: frames (f) are at 30 fps. "Beat" = one beat; at 120 BPM a beat is 15 f and a bar is 60 f. Ease names follow Penner/After Effects usage (outExpo, inOutCubic). Difficulty is rated 1-5. CPU cost per output second at 1080x1920: **L** under 1 s, **M** 1-10 s, **H** over 10 s.

**About the sources.** Trend material online is mostly vendor tutorials. It confirms *which* techniques are popular, not exact numbers. The parameters below are working defaults built from common practice [1-8]; tune them against your own reference edits.

---

## 1. Technique catalog (34 techniques)

### 1A. Cuts and timing

| # | Technique | Looks like / used for | Parameters @30 fps | Local implementation and trigger | Diff / CPU |
|---|---|---|---|---|---|
| 1 | **Beat cut** | Hard cut landing on the beat. The base of every music edit | Cut 0-1 f *before* the beat's transient, because the eye reads a cut late. Minimum shot length 8 f (hype) or 30 f (cinematic) | Trigger: beat or downbeat times (section 5). Snap each planned cut to the nearest beat within ±3 f. FFmpeg `trim` + `concat` | 1 / L |
| 2 | **Velocity ramp** | Fast, then slow on the hit, then fast again. Car, sports, anime, phonk and dance edits [1][5] | Per beat: 3-5x speed for the first 55-65% of the beat, an inOutCubic ease down to 0.25-0.4x on the beat, hold the slow speed 4-8 f, then ramp back up. Total source used: about 2-3 beats per output beat | Build a speed curve s(t), integrate it to t_src(t_out), and sample frames. Below 1x the footage needs 60/120 fps source or interpolation (#26). Above 2x, blend 2-4 source frames (`tmix`) to get natural motion blur. Trigger: beat, plus a flow-magnitude peak in the shot so the slow-down lands on real action | 3 / M (H with RIFE) |
| 3 | **Speed ramp into a cut** | Accelerate into a shot change. Travel, vlog | 1x up to 2.5-4x over the last 6-10 f of a shot, outQuad ease. The next shot starts at 2x and settles to 1x in 6 f | Same time-remap code. Trigger: a cut placed on a downbeat | 2 / L |
| 4 | **Stutter / repeat** | 2-4 frame chunks repeated. Hype, AMV, glitch drops | Chunk of 2-4 f repeated 2-4 times on 1/8 or 1/16 notes, only inside a drop | Index frames into a repeat list. Trigger: hi-hat or onset density in a high-energy section | 1 / L |
| 5 | **Freeze frame + intro card** | Freeze, cut-out stroke, name. Sports "player intro", anime, fashion | Freeze 15-30 f. Background desaturated 60-80% and darkened 0.5 stop. White stroke 6-10 px on the cut-out. Text slides in over 8 f with outExpo | MediaPipe selfie segmentation for people, rembg for objects. Stroke = dilate the mask, minus the mask. Trigger: the top-scored face or action frame on a downbeat | 2 / M |
| 6 | **Slow-mo hero moment** | One 0.25-0.5x shot at the climax. Sports, events, fashion walk | 0.4x for 1-2 bars. Hard rule: never on 24/30 fps source without interpolation | Use native high-fps clips when present (check `r_frame_rate` with ffprobe). Trigger: global flow-magnitude peak, or a jump/throw | 2 / L-H |
| 7 | **Hyperlapse / time-lapse** | City, crowd, sky, cooking prep | 4-16x speed with 4-8 frame blending, then stabilize (#22) | FFmpeg `setpts` + `tmix` + `vidstab`. Trigger: a long (over 8 s) low-motion static or walking shot | 1 / M |

### 1B. Transitions

| # | Technique | Looks like / used for | Parameters @30 fps | Local implementation and trigger | Diff / CPU |
|---|---|---|---|---|---|
| 8 | **Whip pan** | Blurred swish between shots. Travel, location changes, outfit reveals [6] | 4-6 f on each side. Directional blur peaks at 60-100 px. Offset of 30-50% of the frame width in the pan direction. inOutCubic | Best when real: detect camera whips as spikes in global flow (median flow over 25 px/f at 640 px width) and cut at the peak blur frame, matching direction across both shots. Synthetic version: affine shift plus a linear motion-blur kernel (`cv2.filter2D`) | 2 / L |
| 9 | **Zoom-through** | Punch into the frame centre and land in the next shot. Hype, travel | Out-shot scale 100 to 180% over 5 f (inExpo). In-shot scale 60 to 100% over 5 f (outExpo). Radial blur 10-25 px at the peak | Radial blur = average 6-8 scaled copies around the salient point. Zoom about the saliency centroid, not the frame centre | 2 / L |
| 10 | **Spin / roll** | Camera roll 90-180 degrees with blur. Car, sports, AMV | 6-8 f, rotation ±90-180 degrees, rotational blur, scale 110-120% so no borders show | Warp with `cv2.warpAffine` and an angular blur made by averaging several rotations. Use on a downbeat at a section change | 2 / L |
| 11 | **Match cut** | Shape, colour or motion continues across shots (a round plate becomes the round moon) [6] | Hard cut, or a 4-6 f dissolve. Position error of the salient object under 10% of the width | Score every shot pair at the tail and head frames. Similarity = salient-object centroid distance + mask IoU + hue-histogram distance + flow-direction cosine (+ optional CLIP embedding cosine). Pick the best pairs greedily, then align them with a small scale/shift. Highest perceived craft per CPU second | 3 / M |
| 12 | **Object / mask wipe** | Someone walks past the lens, a pole, or a hand covers the lens; the next shot is revealed [6] | Cut at maximum occlusion. With a mask, the next shot shows through the occluder's mask edge over 4-8 f | Detect occlusion frames: luma drop over 50%, or Laplacian sharpness collapse, or a large near-foreground segment covering over 70% of the frame. Cut there. Otherwise, sweep a feathered (20-40 px) matte along the occluder's flow direction | 3 / M |
| 13 | **Light leak / film burn** | Warm flare washes over the cut. Travel, wedding, aesthetic vlog | 10-20 f, additive or screen blend, peak opacity 60-90% on the cut frame | Generate leaks procedurally (moving gaussian blobs with an orange/magenta gradient, animated by noise), so no stock assets are needed | 1 / L |
| 14 | **Dip to black / white, cross-dissolve** | Calm, documentary, emotional transitions | Dissolve 8-15 f; dip 6 f out, 2 f hold, 6 f in. White dip for fashion and wedding | FFmpeg `xfade` (fade, fadeblack, fadewhite) | 1 / L |
| 15 | **Flash cut** | 1-3 f white flash on the hit. Hype, fashion, cars | Exposure +1.5 to +2 stops on frame 0, decaying to 0 over 3 f | Brightness curve on the first frames after the cut. Trigger: downbeat or kick transient with high onset strength | 1 / L |
| 16 | **Glitch / RGB split** | Channel offset, block tearing, scanlines. Tech, gaming, phonk, AMV | 3-6 f. R/B channel offset 4-12 px. 3-8 random horizontal slices shifted 10-60 px. Optional 1-frame invert | Pure NumPy. A "datamosh" look without codec hacking: warp the previous frame with the current frame's Farneback flow for 4-8 f | 2 / L |
| 17 | **Split-screen / grid pop** | 2-4 panels popping in on beats. Product features, montage, fashion lookbook | Each panel pops in over 4 f (scale 90 to 100%, outBack ease) on consecutive beats | Composite on a canvas. Trigger: 2-4 similar-scored shots of one subject, such as outfits | 2 / L |

### 1C. Camera and frame effects

| # | Technique | Looks like / used for | Parameters @30 fps | Local implementation and trigger | Diff / CPU |
|---|---|---|---|---|---|
| 18 | **Impact zoom (bass bounce)** | Frame punches in and decays on each kick [5] | Scale 100 to 106-112% in 2 f, back to 100% over 6-10 f (outExpo). Phonk/AMV: up to 115% | Scale about the face or saliency centre. Trigger: kick or bass onsets (low-band onset envelope from librosa). At most 1 per beat | 1 / L |
| 19 | **Camera shake** | Handheld jolt on hits. Sports, car, AMV, horror | Noise translation 8-20 px (1080-wide), rotation ±1-2 degrees, 6-12 f with an exponential decay, plus a 2-4 frame `tmix` blur | Low-pass filtered Perlin or random noise driving `warpAffine`, scale 105% to hide borders. Trigger: snare or downbeat in a high-energy section | 1 / L |
| 20 | **Smooth push-in (Ken Burns)** | Slow, constant zoom on static or scenic shots. Cinematic travel, documentary, product | 100 to 106-110% over the whole shot. Linear or a very soft sine ease. Pan at most 3% of the width | Crop path anchored on saliency. Trigger: a low-motion shot (median flow under 0.5 px/f) longer than 1.5 s | 1 / L |
| 21 | **Smart reframe 16:9 to 9:16** | Subject stays centred in vertical crops. Any landscape footage | Crop path smoothed so speed is under 2% of the width per frame. Hold still when the subject moves less than 5% | Face, saliency or object track, then a smoothed path (Kalman, or an L1-optimal path as in MediaPipe AutoFlip, Apache-2.0 [9]). Signals: MediaPipe faces, OpenCV spectral-residual saliency | 3 / M |
| 22 | **Stabilize + crop** | Removes jitter. Vlog walking, drone, handheld product shots | Crop 5-10%, smoothing window 15-30 f | FFmpeg `vidstabdetect` / `vidstabtransform`, or an OpenCV feature-trajectory smoother. Trigger: high-frequency camera-path energy | 1 / M |
| 23 | **Letterbox open** | Bars animate to 2.39:1 at the intro. Cinematic travel, car, trailer | Bars grow from 0 to the final height over 20-30 f, inOutCubic. In vertical format, use the bars only as an intro gesture, then remove them | `drawbox` / NumPy | 1 / L |
| 24 | **Fake rack focus** | Background blurs and focus shifts. Product, food, fashion detail | Blur radius 0 to 12-20 px, proportional to the depth difference from the focus plane. Focus moves over 15-24 f | Depth Anything V2 **Small** (Apache-2.0 [10]) on about 1 frame in 5, depth interpolated in between, then layered gaussian blur. Works only on short, slow shots; fast motion flickers | 3 / H |
| 25 | **Product light sweep / shine** | A diagonal specular band glides across the product | 15-20 f, band 8-15% of the object width, additive 30-50% inside the object mask, angle 30-45 degrees | rembg mask (MIT [11]) on a near-static product shot, or MediaPipe for people. Trigger: a product close-up held for 1 s or more | 2 / M |
| 26 | **Frame interpolation** | Smooth slow motion from 30 fps | 2-4x interpolation | Practical-RIFE (MIT [12]) runs on CPU but is slow (roughly 1-3 fps at 1080p; benchmark it yourself). FFmpeg `minterpolate=mi_mode=mci` is cheaper but warps edges. Use it only on the 1-2 hero shots | 2 / H |

### 1D. Text and compositing

| # | Technique | Looks like / used for | Parameters @30 fps | Local implementation and trigger | Diff / CPU |
|---|---|---|---|---|---|
| 27 | **Text behind subject** | Huge title sits behind a person or product. Fashion, travel, vlog intros [3][4] | Text at 25-40% of the frame height, heavy condensed font. Hold 1-2 s on a near-static shot. Text scales 120 to 100% over 8 f | People: MediaPipe selfie/multiclass segmentation (Apache, real time on CPU). Objects: rembg u2net (fast) or BiRefNet (MIT, accurate but seconds per frame on CPU [13]). Smooth the mask over time (EMA 0.6). Layers: background, text, foreground. Trigger: a static subject shot with large mask area (15-50%) during the intro or drop | 3 / M |
| 28 | **Kinetic typography** | Words snap in on beats: scale pops, masked slide-ups, tracking expansion. Music edits, ads | Pop 0 to 110 to 100% over 6 f (outBack). Mask slide-up 8 f (outExpo). Tracking 0 to +15% over a whole bar. One word per beat at most | Render with Pillow/Skia or Blender text and composite. Trigger: beat grid, plus lyrics or copy supplied by the user | 2 / L |
| 29 | **Planar-tracked text / graphic** | Text "stuck" on a wall, floor or table. Travel, product | Tracked plane, motion blur matched to the plate | ORB/AKAZE features, then a RANSAC homography per frame with Kalman smoothing, then `warpPerspective` of the text. Fails on low texture; check the reprojection error | 4 / M |
| 30 | **3D camera-tracked text** | Text standing in 3D space as the camera moves past [7] | Same as #29, but with full parallax | Blender's libmv camera solver works on CPU, but automatic solving is fragile (it needs 8+ well-spread tracks and a solve error under 0.5 px). Ship #29 first; offer #30 only when the solve succeeds | 5 / H |
| 31 | **2.5D photo parallax** | Still photo turned into a dolly-in or orbit. Travel stills, memories, events | Camera move 2-4% of the width over 3-5 s, slight zoom 105% | Depth Anything V2 Small, then (a) OpenCV `remap` with depth-scaled displacement and `cv2.inpaint` (Telea) for the holes, or (b) a displaced plane in Blender. DepthFlow does exactly this, but it is AGPL-3.0 [14]: use it as a reference only if you distribute. Large moves expose holes that only generative inpainting fixes | 3 / M |

### 1E. Look and finish

| # | Technique | Looks like / used for | Parameters | Local implementation | Diff / CPU |
|---|---|---|---|---|---|
| 32 | **Film look** | Grain, halation, soft roll-off, gate weave [15][16] | Grain: luma-only gaussian noise, sigma 3-6 /255, softened by a 0.7 px blur, new pattern each frame. Halation: highlights above 0.8, blurred 15-30 px, tinted red-orange, added at 10-25%. Vignette -0.3 stop. Gate weave: 0.3-1 px random walk. Optional 24 fps cadence. A 3D LUT can't do grain or halation; they must be spatial passes [16] | NumPy, or FFmpeg `noise`, `vignette`, `lut3d`, `gblur`, `blend` | 2 / M |
| 33 | **Colour recipe LUTs + shot matching** | Unified grade: teal-orange, warm-soft (Korean), cool editorial, punchy sports | Korean soft: lift blacks +4-6%, saturation -10-15%, warm white balance +200-400 K, low contrast [17]. Teal-orange: shadows hue 190-200 degrees, skin 25-35 degrees protected | Generate `.cube` LUTs in Python and apply with FFmpeg `lut3d`. First match shots with a Reinhard colour transfer in Lab towards a reference frame, at 50-70% strength | 2 / L |
| 34 | **Overlay textures** | Dust, scratches, VHS lines, paper. Retro, Y2K, AMV | Opacity 10-30%, screen or multiply blend | Procedural noise, so no asset licensing is needed | 1 / L |

### What needs generative AI or a GPU (honest list)

| Want | Status on CPU |
|---|---|
| Object removal from video, outpainting 16:9 to 9:16, sky replacement with relighting, generative transitions (morphing one scene into another) | Needs diffusion or video-inpainting models (such as ProPainter) and realistically a GPU. **Not feasible locally.** Use smart reframing (#21) instead of outpainting |
| AI upscaling (Real-ESRGAN), RIFE slow motion, per-frame BiRefNet, temporally consistent video depth | Feasible on CPU but slow. Budget it for 1-3 hero shots only |
| Anime-style redraw of footage, AI style transfer at quality | GPU, or generative. Out of scope |

---

## 2. Style recipes

Pacing is given as shot length in beats (b). At 120 BPM, 1 b = 0.5 s = 15 f.

| Recipe | Techniques | Pacing | Colour look | Typography | Music |
|---|---|---|---|---|---|
| **Velocity beat edit** (car, dance, AMV-lite) | 2, 18, 19, 15, 16, 4, 10, 8 | 1 b per shot (0.5 b in drops); a ramp on every downbeat | High contrast, crushed blacks, saturation +10%, cool shadows | Minimal: 1-3 word slams in a heavy sans, on drops only | Phonk, trap, EDM, 130-160 BPM, a clear drop [5] |
| **Cinematic travel** | 20, 22, 8, 11, 13, 23, 31, 3, 32 | 2-4 b; 1 bar for scenic shots; one whip or match cut per 4 bars | Teal-orange or warm filmic, soft highlight roll-off, grain | Wide-tracked serif or thin sans place names, fade over 10 f | Orchestral, indie or ambient build, 90-120 BPM |
| **Clean product ad** | 20, 24, 25, 17, 27, 28, 14, 9 | 2 b per shot, hero shot 1 bar, features on beats | Neutral-clean, accurate white, +5% contrast, no grain | Bold geometric sans, feature words popping per beat, price/CTA card at the end | Upbeat minimal house or pop, 110-125 BPM |
| **Editorial fashion** | 27, 15, 14 (white dip), 17, 5, 6, 32 (light), 2 (gentle 0.5x) | 1-2 b; freeze plus intro card per look | Cool desaturated or creamy high-key, matte blacks | Large serif or condensed sans behind the subject, generous tracking | House, techno, R&B, 115-125 BPM |
| **Documentary / mini-doc** | 14, 20, 22, 33, J/L cuts from the talking-head report | Not beat-locked: 3-6 s shots, cuts on phrase ends | Natural, slightly warm, gentle grain | Lower thirds, small sans, fade-in | Low ambient piano, ducked under speech |
| **Hype sports** | 6, 5, 18, 19, 2, 9, 15, 16, 21 | 1 b, 0.5 b in drops; slow-mo on the climax for 2 bars | Punchy, high saturation, team colours | Huge condensed italics, stat slams | Hip-hop, drill, trailer hits, 140+ BPM |
| **Anime-style AMV** (on live action) | 18 (115%), 19, 4, 16, 10, 15, 5, 34 | 0.5-1 b; stutters on 1/16 notes; every snare gets a shake | High contrast, colour pops, optional halftone overlay | Japanese-style vertical text accents (if the user supplies them) | J-rock, phonk, anime OST, 140-170 BPM |
| **Minimal aesthetic vlog** (Korean/Japanese soft) | 20, 14, 33 (warm-soft), 32 (light grain), 22 | 2-4 b, slow; ambient sound kept | Soft warm, lifted blacks, saturation -10% [17] | Small lowercase captions, rounded sans, subtitles as narration [17] | Lo-fi or acoustic, 70-95 BPM |
| **Bollywood / desi trend** (wedding, festival, family) | 8, 12, 15, 6, 13, 5, 18 | Transition on each downbeat (outfit or location changes [18]); slow-mo on emotional peaks | Warm gold, rich saturation, skin protected | Devanagari + Latin decorative script, gold gradient | Bollywood / Punjabi hooks, 95-110 BPM |
| **Brazilian funk / phonk montage** | 4, 18, 19, 16, 2, 10 | 0.5 b; strict kick sync | Dark, red-magenta tint, grain | Distorted chrome text | Funk / phonk, 125-135 BPM with a heavy kick |
| **Food reel** | 20, 24, 7, 25 (on drinks), 9, 17 | 1-2 b; a 3-4 shot "process" run on consecutive beats | Warm, saturation +10%, protect reds and yellows | Rounded bold sans, ingredient names | Upbeat acoustic or funk, 100-120 BPM |

Global rate caps: transitions other than hard cuts at no more than 1 per 4 b (velocity and hype: 1 per 2 b); shake and zoom at most 1 per beat; never two different transitions back to back.

---

## 3. Footage-type detection (local, cheap)

Compute per shot, after shot detection (section 4), on frames downscaled to 320 px at 5 fps:

| Signal | How | Cost |
|---|---|---|
| Face area ratio | MediaPipe face detector: median of the largest face box area divided by frame area | L |
| Speech ratio | Silero VAD (MIT): speech fraction of the shot | L |
| Motion magnitude | Farneback flow, median vector length (px/f at 320 px) | M |
| Camera vs subject motion | Fit a homography to the flow vectors. Low residual means a camera move; high residual means subject motion | M |
| Shot length | From TransNetV2 / PySceneDetect | L |
| Colour stats | Mean HSV saturation; Hasler-Süsstrunk colourfulness; share of sky-blue pixels in the top third; share of green | L |
| Saliency concentration | Spectral-residual saliency (opencv-contrib): fraction of saliency mass in the central 40% | L |
| Optional semantic tag | OpenCLIP ViT-B/32 zero-shot (MIT-style licence [19], about 150M params, roughly 0.1-0.3 s per frame on CPU) with prompts such as "a product on a table", "food on a plate", "a car", "a crowd at a concert", "fashion model". Run it on 1-3 frames per shot | M |

Rules (first match wins):

| Class | Rule | Default recipe |
|---|---|---|
| Talking head | face over 4% of frame **and** speech over 50% | use `editing_grammar.md` |
| Walk-and-talk vlog | speech over 40%, camera motion high, face 1-4% | Minimal aesthetic vlog, or talking-head grammar plus b-roll |
| Product close-up | no face, saliency concentrated (over 60% in the centre), low motion, low background colourfulness, CLIP "product" | Clean product ad |
| Food | CLIP "food" top-1, or a warm hue peak with a top-down view | Food reel |
| Scenic / landscape | no face, low subject motion, sky over 15% or green over 25%, shots over 3 s | Cinematic travel |
| Action / sports | median flow over 3 px/f, high flow residual (subject motion), short shots | Hype sports |
| Car | CLIP "car" top-1 | Velocity beat edit |
| Fashion / person-centric b-roll | full-body person mask 15-50%, no speech | Editorial fashion |
| Crowd / event | many faces (over 5) or CLIP "concert / crowd / wedding" | Bollywood/desi for weddings, otherwise Velocity beat edit at 2 b pacing |
| Mixed project | majority class by duration, weighted by scores | Majority recipe; per-shot transitions still follow each shot's class |

Cheap signals separate talking head, scenic, action and product well; CLIP is needed only for food, car, fashion and wedding. Swap the rules for a gradient-boosted classifier after 100+ labelled shots.

---

## 4. Shot selection for non-speech footage

**Shot boundaries.** TransNetV2 (MIT [20], about 30 MB, CPU-feasible, roughly real time or slower) is the most accurate open option. PySceneDetect (BSD-3 [21]) `AdaptiveDetector` is a fast first pass. Use PySceneDetect by default and TransNetV2 for gradual transitions.

**Per-frame score** (sample 2-5 fps; take the best 0.5-3 s window of each shot):

| Component | Measure | Weight (start) |
|---|---|---|
| Sharpness | Variance of Laplacian on luma, normalized per clip (z-score) | 0.20 |
| Exposure | Penalize clipping (over 2% of pixels at 0 or 255) and mean luma outside 0.3-0.7 | 0.10 |
| Stability | Inverse of high-frequency camera-path energy | 0.10 |
| Motion fit | Gaussian around the recipe's target flow (hype: high; travel: low-medium) | 0.15 |
| Faces | Face present, eyes open (MediaPipe Face Landmarker blink blendshapes below 0.4), smile score | 0.15 |
| Composition | Distance of the salient centroid to the nearest rule-of-thirds or centre point; horizon tilt from Hough lines | 0.10 |
| Aesthetics | LAION aesthetic predictor (below) | 0.20 |

Then pick shots with **MMR diversity**: penalize CLIP-embedding or perceptual-hash similarity over 0.9 to an already-chosen shot. Order the chosen shots by energy arc: a strong opener, building, the best shot at the drop.

**Small models: worth it?**

| Model | Licence | Size / CPU | Verdict |
|---|---|---|---|
| LAION improved-aesthetic-predictor (MLP on CLIP ViT-L/14) | Apache-2.0 code [22] | Head is a few MB; CLIP ViT-L/14 is about 430M params, roughly 1 s per frame on CPU | **Worth it** at 1 frame per shot candidate. It ranks "pretty" frames well, but it is biased toward photographic or art style and blind to motion |
| OpenCLIP ViT-B/32 (+ your own linear aesthetic head) | MIT-style [19] | about 150M params, fast | **Worth it.** One embedding serves tagging (section 3), diversity, match cuts (#11) and a custom head trained on your own picks |
| NIMA (MobileNet / Inception on AVA) | Code MIT in several ports; weight licences unclear [23] | 15-90 MB, fast | Optional. Weaker than CLIP-based scorers; check the weight licence |
| pyiqa (MUSIQ, TOPIQ, NIMA zoo) | **PolyForm Noncommercial** [24] | varies | Avoid for a product; fine for research comparisons |
| aesthetic-predictor-v2.5 (SigLIP) | AGPL-3.0 | about 400M | Licence-heavy; skip unless the project is AGPL |
| TransNetV2 | MIT | 30 MB | Worth it |
| Depth Anything V2 Small | Apache-2.0 (Base/Large are CC-BY-NC) [10] | 25M params, about 1 s per frame at 518 px | Worth it for #24 and #31 only |
| MediaPipe face / segmentation / AutoFlip | Apache-2.0 | small, real time | Essential |
| rembg (u2net) / BiRefNet | MIT / MIT | 4-170 MB / about 900 MB | rembg by default; BiRefNet only for hero stills |
| YOLOv8/11 (Ultralytics) | **AGPL-3.0** | small | Avoid if distributing; use MediaPipe Object Detector (Apache) |

Long term, as in the companion report: log kept and rejected shots and train a gradient-boosted ranker on these features plus the CLIP embedding.

---

## 5. Music sync

**Detectors:**

| Library | What | Licence | Notes |
|---|---|---|---|
| librosa | onsets, `beat_track` (dynamic programming), tempo, RMS, spectral novelty, structure segmentation | ISC [25] | Base layer. No downbeats |
| **Beat This!** (CPJKU, ISMIR 2024) | beats **and downbeats**, without DBN post-processing | **MIT** [26] | **Recommended.** Runs on CPU with `--gpu=-1` |
| BeatNet | beat, downbeat, meter; online and offline | CC-BY-4.0 [27] | OK, but its offline mode pulls in madmom |
| madmom | the classic RNN+DBN beat/downbeat tracker | code BSD, **models CC BY-NC-SA 4.0** [28] | Not for commercial use with the bundled models |
| Essentia | full music information retrieval suite | AGPL-3.0 | Licence-heavy |

**Pipeline.** Beat This! for beats and downbeats; librosa band-split onsets for kicks (below 150 Hz) and snares/hats (2-8 kHz); RMS energy per bar. A drop is a bar-over-bar energy jump over 1.8x plus a spectral-novelty peak. Phrases are 4 or 8 bars from the first strong downbeat.

**Mapping rules:**

1. Cuts land on beats, 0-1 f early. Big transitions (#8-12) land on **phrase starts** (every 4 or 8 bars). The drop gets the recipe's signature move (velocity ramp, flash, slam).
2. Shot length follows section energy: intro or verse 2-4 b, build 1-2 b (accelerating), drop 0.5-1 b, outro 2-4 b. Minimum shot length is 8 f regardless.
3. Kick onsets drive impact zoom (#18); snares drive shake (#19); hi-hat 1/16 notes drive stutter (#4), and only in drops.
4. A speed ramp ends **exactly** on the beat: plan backward from the beat time.
5. Never cut inside a natural camera whip except to perform #8. Prefer cutting where flow is low or at an action peak (contact, landing, a turn).
6. Breaks and silence: hold a shot, or use a slow push-in, and let the audio breathe. Never fill a break with effects.
7. Keep one unbroken "hero" moment per edit: 1-2 bars without effects, so the effects that follow land harder.
8. End on the final downbeat with a 6-10 f hold or a dip to black. Trim music on bar lines and fade over 1-2 beats.

---

## Sources

1. Velocity edits in CapCut (tutorial): https://shotkit.com/velocity-capcut/
2. CapCut slow motion, speed ramp and beat sync: https://www.nemovideo.com/blog/capcut-slow-motion-speed-ramp
3. CapCut, text behind a person: https://www.capcut.com/resource/how-to-put-text-behind-a-person-in-capcut
4. FlexClip, text-behind-person effect: https://flexclip.com/learn/text-behind-person-effect.html
5. Phonk car/drift editing (bass-synced 6% impact zooms): https://onsetengine.pages.dev/use-cases/phonk-car-drift-video-editor ; velocity phonk templates: https://pippit.capcut.com/templates/velocity-edit-phonk
6. Instagram Reel transition trends (match cut, whip pan): https://www.capcut.com/create/instagram-reel-transitions-viral-edit-tips ; travel transitions: https://spliceapp.com/blog/mastering-travel-vlog-transitions-for-instagram/
7. TikTok transitions tutorial: https://www.nemovideo.com/blog/tiktok-transitions-tutorial-for-beginners
8. Speed ramp in CapCut (Filmora guide): https://filmora.wondershare.com/video-editing-tips/speed-ramp-capcut.html
9. MediaPipe (Apache-2.0, includes AutoFlip): https://github.com/google-ai-edge/mediapipe
10. Depth Anything V2 licence note: https://github.com/DepthAnything/Depth-Anything-V2
11. rembg (MIT): https://github.com/danielgatis/rembg
12. Practical-RIFE (MIT): https://github.com/hzwer/Practical-RIFE
13. BiRefNet (MIT): https://github.com/ZhengPeng7/BiRefNet
14. DepthFlow (AGPL-3.0): https://github.com/BrokenSource/DepthFlow
15. Mixing Light, emulating film halation: https://mixinglight.com/color-tutorial/emulating-film-halation-in-davinci-resolve
16. Filmbox Looks guide (gate weave, LUT limits): https://videovillage.com/learn/filmbox-looks/full-guide/grading
17. Aesthetic Korean vloggers (soft warm look, subtitles as narration): https://thesmartlocal.com/korea/aesthetic-korean-vloggers
18. Instagram Reels and Indian weddings (transition reels): https://www.shaadinews.com/?p=14090 ; CapCut desi transitions: https://www.capcut.com/explore/desi-transition
19. OpenCLIP: https://github.com/mlfoundations/open_clip
20. TransNetV2 (MIT): https://github.com/soCzech/TransNetV2
21. PySceneDetect (BSD-3): https://github.com/Breakthrough/PySceneDetect
22. LAION improved-aesthetic-predictor (Apache-2.0): https://github.com/christophschuhmann/improved-aesthetic-predictor ; https://laion.ai/blog/laion-aesthetics/
23. NIMA PyTorch port: https://github.com/yunxiaoshi/Neural-IMage-Assessment
24. IQA-PyTorch (PolyForm Noncommercial): https://github.com/chaofengc/IQA-PyTorch
25. librosa (ISC): https://github.com/librosa/librosa
26. Beat This! (MIT): https://github.com/CPJKU/beat_this ; https://pypi.org/project/beat-this/
27. BeatNet (CC-BY-4.0): https://github.com/mjhydri/BeatNet
28. madmom licences: https://pypi.org/project/madmom/

Licences were checked through the GitHub licence API on 2026-11-15. CPU timings marked "roughly" are estimates; benchmark them on the target machine before relying on them.
