# wt/timeline — status 17 Nov 2026

Goal: after the automatic edit, the person can fix anything on a timeline built on HyperFrames, then get the film again.

## What is in this branch
- `video_studio/timeline.py` (new): the finished edit as a HyperFrames composition `timeline/index.html` (tracks: pieces,
  B-roll, pictures, overlays, captions, music; standard `data-start/duration/media-start/track-index/volume`, media
  hard-linked into `timeline/media`, passes `hyperframes lint` 0/0). `load`/`save`/`normalize` (paths and crops always
  from the finished edit), `rebuild` (re-cut only changed pieces), `render` (rebuild + resume from the render stage).
- `video_studio/studio_ui/timeline.js` (new): "Fix on the timeline" panel under a finished edit. Trim (drag edges or
  numbers), reorder (drag or buttons), delete pieces; move/delete pictures and B-roll; edit/retime/delete captions; music
  level and on/off; undo/redo; Space, Delete, Ctrl+Z, Ctrl+Y; live preview of the cut with captions; autosave.
- Small hooks (shared files): `editor.py` (piece crop recorded; render reads `manifest.timeline` for caption words,
  B-roll, music level/track), `pictures.py` (timeline places), `studio.py` (`GET/POST /api/timeline`,
  `POST /api/timeline/render`, serves `/timeline.js`), `cli.py` (`video-studio timeline export|render`),
  `index.html` (one script tag), packaging spec + smoke.
- Tests: `tests/test_timeline.py` (13, offline; 2 use tiny FFmpeg clips).

## Render path chosen
FFmpeg (Takebloom's own render, resumed from the render stage) over a HyperFrames render of the composition.
See "Timeline 1.9.0" in docs/KNOWN_LIMITATIONS.md for the measured numbers.

## Not done
- Overlays (big text, motion graphics from wt/motion) are shown read-only and placed again by the edit.
- No split/duplicate of a piece, no adding new B-roll/pictures from the timeline.
