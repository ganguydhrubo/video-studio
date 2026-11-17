"""Autopilot (start by itself, retry once, covers) and one drop for clips, pictures and a song."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_studio import autopilot, pictures, studio

FFMPEG = shutil.which("ffmpeg")


class DropSorting(unittest.TestCase):
    def test_clips_pictures_and_songs_are_told_apart(self):
        d = pictures.split_media(["a.MP4", "b.mov", "c.jpg", "d.HEIC", "song.mp3", "e.png", "voice.m4a"])
        self.assertEqual([p.name for p in d["videos"]], ["a.MP4", "b.mov"])
        self.assertEqual([p.name for p in d["pictures"]], ["c.jpg", "d.HEIC", "e.png"])
        self.assertEqual([p.name for p in d["music"]], ["song.mp3", "voice.m4a"])


class PictureSlots(unittest.TestCase):
    def test_pictures_are_spread_in_order_and_never_overlap(self):
        s = pictures.slots(4, 40.0, [])
        self.assertEqual(len(s), 4)
        for (a0, a1), (b0, b1) in zip(s, s[1:]):
            self.assertLess(a1, b0)
        self.assertGreaterEqual(s[0][0], 1.6)          # the opening stays on the film
        self.assertLessEqual(s[-1][1], 40.0 - 1.0)     # and so does the last second
        for a, b in s:
            self.assertTrue(pictures.SHORTEST <= round(b - a, 2) <= pictures.LONGEST)

    def test_busy_moments_are_avoided(self):
        busy = [(5.0, 9.0)]
        for a, b in pictures.slots(3, 20.0, busy):
            self.assertFalse(a < 9.0 and b > 5.0)

    def test_a_short_film_takes_only_what_fits(self):
        s = pictures.slots(30, 12.0, [])
        self.assertLess(len(s), 30)
        self.assertGreater(len(s), 0)
        self.assertEqual(pictures.slots(3, 2.0, []), [])


class CoverTitles(unittest.TestCase):
    def test_long_titles_shrink_to_three_lines_at_most(self):
        lines, size = autopilot.wrap_title("the five things nobody tells you about starting a tea business in assam", 1080, 1920)
        self.assertLessEqual(len(lines), 3)
        self.assertTrue(all(line == line.upper() for line in lines))
        short_lines, short_size = autopilot.wrap_title("Hello", 1080, 1920)
        self.assertEqual(short_lines, ["HELLO"])
        self.assertGreater(short_size, size)
        self.assertEqual(autopilot.wrap_title("", 1280, 720), ([], 0))

    def test_indian_scripts_get_an_indian_font_when_there_is_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("segoeuib.ttf", "NirmalaB.ttf"):
                Path(tmp, name).write_bytes(b"x")
            self.assertEqual(autopilot.pick_font("चाय की कहानी", Path(tmp)).name, "NirmalaB.ttf")
            self.assertEqual(autopilot.pick_font("Tea story", Path(tmp)).name, "segoeuib.ttf")
        with tempfile.TemporaryDirectory() as empty:
            self.assertIsNone(autopilot.pick_font("Tea", Path(empty)))


class CoverRules(unittest.TestCase):
    def make_project(self, tmp):
        root = Path(tmp)
        (root / "output").mkdir()
        (root / "exports").mkdir()
        (root / "output" / "master.mp4").write_bytes(b"film")
        return root

    def test_covers_are_made_once_and_remade_only_when_the_film_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_project(tmp)
            self.assertTrue(autopilot.needs_covers(root))
            for k in autopilot.COVER_SHAPES:
                (root / "exports" / f"cover_{k}.jpg").write_bytes(b"x")
            film_time = (root / "output" / "master.mp4").stat().st_mtime
            (root / "exports" / autopilot.MARK).write_text(json.dumps({"film_time": film_time}))
            self.assertFalse(autopilot.needs_covers(root))
            os.utime(root / "output" / "master.mp4", (film_time + 60, film_time + 60))   # a refine made a new film
            self.assertTrue(autopilot.needs_covers(root))

    def test_covers_saved_by_the_person_are_never_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_project(tmp)
            for k in autopilot.COVER_SHAPES:
                (root / "exports" / f"cover_{k}.jpg").write_bytes(b"x")
            os.utime(root / "output" / "master.mp4", None)
            self.assertFalse(autopilot.needs_covers(root))   # no autopilot mark: they are the person's own

    def test_no_film_no_covers(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(autopilot.needs_covers(Path(tmp)))
            self.assertEqual(autopilot.finish(Path(tmp), say=lambda _: None), {"skipped": True})

    @unittest.skipUnless(FFMPEG, "FFmpeg is not installed")
    def test_real_covers_from_a_short_film(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "output").mkdir()
            subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=360x640:d=2:r=15", "-pix_fmt", "yuv420p",
                            str(root / "output" / "master.mp4")], check=True)
            (root / "project.json").write_text(json.dumps({"name": "Tea gardens at sunrise"}), encoding="utf-8")
            (root / "exports").mkdir()
            (root / "exports" / "Tea_thumbnail.jpg").write_bytes(b"old")
            said = []
            result = autopilot.finish(root, say=said.append)
            self.assertTrue(result.get("ok"), result)
            for k, (w, h) in autopilot.COVER_SHAPES.items():
                out = subprocess.run([shutil.which("ffprobe") or "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                      "stream=width,height", "-of", "csv=p=0", str(root / "exports" / f"cover_{k}.jpg")],
                                     capture_output=True, text=True, check=True).stdout.strip()
                self.assertEqual(out, f"{w},{h}")
            self.assertEqual((root / "exports" / "Tea_thumbnail_auto.jpg").read_bytes(), b"old")
            self.assertEqual((root / "exports" / "Tea_thumbnail.jpg").read_bytes(), (root / "exports" / "cover_reel.jpg").read_bytes())
            self.assertTrue(any("Covers ready" in line for line in said))
            self.assertFalse(autopilot.needs_covers(root))


class JobsRetryOnceThenFinish(unittest.TestCase):
    def test_a_stopped_edit_retries_once_from_where_it_stopped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "project.json").write_text("{}")
            job = studio.Job("edit", ["edit", "a.mp4"], root, "T")
            with patch.object(studio.Job, "start") as start, patch.object(studio.time, "sleep"):
                self.assertTrue(job._autopilot(1))
                start.assert_called_once()
                self.assertEqual(job.args, ["resume", "--project", str(root)])
                self.assertFalse(job._autopilot(1))   # the second failure is shown to the person
            self.assertTrue(any("Trying once more" in line for line in job.lines))

    def test_no_retry_when_the_person_stopped_it_or_for_quick_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "project.json").write_text("{}")
            job = studio.Job("edit", [], root, "T")
            job.status = "stopping"
            self.assertFalse(job._autopilot(1))
            self.assertFalse(studio.Job("restyle", [], root, "R")._autopilot(1))
            self.assertEqual(studio.Job("photos", [], root, "P").retries, 0)

    def test_a_finished_edit_gets_covers(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = studio.Job("edit", [], Path(tmp), "T")
            with patch.object(studio.autopilot, "finish") as finish:
                self.assertFalse(job._autopilot(0))
            finish.assert_called_once()


class OneDropStartsTheRightJob(unittest.TestCase):
    def start(self, tmp, body):
        with patch.object(studio, "workspace", return_value=Path(tmp)), patch.object(studio.Job, "start"), \
                patch("video_studio.machine.default_quality", return_value="draft"):
            r = studio.start_job(body)
        job = studio.JOBS.get(r.get("job")) if r.get("job") else None
        return r, job

    def files(self, tmp, *names):
        out = []
        for n in names:
            p = Path(tmp, n)
            p.write_bytes(b"x")
            out.append(str(p))
        return out

    def test_clips_pictures_and_a_song_go_into_one_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = self.files(tmp, "song.mp3", "b.jpg", "a.mp4", "c.mov")
            r, job = self.start(tmp, {"mode": "edit", "files": f, "options": {"aspect": "9:16"}})
            self.assertEqual(job.kind, "edit")
            names = [Path(a).name for a in job.args[1:5]]
            self.assertEqual(names, ["a.mp4", "c.mov", "b.jpg", "song.mp3"])   # clips first, then pictures, then the song

    def test_pictures_without_a_clip_make_a_photo_film_with_the_song(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = self.files(tmp, "a.jpg", "b.png", "song.wav")
            r, job = self.start(tmp, {"mode": "edit", "files": f, "options": {"aspect": "9:16"}})
            self.assertEqual(job.args[0], "photos")
            self.assertEqual(job.args[job.args.index("--music-file") + 1], f[2])
            self.assertNotIn(f[2], job.args[1:3])

    def test_a_song_alone_is_not_enough(self):
        with tempfile.TemporaryDirectory() as tmp:
            r, job = self.start(tmp, {"mode": "edit", "files": self.files(tmp, "song.mp3")})
            self.assertIn("error", r)

    def test_script_and_ai_clip_are_gone(self):
        with tempfile.TemporaryDirectory() as tmp:
            for mode in ("script", "generate"):
                r, job = self.start(tmp, {"mode": mode, "script": "Hello", "prompt": "tea gardens"})
                self.assertIn("error", r)
                self.assertIsNone(job)


class Page(unittest.TestCase):
    def test_editing_only_page_with_autopilot(self):
        root = Path(studio.__file__).parent / "studio_ui"
        html = (root / "index.html").read_text(encoding="utf-8")
        for gone in ('id="mScript"', 'id="mGenerate"', 'id="mPhotos"', 'id="prompt"', 'id="script"'):
            self.assertNotIn(gone, html)
        self.assertIn('<script src="/autopilot.js"></script>', html)
        self.assertIn('if (typeof autoArm === "function") autoArm();', html)
        js = (root / "autopilot.js").read_text(encoding="utf-8")
        self.assertIn("WAIT = 5", js)
        self.assertIn("A.started", js)   # the same clips are never started twice


if __name__ == "__main__":
    unittest.main()
