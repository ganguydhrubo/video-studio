# Speech work-stream (1.9.0) - status, 17 Nov 2026 (evening)

Branch `wt/speech`, based on 520ff1f. Product direction (17 Nov): Takebloom edits the person's own footage only, so
this stream covers speech in real footage (faster-whisper, word times, captions, karaoke) and the Windows/Kokoro voice
where Takebloom still speaks. No generation features. `ai.py` untouched. No UI change.

## Done and verified

### 1. SAPI word times were 1.5x too big: FIXED (`engines/tts.py`)
- Measured: System.Speech reports word positions as if the audio were 16 kHz, whatever the file format. Zira and
  David at 8, 16, 22.05, 24, 44.1 and 48 kHz: the reported times grow exactly with the rate (24 kHz = x1.5).
  Even the voice's own default format (22.05 kHz) is wrong, so "read the native rate" would not have fixed it.
- Fix: the SAPI script now speaks a `PromptBuilder` with a bookmark after the last word. The bookmark's position is on
  the same wrong clock, so `true scale = real wav length / bookmark position` (works for any voice or format).
  Without a usable bookmark the measured 16/24 scale is used. New pure function `tts.sapi_words()`.
- Cache key for SAPI lines gets "t2", so old lines with wrong times are made again.
- Proof (`%TEMP%\tb-speech\cmp_sapi.py`, 11 Zira lines, 65 s, against faster-whisper small word starts, 180 words):
  before: mean 1318 ms, max 2930 ms (always late). After: mean 71 ms, median 52 ms.
  The fixed SAPI first-word starts are 0-115 ms before the loudness onset (the onset is late for "F"/"Th"); the
  remaining 71 ms is whisper's own error, so the fixed SAPI times are now the reference used below.

### 2. Word starts inside pauses: FIXED for real footage (`video_studio/speech.py`, `engines/ml.py`)
- Measured against the true SAPI times: whisper is early on average (-66 ms) and the first word after a pause can
  start inside the pause, up to 0.84 s early. The caption then lit up on silence.
- `speech.trim_silent_starts()` (NumPy only, in the app): if after a word's start there is a real pause (>= 120 ms
  quiet) with at most 150 ms of sound before it, the start moves to where the sound comes back. Running speech and
  short stop-consonant gaps are never touched. Applied in `ml.transcribe()` on the 16 kHz audio it already decodes,
  so editing, shorts, B-roll and Kokoro alignment all get it. Result has `moved_starts`.
- Tried and dropped: snapping every start to the nearest loudness rise made it worse (mean 71 -> 77 ms).
- Results (small model, start error against the true times):
  - Zira 65 s clean: mean 71 -> 60 ms, max 838 -> 345 ms, words over 150 ms 14 -> 8.
  - David 68 s with loud pink noise (-30 dB): mean 147 -> 134 ms, max 746 -> 306 ms.
  - Two voices 165 s with noise (-38 dB): mean 101 -> 95 ms, max unchanged 689 ms (no pause before that word).

### 3. Karaoke highlight ran early after every pause: FIXED (`engines/captions.py`)
- ASS `\kf` times run one after another. 1.8.1 used only each word's length, so every pause inside a caption line
  made all later words light up early by the sum of the pauses. New `captions.karaoke_body()` puts each pause on the
  space before the next word (`{\k..}`), with positions rounded from the line start (no rounding drift).

### 4. Batched transcription: measured, stays OFF by default (`helpers/ml_helper.py --batch N`)
- With `without_timestamps=False` faster-whisper 1.2.1 drops the unfinished last sentence of each 30 s piece.
  So timestamps stay off; each piece is split back into sentences by `_sentences()`.
- Real runs (`%TEMP%\tb-speech\bench2.py`, small, English, work time without model load):
  - Zira 65 s: normal 27.1 s, b8 23.6 s (-13%). Words 182 vs 181: batched LOST one real word ("month, and tell").
  - David 68 s noisy: normal 30.5 s, b8 24.0 s (-21%). Words equal (182, same 179 correct).
  - 165 s noisy: normal 60.2 s, b8 51.6 s (-14%), b4 + 8 threads 43.1 s (-28%). Words 421 vs 422 (one more right).
  - Start error is a little worse batched (165 s: 101 vs 108 ms mean). Peak RAM 0.71 GB vs 1.24 GB at b8.
  - Threads 8 vs 14 in normal mode: 3-4% only, within noise. Not changed.
- Decision: faster, but not "drops nothing" (lost a word on one clip), and a hallucination guard in batched mode
  throws away a whole 30 s piece instead of one sentence, with no temperature fallback. Left as an opt-in helper flag.

## End-to-end check (real edit): see the bottom of this file

## Tests
- `tests/test_speech.py` (22 tests, offline, about 0.2 s): SAPI scale from the bookmark / fallback / bad rows / real
  file length / cache key; pause trimming on synthetic voice bursts (clean, room noise, fading tail, stop gaps, long
  sound, noise only, next-word limit); `ml.transcribe` with a mocked helper; karaoke timing read back from the ASS.
- Full suite: `python -m unittest discover -s tests` and `node tests/test_file_input.cjs` pass.

## Not done (left for later, nothing half-built in the code)
- Speaker detection (sherpa-onnx): not started, nothing downloaded.
- OpenVINO: not tried.
- Batched transcription by default: not proven safe (see 4).
- Kokoro still gives no word times of its own; its lines are aligned by whisper base.en (now with pause trimming).
