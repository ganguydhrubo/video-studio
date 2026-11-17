# Cut-out 1.9.0 work-stream: status (17 Nov 2026)

Branch `wt/cutout`, based on 520ff1f. Optional **Best quality** cut-out is built, unit-tested and really run.
Fast (MediaPipe) stays the default and its cached masks are reused unchanged.

## What was built

- `video_studio/matting.py` (new, app side): BiRefNet lite model entry (URL, 224,005,088 bytes, MIT, SHA-256
  `5600024376f572a557870a5eb0afb1e5961636bef4e1e22132025467d0f03333`), `ensure(allow_download)` into
  `engines/ml.models_dir()/birefnet/` (refuses without permission, deletes on a bad checksum), measured-speed store
  `cutout_speed.json` in app data, `estimate()` / `describe()` (only from speeds measured on this PC), helper `call()`.
- `video_studio/helpers/matte_helper.py` (new, runs on the user's Python): `walk()` keyframe scheduler (pure, tested),
  `BiRefNet` (DirectML if present, CPU fallback on failure, sigmoid on logits), DIS-flow `warp()`, `best` and `bench`
  subcommands. Keyframes: 2 a second + shot changes + usually the last frame; in between, both keyframe alphas are
  warped and blended by distance; MediaPipe decides where they disagree a lot (>2% of pixels over 0.5); guided filter
  + light temporal smoothing on every frame.
- Shared files (small, grouped): `cutout.py` (`run(quality=, allow_download=)`, `_best_matte`, cache key unchanged for
  fast), `cli.py` (`cutout --best --allow-download`, precheck task `cutout_best`), `studio.py` (`_quick_args` passes
  them; `/cutout-quality.js`; `/api/cutout/quality?file=`), `machine.py` (feature `cutout_best`, `TASK_WORK`,
  measured estimate in `check()`), `index.html` (one select, one tick box, one hint line in the cut-out options, one
  line in `create()`, one script tag), `studio_ui/cutout-quality.js` (new), `packaging/video_studio.spec`.
- Tests: `tests/test_cutout_best.py` (24 tests, model/download mocked). Docs: `docs/LICENSES.md`,
  `## Cut-out 1.9.0` in `docs/KNOWN_LIMITATIONS.md`.

## Real runs (py 3.13, onnxruntime 1.30 CPU, other agents' jobs running, CPU 70-100%)

- Download through `matting.ensure(allow_download=True)` into a scratch models folder: 22.7 s, SHA-256 matched.
- BiRefNet bench (1024 input): 6 threads 16.9 s/frame, 8 threads 31.7 s, 12 threads 23.0 s (noisy under load).
- 6 s 1080x1920 30 fps synthetic clip (public-domain NASA astronaut still: pan, zoom, one hard cut), colour background:
  Fast 23.1 s total (mask 17.7 s, 7.8 fps). Best 375 s total (mask 366.8 s, 13 keyframes at 20.5 s each, 1 cut found,
  90 MediaPipe fixes before the threshold was raised, peak working set 7.1 GB).
- Visual: contact sheet `%TEMP%\tb-cutout\compare\contact_sheet.jpg`. Fast cuts holes in the suit collar ring and
  shoulder; Best keeps the whole suit and a cleaner hair line, and also keeps the held helmet.
- After that run: warp grid cached and guided filter at ~640 px coefficients (warp 147 -> 66 ms, guided 149 -> 74 ms
  a frame); MediaPipe fix threshold 0.4% -> 2%.

## Open

- Peak memory 7 GB is too high for a 16 GB laptop with other apps open; see the memory test below.
- Not run on real footage of a moving person; no object picker; no spill removal; DirectML not usable here.
