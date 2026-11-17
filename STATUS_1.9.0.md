# wt/speed — status 17 Nov 2026 (DONE, committed, not merged)

Branch wt/speed, based on 520ff1f. Footage edit only (ingest, sense, cut, style pass, render, export). ai.py untouched, no UI change.

## What changed
- Style pass (helpers/style_helper.py, engines/style.py): two passes. Pass 1 = matte/face tracking in parallel
  processes (24-frame warm-up per part, face targets + small mattes only where a slam/invert needs them). Pass 2 =
  face path eased over the whole video, then compositing in parallel parts. OpenCV maths with per-channel scalars,
  cached grain/vignette/cards/shadows. Workers from free memory: tracking 0.5 GB, compositing 0.8 GB each at
  1080x1920, 1.5 GB kept free, max 6.
- Fixed in passing: the old multi-worker style pass restarted the camera at the centre in every part, and part seeks
  rounded up and skipped one frame. One worker and many workers now give the same video.
- encoders.py (new): final encode picks the fastest working hardware encoder after a one-time test (slope of 15- vs
  90-frame encodes, quality by VMAF or PSNR, >= 1.25x faster and <= 1 VMAF point worse), cached in encoder.json per
  FFmpeg build; x264 fallback on any failure; config `render.encoder` forces one; TAKEBLOOM_NO_ENCODER_TEST skips.
  Used by compose (final master) and export (reframe).
- Sense (sense_helper.py, engines/sense.py): frame rows in up to 4 time ranges + audio in parallel processes. Same JSON.
- speed.py (new) + editor.py: voice noise reduction starts in the background at transcription, prosody at sense,
  cut pieces encoded up to 3 at a time. Same files.
- tests/test_speed.py (offline; the real-model test is opt-in with TB_TEST_MATTE_MODEL).

## Measured (55 s 1080x1920 test clip, "45 second reel, bold, remove mistakes", --no-ai, whisper small)
PC shared with other agents, so all times are noisy. One queued run, old code then new code:
- Total 533.9 s -> 368.7 s (8m53s -> 6m08s, -31%). Peak memory of one process 1142 MB -> 716 MB; whole tree
  2766 MB -> 3853 MB (more workers at once).
- Stages old -> new: ingest 11.1 -> 7.7, transcribe 23.9 -> 18.3, sense 75.4 -> 27.7, plan 3.9 -> 0.2, cut 36.4 -> 16.5,
  render 374.3 -> 285.5 (style pass 301 -> 264 s with 2 -> 4+6 workers; finishing 62 -> 12 s), validate 6.9 -> 10.7.
- cut.mov, transcript, sense, edit plan, style plan and cleaned voice: byte-identical old vs new.
- Style pass alone on the identical plan (separate queued runs): original 1 worker 674.7 s (peak process 1128 MB);
  new 1+1 workers 402.2 s (794 MB); new 4 drawing + 6 tracking workers 229.7 s (after the seek fix).
- Picture vs original 1 worker, same plan: new 1+1 mean 50.8 dB, min 45.3 dB; new 4+6 mean 45.7 dB, min 42.8 dB,
  0 frames below 40. Before the seek fix the 4+6 run had one frame at 28.1 dB (frame 612, a part start that took
  the next source frame because cut.mov has timestamp gaps at piece joins). Fix: -copyts + 1 s lead-in + trim=start_pts
  (style and sense). Seek scan of the real cut.mov: old seek wrong at 53 of 170 part starts, new seek 0 of 170.
  Comparisons against other edit runs are not valid: the look (palette) is chosen at random per run.
- Encoder test here: h264_qsv chosen, 5.5 vs 29 ms a frame, VMAF 84.1 vs 84.1; test took 13.5 s.

## Not done / not proven
- The full edit above ran before the seek fix (a1a0561); the fix was checked with the style pass alone.
- NVENC and AMF only unit-tested (no such card here). Transcription unchanged. Noise reduction itself is still one core.
- Edits read the shared model store %LOCALAPPDATA%\VideoStudio\models (read only, nothing written) because
  ml.models_dir() ignores VIDEO_STUDIO_HOME; set VIDEO_STUDIO_MODELS for full isolation next time.
- Bench scripts are in the session scratchpad (runedit.py, finalbench.py, psnrbench.py, memwatch.py, psnr.py).
