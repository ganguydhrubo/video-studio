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

## Real-run evidence
See the final report and `docs/KNOWN_LIMITATIONS.md`. Real 2-clip edit in `%TEMP%\tb-stock-runs\jl`.

## Not done / not tested
- No live Pexels or Pixabay call: no key was available on this PC, and no account was made. The HTTP code is tested
  only against recorded answers shaped like the public API documents.
