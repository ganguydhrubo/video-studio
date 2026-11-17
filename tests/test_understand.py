"""The prompt controls the edit: everyday words become engine choices, and impossible asks are said plainly."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_studio import studio
from video_studio.understand import edit_args, understand


class Reading(unittest.TestCase):
    def test_a_full_prompt_sets_every_choice(self):
        r = understand("A 30 second instagram reel, black and white, slow and cinematic, no music, hinglish subtitles, "
                       "karaoke captions, lots of zooms, glitch transitions, don't crop")
        o = r["options"]
        self.assertEqual(o["aspect"], "9:16")
        self.assertEqual(o["target"], 30.0)
        self.assertEqual(o["grade"], "noir")
        self.assertEqual(o["pacing"], "relaxed")
        self.assertTrue(o["no_music"])
        self.assertNotIn("music", o)
        self.assertEqual(o["caption_script"], "roman")
        self.assertEqual(o["captions"], "karaoke")
        self.assertEqual(o["motion"], "dynamic")
        self.assertEqual(o["transition"], "glitch")
        self.assertTrue(o["no_reframe"])
        self.assertTrue(any("black and white" in line for line in r["understood"]))

    def test_everyday_words_do_not_misfire(self):
        self.assertEqual(understand("make it short and remove background noise")["options"], {})
        self.assertEqual(understand("make it short and remove background noise")["cannot"], [])
        self.assertNotIn("aspect", understand("tell my story in a cool way")["options"])
        o = understand("youtube video with slow motion parts")["options"]
        self.assertNotIn("pacing", o)
        self.assertNotIn("motion", o)

    def test_style_recipes_from_plain_words(self):
        self.assertEqual(understand("my wedding highlights")["options"]["template"], "wedding")
        self.assertEqual(understand("cooking video for youtube")["options"]["template"], "food")
        self.assertEqual(understand("cooking video for youtube")["options"]["aspect"], "16:9")

    def test_what_cannot_be_done_is_said(self):
        r = understand("add 3D text and AI-generated scenes, green screen, slow motion")
        self.assertEqual(len(r["cannot"]), 4)
        self.assertTrue(any("3D" in c for c in r["cannot"]))
        self.assertEqual(understand("")["cannot"], [])

    def test_fine_tune_choices_win_over_the_prompt(self):
        o = understand("black and white reel, fast, no music")["options"]
        args = edit_args(o, {"grade": "vivid", "pacing": "", "aspect": "auto"})
        self.assertNotIn("--grade", args)
        self.assertEqual(args[args.index("--pacing") + 1], "fast")
        self.assertEqual(args[args.index("--aspect") + 1], "9:16")
        self.assertIn("--no-music", args)


class AppliedWhenTheEditStarts(unittest.TestCase):
    def start(self, tmp, brief, options=None, names=("a.mp4",)):
        files = []
        for n in names:
            Path(tmp, n).write_bytes(b"x")
            files.append(str(Path(tmp, n)))
        with patch.object(studio, "workspace", return_value=Path(tmp)), patch.object(studio.Job, "start"), \
                patch("video_studio.machine.default_quality", return_value="standard"):
            r = studio.start_job({"mode": "edit", "files": files, "brief": brief, "options": options or {"aspect": "9:16"}})
        return studio.JOBS[r["job"]]

    def test_prompt_choices_reach_the_engine_and_are_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = self.start(tmp, "widescreen youtube video, vintage look, no captions, 4k")
            a = job.args
            self.assertEqual(a[len(a) - 1 - a[::-1].index("--aspect") + 1], "16:9")   # the last --aspect wins in argparse
            self.assertEqual(a[a.index("--grade") + 1], "vintage")
            self.assertEqual(a[a.index("--captions") + 1], "none")
            self.assertEqual(a[len(a) - 1 - a[::-1].index("--quality") + 1], "high")
            saved = json.loads(Path(job.root, "understood.json").read_text(encoding="utf-8"))
            self.assertIn("--grade", saved["applied"])

    def test_a_format_the_person_picked_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = self.start(tmp, "widescreen youtube video", {"aspect": "1:1", "aspect_chosen": True})
            self.assertEqual([job.args[i + 1] for i, x in enumerate(job.args) if x == "--aspect"], ["1:1"])

    def test_a_dropped_song_beats_no_music(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = self.start(tmp, "no music please", names=("a.mp4", "song.mp3"))
            self.assertNotIn("--no-music", job.args)


if __name__ == "__main__":
    unittest.main()
