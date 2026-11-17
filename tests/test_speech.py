"""Speech 1.9.0: SAPI word times, word starts in pauses, karaoke timing. Offline: no voice, model or FFmpeg runs."""

import json
import math
import os
import re
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

os.environ.setdefault("VIDEO_STUDIO_HOME", tempfile.mkdtemp(prefix="vs-unit-"))

import numpy as np  # noqa: E402

from video_studio import speech  # noqa: E402
from video_studio.engines import captions, ml, tts  # noqa: E402


def write_wav(path: Path, samples: np.ndarray, sr: int) -> Path:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    return path


def bursts(spans: list[tuple[float, float]], total: float, sr: int = 16000, noise_db: float = -90.0,
           seed: int = 1) -> np.ndarray:
    """Voice-like bursts (a 180 Hz buzz with harmonics) over a steady noise floor."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(total * sr)) / sr
    x = rng.normal(0, 10 ** (noise_db / 20), len(t))
    voice = sum(np.sin(2 * math.pi * 180 * k * t) / k for k in range(1, 6)) * 0.25
    for a, b in spans:
        m = (t >= a) & (t < b)
        ramp = np.clip((t[m] - a) / 0.01, 0, 1) * np.clip((b - t[m]) / 0.03, 0, 1)
        x[m] += voice[m] * ramp
    return x.astype("float32")


class SapiWordTimes(unittest.TestCase):
    ROWS = "150\t0\t4\tMost\n606.75\t5\t5\tsmall\n1161.75\t11\t10\tbusinesses\n#END\t3000"

    def test_scale_comes_from_the_end_bookmark(self):
        # SAPI said the audio ends at 3.0 s; the file is really 2.0 s long, so every time shrinks by 2/3
        words = tts.sapi_words(self.ROWS, 2.0)
        self.assertEqual([w["text"] for w in words], ["Most", "small", "businesses"])
        self.assertAlmostEqual(words[0]["start"], 0.100, delta=0.002)
        self.assertAlmostEqual(words[1]["start"], 0.4045, delta=0.002)
        self.assertAlmostEqual(words[2]["start"], 0.7745, delta=0.002)

    def test_without_a_bookmark_the_measured_16k_clock_is_used(self):
        words = tts.sapi_words(self.ROWS.rsplit("\n", 1)[0], 5.0)
        self.assertAlmostEqual(tts.SAPI_CLOCK_SCALE, 2 / 3)
        self.assertAlmostEqual(words[2]["start"], round(1.16175 * 2 / 3, 3), delta=0.002)

    def test_a_silly_bookmark_is_ignored(self):
        words = tts.sapi_words(self.ROWS.replace("#END\t3000", "#END\t1"), 5.0)  # ratio 5000: not believable
        self.assertAlmostEqual(words[1]["start"], round(0.60675 * 2 / 3, 3), delta=0.002)

    def test_ends_never_pass_the_next_word_or_the_file(self):
        words = tts.sapi_words(self.ROWS, 0.9)
        for a, b in zip(words, words[1:]):
            self.assertLessEqual(a["end"], b["start"])
            self.assertGreater(a["end"], a["start"])
        self.assertLessEqual(words[-1]["end"], 0.9)
        self.assertLessEqual(words[-1]["start"], 0.8)

    def test_bad_rows_are_skipped(self):
        words = tts.sapi_words("x\t1\t2\tbad\nonly two\tparts\n100\t0\t2\tok\n#END\tnope", 1.0)
        self.assertEqual([w["text"] for w in words], ["ok"])

    def test_the_script_asks_for_the_end_bookmark(self):
        self.assertIn('AppendBookmark("vs_end")', tts.SAPI_SCRIPT)
        self.assertIn("BookmarkReached", tts.SAPI_SCRIPT)
        self.assertIn("#END", tts.SAPI_SCRIPT)

    def test_sapi_reads_the_real_file_length(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "l0.wav"
            write_wav(out, np.zeros(48000, dtype="float32"), 24000)  # 2.0 s at 24 kHz

            def fake_run(args, **kw):
                items = Path(args[args.index("-Items") + 1])
                Path(str(items) + ".result.json").write_text(
                    json.dumps({"id": "l0", "words": self.ROWS}), encoding="utf-8")

            with mock.patch.object(tts.detect, "sapi_voices", return_value=["Microsoft Zira Desktop"]), \
                    mock.patch.object(tts, "run", side_effect=fake_run):
                made = tts._sapi([{"id": "l0", "text": "Most small businesses", "out": str(out)}], "auto", 0)
        self.assertEqual(made["l0"]["source"], "sapi")
        self.assertAlmostEqual(made["l0"]["words"][2]["start"], 0.7745, delta=0.002)

    def test_old_cached_sapi_lines_are_not_reused(self):
        with tempfile.TemporaryDirectory() as d:
            seen = {}

            def fake_sapi(todo, voice, rate):
                seen["out"] = todo[0]["out"]
                write_wav(Path(todo[0]["out"]), np.zeros(1600, dtype="float32"), 16000)
                return {todo[0]["id"]: {"words": [{"text": "Hi", "start": 0.0, "end": 0.1}], "source": "sapi"}}

            with mock.patch.object(tts, "choose_engine", return_value="sapi"), \
                    mock.patch.object(tts, "_sapi", side_effect=fake_sapi), \
                    mock.patch.object(tts.ff, "duration", return_value=0.1):
                tts.synthesize([{"id": "a", "text": "Hi"}], Path(d), "auto", 0, 1.0, align=False)
            old_key = tts.sha(["sapi", "auto", 0, 1.0, "Hi"])
            self.assertNotIn(old_key, seen["out"])


class SilentStarts(unittest.TestCase):
    SR = 16000

    def _check(self, noise_db):
        pcm = bursts([(0.5, 1.2), (2.0, 2.6)], 3.0, noise_db=noise_db)
        # whisper-like mistake: the second word starts 0.6 s early, inside the pause
        words = [{"text": "one", "start": 0.5, "end": 1.2}, {"text": "two", "start": 1.4, "end": 2.6}]
        fixed = speech.trim_silent_starts(words, pcm, self.SR)
        self.assertEqual(fixed[0]["start"], 0.5)
        self.assertLess(abs(fixed[1]["start"] - 2.0), 0.03, fixed)
        self.assertEqual(fixed[1]["end"], 2.6)
        self.assertEqual(words[1]["start"], 1.4)  # input untouched

    def test_start_inside_a_pause_moves_to_the_sound(self):
        self._check(-90.0)

    def test_works_over_room_noise(self):
        self._check(-40.0)

    def test_start_on_the_fading_end_of_the_last_word_moves(self):
        pcm = bursts([(0.2, 1.0), (1.6, 2.2)], 2.6)
        words = [{"text": "a", "start": 0.2, "end": 0.9}, {"text": "b", "start": 0.9, "end": 2.2}]
        fixed = speech.trim_silent_starts(words, pcm, self.SR)
        self.assertLess(abs(fixed[1]["start"] - 1.6), 0.03)

    def test_running_speech_and_short_stop_gaps_are_left_alone(self):
        # one word with an 80 ms closure inside ("people"): not a pause
        pcm = bursts([(0.3, 0.6), (0.68, 1.1), (1.1, 1.6)], 2.0)
        words = [{"text": "people", "start": 0.3, "end": 1.1}, {"text": "talk", "start": 1.1, "end": 1.6}]
        self.assertEqual(speech.trim_silent_starts(words, pcm, self.SR), words)

    def test_a_long_sound_before_the_pause_means_the_start_was_right(self):
        pcm = bursts([(0.3, 0.8), (1.2, 1.6)], 2.0)
        words = [{"text": "long", "start": 0.3, "end": 1.6}]  # 0.5 s of sound before the gap
        self.assertEqual(speech.trim_silent_starts(words, pcm, self.SR)[0]["start"], 0.3)

    def test_noise_only_changes_nothing(self):
        pcm = np.random.default_rng(3).normal(0, 0.05, 32000).astype("float32")
        words = [{"text": "x", "start": 0.4, "end": 1.0}]
        self.assertEqual(speech.trim_silent_starts(words, pcm, self.SR), words)

    def test_never_past_the_next_word(self):
        pcm = bursts([(1.5, 2.0)], 2.5)
        words = [{"text": "a", "start": 0.2, "end": 1.4}, {"text": "b", "start": 0.8, "end": 2.0}]
        fixed = speech.trim_silent_starts(words, pcm, self.SR)
        self.assertLess(fixed[0]["start"], fixed[1]["start"])

    def test_read_wav_mixes_stereo_down(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.wav"
            with wave.open(str(p), "wb") as wf:
                wf.setnchannels(2)
                wf.setsampwidth(2)
                wf.setframerate(8000)
                wf.writeframes(np.array([16384, 0] * 10, dtype="<i2").tobytes())
            x, sr = speech.read_wav(p)
        self.assertEqual((len(x), sr), (10, 8000))
        self.assertAlmostEqual(float(x[0]), 0.25, places=3)


class TranscribeFixesStarts(unittest.TestCase):
    def test_ml_transcribe_moves_starts_from_the_decoded_audio(self):
        pcm = bursts([(0.5, 1.0), (2.0, 2.5)], 3.0)
        reply = {"ok": True, "language": "en", "duration": 3.0, "segments": [],
                 "words": [{"text": "one", "start": 0.5, "end": 1.0}, {"text": "two", "start": 1.3, "end": 2.5}]}

        def fake_ff(args):
            write_wav(Path(args[-1]), pcm, 16000)

        with mock.patch("video_studio.engines.ff.ff", side_effect=fake_ff), \
                mock.patch.object(ml, "call", return_value=reply) as call:
            tr = ml.transcribe(Path("clip.mp4"), model="small")
        self.assertEqual(call.call_args.args[0], "transcribe")
        self.assertEqual(tr["moved_starts"], 1)
        self.assertLess(abs(tr["words"][1]["start"] - 2.0), 0.03)

    def test_a_broken_audio_file_keeps_whisper_times(self):
        data = {"words": [{"text": "a", "start": 0.1, "end": 0.5}]}
        ml._fix_word_starts(data, Path("does-not-exist.wav"))
        self.assertEqual(data["words"][0]["start"], 0.1)


def karaoke_starts(body: str, line_start: float) -> list[float]:
    """When each word's fill begins, read back from an ASS karaoke line."""
    t, out = line_start, []
    for tag, k, text in re.findall(r"\{\\(kf|k)(\d+)\}([^{]*)", body):
        if tag == "kf":
            out.append(round(t, 3))
        t += int(k) / 100
    return out


class Karaoke(unittest.TestCase):
    def test_pauses_between_words_do_not_make_the_highlight_run_early(self):
        chunk = [{"text": "one", "start": 10.0, "end": 10.3}, {"text": "two", "start": 10.9, "end": 11.2},
                 {"text": "three", "start": 11.25, "end": 11.6}, {"text": "four", "start": 12.4, "end": 12.8}]
        body = captions.karaoke_body(chunk, [w["text"] for w in chunk], 10.0)
        starts = karaoke_starts(body, 10.0)
        for w, s in zip(chunk, starts):
            self.assertAlmostEqual(s, w["start"], delta=0.006)
        self.assertEqual(re.sub(r"\{[^}]*\}", "", body), "one two three four")

    def test_rounding_does_not_add_up_on_a_long_line(self):
        chunk = [{"text": f"w{i}", "start": 0.333 * i + 0.004, "end": 0.333 * i + 0.25} for i in range(30)]
        starts = karaoke_starts(captions.karaoke_body(chunk, [w["text"] for w in chunk], chunk[0]["start"]),
                                chunk[0]["start"])
        self.assertLess(max(abs(s - w["start"]) for s, w in zip(starts, chunk)), 0.006)

    def test_overlapping_words_still_give_a_valid_line(self):
        chunk = [{"text": "a", "start": 1.0, "end": 1.5}, {"text": "b", "start": 1.2, "end": 1.4}]
        body = captions.karaoke_body(chunk, ["a", "b"], 1.0)
        self.assertNotIn("-", body)
        self.assertTrue(all(int(k) >= 0 for k in re.findall(r"\\kf?(\d+)", body)))

    def test_build_ass_karaoke_lines_follow_the_words(self):
        words = [{"text": "Hello", "start": 0.5, "end": 0.8}, {"text": "there,", "start": 1.4, "end": 1.7},
                 {"text": "friend", "start": 2.5, "end": 2.9}]
        with tempfile.TemporaryDirectory() as d:
            ass = captions.build_ass(words, 1080, 1920, "karaoke", "midnight", Path(d) / "c.ass").read_text(encoding="utf-8-sig")
        events = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]
        got = []
        for ev in events:
            start = ev.split(",")[1]
            h, m, s = start.split(":")
            got += karaoke_starts(ev.split(",,", 1)[1], int(h) * 3600 + int(m) * 60 + float(s))
        self.assertEqual(len(got), 3)
        for w, s in zip(words, got):
            self.assertAlmostEqual(s, w["start"], delta=0.006)



class BatchedHelper(unittest.TestCase):
    """helpers/ml_helper.py transcribe with a fake faster-whisper: batched mode keeps every word and splits the
    30 s pieces back into sentences; timestamps are never switched on (that drops sentences in faster-whisper 1.2)."""

    @classmethod
    def setUpClass(cls):
        import importlib.util

        spec = importlib.util.spec_from_file_location("ml_helper_under_test", str(ml.HELPER))
        cls.h = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.h)

    @staticmethod
    def W(word, s, e, p=0.9):
        return mock.Mock(word=word, start=s, end=e, probability=p)

    def _fake_fw(self, seen):
        W = self.W
        piece = [W(" Hello", 0.5, 0.8), W(" there.", 0.8, 1.2), W(" This", 2.5, 2.7), W(" is", 2.7, 2.8),
                 W(" one", 2.8, 3.0), W(" piece", 3.0, 3.4), W(" and", 4.6, 4.8), W(" more", 4.8, 5.2)]

        def seg(words):
            return mock.Mock(start=words[0].start, end=words[-1].end, text="".join(w.word for w in words), words=words,
                             no_speech_prob=0.01, compression_ratio=1.2)

        info = mock.Mock(language="en", duration=6.0)

        class Model:
            def __init__(self, *a, **k):
                pass

            def transcribe(self, pcm, **opts):
                seen.setdefault("plain", []).append(opts)
                return iter([seg(piece[:2]), seg(piece[2:6]), seg(piece[6:])]), info

        class Batched:
            def __init__(self, model):
                pass

            def transcribe(self, pcm, **opts):
                seen.setdefault("batched", []).append(opts)
                return iter([seg(piece)]), info  # one segment per 30 s piece

        return mock.Mock(WhisperModel=Model, BatchedInferencePipeline=Batched)

    def _run(self, batch):
        import argparse
        import sys

        seen = {}
        with tempfile.TemporaryDirectory() as d:
            wav = write_wav(Path(d) / "a.wav", np.zeros(16000 * 6, dtype="float32"), 16000)
            out = Path(d) / "out.json"
            args = argparse.Namespace(audio=str(wav), out=str(out), model="small", threads=4, language="en", vocab="",
                                      translate=1, translate_model="small", batch=batch)
            with mock.patch.dict(sys.modules, {"faster_whisper": self._fake_fw(seen)}):
                self.h.transcribe(args)
            return json.loads(out.read_text(encoding="utf-8")), seen

    def test_batched_keeps_every_word_and_splits_sentences(self):
        plain, _ = self._run(0)
        batched, seen = self._run(8)
        self.assertEqual([w["text"] for w in batched["words"]], [w["text"] for w in plain["words"]])
        self.assertEqual(batched["batch"], 8)
        self.assertEqual(seen["batched"][0]["batch_size"], 8)
        self.assertNotIn("without_timestamps", seen["batched"][0])
        self.assertEqual([s["text"] for s in batched["segments"]], ["Hello there.", "This is one piece", "and more"])

    def test_default_is_one_window_after_another(self):
        data, seen = self._run(0)
        self.assertEqual(data["batch"], 0)
        self.assertNotIn("batched", seen)
        self.assertIn("seconds", data)

    def test_sentences_split_on_stops_pauses_and_length(self):
        W = self.W
        words = [W(" a.", 0, 0.2), W(" b", 0.3, 0.4), W(" c", 1.5, 1.6)] + [W(" x", 2 + i * 0.1, 2.05 + i * 0.1) for i in range(5)]
        parts = self.h._sentences(words, max_words=3)
        self.assertEqual([len(p) for p in parts], [1, 1, 3, 3])


if __name__ == "__main__":
    unittest.main()
