# Motion graphics and 3D (branch wt/motion), status 17 Nov 2026

## Done
- `video_studio/motion.py`: plans, draws and places motion pieces inside an automatic edit. Kinds: animated title,
  3D title (three.js extruded letters, fly-in with spin or sway), name tag (lower third), kinetic text on key words,
  end card, simple 3D objects (gem, diamond, cube, ring, knot, ball, star, heart), confetti, sparkles, light leaks.
  Styles: clean, bold, neon, gold, elegant (from the prompt, template or colour look).
- `video_studio/motion_templates/`: one HTML page per kind (GSAP for 2D, three.js for 3D, canvas for particles) plus
  `kit.js`. Vendored offline: three.js 0.187.0 tree-shaken bundle (577 KB, MIT) and a Latin 3D font made from the
  bundled Mukta ExtraBold (OFL). GSAP comes from the HyperFrames install. Recipe in `packaging/motion_vendor/`.
- Drawn by HyperFrames as transparent PNG sequences, all new pieces in one browser run (iframes in one batch page),
  cached by page hash in `cache\motion` (600 MB cap), laid over the cut in the same FFmpeg encode as captions and grade
  (`compose.assemble_video` now takes PNG-sequence overlays with crop and scale).
- Placement avoids slams, B-roll and pictures; captions pause under titles, kinetic words and the end card; the name
  tag sits above the captions. Name tag and title shorten in busy films; what is left out is logged with the reason.
- Prompt reader (`understand.py`): motion words become `--graphics` kinds with their text (quoted title, name, place,
  end text, shape, spin, confetti at start or end, style). 3D text moved from "cannot" to supported; 3D scenes from
  footage stay in "cannot". "neon lower third" no longer turns the whole film cyberpunk. The prompt card says when
  HyperFrames is missing. Templates add a light default (wedding, travel, product advert, viral short and others).
- CLI `edit --graphics auto|off|<kinds>`. Packaging: spec datas + hidden import; `smoke.py` checks the bundle (its
  existing indentation error was fixed).
- Tests: `tests/test_motion.py` (22 tests, renderer mocked). Full suite 204 tests OK, `test_file_input.cjs` OK.

## Real runs (CLI, `--no-ai`, clip_one + clip_two from tb-auto-media)
- Reel 9:16, "3D title ... that spins, neon lower third with my name Asha Rao, confetti at the end": QA 100/100.
  First run 6m57s with motion 27 s (name tag left out, film too busy; fixed). Second run 4m40s, motion 16.5 s
  (3D title from cache, name tag 9.9-12.9 s, confetti 14.8-17.6 s).
- Widescreen 16:9, gold 3D title, name tag with place, kinetic text, 3D stars, end card, confetti: 6 pieces, motion
  43 s of a 4m30s edit, QA 100/100. Frames checked at 3.5, 6.4, 11.0 and 16.6 s.

## Not done / limits
- No new transitions: custom FFmpeg xfade expressions measured 3.6 s per frame (too slow). Light leaks on cuts instead.
- Placement cannot be chosen by hand; no UI control beyond the prompt and templates (design freeze).
- 3D letters are Latin only; Indian-script titles fall back to the flat title.
- The 16:9 3D title framing was tightened after the real run and checked only in a template render, not a full edit.
- Not tested through the Studio window (only the CLI and unit tests of the studio path).
