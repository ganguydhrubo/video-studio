"""Timeline: the finished edit as a HyperFrames composition, fixed by hand and turned back into the film."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("VIDEO_STUDIO_HOME", tempfile.mkdtemp(prefix="tb-timeline-test-"))

from video_studio import pictures, studio, timeline  # noqa: E402
from video_studio.project import create  # noqa: E402
from video_studio.util import read_json, write_json  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
PIECES = [(0.5, 2.0), (3.0, 4.5), (4.6, 5.8)]   # source seconds kept by the automatic edit
WORDS = [("Hello", 0.6, 0.9), ("tea", 1.0, 1.3), ("garden.", 1.4, 1.9), ("Fresh", 3.1, 3.5), ("leaves", 3.6, 4.0),
         ("every", 4.0, 4.4), ("Come", 4.7, 5.0), ("visit.", 5.1, 5.6)]


def _ff(*args):
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def make_project(folder: Path, real_media: bool = False) -> Path:
    """A finished edit project on disk: 3 pieces, captions, one picture, one B-roll, music, big text, a motion list."""
    root = folder / "proj"
    proj = create(root, "Tea", "edit", aspect="9:16", height=640, fps=25)
    b = proj.build
    src = b / "source.mov"
    voice = b / "voice_clean.wav"
    pic = folder / "tea.jpg"
    roll = folder / "roll.mp4"
    (b / "pieces").mkdir(parents=True)
    files = [b / "pieces" / f"p{i:04d}.mov" for i in range(len(PIECES))]
    vf = "crop=134:240:93:0,scale=360:640:flags=lanczos,setsar=1"
    if real_media:
        _ff("-f", "lavfi", "-i", "testsrc2=s=320x240:r=25:d=6", "-f", "lavfi", "-i", "sine=f=330:d=6:sample_rate=48000",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "pcm_s16le", "-ac", "2", str(src))
        _ff("-i", str(src), "-vn", "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(voice))
        for (a, z), f in zip(PIECES, files):
            _ff("-ss", f"{a}", "-t", f"{z - a:.3f}", "-i", str(src), "-filter_complex", f"[0:v]{vf},fps=25,format=yuv420p[v]",
                "-map", "[v]", "-map", "0:a", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "14", "-c:a", "pcm_s16le",
                "-ar", "48000", "-t", f"{z - a:.3f}", str(f))
        _ff("-f", "lavfi", "-i", "color=c=green:s=64x64", "-frames:v", "1", str(pic))
    else:
        for f in (src, voice, pic, roll, *files):
            f.write_bytes(b"x")
    roll.write_bytes(roll.read_bytes() if roll.exists() else b"x")
    write_json(b / "ingest.json", {"source": str(src), "width": 320, "height": 240, "duration": 6.0, "fps": 25})
    write_json(b / "transcript.json", {"words": [{"text": t, "start": s, "end": e} for t, s, e in WORDS]})
    pieces, out = [], 0.0
    for (a, z), f in zip(PIECES, files):
        ws = [{"text": t, "start": round(out + s - a, 3), "end": round(out + e - a, 3)} for t, s, e in WORDS if s >= a and e <= z + 0.01]
        pieces.append({"file": str(f), "src_start": a, "src_end": z, "out_start": round(out, 3), "utt": f"u{len(pieces) + 1:03d}",
                       "punch": 1.0, "vf": vf, "words": ws})
        out += z - a
    (b / "audio").mkdir()
    (b / "audio" / "music_pop_42.wav").write_bytes(b"x")
    write_json(b / "edit_manifest.json", {
        "source": str(src), "fps": 25, "size": [360, 640], "total": round(out, 3), "pieces": pieces, "cut": str(b / "cut.mov"),
        "pictures": [{"t0": 1.6, "t1": 2.8, "name": "tea.jpg"}],
        "broll": [{"t0": 3.0, "t1": 3.6, "path": str(roll), "src": 0.2, "why": "you said leaves"}],
        "music": {"source": "generated", "genre": "pop", "bpm": 100}, "caption_place": {"y": 0.7},
        "motion": [{"t0": 0.2, "t1": 1.0, "text": "TEA"}]})
    write_json(b / "style_plan.json", {"slams": [{"t0": 2.0, "t1": 2.4, "text": "FRESH"}]})
    write_json(b / "edit_plan.json", {"mode": "speech"})
    data = proj.data
    data["edit_options"] = {"pictures": [str(pic)]}
    data["settings"]["music_enabled"] = True
    proj.save(data)
    for st in ("ingest", "transcribe", "sense", "plan", "cut", "render", "validate", "export"):
        proj.mark(st, "done")
    return root


class Model(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.proj = timeline.Project(make_project(self.tmp))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_automatic_edit_becomes_tracks(self):
        m = timeline.base_model(self.proj)
        self.assertEqual([c["id"] for c in m["cuts"]], ["c1", "c2", "c3"])
        self.assertAlmostEqual(m["total"], 1.5 + 1.5 + 1.2, places=3)
        self.assertEqual([c["start"] for c in m["cuts"]], [0.0, 1.5, 3.0])
        self.assertEqual(" ".join(c["text"] for c in m["captions"]), " ".join(t for t, _, _ in WORDS))
        self.assertTrue(all(c["cut"] in ("c1", "c2", "c3") for c in m["captions"]))
        self.assertEqual(m["pictures"][0]["name"], "tea.jpg")
        self.assertEqual(m["broll"][0]["why"], "you said leaves")
        self.assertEqual(m["music"]["volume"], 0.14)
        self.assertEqual(m["music"]["source"], "generated")
        groups = {o["group"] for o in m["overlays"]}
        self.assertEqual(groups, {"Big text", "Motion"})   # any timed list in the manifest is shown
        self.assertEqual((m["cuts"][0]["min"], m["cuts"][0]["max"]), (0.0, 6.0))   # crop known: can be made longer

    def test_composition_is_hyperframes_and_reads_back_the_same(self):
        m = timeline.load(self.proj)
        html = timeline.comp_path(self.proj).read_text(encoding="utf-8")
        self.assertIn('data-composition-id="takebloom-edit"', html)
        self.assertIn('data-width="360" data-height="640"', html)
        self.assertEqual(html.count('data-tb-kind="cut"'), 3)
        for attr in ("data-media-start", "data-track-index", "data-has-audio", "data-volume"):
            self.assertIn(attr, html)
        self.assertNotIn("crossorigin", html)
        self.assertIn('src="media/p0000.mov"', html)
        self.assertNotIn('src="../', html)                 # HyperFrames reads media only inside its folder
        linked = timeline.comp_path(self.proj).parent / "media" / "p0000.mov"
        self.assertTrue(os.path.samefile(linked, self.proj.build / "pieces" / "p0000.mov"))   # a hard link, no copy
        self.assertIn("data-no-timeline", html)
        back = timeline.normalize(self.proj, timeline.parse(self.proj))
        self.assertEqual([(c["in"], c["out"]) for c in back["cuts"]], [(c["in"], c["out"]) for c in m["cuts"]])
        self.assertEqual([(c["text"], c["start"]) for c in back["captions"]], [(c["text"], c["start"]) for c in m["captions"]])
        self.assertEqual(back["pictures"][0]["t0"], m["pictures"][0]["t0"])

    def test_trim_delete_and_reorder_ripple_and_captions_follow_their_piece(self):
        m = timeline.load(self.proj)
        come = next(c for c in m["captions"] if c["text"].startswith("Come"))
        m["cuts"] = [m["cuts"][2], m["cuts"][1]]          # delete the first piece, play the last one first
        m["cuts"][1]["in"] = 3.55                          # trim the start of "Fresh leaves every"
        out = timeline.save(self.proj, m)
        self.assertEqual([c["id"] for c in out["cuts"]], ["c3", "c2"])
        self.assertFalse((timeline.comp_path(self.proj).parent / "media" / "p0000.mov").exists())   # unused links go
        self.assertAlmostEqual(out["total"], 1.2 + 0.95, places=3)
        come2 = next(c for c in out["captions"] if c["text"].startswith("Come"))
        self.assertAlmostEqual(come2["start"], come["start"] - 3.0, places=3)   # moved to the front with its piece
        hello = next(c for c in out["captions"] if c["text"].startswith("Hello"))
        self.assertIsNone(hello["start"])                  # its piece is gone: hidden, kept for undo
        words = [w["text"] for w in timeline.caption_words(out)]
        self.assertNotIn("Fresh", words)                   # trimmed away with the start of its piece
        self.assertIn("leaves", words)
        self.assertNotIn("Hello", words)
        self.assertEqual(words[0], "Come")
        html = timeline.comp_path(self.proj).read_text(encoding="utf-8")
        self.assertIn('data-hidden="true"', html)

    def test_edited_and_retimed_captions(self):
        m = timeline.load(self.proj)
        cap = next(c for c in m["captions"] if c["text"].startswith("Hello"))
        cap["text"] = "Welcome to our tea garden"
        out = timeline.save(self.proj, m)
        words = timeline.caption_words(out)
        five = [w for w in words if w["start"] < 1.5]
        self.assertEqual([w["text"] for w in five], ["Welcome", "to", "our", "tea", "garden"])
        self.assertTrue(all(a["end"] <= b["start"] + 1e-6 for a, b in zip(five, five[1:])))
        # untouched captions keep the spoken word timings
        come = [w for w in words if w["text"] == "Come"][0]
        self.assertAlmostEqual(come["start"], 3.0 + 0.1, places=3)
        # move a caption later inside its piece
        cap2 = next(c for c in out["captions"] if c["text"].startswith("Come"))
        cap2["at"] += 0.3
        out2 = timeline.save(self.proj, out)
        come2 = [w for w in timeline.caption_words(out2) if w["text"] == "Come"][0]
        self.assertAlmostEqual(come2["start"], 3.4, places=3)
        # an empty caption is removed
        cap3 = next(c for c in out2["captions"] if c["text"].startswith("Come"))
        cap3["text"] = "   "
        out3 = timeline.save(self.proj, out2)
        self.assertFalse(any(c["text"].startswith("Come") for c in out3["captions"]))

    def test_nothing_from_the_panel_can_point_at_other_files_or_break_limits(self):
        m = timeline.load(self.proj)
        m["cuts"][0].update({"piece": "C:/Windows/evil.mov", "vf": "movie=evil", "in": -50, "out": 99})
        m["cuts"].append({"id": "c99", "in": 0, "out": 1})
        m["pictures"][0].update({"path": "C:/Windows/win.ini", "t0": 0.0, "t1": 100})
        m["music"]["volume"] = 9
        out = timeline.normalize(self.proj, m)
        self.assertEqual(out["cuts"][0]["piece"], str(self.proj.build / "pieces" / "p0000.mov"))
        self.assertNotIn("evil", out["cuts"][0]["vf"])
        self.assertEqual((out["cuts"][0]["in"], out["cuts"][0]["out"]), (0.0, 6.0))
        self.assertEqual(len(out["cuts"]), 3)
        self.assertTrue(out["pictures"][0]["path"].endswith("tea.jpg"))
        self.assertLessEqual(out["pictures"][0]["t1"] - out["pictures"][0]["t0"], 8.0)
        self.assertLessEqual(out["music"]["volume"], 0.14 * 2.5)
        with self.assertRaises(timeline.ToolError):
            timeline.normalize(self.proj, {"cuts": []})

    def test_without_a_crop_record_pieces_trim_only_inside_the_automatic_cut(self):
        m = read_json(self.proj.build / "edit_manifest.json")
        for pc in m["pieces"]:
            pc.pop("vf")
        write_json(self.proj.build / "edit_manifest.json", m)
        tl = timeline.load(self.proj)
        tl["cuts"][0].update({"in": 0.0, "out": 3.0})
        out = timeline.save(self.proj, tl)
        self.assertEqual((out["cuts"][0]["in"], out["cuts"][0]["out"]), (0.5, 2.0))

    def test_changes_made_in_another_hyperframes_tool_are_read(self):
        timeline.load(self.proj)
        path = timeline.comp_path(self.proj)
        html = path.read_text(encoding="utf-8")
        # trim the first piece to 1 s by editing the standard attributes, and set the music level
        html = html.replace('id="c1" src="media/p0000.mov" data-start="0.000" data-duration="1.500" data-media-start="0.000"',
                            'id="c1" src="media/p0000.mov" data-start="0.000" data-duration="1.000" data-media-start="0.250"')
        html = html.replace('data-volume="0.140"', 'data-volume="0.070"')
        path.write_text(html, encoding="utf-8")
        out = timeline.load(self.proj)
        self.assertEqual((out["cuts"][0]["in"], out["cuts"][0]["out"]), (0.75, 1.75))
        self.assertAlmostEqual(out["total"], 1.0 + 1.5 + 1.2, places=3)
        self.assertEqual(out["music"]["volume"], 0.07)

    def test_a_new_automatic_edit_replaces_an_old_timeline(self):
        timeline.load(self.proj)
        m = read_json(self.proj.build / "edit_manifest.json")
        m["pieces"] = m["pieces"][:2]
        m["total"] = 3.0
        write_json(self.proj.build / "edit_manifest.json", m)
        out = timeline.load(self.proj)
        self.assertEqual(len(out["cuts"]), 2)
        self.assertIn(timeline.signature(m), timeline.comp_path(self.proj).read_text(encoding="utf-8"))

    def test_music_switched_off_is_kept(self):
        m = timeline.load(self.proj)
        m["music"]["on"] = False
        timeline.save(self.proj, m)
        self.assertFalse(timeline.load(self.proj)["music"]["on"])


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class MakeTheFilmAgain(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.proj = timeline.Project(make_project(self.tmp, real_media=True))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_only_changed_pieces_are_cut_and_the_render_gets_the_persons_choices(self):
        m = timeline.load(self.proj)
        m["cuts"] = [m["cuts"][0], m["cuts"][2], m["cuts"][1]]   # reorder
        m["cuts"][0]["out"] = 1.5                                 # trim inside the cached piece
        m["cuts"][1]["out"] = 6.0                                 # longer than the automatic cut: from the source
        m["pictures"][0].update({"t0": 0.4, "t1": 1.4})
        m["broll"] = []
        m["music"]["volume"] = 0.07
        cap = next(c for c in m["captions"] if c["text"].startswith("Come"))
        cap["text"] = "Come and visit us"
        timeline.save(self.proj, m)
        new = timeline.rebuild(self.proj)
        self.assertEqual(len(new["pieces"]), 3)
        self.assertEqual(new["pieces"][2]["file"], str(self.proj.build / "pieces" / "p0001.mov"))   # untouched: reused
        self.assertIn("timeline_pieces", new["pieces"][0]["file"])
        self.assertAlmostEqual(new["total"], 1.0 + 1.4 + 1.5, delta=0.12)
        st = read_json(self.proj.state_file)
        self.assertEqual(st["cut"]["status"], "done")
        self.assertNotIn("render", st)                    # the edit resumes from the render stage
        tl = new["timeline"]
        self.assertEqual(tl["pictures"][0]["t0"], 0.4)
        self.assertEqual(tl["broll"], [])
        self.assertEqual(tl["music"]["volume"], 0.07)
        self.assertIn("us", [w["text"] for w in tl["words"]])
        self.assertTrue(all(c["cut"] in ("c1", "c2", "c3") for c in tl["cues"]))
        # a second identical rebuild cuts nothing again
        again = timeline.rebuild(self.proj)
        self.assertEqual(read_json(self.proj.state_file)["cut"]["recut"], 0)
        self.assertAlmostEqual(again["total"], new["total"], places=2)
        # the new edit's timeline keeps the person's caption text
        out = timeline.load(self.proj)
        self.assertTrue(any(c["text"] == "Come and visit us" for c in out["captions"]))
        self.assertEqual([round(c["out"] - c["in"], 2) for c in out["cuts"]], [1.0, 1.4, 1.5])

    def test_the_picture_pass_uses_the_timeline_places(self):
        m = timeline.load(self.proj)
        m["pictures"][0].update({"t0": 0.4, "t1": 1.4, "move": "pan_left"})
        timeline.save(self.proj, m)
        man = timeline.rebuild(self.proj)
        seen = {}
        with patch.object(pictures, "render_still", side_effect=lambda pic, dest, w, h, fps, sec, move: seen.update(move=move, sec=sec) or dest), \
                patch("video_studio.broll.apply", side_effect=lambda video, cuts, *a: seen.update(cuts=cuts) or video):
            pictures.apply_pass(self.proj, man, Path("v.mp4"), {}, 360, 640, man["total"])
        self.assertEqual(seen["move"], "pan_left")
        self.assertEqual((seen["cuts"][0]["t0"], seen["cuts"][0]["t1"]), (0.4, 1.4))
        self.assertEqual(man["pictures"][0]["path"], str(self.tmp / "tea.jpg"))


class StudioRoutes(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_get_save_and_make_again(self):
        with patch.object(studio, "workspace", return_value=self.tmp), patch.object(timeline, "preview_file", return_value=None):
            got = studio.timeline_get(str(self.root))
            self.assertEqual(len(got["cuts"]), 3)
            self.assertTrue(got["file"].endswith("index.html"))
            got["cuts"] = got["cuts"][1:]
            saved = studio.timeline_post(False, {"root": str(self.root), "model": got})
            self.assertEqual(len(saved["cuts"]), 2)
            self.assertIn("error", studio.timeline_post(False, {"root": str(self.root), "model": {"cuts": []}}))
            self.assertIn("error", studio.timeline_get(str(self.tmp / "nowhere")))
            with patch.object(studio.Job, "start", lambda self: None):
                job = studio.timeline_post(True, {"root": str(self.root)})
            self.assertIn("job", job)
            self.assertEqual(studio.JOBS[job["job"]].args[:2], ["timeline", "render"])
            studio.JOBS.pop(job["job"], None)

    def test_the_panel_script_is_served_and_wired(self):
        ui = Path(studio.__file__).parent / "studio_ui"
        self.assertTrue((ui / "timeline.js").is_file())
        self.assertIn('<script src="/timeline.js"></script>', (ui / "index.html").read_text(encoding="utf-8"))
        js = (ui / "timeline.js").read_text(encoding="utf-8")
        for needed in ("/api/timeline", "/api/timeline/render", "ctrl", "Delete"):
            self.assertIn(needed, js)


if __name__ == "__main__":
    unittest.main()
