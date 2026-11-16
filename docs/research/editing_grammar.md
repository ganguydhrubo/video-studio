# Editing grammar for automatic talking-head reels

Research date: 2026-11-15. Scope: rules and small local models (CPU, 16 GB RAM) for Hindi, Hinglish and English talking-head shorts. No LLM in the decision loop.

**How much to trust the numbers.** No platform publishes editing specs. Almost every numeric "rule" below comes from editors' tutorials or caption- and clip-tool vendor blogs. These are useful as defaults, not as laws. Netflix subtitle specs, Auto-Editor defaults and the academic papers are the only firm sources. Treat every number as a **tunable default that you then fit to your own EDL pairs** (section 4).

---

## 1. Numeric craft rules

### 1.1 Pacing and shot length

| Rule | Default | Range | Source |
|---|---|---|---|
| Max time on one unchanged frame (no cut, zoom or overlay change) | 3.0 s | 2-4 s | ClipFlip "cut every 2-3 s, past 3 s attention drifts" [1]; faceless guides say 2-4 s [2] |
| Visual change cadence in the first 10 s | every 1.5-2.5 s | 1-3 s | [1][2] |
| Visual change cadence after 10 s | every 3-5 s | 2.5-7 s | first retention dip often at 5-7 s [2] |
| Min shot length (avoid flicker) | 0.6 s | 0.4-0.8 s | practitioner consensus; Vizard warns punch-ins "cycling too fast" [3] |
| Cut-rate cap | 1 cut per 0.8 s on average | n/a | same |
| Long-form attention reset | every 30-60 s | n/a | [2] (for reference only, not reels) |

Codable form: a "staleness" timer that forces a visual event (cut, punch, b-roll, text slam) when `t_since_last_visual_change > 3.0 s`. It must never fire if the next event would land within 0.6 s of the previous one.

### 1.2 Jump-cut hiding with punch-in

| Rule | Default | Range | Source |
|---|---|---|---|
| Punch-in scale for a hard cut | 115% | 110-120% (up to 125%) | 1of10 [4]; FireCut 110-115% [5]; CapCut keyframe guide 110-125% [6] |
| Which cuts get a punch | every 2nd-3rd cut, alternating wide/tight | n/a | [4] |
| Never stack | two tight-tight cuts in a row; return to 100% at a sentence start | n/a | [3][4] |
| Emphasis "slam" zoom | 130-140%, 2-4 frames ease-in, hold to end of word or phrase | n/a | Hormozi-style practice; keep face inside the safe zone |
| Slow push (Ken Burns) on a long take | 100 -> 108% over 3-6 s | 105-110% | [6] |
| Reframe anchor | keep eye line at about 1/3 from the top after scaling | n/a | standard framing |

Scaling 1080p source by 120% costs about 17% resolution. If the source is 4K, punches up to 150% stay sharp. Gate the maximum zoom on the source resolution.

### 1.3 Pause / silence trimming

| Rule | Default | Source |
|---|---|---|
| Silence detection threshold | 4% amplitude (about -28 dBFS), or about 15-20 dB below the speech median | Auto-Editor default `audio:threshold=0.04` [7] |
| Padding kept around speech | 0.2 s each side (Auto-Editor); 0.1 s (jumpcutter) | [7][8] |
| Min silence worth cutting | 0.5 s (jumpcutter); for reels use 0.25-0.35 s | [8]; reel practice is tighter |
| Keep a "breath" pause before a punchline or reveal | 0.25-0.4 s | craft convention: the pause is the emphasis |
| Filler words ("um", "uh", Hindi "matlab", "vo", "haan to") | cut if isolated with more than 80 ms gap on both sides | PodcastFillers task framing [9] |
| Retakes / false starts | if an n-gram of 3 or more words repeats within 10 s, keep the **last** take | standard editor practice (Riverside "stumbles and restarts" [10]) |

Use the threshold relative to each clip (percentile of the RMS envelope) rather than absolute. Phone recordings vary by more than 10 dB.

### 1.4 J/L cuts

| Rule | Default | Source |
|---|---|---|
| J-cut audio lead (next clip's audio starts before its picture) | 4-8 frames at 30 fps (0.13-0.27 s) | 3-12 frames [11]; CapCut 6-12 frames at 30 fps [12]; FireCut 1-10 frames [13] |
| L-cut (previous audio continues over the new picture, e.g. into b-roll) | 6-15 frames | [11] |
| B-roll in/out | picture leads speech by about 0.2 s on entry, holds about 0.3 s after the phrase | derived from the same ranges |
| Audio crossfade at every cut | 10-20 ms equal-power fade | avoids clicks (standard DSP practice) |

On a single-camera talking head, J/L cuts mostly matter at b-roll boundaries and jump-cut joins. A 2-4 frame audio pre-roll hides a hard visual cut well.

### 1.5 Hook (first 1-3 s)

| Rule | Default | Source |
|---|---|---|
| First spoken word | by 0.3 s (trim all lead-in silence, no logo or intro) | vidIQ / Subscribr: intro cards, logos and "hey guys" kill the hook [14][15] |
| Hook length | 1.5-3 s, one claim, question or outcome | Opus hook formulas [16] |
| Visual events in the first 3 s | 2-3 (cold-open punch, text slam of the key phrase, SFX hit) | [1][16] |
| On-screen text in frame 0 | yes, 2-5 words, the "promise" | caption-tool practice |
| Target metric | "viewed vs swiped away" of 70% or more viewed; about 80% retention at 3 s | third-party benchmarks, not official [15][17] |
| Reorder | allowed: lift the strongest line from later in the take to t=0, then replay it in context | MrBeast guide: the first minute is where viewers are lost [18][19] |

A deterministic hook picker scores candidate sentences (section 2). Keep the top sentence of 3-12 words if its score exceeds the take median by 1.5 SD. Otherwise start at the first sentence after the trimmed silence.

### 1.6 Pattern interrupts: what to use when

| Trigger | Device | Constraint |
|---|---|---|
| Staleness timer exceeds 3 s and no emphasis word nearby | punch-in or punch-out (alternate) | min 0.6 s since the last event |
| Emphasis-word score in the top 10% | text slam (word alone, big, 1-3 frames pop) plus optional zoom to 130% | no more than 1 slam per 4 s |
| Number, money, brand, product, place noun | b-roll, icon or logo cutaway 1.2-2.5 s, L-cut audio | no more than 30-40% of runtime off-face |
| Topic shift (new sentence with a discourse marker: "but", "lekin", "par", "so", "toh", "here's the thing") | whoosh SFX plus hard cut or slide | 1 per section |
| Punchline / reveal | 0.3 s silence beat, then hit SFX plus slam | max 2-3 "hits" per 30 s |
| List items ("first", "pehla", "number one") | numbered card | n/a |

Source cadence: 2-3 s or 3-5 s depending on the guide [2]. Vendor claim: a scene change every 2-3 s averages 82% retention vs 71% at 4-5 s, which is unverified [2]. Warning repeated across sources: a break has to change attention, not merely add a cut [2]. SFX should sit 6-10 dB under the voice.

### 1.7 Captions

| Rule | Default | Source |
|---|---|---|
| Words per caption chunk | 2-3 (max 4; 1 for slam words) | Hormozi-style guides [20][21] |
| Highlighted words per chunk | 1 (max 1-3 per beat) | [20] |
| Font size | 7-9% of frame height (about 135-170 px cap height at 1920) | Blitzcut 7-10%, below 5% unreadable, above 12% too much [22]; OpenClip 7-9% [23] |
| Style | heavy sans (Montserrat/Poppins ExtraBold; Devanagari: Noto Sans Devanagari Bold / Mukta ExtraBold), all caps for Latin, 6-10 px stroke or shadow | [20] |
| Vertical placement | centre line at 55-70% from the top (below the chin, above the UI) | 55-78% [21] |
| Safe zone (1080x1920) | keep inside about 900x1400 centred; avoid the right 120-150 px and the bottom 320-480 px | third-party measurements [24][25] |
| Reading speed ceiling | 17-20 chars/s (English), Hindi up to 22 CPS (Netflix adult) | Netflix guides [26] |
| Min on-screen time | 0.3 s per chunk for word-pop; 5/6 s for sentence subtitles | Netflix general req. [27] |
| Script for Hinglish | match what was said: Roman for English words, Devanagari or Roman for Hindi consistently per video | owner choice |

### 1.8 Music and loudness

| Rule | Default | Source |
|---|---|---|
| Final integrated loudness | -14 LUFS, true peak -1 dBTP | common platform normalisation target [28] |
| Voice level | about -14 to -16 LUFS short-term | [28] |
| Music under speech | 18-24 dB below voice (about -32 to -38 LUFS) | 18-25 dB [28]; -18 narration plus 20 LU gives about -38 LUFS bed [29] |
| Music in gaps / intro / outro | duck released to -6 to -10 dB under the voice level | vendor guides |
| Duck attack / release | 50-150 ms attack, 300-600 ms release (no pumping) | standard side-chain practice |
| SFX peaks | 6-10 dB under the voice peak; whooshes lower | practice |

### 1.9 Retention heuristics worth encoding

- The MrBeast production guide (leaked 2024) focuses on CTR, AVD and AVP, treats the first minute as where most viewers are lost, and escalates stakes over time [18][19]. For reels, the equivalent is: front-load the payoff and never let the middle sag. Put the second-best line at about 60% of runtime.
- Cut everything that does not advance the promise of the hook. In rules: drop sentences with a low "content score" (no nouns or numbers, high filler ratio) when the target duration is exceeded.
- End on the payoff. No "thanks for watching". A loopable last line (cut on the final word with no tail) helps replays on Shorts.

---

## 2. Signals for automatic emphasis and highlight selection

**Acoustic prominence.** Word-level prominence is carried by F0 (pitch excursion), energy and duration. In Suni et al.'s wavelet work, f0 and energy contribute about equally and duration adds independent information. Their continuous-wavelet method matched human 4-level prominence ratings [30][31]. Hindi marks focus mainly with an F0 rise on the focused word and compression after it, more than with lexical stress. So pitch excursion plus post-word drop is a better Hindi cue than loudness alone (general phonetics finding; validate on your own data).

Codable per-word features (from forced-aligned word timestamps, e.g. faster-whisper word times):

| Feature | Computation | Weight (start) |
|---|---|---|
| Pitch excursion | max F0 in word minus speaker median (semitones), z-scored per clip | 0.30 |
| Energy | word RMS dB minus local 3 s median, z-scored | 0.25 |
| Duration | word duration / expected (per-phone or per-char), z-scored | 0.20 |
| Pre-pause | silence before the word over 150 ms | 0.10 |
| Post-pause | silence after over 250 ms (punchline) | 0.15 |

**Lexical cues** (additive bonuses): numbers and currency (₹, "lakh", "crore", "%"), superlatives ("best", "sabse", "never", "kabhi nahi"), negation/contrast ("but", "lekin", "nahi"), second-person ("you", "aap", "tum"), named entities and brands, and words also in the hook. Function words get capped low: a stressed "the" or "hai" must not slam. BERT-based text-only prominence prediction reaches about 83% on the Helsinki Prosody Corpus vs about 82% for a BiLSTM [32]. Text helps, but not dramatically. A POS/stopword filter plus acoustics captures most of the value.

**Highlight / moment selection research:**
- *Rhapsody* (2025): 13K podcast episodes with segment highlight labels from YouTube "most replayed". Audio features helped a fine-tuned model, but the task "remains difficult" even for state-of-the-art models [33]. In short, highlight detection from speech is not solved; expect modest accuracy.
- Speech-emotion-based podcast highlight detection (log-mel CNN) [34]: arousal is a usable proxy for "interesting".
- Linke and Schuppler 2025: wav2vec2-XLSR fine-tuned as a word prominence detector, 85.5% accuracy on Austrian German conversation. No released checkpoint was found [35].
- Spotify Podcast Dataset (100K episodes) / TREC: linguistic style is predictive of engagement. Large, research-licence only [33].

**Practical score for "which sentence is the hook / which moments to keep":**
`sentence_score = mean(top-3 word prominence) + 0.5*arousal(laughter, energy variance) + lexical_bonus − filler_ratio − 0.3*position_penalty(if in first 2 s of a retake)`.

---

## 3. Open models and datasets: CPU feasibility and real usefulness

| Name | What it does | Licence | Size / CPU | Useful for talking-head reels? |
|---|---|---|---|---|
| **faster-whisper / whisper.cpp** (word timestamps) | ASR plus word times, Hindi/English | MIT | small/medium int8: 0.5-1.5 GB RAM; about 0.3-1x real time on CPU | **Essential.** The backbone for every rule above |
| **Silero VAD** | speech/silence | MIT | about 2 MB, much faster than real time | **Yes.** Better silence cuts than an RMS threshold on noisy rooms |
| **Wavelet Prosody Toolkit** (Suni, Helsinki) [30] | word prominence and boundary strength from F0, energy, duration | MIT | pure DSP, seconds per minute of audio | **Yes. Best ready-made emphasis signal.** Language-agnostic, no training |
| **Praat-parselmouth / librosa pyin** | F0, intensity | GPL-3 (parselmouth) / ISC (librosa) | DSP, fast | Yes. Building blocks if you hand-roll prominence. Watch parselmouth's GPL if you distribute |
| **Helsinki-NLP/prosody** [32] | text-only prominence (BERT) | check repo (dataset from LibriTTS) | about 440 MB BERT-base, OK on CPU | Marginal. English-only; acoustic features beat it for spoken emphasis |
| **wav2vec2 prominence detector** (Linke & Schuppler) [35] | acoustic word prominence | no public weights found | n/a | Not usable now. Same idea is trainable yourself (below) |
| **TransNetV2** | shot boundary detection | MIT [36] | about 30 MB, CPU OK (slow but fine) | Low. Talking-head raw footage has few shots; useful only for analysing *your finished edits* to recover cut positions |
| **Lighthouse** (LINE) [37] | unified moment retrieval/highlight (Moment-DETR, QD-DETR, CG-DETR, UVCOM...) | Apache-2.0 code; QVHighlights annotations CC BY-NC-SA | small heads; CPU only with CLIP features; videos capped at 150 s | **Hype for this use case.** Trained on vlog/news text-query retrieval; a talking head looks the same every frame, so visual highlight scores are near-flat. Non-commercial weights |
| **Moment-DETR / QVHighlights** [38] | text-query moment retrieval | MIT code, NC data | same | Same verdict. Only if you want "find the bit where I talk about X" search |
| **UMT** (CVPR 22) [39] | audio-visual highlight detection | check TencentARC repo | needs GPU-extracted features | No. Feature pipeline heavy; trained on vlogs/YouTube highlights |
| **TVSum / SumMe** | generic video summarisation benchmarks | TVSum videos CC-BY 3.0; SumMe and many derived models non-commercial [40] | n/a | No. Visual-diversity summarisation, wrong signal for monologues |
| **AutoTransition** (ECCV 22) [41] | recommends a transition type per shot pair from vision plus audio, trained on editing-app templates | check repo (acherstyx/AutoTransition) | transformer plus pretrained features, CPU feasible offline | Low-to-moderate. It learns template-style montage transitions, not talking-head grammar. A rule table (1.6) does the same job more controllably |
| **PodcastFillers / FillerNet** [9] | filler-word detection (uh, um) | **non-commercial research only** | small | Research-only; English fillers only. Instead, use Whisper with fillers kept (prompt with "um, uh") plus a Hindi filler lexicon |
| **Rhapsody** [33] | podcast highlight labels from "most replayed" | check arXiv/data card | dataset | Good *idea* to copy: use your own channels' retention graphs as labels |
| **Laughter detection** (e.g. jrgillick/laughter-detection) | laugh events | licence unverified | small CNN, CPU OK | Nice-to-have: laughter marks a keep-worthy moment |
| **MediaPipe Face Detector / Landmarker** | face box for punch-in reframing, blink/mouth-open | Apache-2.0 | a few MB, real-time CPU | **Yes.** Needed so punch-ins stay centred and cuts avoid mid-blink |

**Bottom line.** The academic "highlight detection" and "automatic editing" models (Moment-DETR, UMT, TVSum-trained summarisers, AutoTransition) target multi-shot visual content and mostly carry non-commercial data licences. For a single-speaker talking head the information is in the **audio and the words**, not the pixels. The usable local stack is ASR with word times, VAD, prosody DSP, a face tracker, and your own trained small ranker.

---

## 4. Ranked recommendation

### Hard-code (deterministic; do this first)
1. **Silence and retake trimming**: VAD plus RMS-relative threshold, 0.25-0.35 s min gap, 0.1-0.2 s pad, keep-last-take on repeated n-grams, filler lexicon (EN plus HI).
2. **Hook rule**: first word by 0.3 s; optionally lift the top-scoring sentence (3-12 words) to the front.
3. **Staleness timer**: forced visual event every 3 s (2 s in the first 10 s), min 0.6 s between events.
4. **Punch-in alternation**: 100% / 115% on alternate cuts, 130-140% slams on top-10% prominence words, face-anchored crop, reset at sentence starts.
5. **Caption engine**: 2-3 words/chunk, 1 highlight, 7-9% height, centre at 60-65% height, safe-zone clamp, CPS check.
6. **Audio mix**: voice to -14 LUFS / -1 dBTP; music 18-24 dB under voice with 100 ms / 450 ms duck; SFX 6-10 dB under voice; 10-20 ms crossfades; 2-6 frame J-cut pre-roll at joins.
7. **Device table** (1.6): lexical and prosodic triggers mapped to slam, b-roll, SFX or card, with per-device rate caps.

### Integrate (small, local, permissive)
8. **Wavelet Prosody Toolkit** (MIT) or a hand-rolled F0/energy/duration z-score to get the per-word prominence score. Highest value per hour spent.
9. **Silero VAD, MediaPipe face** for robustness.

### Train from your own past edits (best long-term lever)
You hold **raw footage, EDL JSON and final video** triples. That is exactly the supervision the public datasets lack, and it matches your voice, languages and taste.
10. **Keep/cut classifier per word or segment.** Align the raw transcript to the EDL to label each word kept or cut. Features: prosody z-scores, pause lengths, filler flag, n-gram repeat flag, position, lexical flags. Model: gradient-boosted trees (LightGBM/XGBoost, MIT/Apache, trains in seconds on CPU). With even 20-30 edited videos this will likely beat any public model. It also tells you which hand-tuned thresholds to move.
11. **Emphasis/slam ranker.** Label = the words where your EDL placed a zoom, text slam or SFX. Same features plus a bag of lexical cues. Output: probability, then take the top-k under the rate caps.
12. **Device chooser** (zoom vs b-roll vs slam vs nothing) as a small multiclass GBDT on the same features plus time-since-last-event. This replaces AutoTransition with something trained on *your* grammar.
13. **Later, optional**: once you have more than 100 videos, fine-tune a small wav2vec2/XLS-R-base head (about 95M params, CPU inference feasible, training slow on CPU, so rent a GPU once) for acoustic prominence, following Linke & Schuppler. Only worth it if (11) plateaus.
14. **Outcome labels**: export YouTube Studio and Instagram retention curves per published video and attach dips and rewatch peaks to EDL segments (Rhapsody's idea [33]). Use these to re-weight the rules, not as a model input on day one.

**Don't bother with**: Moment-DETR/Lighthouse/UMT/TVSum summarisers (wrong modality, NC licences), AutoTransition (template montage style), PodcastFillers (NC licence).

**Evaluation**: hold out 20% of your past edits. Metrics: word-level keep/cut F1 vs your EDL, slam-word precision@k, cut-time error (ms) vs your cuts, and final duration error.

---

## Sources
1. ClipFlip, "What we learned from analyzing 44,000 short-form clips" — https://clipflip.io/resources/how-to-make-viral-clips
2. GoFaceless pattern breaks — https://www.gofaceless.ai/blog/optimize-video-engagement-pattern-breaks ; Joyspace pattern interrupts — https://joyspace.ai/pattern-interrupt-reset-attention-span ; Cursa pacing guide — https://cursa.app/en/page/pacing-pattern-interrupts-and-retention-timing-guides
3. Vizard, punch-ins cycling too fast — https://agent.vizard.ai/how-to/stop-the-punch-ins-cycling-too-fast-in-a-talking-head-edit.html
4. 1of10, jump cuts — https://1of10.com/blog/what-is-a-jump-cut/
5. FireCut, zoom in Premiere — https://firecut.ai/blog/how-to-zoom-in-on-premiere-pro-a-step-by-step-guide-for-beginners/
6. Cursa, CapCut keyframes — https://cursa.app/en/page/keyframes-in-capcut-smooth-zooms-reframes-and-motion-that-follows-the-subject
7. Auto-Editor — https://github.com/wyattblue/auto-editor
8. jumpcutter — https://pypi.org/project/jumpcutter/0.1.2
9. PodcastFillers (Adobe Research) — https://podcastfillers.github.io ; licence https://zenodo.org/records/7121457 ; paper https://arxiv.org/abs/2203.15135
10. Riverside, stumbles and restarts — https://riverside.com/university-videos/turn-stumbles-and-restarts-into-clean-intentional-cuts
11. Cutback, J/L cuts — https://cutback.video/blog/jump-cut-vs-j-cut-vs-l-cut-whats-the-difference ; Edicionvideopro — https://edicionvideopro.com/?p=1199
12. CapCut, J/L cuts — https://www.capcut.com/create/j-cuts-and-l-cuts-dialogue-edits
13. FireCut, split edits — https://firecut.ai/blog/mastering-the-split-edit-how-j-cuts-and-l-cuts-work/
14. vidIQ Shorts — https://vidiq.com/blog/post/get-more-views-youtube-shorts/
15. Subscribr, Shorts watch time — https://subscribr.ai/youtube-strategy/youtube-shorts-low-watch-time-boost
16. Opus Clip, Shorts hook formulas — https://www.opus.pro/blog/youtube-shorts-hook-formulas
17. Bytecap Shorts retention benchmarks — https://www.bytecap.io/research/youtube-shorts-retention-benchmarks
18. "How to succeed in MrBeast production" (text) — https://www.alexanderjarvis.com/memo-how-to-succeed-in-mrbeast-production/
19. Tubefilter coverage — https://www.tubefilter.com/2024/09/17/mrbeast-internal-production-guide-leaked-key-points/
20. Fluxnote, Hormozi captions — https://fluxnote.io/guides/hormozi-style-captions-tutorial ; Kapwing template — https://www.kapwing.com/explore/hormozi-style-captions-template
21. Blitzcut, caption placement — https://blitzcutai.com/blog/best-caption-placement-short-form-video
22. Blitzcut, Shorts caption style — https://blitzcutai.com/blog/best-caption-style-youtube-shorts-2026
23. OpenClip caption styling — https://openclip.app/guides/caption-styling-guide.md
24. Reap, safe zones — https://reap.video/blog/short-form-video-safe-zones
25. House of Marketers, safe zones — https://www.houseofmarketers.com/guide-to-safe-zones-tiktok-facebook-instagram-stories
26. Netflix Hindi Timed Text Style Guide — https://backlothelp.netflix.com/hc/en-us/articles/115003196707
27. Netflix Timed Text general requirements — https://backlothelp.netflix.com/hc/en-us/articles/215758617
28. VidPros, music too loud — https://vidpros.com/fix-background-music-too-loud-video/ ; OpenClip ducking — https://openclip.app/learn/audio-ducking
29. Overseeros, music under voiceover — https://www.overseeros.com/blog/background-music-volume-under-voiceover
30. Wavelet Prosody Toolkit (asuni) — mirror https://gitee.com/atomai/wavelet_prosody_toolkit (upstream github.com/asuni/wavelet_prosody_toolkit)
31. Suni et al., hierarchical prosody / CWT — https://arxiv.org/pdf/1510.01949
32. Talman et al. 2019, prominence from text (Helsinki Prosody Corpus) — https://arxiv.org/abs/1908.02262
33. Rhapsody: highlight detection in podcasts (2025) — https://arxiv.org/abs/2505.19429
34. Highlight detection in podcasts, multimodal SER — https://dspace.kmitl.ac.th/items/119fc6f7-d0b3-4605-8106-5c47c9812239/full
35. Linke & Schuppler 2025, prominence-aware ASR — https://arxiv.org/abs/2509.10116
36. TransNetV2 PyTorch — https://pypi.org/project/transnetv2-pytorch/
37. Lighthouse (LINE) — https://github.com/line/lighthouse ; paper https://aclanthology.org/2024.emnlp-demo.6.pdf
38. Moment-DETR / QVHighlights — https://arxiv.org/abs/2107.09609
39. UMT — https://arxiv.org/abs/2203.12745
40. TVSum — https://github.com/yalesong/tvsum
41. AutoTransition — https://arxiv.org/abs/2207.13479 (code: github.com/acherstyx/AutoTransition)
