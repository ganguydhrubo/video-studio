# Speech work-stream (1.9.0) - status when stopped, 17 Nov 2026

Branch `wt/speech`, based on 520ff1f. Only one file changed so far: `video_studio/helpers/ml_helper.py`.

## Done and verified

### Root cause of the mid-word / late caption highlight (SAPI voice): FOUND, not yet fixed
- `engines/tts.py` `_sapi()` saves the Windows voice at 24 kHz (`SpeechAudioFormatInfo(24000, ...)`), but SAPI's
  `SpeakProgress` `AudioPosition` is counted as if the audio were 16 kHz (the voice's own rate).
- So every word time is 1.5 times too big. The error grows along each line: about 0.1 s at the first word, 1 to 2.5 s
  by the last word. The highlight runs later and later, and past the end of the line.
- Evidence: 11 SAPI lines (Zira), SAPI word starts against faster-whisper word starts on the same audio.
  A straight-line fit per line gives slope 0.659 to 0.695 (mean about 0.67, which is 16000/24000).
  The spread after the fit is only 35 to 60 ms (one line 178 ms). So after the fix, the SAPI times are good.
  Script: `%TEMP%\tb-speech\mk60.py` makes the clip. The comparison was run inline (see the steps below).
- `_map_to_script` / `estimate_words` are not the cause. Whisper alignment is skipped for SAPI, because SAPI
  returns all the words.

### Batched transcription (ml_helper.py `transcribe`, new `--batch N`, default 0 = old behaviour)
- Uses `BatchedInferencePipeline`. `without_timestamps` is left at its default (True). Each batched piece of up to
  30 s is split back into sentences by the new `_sentences()`, using word punctuation and pauses.
- Bug found in faster-whisper 1.2.1: with `without_timestamps=False`, the batched mode drops the unfinished last
  sentence of each 30 s piece. In the test, 2 of 11 sentences were lost (163 of 182 words). Do not turn timestamps on.
- The meaning pass (translate) stays one window after another, as before. Its speed is unchanged, and its
  sentence-sized segments still map to lines.
- The output JSON now also has `batch` and `seconds: {load, total}`.

## Speed numbers measured (65 s SAPI speech clip, `%TEMP%\tb-speech\bench\speech60.wav`, 14 threads)
These numbers are noisy: other agents were running tests on the same laptop at the same time.
- Run A: small, one window after another: 46.2 s wall, RTF 0.71, 182 words.
  Batched (with the dropped-sentence bug): b4 36.7 s, b8 42.6 s, b16 38.3 s.
- In-process, model already loaded: small batched b8 (timestamps off) 26.4 s, all 182 words.
  Timestamps on: 33.7 s.
- Run B (machine busier): small, one window after another: 66.7 s (RTF 1.03). Batched b8 with the sentence split:
  79.7 s (13.6 s of that was model load), 181 words.
- Conclusion so far: on this CPU, batching gains little on a 65 s clip. VAD packs only about 3 pieces, so
  batch > 3 adds nothing. **Not proven faster. Do not turn it on by default yet.** The turbo vs small measurement
  was stopped before it finished.
- Ideas not yet measured: `beam_size=1` in batched mode, fewer threads (P-cores only, 6 to 8),
  and longer clips (3 to 10 min), where batching should help more.

## Half-done / not started
- `engines/tts.py` `_sapi()`: SAPI timing fix not written yet.
- Onset snapping: not written yet. Plan: new `video_studio/speech.py`, NumPy only, so it runs inside the app.
- Speaker detection: not started. sherpa-onnx is not installed here, and no models have been downloaded.
- OpenVINO: not tried.
- No tests, no docs, no UI change yet.

## Exact next steps
1. **Fix the SAPI timing in `tts._sapi`.** In the C# code, read the voice's native rate:
   `s.Voice.SupportedAudioFormats` (or keep 24 kHz). Return the native rate with the words, then scale each
   `AudioPosition` by native/24000. Safety net: if the last word start is past the wav duration, rescale the times
   linearly. Check against whisper on `speech60.wav`: the error should drop from about 1.3 s mean to about 50 ms.
2. **Write `video_studio/speech.py`.**
   - `snap_words(words, pcm, sr)`: a 5 ms-hop log-energy and spectral-flux onset envelope. Snap each word start to
     the strongest onset within +/-120 ms, never past the next word.
   - Use it in `tts.synthesize`, after `_map_to_script` (read the 24 kHz wav).
   - Use it in `ml.transcribe`, before the temporary 16 kHz wav is deleted.
   - Test `tests/test_speech.py` with synthetic bursts at known onsets, and assert the error is under 80 ms.
3. **Measure batching on longer audio.**
   - Choose the batch size from free RAM. Peak RAM for small was 0.70 to 0.85 GB at every batch size.
   - Wire `--batch` through `engines/ml.py` `transcribe()`.
   - Turn batching on only if real-time factor improves; report it.
4. **Speaker detection.**
   - Add a `diarize` subcommand to `ml_helper.py`: `sherpa_onnx.OfflineSpeakerDiarization` with
     `OfflineSpeakerSegmentationPyannoteModelConfig` + `SpeakerEmbeddingExtractorConfig` + `FastClusteringConfig`,
     16 kHz float32, giving `.process(audio).sort_by_start_time()`.
   - Attach `speaker` to each word, and split utterances on a speaker change in `editor._utterances`.
   - In `story.build`, `fit` and `accept_review`, keep question -> answer pairs together.
   - Add `"speaker"` to `studio._review_lines` and to the plan utterances, plus a small tag in `index.html`
     around line 1451, in the lines tab (not in the uploads section).
   - Add `sherpa_onnx` / `sherpa-onnx` to `setup.MODULES` and `setup.PIP_PACKAGES` only.
   - Add `video-studio install speakers` (show the plan, ask first).
   - Test on a two-voice SAPI dialogue (David + Zira).
5. Add `docs/KNOWN_LIMITATIONS.md` "## Speech 1.9.0", `docs/LICENSES.md` and the packaging spec hidden import for
   `speech.py`. Run the full test suite.

## Models / licences (planned; NOTHING downloaded yet. Check the size and SHA before any download)
- sherpa-onnx (pip, Apache-2.0).
- Segmentation: `sherpa-onnx-pyannote-segmentation-3-0.tar.bz2` from
  https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-segmentation-models.
  About 6 MB. pyannote/segmentation-3.0 is MIT: confirm this.
- Speaker embedding: `3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx` (3D-Speaker, Apache-2.0, about 28 MB) from
  https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-recongition-models.
  Confirm the licence and size. Avoid any CC-BY-NC model.
- OpenVINO (Apache-2.0): optional. Ship it only if it keeps word timestamps and is measurably faster. Not tested.
