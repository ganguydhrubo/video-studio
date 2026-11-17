"""Motion graphics and 3D inside an edit: prompt words, placement, pages, cache, compositing and the skip path.

Offline and fast: the HyperFrames renderer is mocked (it writes a few transparent PNG frames)."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_studio import motion
from video_studio.understand import edit_args, motion_wishes, understand


class PromptWords(unittest.TestCase):
    def setUp(self):
        self.ready = patch.object(motion, "ready_note", return_value="")
        self.ready.start()

    def tearDown(self):
        self.ready.stop()

    def test_the_card_says_when_hyperframes_is_missing(self):
        with patch.object(motion, "ready_note", return_value="Motion graphics need HyperFrames, which is free."):
            r = understand("confetti at the end")
        self.assertTrue(any("HyperFrames" in c and "still made" in c for c in r["cannot"]))
        self.assertEqual(r["options"]["graphics"], "confetti")

    def test_3d_title_lower_third_and_confetti(self):
        r = understand('reel with a 3D title "Tea Time" that spins, neon lower third with my name Asha Rao, confetti at the end')
        o = r["options"]
        self.assertEqual(o["graphics"], "title3d,lower_third,confetti")
        self.assertNotIn("grade", o)   # "neon lower third" is the name tag's style, not the film's colour
        w = motion_wishes('3D title "Tea Time" that spins, neon lower third with my name Asha Rao, confetti at the end')
        self.assertEqual((w["title"], w["name"], w["style"], w["spin"], w["confetti_at"]), ("Tea Time", "Asha Rao", "neon", True, "end"))
        lines = " ".join(r["understood"])
        self.assertIn("3D title", lines)
        self.assertIn("Asha Rao", lines)
        self.assertIn("Confetti", lines)
        self.assertEqual(r["cannot"], [])
        self.assertIn("--graphics", edit_args(o, {}))

    def test_other_pieces_and_words(self):
        self.assertEqual(motion_wishes("kinetic text and an end card saying \"Follow for more\"")["kinds"], ["callout", "end_card"])
        self.assertEqual(motion_wishes("kinetic text and an end card saying \"Follow for more\"")["end"], "Follow for more")
        w = motion_wishes("floating 3d hearts with sparkles and light leaks")
        self.assertEqual(w["kinds"], ["shape3d", "particles", "light_leak"])
        self.assertEqual((w["shape"], w["count"]), ("heart", 3))
        self.assertEqual(motion_wishes("intro/outro titles")["kinds"], ["title", "end_card"])
        self.assertEqual(motion_wishes("confetti at the start")["confetti_at"], "start")
        self.assertEqual(motion_wishes("name tag, name: Priya Sharma, place: Darjeeling")["place"], "Darjeeling")
        self.assertTrue(motion_wishes("no titles please")["off"])
        self.assertEqual(understand("no graphics")["options"]["graphics"], "off")

    def test_everyday_words_do_not_add_motion(self):
        for brief in ("make it short and remove background noise", "hinglish subtitles, fast", "slow motion parts",
                      "start strong, golden hour look", "bring the ring ceremony first"):
            self.assertEqual(motion_wishes(brief)["kinds"], [], brief)
        self.assertEqual(understand("neon city vibes")["options"]["grade"], "cyberpunk")
        self.assertNotIn("motion", understand("add motion graphics")["options"])

    def test_3d_from_footage_is_still_said_plainly(self):
        r = understand("turn my video into 3D")
        self.assertTrue(any("3D scenes built from your footage" in c for c in r["cannot"]))
        self.assertNotIn("graphics", r["options"])
        self.assertEqual(understand("make it 3D")["options"]["graphics"], "title3d")


class Placement(unittest.TestCase):
    WANT = {"kinds": ["title3d", "lower_third", "callout", "end_card", "confetti"], "name": "Asha", "place": "",
            "callout_words": [{"text": "Biggest", "start": 9.0}, {"text": "Free", "start": 10.0}, {"text": "Today", "start": 16.0}]}

    def test_pieces_avoid_the_films_own_big_text_and_cutaways(self):
        busy = [(0.0, 2.2), (12.0, 14.5)]   # hook slam at the start, a B-roll cutaway in the middle
        items, notes = motion.place(self.WANT, 30.0, busy)
        kinds = [i["kind"] for i in items]
        self.assertEqual(sorted(kinds), sorted(["title3d", "lower_third", "callout", "callout", "end_card", "confetti"]))
        for it in items:
            for a, b in busy:
                self.assertFalse(it["start"] < b and it["end"] > a, it)
        text = sorted((i["start"], i["end"]) for i in items if i["kind"] in motion.TEXT_LAYER)
        for (a0, a1), (b0, b1) in zip(text, text[1:]):
            self.assertLessEqual(a1, b0)   # titles, name tag, kinetic words and end card never overlap
        title = next(i for i in items if i["kind"] == "title3d")
        self.assertGreaterEqual(title["start"], 2.2)
        end = next(i for i in items if i["kind"] == "end_card")
        self.assertAlmostEqual(end["end"], 30.0, places=2)
        confetti = next(i for i in items if i["kind"] == "confetti")
        self.assertGreaterEqual(confetti["start"], end["start"])
        quiet = motion.quiet_spans(items)
        self.assertTrue(any(a <= title["start"] < b for a, b in quiet))
        lower = next(i for i in items if i["kind"] == "lower_third")
        self.assertFalse(any(a <= lower["start"] + 0.1 < b for a, b in quiet))   # captions stay under the name tag

    def test_short_or_crowded_films_say_what_was_left_out(self):
        self.assertEqual(motion.place(self.WANT, 3.0, [])[0], [])
        items, notes = motion.place({"kinds": ["lower_third"], "name": "", "place": ""}, 20.0, [])
        self.assertEqual(items, [])
        self.assertTrue(any("no name" in n for n in notes))
        items, notes = motion.place({"kinds": ["end_card"]}, 20.0, [(16.0, 20.0)])
        self.assertEqual(items, [])
        self.assertTrue(any("End card left out" in n for n in notes))

    def test_light_leaks_sit_on_cuts(self):
        items, _ = motion.place({"kinds": ["light_leak"]}, 30.0, [], cuts=[4.0, 5.0, 11.0, 20.0, 29.5])
        centres = [round((i["start"] + i["end"]) / 2, 2) for i in items]
        self.assertEqual(centres, [4.0, 11.0, 20.0])

    def test_callout_words_come_from_the_spoken_words(self):
        pieces = [{"words": [{"text": "This", "start": 1.0}, {"text": "BIGGEST!", "start": 1.4}]},
                  {"words": [{"text": "biggest", "start": 5.0}, {"text": "mistake", "start": 5.4}]}]
        self.assertEqual(motion.callout_words(pieces, ["biggest", "mistake"], {"mistake"}), [{"text": "BIGGEST", "start": 1.4}])


class Pages(unittest.TestCase):
    def test_every_template_fills_and_follows_the_render_contract(self):
        want = {"style": "neon", "title": "Tea Time", "name": "Asha <b>Rao</b>", "place": "Darjeeling", "shape": "star", "count": 3}
        for kind in motion.KINDS:
            it = {"kind": kind, "start": 0, "end": 3.0, "seed": 5, "text": "Biggest", "with_end": kind == "shape3d"}
            x, y, bw, bh = motion.box(it, 1080, 1920, 0.72, False)
            self.assertTrue(0 <= x and 0 <= y and x + bw <= 1080 and y + bh <= 1920 and bw % 2 == 0 and bh % 2 == 0, kind)
            html = motion.page(kind, motion.values(it, want, bw, bh, 1080, 1920, "midnight"), bw, bh, 3.0, 30)
            self.assertNotIn("{{", html, kind)
            self.assertIn('data-composition-id="main"', html)
            self.assertIn('window.__timelines["main"]', html)
            self.assertIn('class="clip"', html)
            self.assertNotIn("Math.random", html)
            self.assertNotIn("Date.now", html)
            self.assertNotIn("repeat: -1", html)
            self.assertNotIn("http://", html.replace('http://www.w3.org', ''))
            self.assertNotIn("<b>Rao</b>", html.replace(json.dumps("Asha <b>Rao</b>"), ""))   # text goes in as data
            self.assertEqual("three-motion.min.js" in html, motion.KINDS[kind]["three"], kind)

    def test_lower_third_sits_above_the_captions(self):
        x, y, bw, bh = motion.box({"kind": "lower_third"}, 1080, 1920, 0.72, False)
        self.assertLessEqual(y + bh, 0.72 * 1920 - 0.05 * 1920)
        x, y, bw, bh = motion.box({"kind": "lower_third"}, 1920, 1080, 0.9, False)
        self.assertLessEqual(y + bh, 0.86 * 1080)

    def test_3d_letters_only_for_latin_text(self):
        self.assertTrue(motion.can_draw_3d("Tea Time 2026!"))
        self.assertFalse(motion.can_draw_3d("चाय का समय"))
        self.assertEqual(motion._lines("Tea Garden Diaries"), ["Tea Garden", "Diaries"])

    def test_vendored_files_and_licences_are_there(self):
        root = Path(motion.__file__).parent / "motion_templates"
        for f in ("kit.js", "vendor/three-motion.min.js", "vendor/three.LICENSE.txt", "vendor/mukta_extrabold.typeface.json"):
            self.assertTrue((root / f).is_file(), f)
        for file in motion.FONTS.values():
            self.assertTrue((Path(motion.__file__).parent / "assets" / "fonts" / file).is_file())
        self.assertIn("MIT", (root / "vendor" / "three.LICENSE.txt").read_text(encoding="utf-8"))


def fake_render(frames_wanted):
    """Stand-in for `node hyperframes render`: writes transparent PNG frames where --output points."""
    from PIL import Image

    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        out = Path(cmd[cmd.index("--output") + 1])
        out.mkdir(parents=True, exist_ok=True)
        for i in range(1, frames_wanted + 1):
            Image.new("RGBA", (8, 8), (255, 0, 0, 128)).save(out / f"frame_{i:06d}.png")

        class R:
            returncode, stdout, stderr = 0, "", ""
        return R()
    return run, calls


class Rendering(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"VIDEO_STUDIO_HOME": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_pieces_are_cached_by_their_page(self):
        run, calls = fake_render(60)
        work = Path(self.tmp.name) / "work"
        with patch.object(motion, "run", run), patch.object(motion, "hyperframes_cli", return_value=Path("hf.mjs")), \
                patch("video_studio.detect.node_path", return_value=Path("node.exe")), \
                patch("video_studio.engines.hyperframes._vendored_gsap", return_value=Path(motion.__file__)):
            a = motion.render_piece("<html>one</html>", "title", 100, 50, 2.0, 30, work)
            b = motion.render_piece("<html>one</html>", "title", 100, 50, 2.0, 30, work)
            c = motion.render_piece("<html>two</html>", "title3d", 100, 50, 2.0, 30, work)
        self.assertFalse(a[1])
        self.assertTrue(b[1])
        self.assertEqual(a[0], b[0])
        self.assertNotEqual(a[0], c[0])
        self.assertEqual(len(calls), 2)
        self.assertIn("png-sequence", calls[0])
        self.assertTrue((work / "three-motion.min.js").is_file())   # 3D pieces get three.js and the 3D font

    def test_a_failed_render_is_a_plain_error(self):
        class Bad:
            returncode, stdout, stderr = 1, "", "Chrome crashed"
        with patch.object(motion, "run", return_value=Bad()), patch.object(motion, "hyperframes_cli", return_value=Path("hf.mjs")), \
                patch("video_studio.detect.node_path", return_value=Path("node.exe")), \
                patch("video_studio.engines.hyperframes._vendored_gsap", return_value=Path(motion.__file__)):
            with self.assertRaises(motion.ToolError):
                motion.render_piece("<html>x</html>", "title", 100, 50, 2.0, 30, Path(self.tmp.name) / "w")


class FakeProj:
    def __init__(self, root: Path, brief: str, template: str = "", graphics: str = "auto"):
        self.build = root
        self.data = {"brief": brief, "name": "My film", "edit_options": {"template": template, "graphics": graphics}}
        self.settings = {"grade": "none"}


class EditPass(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.m = {"fps": 30, "pieces": [{"out_start": 0.0, "words": []}, {"out_start": 8.0, "words": []}],
                  "broll": [{"t0": 10.0, "t1": 12.0}], "pictures": []}

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_hyperframes_skips_motion_and_says_how_to_set_it_up(self):
        proj = FakeProj(self.root, "3D title and confetti at the end")
        with patch.object(motion, "available", return_value=(False, "Motion graphics need HyperFrames, which is free.")):
            out = motion.apply(proj, self.m, {"title": "Tea Time"}, {}, 1080, 1920, 20.0, "midnight")
        self.assertEqual(out["overlays"], [])
        self.assertIn("HyperFrames", self.m["motion"]["skipped"])

    def test_the_pass_returns_overlays_quiet_spans_and_sounds(self):
        proj = FakeProj(self.root, '3D title "Tea Time", name tag name: Asha Rao, confetti at the end')
        frames = self.root / "frames"
        frames.mkdir()
        with patch.object(motion, "available", return_value=(True, "")), \
                patch.object(motion, "render_piece", return_value=(frames, False, 1.0)) as rp:
            out = motion.apply(proj, self.m, {"title": "Ignored"}, {"slams": [{"t0": 0.0, "t1": 1.5}]}, 1080, 1920, 20.0, "midnight")
        kinds = [i["kind"] for i in out["items"]]
        self.assertEqual(kinds, ["title3d", "lower_third", "confetti"])
        self.assertEqual(len(out["overlays"]), 3)
        self.assertTrue(all(o["frames"] == str(frames) for o in out["overlays"]))
        self.assertTrue(out["quiet"])
        self.assertTrue(any(s[0] == "hit" for s in out["sfx"]))
        first_html = rp.call_args_list[0].args[0]
        self.assertIn("Tea Time", first_html)
        self.assertEqual(rp.call_args_list[2].args[2:4], (540, 960))   # confetti is drawn at half size, then scaled up
        self.assertEqual(len(self.m["motion"]["items"]), 3)

    def test_template_default_is_light_and_off_wins(self):
        self.assertEqual(motion.wanted(FakeProj(self.root, "my day", template="wedding"))["kinds"], ["title", "particles", "end_card"])
        self.assertEqual(motion.wanted(FakeProj(self.root, "my day", template="wedding"))["style"], "elegant")
        self.assertEqual(motion.wanted(FakeProj(self.root, "my day")), {})
        self.assertEqual(motion.wanted(FakeProj(self.root, "no graphics", template="wedding")), {})
        self.assertEqual(motion.wanted(FakeProj(self.root, "3d title", graphics="off")), {})
        brief = "my day\nTemplate - Mini documentary: Mini documentary: calm narration, titles for places and dates."
        self.assertEqual(motion.wanted(FakeProj(self.root, brief))["kinds"], ["title"])   # the template, not its words

    def test_non_latin_3d_title_falls_back_to_a_flat_title(self):
        proj = FakeProj(self.root, "3d title")
        with patch.object(motion, "available", return_value=(True, "")), \
                patch.object(motion, "render_piece", return_value=(self.root, False, 1.0)):
            out = motion.apply(proj, self.m, {"title": "चाय का समय"}, {}, 1080, 1920, 20.0, "midnight")
        self.assertEqual([i["kind"] for i in out["items"]], ["title"])


class Compositing(unittest.TestCase):
    def test_png_sequences_are_overlaid_in_the_one_encode(self):
        from video_studio.engines import compose

        seen = {}

        def fake_ff(args, **kw):
            seen["args"] = args
            seen["graph"] = Path(args[args.index("-/filter_complex") + 1]).read_text(encoding="utf-8")
            Path(args[-1]).write_bytes(b"x")
        with tempfile.TemporaryDirectory() as tmp:
            ov = {"frames": str(Path(tmp) / "f"), "start": 2.0, "end": 5.0, "x": 0, "y": 300, "w": 1080, "h": 600, "fps": 30}
            with patch.object(compose.ff, "ff", fake_ff):
                compose.assemble_video([Path(tmp) / "a.mp4"], [10.0], [], 0.0, 30, 1080, 1920, "", None, "draft",
                                       Path(tmp) / "out.mp4", overlays=[ov], total=10.0)
        self.assertIn("frame_%06d.png", " ".join(seen["args"]))
        self.assertIn("setpts=PTS-STARTPTS+2.000/TB", seen["graph"])
        self.assertIn("overlay=0:300:eof_action=pass", seen["graph"])


if __name__ == "__main__":
    unittest.main()
