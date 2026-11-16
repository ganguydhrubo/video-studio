# Cut-out 1.9.0 work-stream: status (stopped 17 Nov 2026, owner ended the day)

Branch `wt/cutout`, based on 520ff1f. **No product code has been changed yet.** The first session ended before any
edit was saved; the second session only did research and measurements. Everything below is research to build on.

## Done and verified

- Read `cutout.py`, `helpers/ml_helper.py` (`matte`, `_Segmenter`, `_guided`, `_mask_encoder`), `restyle.py`,
  `machine.py` (`TASK_WORK`, `check`, `assess`), `cli.py` (`cmd_cutout`, parser ~line 949), `studio.py`
  (`_quick_args` cutout branch ~line 933, `/brief-assistant.js` route ~line 459, `/api/machine/check`),
  `index.html` (cut-out options ~line 601, `create()` ~line 1220), `tests/test_cutout.py`, `packaging/video_studio.spec`.
- Model found and downloaded (to scratch only, not the app's model folder):
  - **BiRefNet lite (swin-v1-tiny), general**: `https://github.com/ZhengPeng7/BiRefNet/releases/download/v1/BiRefNet-general-bb_swin_v1_tiny-epoch_232.onnx`
  - Size 224,005,088 bytes (214 MB). SHA-256 (computed here; the publisher gives none):
    `5600024376f572a557870a5eb0afb1e5961636bef4e1e22132025467d0f03333`
  - Licence MIT (ZhengPeng7/BiRefNet; rembg, which mirrors the same file, is also MIT).
  - Input `input_image` float32 [1,3,1024,1024] (fixed: 512 and 768 fail at `/decoder/Split_64`, so the size
    cannot be lowered by editing the graph). Output `output_image` [1,1,1024,1024], **logits** (apply sigmoid).
    Preprocess like rembg: RGB, /255, ImageNet mean/std, NCHW.
  - Graph has 80 GatherND + 72 ScatterND (deformable conv as gathers), which is why CPU is slow.
- MediaPipe selfie model SHA-256 (same URL as `setup.MODELS`): `c6748b1253a99067ef71f7e26ca71096cd449baefa8f101900ea23016507e0e0`, 16,371,837 bytes.
- Other BiRefNet ONNX files in the same release are 940 MB to 1.1 GB (general, portrait, matting, HR, 512 fp16): too heavy, leave out.

## Speed measured (CPU was at 99-100% from other agents, so these are worst case)

- BiRefNet lite, CPU, onnxruntime 1.30 on py 3.13: session load 6-9 s; one 1024x1024 frame **22-47 s with 6-8 threads**
  under full load. Must be measured again on a quiet PC (expect a few seconds a frame). Either way, BiRefNet on every
  frame ("best") will be very slow on this laptop; "balanced" needs keyframes far apart plus flow.
- **DirectML: does not work here.** In a scratch py 3.12 venv, `onnxruntime-directml` 1.24.4 shows
  `DmlExecutionProvider`, but the first BiRefNet run fails on Intel UHD Graphics (driver 32.0.101.7076) with
  `887A0020 ... driver's state is probably suspect`.
- **Do not add `onnxruntime-directml` to `setup.PIP_PACKAGES`:** `kokoro-onnx` depends on `onnxruntime`; installing both
  made pip put `onnxruntime` 1.32 over it and the DML provider disappeared (checked in the scratch venv). It only works
  after uninstalling `onnxruntime` and force-reinstalling the DirectML wheel. Code may still *use* DML when present, but
  must fall back to CPU when a run fails.
- OpenCV 5.0 on py 3.13 here has **no CSRT or KCF tracker** (only MIL, Nano, Vit, DaSiamRPN; the last three need models).
  `cv2.DISOpticalFlow_create` exists. So the object tracker must fall back to a DIS median-flow box tracker.

## Planned design (not written yet)

1. `video_studio/matting.py` (new, app side, pure Python): BiRefNet model entry (URL, size, SHA-256, licence), path in
   `engines/ml.models_dir()/birefnet/`, `ensure(allow_download=False)` that refuses without approval and checks SHA-256,
   `pick_quality(requested, seconds, tier, model_present, box)`, per-quality time estimate (defaults + measured speeds
   saved in app data), cache-key parts (model + quality + box/at).
2. `video_studio/helpers/matte_helper.py` (new; spec glob `*_helper.py` already bundles it). Loaded from `ml_helper.matte`
   via importlib when quality is not `fast` or a box is given; `fast` stays exactly today's path.
   - `BiRefNet` class: DML then CPU providers, CPU fallback on a failed run, sigmoid on output.
   - Keyframes: every N frames + shot change (thumbnail difference) + last frame; `best` = every frame.
   - Between keyframes: DIS flow warp of alpha forwards from the previous key and backwards from the next key, blended
     by distance; MediaPipe mask fixes only gross disagreement (person mode).
   - Keyframes: flow-compensated temporal smoothing. Full-resolution guided-filter edge refine (small radius),
     colour-distance check in the uncertain band (dark-area bleed), blur-fusion foreground colour estimate written as a
     half-size "clean edge" video for spill suppression (FFmpeg `maskedmerge` in gbrp inside the edge band).
   - Object mode: phase 1 tracks the box (CSRT/KCF if present, else DIS median-flow) forwards and backwards from `--at`
     (small JPEG-cached frames), smoothed; phase 2 runs BiRefNet on a 20% padded crop at keyframes, flow in between,
     alpha clamped to the padded box. `fast` upgrades to `balanced` in object mode.
   - SAM 2: not tried. Leave it out unless a small Apache-2.0 ONNX export clearly beats the crop approach.
3. `cutout.py`: `run(..., quality="auto", box=None, at=0.0, allow_download=False)`, new cache key, spill graph.
4. Small shared edits: `cli.py` (`--quality --box --at --allow-download`, estimate in precheck), `studio.py`
   (`_quick_args` passes them; `/cutout-picker.js` route; small `/api/cutout` status), `machine.py` (`check()` takes an
   estimate), `index.html` (one script tag + one line in `create()` merging `CutoutPicker.options()`),
   new `studio_ui/cutout-picker.js` (drag a box on a still frame, quality select, download permission tick).
5. Tests `tests/test_cutout_hq.py` (mock onnxruntime; quality choice, keyframes/shot change, flow shape/dtype,
   tracker fallback, cache keys, download refused without approval). Docs: `docs/LICENSES.md`,
   `## Cut-out 1.9.0` at the end of `docs/KNOWN_LIMITATIONS.md`.

## Exact next steps

1. On a quiet PC (check `curl -s http://127.0.0.1:7860/api/jobs`), re-run the CPU benchmark with 6 and 12 threads to
   get the true seconds per frame; choose the balanced keyframe gap from it.
2. Write `matting.py` + `matte_helper.py` + tests, commit.
3. Wire cutout.py / cli / studio / picker, commit.
4. Real runs with `VIDEO_STUDIO_HOME` and `VIDEO_STUDIO_MODELS` set to scratch folders (never the real ones):
   fast / balanced / best on a 6 s 1080x1920 clip, plus one object box; fps, peak RAM, contact sheets.

Scratch files from this session (probe scripts, the downloaded model, the DML venv) are in
`%TEMP%\claude\...\scratchpad\cutout\`.
