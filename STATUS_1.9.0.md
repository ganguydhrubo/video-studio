# Learning work-stream (1.9.0) - status, 17 Nov 2026

Branch `wt/learn`, based on `wt/auto` ac91c13. Takebloom now learns from every edit, on this PC, with plain maths (no AI).

## Done
- `video_studio/signals.py` (new): one append-only file `edit_signals.jsonl` in the app home. Signals with weights:
  prompt / Fine-tune choices, refines, transcript reviews (keep/cut labels), covers saved, downloads, shares, stopped
  edits, the same clips edited again with another choice, restyle choices, stars and notes ("too fast", "less zoom").
- Evidence is a decayed Beta score per (choice, value) (0.85 per edit). The same signals nudge the existing taste
  bandit in memory.db (discounted 0.97 per edit), so Takebloom's own picks drift too. Ratings are not counted twice.
- Prediction: when the prompt, Fine-tune, a style template, "Edit like" or a dropped song leave a choice open, a
  confident learned value is applied (`--pacing fast` etc.) and said in the "Takebloom will" card with its real count:
  "Pace: fast (learned: you chose it in 2 of your last 2 edits)". Explicit choices always win. Passive downloads of
  Takebloom's own choices never become a suggestion.
- Keep/cut model retrains in a background thread after an edit finishes, from the person's reviews (2+ recordings,
  something new). `learn.fit` uses NumPy (same maths, unit-tested equal). A hand-trained model is kept unless it is off.
- UI (design unchanged): learned lines in the existing card (`understand.js`), "Learned from your edits" block with real
  counts and "Forget what you learned" on the System page (`studio_ui/learned.js`), refine chips "Less zoom" and
  "No captions".
- Endpoints: `GET /api/learned`, `POST /api/learned/forget`, `POST /api/signal` (share actions), `learned` in
  `/api/brief/understand`; hooks in start_job, refine/review, cancel, feedback, covers, `/media?download=1`, job finish.
- Tests: `tests/test_learning.py` (14, offline), incl. a 9-edit simulated sequence. Full suite 196 OK (venv and py -3.13),
  `node tests/test_file_input.cjs` OK. `packaging/smoke.py` also fixed (a mis-indented line from wt/auto).

## Real runs (port 7886, home %TEMP%\tb-learn, through tb_heavy)
- Edit 1 "fast reel, no captions" (clip_two, 502 s in a busy queue) + download; edit 2 "no captions please" + Fine-tune
  fast (clip_one, 530 s) + download, 5-star rating, Instagram share (counted once with the download).
- Card for the next empty prompt: "Pace: fast ...2 of your last 2 edits", "Captions: none ...". With "slow, karaoke" in
  the prompt: no learned lines. Edit 3 (empty prompt) got `--pacing fast --captions none`; its film used fast / none.
- Reviews on both films (one line unticked each) -> re-edits 104 s / 84 s -> background retrain: 57 words, model off
  (balanced 0.553 vs rules 0.5, accuracy lower). Edge page check: card and System block render, no page errors.

## Not done / open
- CLI `video-studio feedback` does not write a signal (only the window does).
- Length, format, story and opening are recorded but not predicted.
- Keep/cut needs many more reviews before it can turn on; untested at scale.
