"""Fast unit tests (no rendering). Run: python -m unittest discover -s tests -v"""

import os
import re
import tempfile
import unittest
from pathlib import Path

_HOME = tempfile.mkdtemp(prefix="vs-unit-")
os.environ["VIDEO_STUDIO_HOME"] = _HOME

from video_studio import editor, memory, planner  # noqa: E402
from video_studio.ai import extract_json  # noqa: E402
from video_studio.engines import captions, music  # noqa: E402
from video_studio.engines.compose import mix_audio  # noqa: F401,E402  (import check)


def W(text, s, e):
    return {"text": text, "start": s, "end": e}


class Captions(unittest.TestCase):
    def test_clean_words_sorted_non_overlapping(self):
        words = captions.clean_words([W("b", 1.2, 2.5), W("a", 0, 1), W("c", 2.1, 3), W(" ", 4, 5)])
        self.assertEqual([w["text"] for w in words], ["a", "b", "c"])
        for a, b in zip(words, words[1:]):
            self.assertLessEqual(a["end"], b["start"] + 1e-9)

    def test_srt_cues_never_overlap(self):
        words = [W(f"w{i}", i * 0.15, i * 0.15 + 0.6) for i in range(40)]
        with tempfile.TemporaryDirectory() as d:
            text = captions.build_srt(words, Path(d) / "c.srt", per_chunk=2).read_text(encoding="utf-8")
        cues = [(int(a) * 3600 + int(b) * 60 + int(c) + int(m) / 1000, int(e) * 3600 + int(f) * 60 + int(g) + int(n) / 1000)
                for a, b, c, m, e, f, g, n in re.findall(r"(\d\d):(\d\d):(\d\d),(\d+) --> (\d\d):(\d\d):(\d\d),(\d+)", text)]
        self.assertTrue(cues)
        for (s1, e1), (s2, _) in zip(cues, cues[1:]):
            self.assertGreater(e1, s1)
            self.assertLessEqual(e1, s2)

    def test_ass_has_highlight_events(self):
        with tempfile.TemporaryDirectory() as d:
            ass = captions.build_ass([W("Hello", 0, .5), W("world", .5, 1)], 1080, 1920, "bold_pop", "midnight", Path(d) / "c.ass")
            text = ass.read_text(encoding="utf-8-sig")
        self.assertIn("PlayResX: 1080", text)
        self.assertEqual(text.count("Dialogue:"), 2)


class Planner(unittest.TestCase):
    def test_parse_directives(self):
        blocks = planner.parse_script("# Title\n\nHook line here. [3D: rocket]\n\n[STAT: 70% | of creators]\n\n- one\n- two\n")
        self.assertTrue(any(b["directives"] for b in blocks["blocks"]) if isinstance(blocks, dict) else blocks)

    def test_headline_trims_stopwords(self):
        h = planner.headline_for("What if your laptop could make the whole video for you", 5)
        self.assertFalse(h.split()[-1].lower() in planner.STOP)

    def test_tone(self):
        self.assertIsInstance(planner.detect_tone("Hurry, this deal ends tonight!"), str)


class Editor(unittest.TestCase):
    def test_brief_guessing(self):
        self.assertEqual(editor._guess_target("make a 45 second reel"), 45)
        self.assertEqual(editor._guess_target("2 min youtube video"), 120)
        self.assertEqual(editor._guess_aspect("Instagram reel please"), "9:16")
        self.assertEqual(editor._guess_aspect("landscape youtube video"), "16:9")
        self.assertEqual(editor._guess_language("Hinglish reel"), "hi")

    def test_retake_detection_keeps_last_take(self):
        utts = editor._utterances([W("So", 0, .2), W("the", .2, .4), W("best", .4, .6), W("way", .6, .8), W("is.", .8, 1),
                                   W("So", 2.5, 2.7), W("the", 2.7, 2.9), W("best", 2.9, 3.1), W("way", 3.1, 3.3),
                                   W("is", 3.3, 3.5), W("simple.", 3.5, 4)])
        self.assertEqual(len(utts), 2)
        self.assertEqual(editor.find_retakes(utts), {utts[0]["id"]})

    def test_indic_tokens_stay_whole(self):
        self.assertEqual(editor._norm("प्रोडक्ट आज, के"), ["प्रोडक्ट", "आज", "के"])

    def test_fit_target_keeps_opening(self):
        utts = [{"id": f"u{i}", "start": i * 5.0, "end": i * 5.0 + 5, "text": "word " * (i + 1)} for i in range(6)]
        kept = editor._fit_target(utts, [u["id"] for u in utts], 16)
        self.assertEqual(kept[0], "u0")
        self.assertLessEqual(len(kept) * 5, 16 * 1.05 + 5)


class Memory(unittest.TestCase):
    def test_feedback_moves_taste(self):
        memory.upsert_project("t1", name="t", path=".", status="x", aspect="9:16", tone="calm",
                              decisions={"look": "neon", "music": "calm"}, summary="test")
        before = {(r["dimension"], r["arm"]): r["mean"] for r in memory.taste_table() if r["bucket"] == "global"}
        memory.add_feedback("t1", 5, "loved it")
        after = {(r["dimension"], r["arm"]): r["mean"] for r in memory.taste_table() if r["bucket"] == "global"}
        self.assertGreater(after[("look", "neon")], before.get(("look", "neon"), 0.5) - 1e-9)

    def test_failure_classification(self):
        sig, _ = memory.record_failure("t1", "render", "EEVEE: GPU out of memory")
        self.assertTrue(sig)


class Misc(unittest.TestCase):
    def test_extract_json(self):
        self.assertEqual(extract_json('text ```json\n{"a": [1, 2]}\n``` more'), {"a": [1, 2]})
        self.assertEqual(extract_json('prefix [1, {"b": "}"}] suffix'), [1, {"b": "}"}])

    def test_music_and_sfx_synthesis(self):
        audio = music.compose("calm", 2.0, seed=1)
        self.assertGreater(len(audio), 48000)
        self.assertGreater(len(music.sfx("pop")), 100)


class LocalBrain(unittest.TestCase):
    def test_transliteration_restores_english_loanwords(self):
        from video_studio import brain

        out = [brain.transliterate(w) for w in "प्रोडक्ट डिजिटल लॉन्च नहीं".split()]
        self.assertEqual(out, ["product", "digital", "launch", "nahi"])

    def test_hook_text_ignores_edit_instructions(self):
        from video_studio.editor import _hook_from_brief

        self.assertEqual(_hook_from_brief("cinematic travel reel, beat sync, 12 seconds"), "TRAVEL")
        self.assertEqual(_hook_from_brief("make it punchy"), "")

    def test_brain_picks_hook_and_slams_without_ai(self):
        from video_studio import brain, editor

        text = "aaj main aapko ek secret batata hoon. revenue double karne ka simple tarika. pehle product launch karo"
        words, t = [], 0.0
        for w in text.split():
            words.append({"text": w, "start": t, "end": t + 0.3})
            t += 0.35 if not w.endswith(".") else 0.9
        utts = editor._utterances(words)
        res = brain.analyze(words, None, utts)
        self.assertTrue(res["hook"]["text"])
        self.assertNotIn("AAJ", res["hook"]["text"].split())  # stop words never make the hook

    def test_shorts_pick_complete_non_overlapping(self):
        from video_studio import shorts

        words, t = [], 0.0
        for i in range(160):
            w = f"word{i % 23}" + ("." if i % 9 == 8 else "")
            words.append({"text": w, "start": t, "end": t + 0.3})
            t += 0.9 if w.endswith(".") else 0.35
        clips = shorts.pick(words, None, 2, 10, 25)
        self.assertEqual(len(clips), 2)
        self.assertLessEqual(clips[0]["end"], clips[1]["start"])
        for c in clips:
            self.assertTrue(10 <= c["end"] - c["start"] <= 25)

    def test_story_editor_builds_a_story(self):
        from video_studio import story

        lines = ["Hello friends, my name is Dhrubo", "AI is hard to explain", "It does admin work for you",
                 "It saves 3 hours every day, never pay for this again", "It does admin work for you, really",
                 "So try it, it will make your life easier", "See you tomorrow, subscribe"]
        utts = [{"id": f"u{i}", "start": i * 5.0, "end": i * 5.0 + 4.5, "text": t} for i, t in enumerate(lines)]
        tr = [{"start": u["start"], "end": u["end"], "text": u["text"]} for u in utts]
        keep, notes = story.build(utts, set(), tr, {u["id"]: 0.0 for u in utts}, None, "reel", reel=True)
        self.assertNotIn("u0", keep)               # self-introduction cut
        self.assertNotIn("u6", keep)               # sign-off cut
        self.assertEqual(keep[0], "u3")            # strongest claim opens the video
        self.assertEqual(keep[-1], "u5")           # ends on the payoff
        self.assertEqual(sum(k in keep for k in ("u2", "u4")), 1)  # the repeated point appears once
        self.assertTrue(any("Cold open" in n for n in notes))

    def test_broll_matches_meaning(self):
        from video_studio import broll

        index = [{"path": "gst.mp4", "name": "admin film", "segments": [
            {"start": 0.0, "end": 5.0, "text": "Filing GST returns and invoices takes hours"},
            {"start": 5.0, "end": 9.0, "text": "A sunny beach holiday"}]}]
        lines = [{"id": "a", "t0": 0.0, "t1": 4.0, "en": "Hook line"},
                 {"id": "b", "t0": 4.0, "t1": 8.0, "en": "You will have to fill GST returns and invoices"},
                 {"id": "c", "t0": 8.0, "t1": 12.0, "en": "Payoff"}]
        cuts = broll.plan(lines, index, [], 12.0)
        self.assertEqual(len(cuts), 1)
        self.assertEqual(cuts[0]["src"], 0.2)      # the GST moment, not the beach
        self.assertTrue(4.0 < cuts[0]["t0"] < cuts[0]["t1"] <= 8.0)

    def test_meaning_check_any_language(self):
        from video_studio import brain

        tr = [{"start": 0.0, "end": 4.0, "text": "There is no product that will start your revenue in a month."},
              {"start": 4.0, "end": 8.0, "text": "It feels like it takes 3 hours, here it is 20 minutes."}]
        hook = brain.meaning_hook(tr[:1], [], {}, 8.0)
        self.assertIn("NO", hook["text"].split())  # never flips the meaning by dropping the negation
        h2 = brain.meaning_hook(tr[1:], [], {}, 8.0)["text"]
        self.assertTrue(any(c.isdigit() for c in h2) and not h2.startswith("LIKE"), h2)  # numbers make the hook
        words = [{"text": "लगता", "start": 4.2, "end": 4.5}, {"text": "मिनट", "start": 6.0, "end": 6.4}]
        scored = brain.score_words(words, brain.romanize_words(words), None)
        brain.verify_with_translation(scored, words, tr)
        by = {brain._tok(s["display"]): s for s in scored}
        self.assertFalse(by["light"]["clean"])   # 'lagta' only sounds like 'light'; the translation says 'feels'
        self.assertTrue(by["minute"]["clean"])   # 'minute' really was said

    def test_effects_follow_meaning_and_voice(self):
        from video_studio.engines import style

        texts = ["aaj", "main", "product", "launch", "karunga", "lekin", "kya", "aap", "ready", "ho", "pehla", "step", "simple", "hai"]
        words = [{"text": t, "start": i * 0.6, "end": i * 0.6 + 0.5} for i, t in enumerate(texts)]
        info = [{"clean": True, "score": 0.0, "features": {"f0": 0, "db": 0, "stretch": 0, "stop": 0}} for _ in texts]
        info[2]["features"].update(f0=3, db=2.5, stretch=2)  # 'product' shouted
        pieces = [{"out_start": 0.0}, {"out_start": 3.55}, {"out_start": 5.95}]  # cuts just before 'kya' and 'pehla'
        plan = style.build_plan(pieces, words, 8.4, {"word_info": info}, "viral")
        kinds = {round(t["t"], 2): t["kind"] for t in plan["transitions"]}
        self.assertEqual(kinds.get(3.55), "zoom")    # zooms into the question
        self.assertEqual(kinds.get(5.95), "flash")   # flashes on a new point
        self.assertIn(1.2, plan["punches"])          # punch-in on the stressed word
        self.assertTrue(any("stressed 'product'" in r for r in plan["reasons"]))

    def test_style_dna_maps_to_edit_parameters(self):
        from video_studio.engines import styledna

        params = styledna.apply({"shot_len": 0.8, "beats_per_cut": 1.1, "zooms_per_min": 30, "lab": [120, 130, 125], "lab_sd": [50, 8, 9]})
        self.assertEqual(params["beats_per_cut"], 1)
        self.assertLess(params["shot_hi"], 2.0)
        self.assertIn("grade_ref", params)

    def _story_fixture(self):
        lines = ["Hello friends, my name is Dhrubo", "AI is hard to explain", "It files GST and makes your Excel dashboard",
                 "It saves 3 hours every day, never pay for this again", "But people still do not try",
                 "So try it, it will make your life easier", "See you tomorrow, subscribe"]
        utts = [{"id": f"u{i}", "start": i * 5.0, "end": i * 5.0 + 4.5, "text": t} for i, t in enumerate(lines)]
        en = {u["id"]: u["text"] for u in utts}
        return utts, en, {u["id"]: 0.0 for u in utts}

    def test_ai_review_is_checked_before_it_is_trusted(self):
        from video_studio import story

        utts, en, strength = self._story_fixture()
        draft = ["u3", "u1", "u2", "u5"]
        # a good answer: the opening stays first, everything after it returns to recorded order, unknown ids are dropped
        keep, notes = story.accept_review({"keep": ["u3", "u5", "u2", "u1", "u99"], "idea": "AI saves hours",
                                           "reasons": {"u4": "weak"}}, utts, set(), en, strength, draft, None)
        self.assertEqual(keep, ["u3", "u1", "u2", "u5"])
        self.assertTrue(any("The idea" in n for n in notes))
        # an opening that leans on the line before it ("But ...") is refused; the draft's opening is used
        keep, notes = story.accept_review({"keep": ["u4", "u1", "u2", "u3", "u5"]}, utts, set(), en, strength, draft, None)
        self.assertEqual(keep[0], "u3")
        self.assertEqual(keep[1:], ["u1", "u2", "u4", "u5"])
        # a retake never comes back, a cut that is too thin is rejected, garbage is rejected
        keep, _ = story.accept_review({"keep": ["u3", "u1", "u2", "u5"]}, utts, {"u1"}, en, strength, draft, None)
        self.assertNotIn("u1", keep)
        self.assertIsNone(story.accept_review({"keep": ["u3"]}, utts, set(), en, strength, draft, None)[0])
        self.assertIsNone(story.accept_review({"keep": "all of them"}, utts, set(), en, strength, draft, None)[0])
        self.assertIsNone(story.accept_review([], utts, set(), en, strength, draft, None)[0])
        # too long for the target: trimmed by story value, opening and ending kept
        keep, _ = story.accept_review({"keep": [u["id"] for u in utts]}, utts, set(), en, strength, draft, 14.0)
        self.assertLessEqual(sum(4.5 for _ in keep), 14.0 * 1.12 + 4.5)
        self.assertEqual((keep[0], keep[-1]), ("u0", "u6"))
        self.assertEqual(story.visuals({"visuals": {"u2": ["tax form", "invoice"], "u9": "x"}}, ["u2"]), {"u2": "tax form invoice"})

    def test_story_keeps_examples_and_distrusts_mangled_lines(self):
        from video_studio import story

        utts, en, strength = self._story_fixture()
        by_id = {u["id"]: u for u in utts}
        self.assertGreater(story.line_value("u2", en, by_id, strength), story.line_value("u4", en, by_id, strength))
        self.assertFalse(story.reliable({"text": "फिर आपको"}, "They are making a series by giving money, applying for a job. How limited is your job?"))
        self.assertTrue(story.reliable({"text": "AI is hard to explain"}, "AI is hard to explain"))
        # an English video has no translation: its own transcript is the meaning
        tr = story.english({"language": "en", "segments": [{"start": 0, "end": 2, "text": "Hello"}], "translation": []})
        self.assertEqual(tr[0]["text"], "Hello")
        self.assertEqual(story.english({"language": "hi", "segments": [{"start": 0, "end": 2, "text": "x"}]}), [])

    def test_broll_ignores_generic_words_and_uses_visual_hints(self):
        from video_studio import broll

        index = [{"path": "hr.mp4", "name": "hr film", "segments": [
            {"start": 0.0, "end": 5.0, "text": "People explain the platform to new joiners"},
            {"start": 5.0, "end": 9.0, "text": "The spreadsheet fills itself with invoices"},
            *({"start": 9.0 + 4 * i, "end": 13.0 + 4 * i, "text": t} for i, t in enumerate(
                ["Attendance and leave", "Salaries on payday", "Interview scheduling", "Offer letters"]))]}]
        lines = [{"id": "a", "t0": 0.0, "t1": 4.0, "en": "Hook line"},
                 {"id": "b", "t0": 4.0, "t1": 8.0, "en": "It is tough to explain this platform to people"},
                 {"id": "c", "t0": 8.0, "t1": 12.0, "en": "All the work gets done", "visual": "spreadsheet, invoices"},
                 {"id": "d", "t0": 12.0, "t1": 16.0, "en": "Payoff"}]
        cuts = broll.plan(lines, index, [], 16.0)
        self.assertEqual([c["src"] for c in cuts], [5.2])   # the visual hint matched; 'people/explain/platform' did not

    def test_long_line_breaks_at_a_pause_not_mid_phrase(self):
        words, t = [], 0.0
        for i in range(30):  # one unpunctuated run of speech with a single breath after word 20
            words.append(W(f"w{i}", t, t + 0.3))
            t += 0.35 + (0.25 if i == 19 else 0.0)
        utts = editor._utterances(words)
        self.assertEqual(utts[0]["words"][-1]["text"], "w19")
        self.assertEqual(sum(len(u["words"]) for u in utts), 30)

    def test_stretched_word_is_ended_so_the_hole_can_be_heard_again(self):
        from types import SimpleNamespace as NS

        from video_studio.helpers import ml_helper

        self.assertEqual(ml_helper._word_end(NS(word=" hello", start=1.0, end=1.4)), 1.4)       # a normal word is untouched
        self.assertAlmostEqual(ml_helper._word_end(NS(word=" राहें।", start=100.9, end=112.7)), 102.1)  # 11.8 s "word"

    def test_decision_assist_offers_openings_and_a_forecast(self):
        from video_studio import story

        utts, en, strength = self._story_fixture()
        keep = ["u1", "u2", "u3", "u5"]
        opts = story.openings(utts, set(), en, strength, keep)
        self.assertEqual(sum(o["chosen"] for o in opts), 1)                 # the current opening is always offered
        self.assertIn("u3", [o["id"] for o in opts])                         # the line with the number is offered
        self.assertFalse({"u0", "u6", "u4"} & {o["id"] for o in opts})       # never an intro, a sign-off or a "But ..." line
        self.assertIn("has a number", next(o for o in opts if o["id"] == "u3")["why"])
        fc = story.forecast(utts, keep, en, strength, {})
        self.assertTrue(any(f["level"] == "risk" and "opening" in f["text"].lower() for f in fc))   # "AI is hard to explain"
        fc = story.forecast(utts, ["u3", "u1", "u2", "u5"], en, strength, {})
        self.assertTrue(any(f["level"] == "good" and "opening" in f["text"].lower() for f in fc))
        self.assertTrue(any(f["level"] == "good" and "ends" in f["text"].lower() for f in fc))

    def test_paid_ai_is_never_a_silent_fallback(self):
        from video_studio import ai, config

        class Ready(ai.Status):
            signed_in = True

        d = ai.Director("auto")
        d._status = lambda p: ai.Status(installed=True, signed_in=True)
        keys = [p.key for p in d.chain()]
        self.assertTrue(keys and not ({"claude", "codex"} & set(keys)))
        d = ai.Director("claude")
        d._status = lambda p: ai.Status(installed=True, signed_in=True)
        self.assertEqual(d.chain()[0].key, "claude")          # picked on purpose: used
        self.assertNotIn("codex", [p.key for p in d.chain()])
        self.assertFalse(config.get("ai.paid_fallback"))

    def test_ai_install_is_guarded(self):
        from video_studio import ai, detect, studio

        for p in ai.providers():  # every provider has a recipe the Install button can show
            self.assertIn(p.key, detect.INSTALL_RECIPES)
        info = studio.install_info("opencode")
        self.assertTrue(info.get("blocked") or "opencode-ai" in info["command"])
        self.assertIn("error", studio.ai_install("not-a-provider"))
        self.assertIn("blocked", studio.install_info("ffmpeg-but-not-real"))


if __name__ == "__main__":
    unittest.main()
