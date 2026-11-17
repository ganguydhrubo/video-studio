"""J-cuts and L-cuts: which joins get one, J or L, how much, and that every kept word stays exactly in place."""

import math
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from video_studio import jlcut
from video_studio.understand import edit_args, understand

SR = 48000


def piece(utt, s0, s1, out, first_word_after=0.07):
    return {"utt": utt, "src_start": s0, "src_end": s1, "out_start": out,
            "words": [{"text": "w", "start": out + first_word_after, "end": out + first_word_after + 0.3}]}


def write(path, pcm):
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes((np.clip(pcm, -1, 0.99997) * 32768).round().astype("<i2").tobytes())


def read(path):
    with wave.open(str(path), "rb") as wf:
        return np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2").astype("float32") / 32768


class WhichJoins(unittest.TestCase):
    bounds = [(0.0, 10.0), (10.0, 20.0)]

    def test_inside_one_line_never(self):
        self.assertIsNone(jlcut.join_kind(piece("u1", 1, 2, 0), piece("u1", 2.5, 3, 1), self.bounds, []))

    def test_next_line_straight_after_is_plain(self):
        self.assertIsNone(jlcut.join_kind(piece("u1", 1, 2, 0), piece("u2", 2.4, 3, 1), self.bounds, []))

    def test_new_clip_new_shot_new_moment(self):
        self.assertEqual(jlcut.join_kind(piece("u1", 8, 9.5, 0), piece("u2", 10.5, 12, 1.5), self.bounds, []), "new clip")
        shots = [{"start": 0, "end": 2.2}, {"start": 2.2, "end": 10}]
        self.assertEqual(jlcut.join_kind(piece("u1", 1, 2, 0), piece("u2", 2.4, 3, 1), self.bounds, shots), "new shot")
        self.assertEqual(jlcut.join_kind(piece("u1", 1, 2, 0), piece("u2", 5, 6, 1), self.bounds, []), "new moment")
        self.assertEqual(jlcut.join_kind(piece("u3", 5, 6, 0), piece("u1", 1, 2, 1), self.bounds, []), "new moment")

    def test_clip_bounds_follow_ingest_order(self):
        self.assertEqual(jlcut.clip_bounds({"clips": [{"duration": 12.5}, {"duration": 13.2}]}), [(0.0, 12.5), (12.5, 25.7)])


class Handles(unittest.TestCase):
    words = [{"start": 1.0, "end": 1.5}, {"start": 4.0, "end": 4.6}]

    def test_free_footage_stops_at_words_kept_pieces_and_clip_edges(self):
        self.assertAlmostEqual(jlcut.free_before(3.0, 0.0, self.words, []), 3.0 - 1.53)
        self.assertAlmostEqual(jlcut.free_before(3.0, 2.8, self.words, []), 0.2)
        self.assertAlmostEqual(jlcut.free_before(3.0, 0.0, self.words, [(2.0, 2.9)]), 0.1)
        self.assertAlmostEqual(jlcut.free_after(2.0, 10.0, self.words, []), 4.0 - 0.03 - 2.0)
        self.assertAlmostEqual(jlcut.free_after(2.0, 2.25, self.words, []), 0.25)
        self.assertEqual(jlcut.free_after(4.2, 10.0, self.words, []), 0.0)   # inside a word: nothing free


class Choosing(unittest.TestCase):
    bounds = [(0.0, 10.0), (10.0, 20.0)]

    def two(self):
        # clip 1 line ends at 8.0 (last word ends 7.9), clip 2 line starts at 11.0 (first word 11.07)
        return [piece("u1", 6.0, 8.0, 0.0), piece("u2", 11.0, 14.0, 2.0)]

    def words(self):
        return [{"start": 6.07, "end": 7.9}, {"start": 11.07, "end": 13.9}]

    def test_sound_running_on_gives_an_l_cut(self):
        made, _ = jlcut.plan(self.two(), self.words(), self.bounds, [], lambda a, b: -30.0)
        self.assertEqual(made[0]["kind"], "L")
        self.assertEqual(made[0]["amount"], 0.4)
        self.assertEqual(made[0]["cut_at"], 2.0)

    def test_quiet_tail_and_speech_at_once_gives_a_j_cut(self):
        made, _ = jlcut.plan(self.two(), self.words(), self.bounds, [], lambda a, b: -50.0 if a >= 8.0 and b <= 9 else -40)
        self.assertEqual(made[0]["kind"], "J")
        self.assertEqual(made[0]["out"], 2.0)
        self.assertEqual(made[0]["src"], 11.0)

    def test_short_handles_mean_a_plain_cut(self):
        words = self.words() + [{"start": 8.1, "end": 9.95}, {"start": 10.0, "end": 10.85}]
        made, notes = jlcut.plan(self.two(), words, self.bounds, [], lambda a, b: -30.0)
        self.assertEqual(made, [])
        self.assertIn("less than 0.3 s", notes[0])

    def test_silent_extra_sound_is_not_a_j_cut(self):
        made, notes = jlcut.plan(self.two(), self.words(), self.bounds, [], lambda a, b: -90.0)
        self.assertEqual(made, [])
        self.assertIn("silent", notes[0])

    def test_never_more_than_forty_percent_of_a_short_piece(self):
        pieces = [piece("u1", 7.4, 8.0, 0.0), piece("u2", 11.0, 11.6, 0.6)]
        made, _ = jlcut.plan(pieces, [], self.bounds, [], lambda a, b: -30.0)
        self.assertEqual(made, [])   # 40% of 0.6 s is 0.24 s, under the 0.3 s floor


class Sound(unittest.TestCase):
    """A real (synthetic) sound check: words stay sample-exact, length is the same, the join has no click."""

    def build(self, kind):
        tmp = Path(tempfile.mkdtemp())
        t = np.arange(int(20 * SR)) / SR
        src = np.where(t < 10, 0.2 * np.sin(2 * np.pi * 220 * t), 0.2 * np.sin(2 * np.pi * 330 * t)).astype("float32")
        src += np.where(t < 10, 0.02, -0.02).astype("float32") * np.sin(2 * np.pi * 50 * t)
        write(tmp / "src.wav", src)
        src = read(tmp / "src.wav")
        pieces = [piece("u1", 6.0, 8.0, 0.0), piece("u2", 11.0, 14.0, 2.0)]
        base = []
        for pc in pieces:   # the way editor.cut makes each piece: linear 12 ms fade in, 15 ms fade out
            seg = src[int(pc["src_start"] * SR):int(pc["src_end"] * SR)].copy()
            fi, fo = int(jlcut.FADE_IN * SR), int(jlcut.FADE_OUT * SR)
            seg[:fi] *= (np.arange(fi) + 0.5) / fi
            seg[-fo:] *= 1 - (np.arange(fo) + 0.5) / fo
            base.append(seg)
        write(tmp / "plain.wav", np.concatenate(base))
        join = {"kind": kind, "amount": 0.4, "src": 11.0 if kind == "J" else 8.0, "out": 2.0}
        facts = jlcut.render(tmp / "plain.wav", tmp / "src.wav", [join], tmp / "jl.wav")
        return read(tmp / "plain.wav"), read(tmp / "jl.wav"), src, facts

    def test_j_cut_sound_leads_by_the_planned_amount_and_words_stay_put(self):
        plain, jl, src, facts = self.build("J")
        self.assertEqual(len(plain), len(jl))                       # same length, to the sample
        lo, hi = facts["added"][0]["from_sample"], facts["added"][0]["to_sample"]
        self.assertEqual(lo, int(1.6 * SR))                          # starts 0.4 s before the picture cut at 2.0 s
        self.assertEqual(hi, int(2.0 * SR) + int(jlcut.FADE_IN * SR))
        self.assertTrue(np.array_equal(plain[:lo], jl[:lo]))         # every sample outside the join is untouched
        self.assertTrue(np.array_equal(plain[hi:], jl[hi:]))
        # the incoming sound joins its piece seamlessly: out == source across the piece's fade-in
        k = int(2.0 * SR)
        n = int(jlcut.FADE_IN * SR)
        self.assertLess(np.max(np.abs(jl[k:k + n] - src[int(11.0 * SR):int(11.0 * SR) + n])), 2e-4)
        # no click: no sample-to-sample jump bigger than the tones themselves make
        self.assertLess(np.max(np.abs(np.diff(jl[lo - 100:hi + 100]))), 0.06)
        lead = jl[int(1.7 * SR):int(1.95 * SR)] - plain[int(1.7 * SR):int(1.95 * SR)]
        self.assertGreater(float(np.sqrt(np.mean(lead ** 2))), 0.1)  # the next clip really is heard early

    def test_l_cut_sound_trails_under_the_new_picture(self):
        plain, jl, src, facts = self.build("L")
        lo, hi = facts["added"][0]["from_sample"], facts["added"][0]["to_sample"]
        self.assertEqual(lo, int(2.0 * SR) - int(jlcut.FADE_OUT * SR))
        self.assertEqual(hi - lo, int(0.4 * SR) + int(jlcut.FADE_OUT * SR))
        self.assertTrue(np.array_equal(plain[:lo], jl[:lo]) and np.array_equal(plain[hi:], jl[hi:]))
        n = int(jlcut.FADE_OUT * SR)
        self.assertLess(np.max(np.abs(jl[lo:lo + n] - src[int(8.0 * SR) - n:int(8.0 * SR)])), 2e-4)
        self.assertLess(np.max(np.abs(np.diff(jl[lo - 100:hi + 100]))), 0.06)
        self.assertLess(abs(jl[hi - 1] - plain[hi - 1]), 0.01)       # the trailing sound fades right out


class Switches(unittest.TestCase):
    def proj(self, **opts):
        return SimpleNamespace(data={"edit_options": opts}, read=lambda name: {})

    def test_hard_cuts_and_montages_are_left_alone(self):
        pcs = [piece("u1", 6.0, 8.0, 0.0), piece("u2", 11.0, 14.0, 2.0)]
        self.assertEqual(jlcut.apply(self.proj(hard_cuts=True), {}, [], pcs, Path("x"), Path("y"))["jl"], [])
        self.assertEqual(jlcut.apply(self.proj(), {"mode": "montage"}, [], pcs, Path("x"), Path("y"))["jl"], [])

    def test_prompt_words_turn_it_off(self):
        for brief in ("hard cuts only please", "no J-cuts", "reel, no split edits", "no l cuts"):
            r = understand(brief)
            self.assertTrue(r["options"].get("hard_cuts"), brief)
            self.assertIn("--hard-cuts", edit_args(r["options"], {}))
        self.assertNotIn("hard_cuts", understand("smooth cinematic edit")["options"])


if __name__ == "__main__":
    unittest.main()
