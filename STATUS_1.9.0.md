# Takebloom 1.9.0: J-cuts, L-cuts and free stock footage (branch wt/stock)

Ideas only from OpenMontage (AGPL-3.0); no code or text copied. All code here is new and MIT.

## Done
- `video_studio/jlcut.py` (new): J-cuts and L-cuts at joins that change clip, camera shot or moment. On by default;
  off with `--hard-cuts` or prompt words such as "hard cuts only", "no J-cuts", "no split edits". Skipped for music
  montages. Rule, amounts and safety checks are written at the top of the module.
- `video_studio/stock.py` (new): optional free stock B-roll from Pexels and Pixabay with the person's own free key
  (`video-studio config set stock.pexels_key KEY`, `stock.pixabay_key`, or `PEXELS_API_KEY` / `PIXABAY_API_KEY`).
  Turned on by `--stock` or prompt words ("stock footage", "b-roll", "Pexels"...). Standard library `urllib` only.
  Writes `exports/credits.txt`.
- Shared files, small grouped edits: `editor.py` (edit options; J/L hook at the end of the cut stage; stock pass after
  B-roll; render uses the J/L voice), `understand.py` (hard-cuts rule, stock line replaced by a key-aware line, two
  switches), `cli.py` (`--hard-cuts`, `--stock`; config list/get/set never print a key), `config.py` (two key settings,
  `shown()`), `packaging/video_studio.spec` (hidden imports).
- No UI change (design freeze): the prompt and the "Takebloom will" card already carry both features.
- Tests: `tests/test_jlcut.py` (14), `tests/test_stock.py` (20), fully offline.
- Docs: `docs/LICENSES.md` (Pexels and Pixabay terms), `docs/KNOWN_LIMITATIONS.md` (new section at the end).

## Real-run evidence (17 Nov 2026, this laptop, `py -3.13` through tb_heavy.py)
- Real 2-clip edit (test clips with a pink-noise room and a soft cafe chord added), 9:16, `--no-ai`, draft:
  `video-studio edit room_one.mp4 cafe_two.mp4 "a 9:16 reel about my tea shop, keep everything"`.
  9 pieces, 15.85 s, QA 100/100. One join changes clip: L-cut at 9.615 s, sound trails by 0.32 s (handle -30.9 dBFS).
- Voice track: J/L and plain both 15.8483 s (same samples as the film). Samples differ only in 9.600-9.929 s; every
  other sample is identical. Under the new picture the level goes from -41.9 to -22.8 dBFS (9.62-9.67 s), then fades.
  Biggest sample step at the join 0.155, plain 0.153, the film's 99.9th percentile 0.165: no click.
- Found and fixed: the old voice track (cut pieces' sound joined end to end) ran early by 0, 30, 50, 60, 75, 75, 88,
  88, 95 ms at the 9 pieces (cross-correlation with the cleaned source). The new track: 0 or 1 sample at 48 kHz.
- Stock, real render, no key: the log says "Stock footage skipped. Free stock footage needs your own free key..." and
  the edit finished (QA 100/100). Stock with a local stand-in for the Pexels API (real urllib, real download, real
  overlay): one request `query=tea&orientation=portrait&size=medium&per_page=15` with the key header, one 33 MB clip
  cached, cutaway 1.55-3.78 s, `exports/credits.txt` written.
- Tests: full suite 243 OK (packaging venv and py -3.13); `node tests/test_file_input.cjs` passes.

## Not done / not tested
- No live Pexels or Pixabay call: no key was available on this PC, and no account was made. The HTTP code is tested
  only against recorded answers shaped like the public API documents.
