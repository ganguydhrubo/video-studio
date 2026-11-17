"""Learning from every edit (video_studio/signals.py): signals, taste updates, honest suggestions, keep/cut retrain."""

import json
import os
import random
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("VIDEO_STUDIO_HOME", tempfile.mkdtemp(prefix="vs-learn-"))

from video_studio import learn, memory, signals, studio  # noqa: E402


class Home(unittest.TestCase):
    """Every test gets its own empty app home and projects folder."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name, "home")
        self.home.mkdir()
        self.env = patch.dict(os.environ, {"VIDEO_STUDIO_HOME": str(self.home)})
        self.env.start()
        self.clips = Path(self.tmp.name, "clips")
        self.clips.mkdir()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def clip(self, name="a.mp4", size=10):
        p = self.clips / name
        p.write_bytes(b"x" * size)
        return str(p)

    def start(self, brief="", options=None, files=None):
        files = files or [self.clip()]
        with patch.object(studio, "workspace", return_value=Path(self.tmp.name)), patch.object(studio.Job, "start"), \
                patch("video_studio.machine.default_quality", return_value="standard"):
            r = studio.start_job({"mode": "edit", "files": files, "brief": brief, "options": options or {"aspect": "9:16"}})
        return studio.JOBS[r["job"]]

    def finish(self, job, **decisions):
        """Pretend the engine finished: the project files the real editor writes, then the finish hook."""
        root = Path(job.root)
        (root / "build").mkdir(parents=True, exist_ok=True)
        (root / "output").mkdir(parents=True, exist_ok=True)
        base = {"pacing": "balanced", "captions": "bold_pop", "music": "uplifting", "motion": "dynamic", "transition": "smooth"}
        args = job.args
        for o in ("pacing", "captions", "music", "motion", "transition"):
            if f"--{o}" in args:
                base[o] = args[len(args) - 1 - args[::-1].index(f"--{o}") + 1]
        base.update(decisions)
        (root / "build" / "creative.json").write_text(json.dumps({"decisions": base, "reasons": {}}), encoding="utf-8")
        settings = {"aspect": "9:16", "music_enabled": "--no-music" not in args}
        if "--grade" in args:
            settings["grade"] = args[args.index("--grade") + 1]
        (root / "project.json").write_text(json.dumps({"id": root.name, "name": root.name, "kind": "edit", "brief": "",
                                                       "settings": settings, "edit_options": {"fx": "bold"}}), encoding="utf-8")
        (root / "output" / "master.mp4").write_bytes(b"film")
        signals.on_finished(root, background=False)
        return root

    def pacing_line(self, job):
        saved = json.loads(Path(job.root, "understood.json").read_text(encoding="utf-8"))
        return next((s for s in saved.get("learned", []) if s["option"] == "pacing"), None)


class SuggestionsChangeAndExplicitChoicesWin(Home):
    def test_a_sequence_of_edits(self):
        # 1. nothing learned yet: the edit is not touched
        first = self.start("a quick fast reel")
        self.assertIsNone(self.pacing_line(first))
        signals.on_download(self.finish(first) / "output" / "master.mp4")
        # 2. fast again, from Fine-tune
        second = self.start("", {"aspect": "9:16", "pacing": "fast"}, files=[self.clip("b.mp4")])
        self.finish(second)
        # 3. an empty prompt: the learned pace is used, said with the real count, and reaches the engine
        third = self.start("", files=[self.clip("c.mp4")])
        line = self.pacing_line(third)
        self.assertIsNotNone(line)
        self.assertEqual(line["value"], "fast")
        self.assertEqual(line["line"], "Pace: fast (learned: you chose it in 2 of your last 2 edits)")
        self.assertEqual(third.args[third.args.index("--pacing") + 1], "fast")
        signals.on_download(self.finish(third) / "output" / "master.mp4")
        # 4. the prompt says slow: the person's words win, nothing learned is applied to the pace
        fourth = self.start("slow and calm please", files=[self.clip("d.mp4")])
        self.assertIsNone(self.pacing_line(fourth))
        self.assertEqual([fourth.args[i + 1] for i, a in enumerate(fourth.args) if a == "--pacing"], ["relaxed"])
        root4 = self.finish(fourth)
        signals.on_refine(root4, "calmer")
        # 5. their latest own choice was relaxed, so fast is no longer pushed
        fifth = self.start("", files=[self.clip("e.mp4")])
        self.assertIsNone(self.pacing_line(fifth))
        self.finish(fifth)
        # 6-7. two more relaxed edits, one downloaded: now relaxed is the learned pace
        for name in ("f.mp4", "g.mp4"):
            job = self.start("", {"aspect": "9:16", "pacing": "relaxed"}, files=[self.clip(name)])
            signals.on_download(self.finish(job) / "output" / "master.mp4")
        eighth = self.start("", files=[self.clip("h.mp4")])
        line = self.pacing_line(eighth)
        self.assertEqual(line["value"], "relaxed")
        self.assertIn("of your last 6 edits", line["line"])
        # a Fine-tune choice still wins over it
        ninth = self.start("", {"aspect": "9:16", "pacing": "balanced"}, files=[self.clip("i.mp4")])
        self.assertIsNone(self.pacing_line(ninth))
        self.assertEqual([ninth.args[i + 1] for i, a in enumerate(ninth.args) if a == "--pacing"], ["balanced"])

    def test_the_card_endpoint_and_start_agree(self):
        for name in ("a.mp4", "b.mp4", "c.mp4"):
            self.finish(self.start("no captions", files=[self.clip(name)]))
        said = signals.suggest({}, {})
        self.assertEqual([(s["option"], s["value"]) for s in said], [("captions", "none")])
        self.assertEqual(signals.flags(said), ["--captions", "none"])
        self.assertEqual(signals.suggest({"captions": "karaoke"}, {}), [])        # the prompt names captions
        self.assertEqual(signals.suggest({}, {"captions": "clean"}), [])          # Fine-tune names captions
        self.assertEqual(signals.suggest({"template": "wedding"}, {}), [])        # a style recipe sets captions

    def test_a_song_in_the_drop_is_the_music(self):
        for name in ("a.mp4", "b.mp4", "c.mp4"):
            self.finish(self.start("no music", files=[self.clip(name)]))
        self.assertEqual([s["option"] for s in signals.suggest({}, {})], ["music_enabled"])
        self.assertEqual(signals.suggest({}, {}, has_song=True), [])
        job = self.start("", files=[self.clip("d.mp4"), self.clip("song.mp3")])
        self.assertNotIn("--no-music", job.args)

    def test_passive_downloads_alone_never_make_a_suggestion(self):
        # Takebloom picked these by itself; downloading them must not turn its own guess into "your taste"
        for name in ("a.mp4", "b.mp4", "c.mp4", "d.mp4"):
            signals.on_download(self.finish(self.start("", files=[self.clip(name)])) / "output" / "master.mp4")
        self.assertEqual(signals.suggest({}, {}), [])


class Signals(Home):
    def test_one_append_only_file_with_every_kind(self):
        job = self.start("fast reel")
        root = self.finish(job)
        signals.on_refine(root, "less_motion")
        signals.on_covers(root)
        signals.on_download(root / "output" / "master.mp4")
        signals.on_share(root, "instagram")
        signals.on_rating(root, 4, "too fast, less zoom please")
        signals.on_stopped(root)
        signals.on_restyle({"grade": "vintage", "fx": "auto"})
        kinds = [e["kind"] for e in signals.events()]
        self.assertEqual(kinds, ["edit", "finished", "refine", "covers", "download", "share", "rating", "stopped", "restyle"])
        self.assertTrue(signals.path().name == "edit_signals.jsonl" and signals.path().parent == self.home)
        share = signals.events()[5]
        self.assertTrue(share["dup"])   # download and share of the same film version count once

    def test_refines_and_notes_read_the_right_way_round(self):
        root = self.finish(self.start(""))
        ev = signals.on_refine(root, "faster")
        self.assertEqual(ev["set"], {"pacing": "fast"})
        self.assertEqual(ev["replaced"], {"pacing": "balanced"})
        asked, disliked = signals.read_note("Too fast, less zoom please, no captions")
        self.assertEqual(asked, {"motion": "subtle", "captions": "none"})
        self.assertEqual(disliked, {"pacing": "fast"})
        ev = signals.on_refine(root, "less_motion")
        self.assertEqual(ev["set"], {"motion": "subtle", "fx": "clean"})
        self.assertEqual(ev["replaced"], {"motion": "dynamic", "fx": "bold"})

    def test_editing_the_same_clips_again_marks_what_was_replaced(self):
        clip = self.clip("same.mp4")
        self.finish(self.start("vintage look", files=[clip]))
        again = signals.events()
        job = self.start("black and white", files=[clip])
        ev = signals.events()[-1]
        self.assertEqual(ev["kind"], "edit")
        self.assertEqual(ev["replaced"], {"grade": "vintage"})
        self.assertGreater(len(signals.events()), len(again))
        self.assertTrue(job)
        neg = [c for c in signals.contributions(ev) if c[:2] == ("grade", "vintage")]
        self.assertEqual(neg[-1][2:], (0.0, signals.WEIGHTS["reedit_replaced"]))

    def test_stopping_counts_against_what_was_learned_only(self):
        for name in ("a.mp4", "b.mp4", "c.mp4"):
            self.finish(self.start("no captions", files=[self.clip(name)]))
        job = self.start("fast", files=[self.clip("d.mp4")])
        ev = signals.on_stopped(job.root)
        self.assertEqual(ev["learned"], {"captions": "none"})
        self.assertEqual(signals.contributions(ev), [("captions", "none", 0.0, signals.WEIGHTS["stopped"])])

    def test_the_taste_bandit_moves_too_and_old_evidence_fades(self):
        for name in ("a.mp4", "b.mp4"):
            self.finish(self.start("fast reel", files=[self.clip(name)]))
        rows = {(r["dimension"], r["arm"]): r for r in memory.taste_table() if r["bucket"] == "global"}
        self.assertGreater(rows[("pacing", "fast")]["mean"], 0.6)
        self.assertLess(rows[("pacing", "relaxed")]["mean"], 0.5)
        before = rows[("pacing", "fast")]["alpha"]
        self.start("", files=[self.clip("c.mp4")])   # a new edit discounts the table a little
        after = {(r["dimension"], r["arm"]): r for r in memory.taste_table() if r["bucket"] == "global"}[("pacing", "fast")]
        self.assertAlmostEqual(after["alpha"], 1 + signals.TABLE_GAMMA * (before - 1), places=6)
        # and the bandit now prefers fast when nothing is said
        picks = [memory.choose("pacing", "neutral", "9:16", explore=False, rng=random.Random(i))[0] for i in range(3)]
        self.assertEqual(set(picks), {"fast"})

    def test_a_broken_line_is_skipped_and_forget_clears_everything(self):
        self.finish(self.start("fast reel"))
        with signals.path().open("a", encoding="utf-8") as fh:
            fh.write('{"kind": "edit", "root": "half-writ')
        self.assertEqual(len(signals.events()), 2)
        summary = signals.summary()
        self.assertEqual((summary["edits"], summary["choices"], summary["finished"]), (1, 1, 1))
        r = signals.forget()
        self.assertEqual(r["signals"], 2)
        self.assertFalse(signals.path().exists())
        self.assertEqual(memory.taste_table(), [])
        self.assertEqual(signals.summary()["events"], 0)

    def test_signals_never_break_an_edit(self):
        with patch.object(signals, "record", side_effect=OSError("disk full")):
            job = self.start("fast reel")
        self.assertIn("--pacing", job.args)


def _transcript(seed: int, n: int = 60) -> tuple[list[dict], list[dict]]:
    rng = random.Random(seed)
    vocab = ["the", "price", "is", "um", "today", "we", "show", "how", "uh", "this", "works", "really", "well", "so"]
    words, t = [], 0.0
    for i in range(n):
        d = rng.uniform(0.2, 0.5)
        words.append({"text": rng.choice(vocab) + ("." if i % 6 == 5 else ""), "start": round(t, 2), "end": round(t + d, 2), "p": rng.uniform(0.3, 1)})
        t += d + rng.uniform(0.05, 0.6)
    utts = []
    for k in range(0, n, 6):
        chunk = words[k:k + 6]
        utts.append({"id": f"u{k // 6}", "start": chunk[0]["start"], "end": chunk[-1]["end"], "text": " ".join(w["text"] for w in chunk)})
    return words, utts


class KeepCutFromReviews(Home):
    def reviewed(self, name, seed):
        root = Path(self.tmp.name, name)
        (root / "build").mkdir(parents=True)
        words, utts = _transcript(seed)
        (root / "build" / "transcript.json").write_text(json.dumps({"words": words}), encoding="utf-8")
        (root / "build" / "edit_plan.json").write_text(json.dumps({"utterances": utts, "keep": [u["id"] for u in utts]}), encoding="utf-8")
        keep = [u["id"] for u in utts if "um" not in u["text"].split()]
        return signals.on_review(root, keep, "", None), root

    def test_reviews_become_labels_and_retrain_in_the_background(self):
        ev, _ = self.reviewed("one", 1)
        self.assertTrue(ev["kept"] and ev["cut"] and ev["removed"])
        self.assertEqual(signals.maybe_retrain()["skipped"], "needs 2 reviewed edits")
        self.reviewed("two", 2)
        result = signals.maybe_retrain()
        self.assertTrue(result["ok"])
        self.assertEqual(result["source"], signals.SOURCE)
        self.assertEqual(result["reviewed"], 2)
        self.assertTrue(result["in_use"])
        self.assertTrue((self.home / signals.KEEP_FILE).is_file())
        self.assertEqual(signals.maybe_retrain()["skipped"], "nothing new")
        k = signals.summary()["keep_model"]
        self.assertTrue(k["trained"] and k["in_use"])
        self.assertEqual(k["reviewed"], 2)
        signals.forget()
        self.assertFalse((self.home / signals.KEEP_FILE).exists())

    def test_a_model_trained_by_hand_is_kept_unless_it_is_off(self):
        (self.home / signals.KEEP_FILE).write_text(json.dumps({"ok": True, "enabled": True, "features": learn.FEATURES,
                                                               "weights": [0.0] * len(learn.FEATURES)}), encoding="utf-8")
        self.reviewed("one", 1)
        self.reviewed("two", 2)
        result = signals.maybe_retrain()
        self.assertFalse(result["in_use"])
        self.assertNotIn("source", json.loads((self.home / signals.KEEP_FILE).read_text(encoding="utf-8")))
        signals.forget()
        self.assertTrue((self.home / signals.KEEP_FILE).exists())   # never deletes a model trained by hand

    def test_numpy_and_plain_python_fit_agree(self):
        rng = random.Random(4)
        X = [[1.0] + [rng.random() for _ in range(4)] for _ in range(80)]
        y = [1 if x[1] + 0.3 * x[2] > 0.6 else 0 for x in X]
        fast = learn.fit(X, y, iters=200)
        with patch.dict(sys.modules, {"numpy": None}):
            slow = learn.fit(X, y, iters=200)
        for a, b in zip(fast, slow):
            self.assertAlmostEqual(a, b, places=6)


if __name__ == "__main__":
    unittest.main()
