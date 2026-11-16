"""1.8.1 sharing: ready captions per site, covers saved from the window, Show in folder, preview copies for video
the window cannot play (HEVC), and the app page is never cached across updates. No network, no posting."""
import base64
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("VIDEO_STUDIO_HOME", tempfile.mkdtemp(prefix="vs-test-"))

from video_studio import studio  # noqa: E402

POST = """Video: Demo  (51 s)

== YouTube ==
Title (20/100): No Need For Cloud #shorts
Description: bro ye video...
#edit #ai #tech

== Instagram ==
No Need For Cloud 👇

bro ye video...

#edit #ai #tech

Cover image: the *_thumbnail.jpg next to this file (hook frame).
Made locally from your video's hook and key words - edit freely before posting."""

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64


def make_project(base: Path) -> Path:
    root = base / "demo-1234"
    (root / "exports").mkdir(parents=True)
    (root / "output").mkdir()
    (root / "build").mkdir()
    (root / "project.json").write_text('{"id": "x", "name": "Demo"}', encoding="utf-8")
    (root / "output" / "master.mp4").write_bytes(b"m")
    (root / "exports" / "Demo_reels.mp4").write_bytes(b"r")
    (root / "exports" / "Demo_thumbnail.jpg").write_bytes(JPEG)
    (root / "exports" / "Demo_post.txt").write_text(POST, encoding="utf-8")
    (root / "build" / "edit_plan.json").write_text('{"hook_text": "NO NEED FOR CLOUD"}', encoding="utf-8")
    return root


class Share(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.root = make_project(self.base)
        self.ws = patch.object(studio, "workspace", return_value=self.base)
        self.ws.start()

    def tearDown(self):
        self.ws.stop()
        self.tmp.cleanup()

    def test_every_site_gets_a_ready_caption_and_the_vertical_video(self):
        info = studio.share_info(str(self.root))
        self.assertTrue(info["video"].endswith("Demo_reels.mp4"))
        sites = {s["key"]: s for s in info["sites"]}
        self.assertEqual(set(sites), {"instagram", "youtube", "tiktok", "facebook", "linkedin", "x", "whatsapp"})
        self.assertIn("#edit", sites["instagram"]["caption"])
        self.assertNotIn("Cover image", sites["instagram"]["caption"])
        self.assertTrue(sites["youtube"]["caption"].startswith("No Need For Cloud #shorts"))
        self.assertLessEqual(len(sites["x"]["caption"]), 280)
        self.assertTrue(all(s["url"].startswith("https://") for s in info["sites"]))

    def test_projects_outside_studio_are_refused(self):
        other = make_project(Path(tempfile.mkdtemp()))
        self.assertIn("error", studio.share_info(str(other)))
        self.assertIn("error", studio.save_covers(str(other), {"reel": "data:image/jpeg;base64," + base64.b64encode(JPEG).decode()}))
        self.assertIn("error", studio.reveal(str(other / "output" / "master.mp4")))

    def test_covers_are_saved_and_the_reel_cover_becomes_the_thumbnail(self):
        url = "data:image/jpeg;base64," + base64.b64encode(JPEG + b"cover").decode()
        r = studio.save_covers(str(self.root), {"reel": url, "grid": url, "youtube": url, "evil": url})
        self.assertEqual(set(r["covers"]), {"reel", "grid", "youtube"})
        exports = self.root / "exports"
        self.assertEqual((exports / "Demo_thumbnail.jpg").read_bytes(), JPEG + b"cover")
        self.assertEqual((exports / "Demo_thumbnail_auto.jpg").read_bytes(), JPEG)  # the automatic cover is kept
        self.assertEqual(set(studio.share_info(str(self.root))["covers"]), {"reel", "grid", "youtube"})

    def test_cover_must_be_a_real_jpeg(self):
        png = "data:image/jpeg;base64," + base64.b64encode(b"\x89PNG....").decode()
        self.assertIn("error", studio.save_covers(str(self.root), {"reel": png}))
        self.assertIn("error", studio.save_covers(str(self.root), {"reel": "data:image/png;base64,AAAA"}))

    def test_show_in_folder_selects_the_file(self):
        with patch.object(studio.subprocess, "Popen") as popen:
            r = studio.reveal(str(self.root / "exports" / "Demo_reels.mp4"))
        self.assertTrue(r["ok"])
        if os.name == "nt":
            self.assertEqual(popen.call_args.args[0][0], "explorer")
            self.assertTrue(popen.call_args.args[0][1].startswith("/select,"))

    def test_playable_video_needs_no_copy_and_hevc_gets_one(self):
        from video_studio.engines import ff
        clip = self.root / "output" / "master.mp4"
        with patch.object(ff, "probe", return_value={}), patch.object(ff, "stream", return_value={"codec_name": "h264"}):
            r = studio.preview_copy(str(clip))
        self.assertTrue(r["ready"] and r["playable"])
        hevc = self.base / "_uploads" / "new.MOV"
        hevc.parent.mkdir()
        hevc.write_bytes(b"h" * 10)

        def fake_ff(args, **kw):
            Path(args[-1]).write_bytes(b"preview")
        with patch.object(ff, "probe", return_value={}), patch.object(ff, "stream", return_value={"codec_name": "hevc"}), \
                patch.object(ff, "ff", side_effect=fake_ff), patch.object(studio.threading, "Thread") as thread:
            thread.side_effect = lambda target, daemon: type("T", (), {"start": staticmethod(target)})()
            first = studio.preview_copy(str(hevc))
            again = studio.preview_copy(str(hevc))
        self.assertEqual(first, {"making": True, "codec": "hevc"})
        self.assertTrue(again["ready"])
        self.assertTrue(Path(again["path"]).is_file())
        self.assertIn("_previews", again["path"])

    def test_unknown_files_get_no_preview(self):
        self.assertIn("error", studio.preview_copy(str(Path(tempfile.mkdtemp()) / "x.mov")))


class PageIsNeverStale(unittest.TestCase):
    def test_main_page_and_scripts_are_not_cached(self):
        src = Path(studio.__file__).read_text(encoding="utf-8")
        self.assertIn('"Cache-Control", "no-store"', src)
        self.assertIn('(".js", ".html", ".css")', src)


if __name__ == "__main__":
    unittest.main()
