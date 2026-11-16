# wt/speed — status 17 Nov 2026 (WIP, NOT verified)

Goal: make the style pass (helpers/style_helper.py) much faster. Real edit 16 Nov 23:22 (52 s clip): style pass = 13 of 20.5 min,
ran with 1 worker (engines/style.py _parallelism gives 1 worker when free RAM < ~3.3 GB) at ~2 frames/s.

Profile (60 frames, original code): MediaPipe segmentation 32 s (every frame), numpy full-frame maths 25 s, bloom 9 s.

Changed (uncommitted before this WIP commit):
- Matte.__call__(bgr, k, need): full matte only inside slam/invert windows (matte_needed(t), 0.4 s lead-in);
  elsewhere segmentation every FACE_STEP=4 frames just for the face; camera still eases to the face every frame.
- bloom() and new finish() use OpenCV multi-core ops (VIGN3/GRAIN3 precomputed, grain strength baked in).
- frame float conversion in one pass; ZERO_MATTE stands in when the matte is not drawn.

Measured (CPU shared with 3 other agents, so noisy): slam range 180-260: 101 s -> 68 s; plain range 300-360: 79 s -> 49 s.
Picture match vs original: PSNR 32.4 dB on one range (the other comparison printed nothing) -> NOT acceptable yet.
Likely cause: camera path (face eased toward a target measured every 4 frames instead of every frame).

Next:
1. Re-measure on an idle PC. Compare frames visually (ffmpeg -vf select + hstack) old vs new.
2. If the camera path differs: interpolate the face target linearly between measurements, or run segmentation
   at 1/8 size every frame for the face only. Then expect PSNR > 40 dB except intentional changes.
3. Grain/vignette maths must match exactly (check with a matte-free plan).
4. Hardware encoder choice: measured on this laptop QSV veryfast 5.19 s VMAF 96.6 vs x264 medium (the standard
   preset) 9.92 s VMAF 96.9 for 20 s 1080x1920 -> QSV ~1.9x faster at equal quality. Plan: machine.benchmark()
   (2 s test, x264 vs each working hw encoder, VMAF if libvmaf), store in machine.json, ff.x264_args picks the
   faster encoder for FINAL encodes only, with automatic x264 retry if a hw encode fails (qsv needs nv12, not yuv420p).
5. Use measured benchmark fps in machine.assess() time estimates.
Test inputs: %TEMP%\tb-speed\ (spec.json from scratchpad mkspec.py, base_180.mp4 / base_300.mp4 = original output).
