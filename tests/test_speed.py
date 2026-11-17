"""1.9.0 speed work: the two-pass style renderer, the worker counts and the automatic final encoder.
Offline: no models, no network. The style-helper checks need a Python with OpenCV (the app's ML Python) and FFmpeg;
they are skipped when neither is found."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("VIDEO_STUDIO_HOME", tempfile.mkdtemp(prefix="vs-speed-"))

from video_studio import encoders  # noqa: E402
from video_studio.engines import style  # noqa: E402
from video_studio.util import ToolError, app_data_dir  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "video_studio" / "helpers" / "style_helper.py"


def bench(x264=(60.0, 90.0), **hw):
    """A benchmark result: encoder -> (ms per frame, quality)."""
    def row(ms, q):
        return {"ok": True, "start_s": 0.5, "ms_per_frame": ms, "typical_s": round(0.5 + encoders.TYPICAL * ms / 1000, 1),
                "quality": q, "mb": 3.0}
    return {"ok": True, "metric": "vmaf", "results": {"libx264": row(*x264), **{k: row(*v) for k, v in hw.items()}}}


class EncoderChoice(unittest.TestCase):
    def setUp(self):
        encoders._path().unlink(missing_ok=True)

    def test_a_faster_hardware_encoder_with_the_same_picture_wins(self):
        self.assertEqual(encoders.decide(bench(h264_qsv=(25.0, 89.6))), "h264_qsv")

    def test_the_fastest_of_several_good_encoders_wins(self):
        self.assertEqual(encoders.decide(bench(h264_qsv=(30.0, 90.0), h264_nvenc=(12.0, 89.5))), "h264_nvenc")

    def test_a_worse_picture_is_never_traded_for_speed(self):
        self.assertEqual(encoders.decide(bench(h264_qsv=(10.0, 87.0))), "libx264")

    def test_a_barely_faster_encoder_is_not_worth_it(self):
        self.assertEqual(encoders.decide(bench(h264_qsv=(55.0, 90.0))), "libx264")

    def test_failed_or_missing_results_mean_x264(self):
        self.assertEqual(encoders.decide({"ok": False, "results": {}}), "libx264")
        b = bench(h264_qsv=(20.0, 90.0))
        b["results"]["h264_qsv"] = {"ok": False, "error": "device not found"}
        self.assertEqual(encoders.decide(b), "libx264")
        b = bench(h264_qsv=(20.0, 90.0))
        b["results"]["libx264"]["quality"] = None
        self.assertEqual(encoders.decide(b), "libx264")

    def test_psnr_is_used_when_ffmpeg_has_no_vmaf(self):
        b = bench(x264=(60.0, 41.0), h264_qsv=(25.0, 40.6))
        b["metric"] = "psnr"
        self.assertEqual(encoders.decide(b), "h264_qsv")
        b["results"]["h264_qsv"]["quality"] = 40.0
        self.assertEqual(encoders.decide(b), "libx264")

    def test_hardware_arguments_are_constant_quality_high_profile(self):
        for enc in encoders.HARDWARE:
            args = encoders.hw_args(enc, "standard")
            self.assertEqual(args[args.index("-c:v") + 1], enc)
            self.assertIn("-profile:v", args)
            self.assertIn("20", args)  # the same number x264 uses for standard
        self.assertIn("nv12", encoders.hw_args("h264_qsv", "draft"))  # Quick Sync takes nv12, not yuv420p
        with self.assertRaises(ValueError):
            encoders.hw_args("libx265", "standard")

    def test_the_test_runs_once_per_ffmpeg_and_is_cached(self):
        calls = []

        def fake_bench(ffmpeg=None, candidates=None):
            calls.append(ffmpeg)
            return bench(h264_qsv=(25.0, 90.0))

        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "ffmpeg.exe"
            exe.write_bytes(b"x" * 10)
            with patch.object(encoders, "benchmark", fake_bench):
                self.assertEqual(encoders.choice(str(exe))["encoder"], "h264_qsv")
                self.assertEqual(encoders.choice(str(exe))["encoder"], "h264_qsv")
                self.assertEqual(len(calls), 1)
                exe.write_bytes(b"y" * 20)  # FFmpeg replaced -> test again
                encoders.choice(str(exe))
                self.assertEqual(len(calls), 2)

    def test_config_can_force_the_encoder(self):
        with patch.object(encoders.config, "get", lambda k: "libx264" if k == "render.encoder" else None):
            self.assertEqual(encoders.final_encoder(), "libx264")
        with patch.object(encoders.config, "get", lambda k: "h264_nvenc" if k == "render.encoder" else None):
            self.assertEqual(encoders.final_encoder(), "h264_nvenc")
        with patch.object(encoders.config, "get", lambda k: "something" if k == "render.encoder" else None):
            self.assertEqual(encoders.final_encoder(), "libx264")

    def test_a_failed_hardware_encode_is_redone_with_x264_and_not_used_again(self):
        from video_studio.engines import ff

        runs = []

        def fake_ff(args, **kw):
            runs.append(list(args))
            if "h264_qsv" in args:
                raise ToolError("ffmpeg failed (exit 1):\n  MFX session error")
            return "ok"

        with patch.object(encoders, "choice", lambda *a, **k: {**(encoders.read_json(encoders._path(), {}) or {}),
                                                              "encoder": "h264_qsv"}), \
                patch.object(encoders.config, "get", lambda k: None), patch.object(ff, "ff", fake_ff):
            self.assertEqual(encoders.encode(["-i", "in.mp4"], "standard", ["out.mp4"]), "ok")
            self.assertIn("h264_qsv", runs[0])
            self.assertIn("libx264", runs[1])
            self.assertEqual(runs[1][:2], ["-i", "in.mp4"])
            self.assertEqual(runs[1][-1], "out.mp4")
            self.assertEqual(encoders.final_encoder(), "libx264")  # marked failed on this PC
            self.assertIn("h264_qsv", json.loads((app_data_dir() / encoders.CACHE).read_text())["failed"])

    def test_an_x264_failure_is_not_hidden(self):
        from video_studio.engines import ff

        def fake_ff(args, **kw):
            raise ToolError("ffmpeg failed")

        with patch.object(encoders, "final_encoder", lambda: "libx264"), patch.object(ff, "ff", fake_ff):
            with self.assertRaises(ToolError):
                encoders.encode(["-i", "a"], "standard", ["b"])


class StyleWorkers(unittest.TestCase):
    def test_low_memory_gives_one_worker_each(self):
        p = style._parallelism(cores=16, free_gb=1.2)
        self.assertEqual((p["workers"], p["track_workers"]), (1, 1))
        self.assertEqual(p["enc_threads"], 0)

    def test_four_gb_free_runs_parts_side_by_side(self):
        p = style._parallelism(cores=16, free_gb=4.0)
        self.assertGreaterEqual(p["track_workers"], 4)  # tracking is one core per worker: the main speed-up
        self.assertGreaterEqual(p["workers"], 2)
        self.assertLessEqual(p["track_workers"] * style.TRACK_GB + style.KEEP_FREE_GB, 4.0)
        self.assertLessEqual(p["workers"] * style.RENDER_GB + style.KEEP_FREE_GB, 4.0)

    def test_never_more_than_the_processor_or_six(self):
        p = style._parallelism(cores=4, free_gb=64)
        self.assertEqual((p["workers"], p["track_workers"]), (1, 2))
        p = style._parallelism(cores=64, free_gb=64)
        self.assertEqual((p["workers"], p["track_workers"]), (6, 6))

    def test_bigger_frames_need_more_memory_per_worker(self):
        hd = style._parallelism(cores=16, free_gb=4.0)
        uhd = style._parallelism(cores=16, free_gb=4.0, pixels=2160 * 3840)
        self.assertLess(uhd["track_workers"], hd["track_workers"])


class EditSideBySide(unittest.TestCase):
    def test_run_all_keeps_job_order_and_reports_progress(self):
        import time as _t

        from video_studio import speed

        seen = []

        def slow_first(n):
            _t.sleep(0.05 if n == 0 else 0)
            return n * 10

        self.assertEqual(speed.run_all(slow_first, range(6), 3, seen.append), [0, 10, 20, 30, 40, 50])
        self.assertEqual(seen, [1, 2, 3, 4, 5, 6])
        self.assertEqual(speed.run_all(slow_first, [], 3), [])

    def test_run_all_raises_the_first_error(self):
        from video_studio import speed

        def boom(n):
            if n == 2:
                raise ToolError("ffmpeg failed")
            return n

        with self.assertRaises(ToolError):
            speed.run_all(boom, range(5), 2)

    def test_piece_and_sense_workers_follow_memory(self):
        from video_studio import speed
        from video_studio.engines import sense

        self.assertEqual(speed.piece_workers(free_gb=1.1, cores=16), 1)
        self.assertEqual(speed.piece_workers(free_gb=8, cores=16), 3)
        self.assertEqual(speed.piece_workers(free_gb=8, cores=4), 1)
        self.assertEqual(sense.workers(cores=16, free_gb=1.5), 1)
        self.assertEqual(sense.workers(cores=16, free_gb=8), 4)
        self.assertEqual(sense.workers(cores=4, free_gb=8), 1)

    def test_voice_cleanup_runs_in_the_background_and_is_picked_up_by_the_cut(self):
        from video_studio import speed
        from video_studio.engines import ff, ml

        with tempfile.TemporaryDirectory() as tmp:
            build = Path(tmp)
            calls = []

            def fake_call(cmd, **kw):
                calls.append(cmd)
                Path(kw["dest"]).write_bytes(b"RIFF")
                return {"ok": True}

            with patch.object(ml, "available", lambda m: True), patch.object(ml, "call", fake_call), \
                    patch.object(ff, "ff", lambda args, **kw: Path(args[-1]).write_bytes(b"RIFF")):
                self.assertTrue(speed.start_voice_cleanup(build, build / "source.mov"))
                self.assertFalse(speed.start_voice_cleanup(build, build / "source.mov"))  # never twice
                self.assertEqual(speed.wait_voice_cleanup(build), build / "voice_dn.wav")
            self.assertEqual(calls, ["denoise"])
            self.assertTrue((build / "voice_raw.wav").exists())
            self.assertFalse((build / "voice_dn.part.wav").exists())
            self.assertIsNone(speed.wait_voice_cleanup(build))  # nothing running any more
            self.assertFalse(speed.start_voice_cleanup(build, build / "source.mov"))  # already cleaned

    def test_voice_cleanup_failure_leaves_the_cut_to_do_it(self):
        from video_studio import speed
        from video_studio.engines import ff, ml

        with tempfile.TemporaryDirectory() as tmp:
            build = Path(tmp)

            def bad_call(cmd, **kw):
                raise ToolError("ML helper 'denoise' failed")

            with patch.object(ml, "available", lambda m: True), patch.object(ml, "call", bad_call), \
                    patch.object(ff, "ff", lambda args, **kw: Path(args[-1]).write_bytes(b"RIFF")):
                self.assertTrue(speed.start_voice_cleanup(build, build / "source.mov"))
                self.assertIsNone(speed.wait_voice_cleanup(build))
            self.assertFalse(speed.start_voice_cleanup(build, build / "source.mov", enhance=False))


def _cv2_python():
    for cmd in ([sys.executable], ["py", "-3.13"], ["py", "-3"]):
        try:
            if subprocess.run([*cmd, "-c", "import cv2, numpy"], capture_output=True, timeout=60).returncode == 0:
                return cmd
        except (OSError, subprocess.SubprocessError):
            continue
    return None


PY = _cv2_python()
FFMPEG = shutil.which("ffmpeg")

# Runs inside the ML Python: loads the helper with a tiny plan, swaps MediaPipe for a deterministic fake segmenter,
# and checks the two-pass tracker (3 parts, warm-up, whole-video face path) against the old one-pass loop.
DRIVER = textwrap.dedent(r'''
    import importlib.util, json, sys
    import numpy as np
    sys.argv = ["style_helper.py", sys.argv[1]]
    spec = importlib.util.spec_from_file_location("sh", HELPER)
    sh = importlib.util.module_from_spec(spec); spec.loader.exec_module(sh)

    class View:
        def __init__(self, a): self.a = a
        def numpy_view(self): return self.a
    class Res:
        def __init__(self, a): self.confidence_masks = [View(a)]
    class FakeSeg:
        def segment_for_video(self, img, ms):
            k = round(ms * sh.FPS / 1000)
            h, w = sh.H // 4, sh.W // 4
            yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
            cx, top = w * (0.5 + 0.25 * np.sin(k / 9)), h * (0.3 + 0.1 * np.cos(k / 13))
            person = ((np.abs(xx - cx) < w * 0.22) & (yy > top)).astype(np.float32)
            if k % 17 == 5:
                person[:] = 0  # nobody in this frame: the face must hold
            return Res((1 - person * (0.8 + 0.1 * np.sin(k)))[..., None].astype(np.float32))
        def close(self): pass
    class FakeMp:
        class ImageFormat: SRGB = 1
        @staticmethod
        def Image(image_format, data): return data

    def fake_init(self, model):
        self.seg, self.prev, self.mp = FakeSeg(), None, FakeMp
    sh.Matte.__init__ = fake_init

    total = int(round(sh.P["duration"] * sh.FPS))
    # old one-pass loop: measure every frame from frame 0, ease the face every frame
    m = sh.Matte(None); face = sh.FACE0; ref_faces, ref_masks = [], {}
    for k, raw in sh.frames(sh.decoder(0), 0, total):
        mask, target = m.measure(raw, k)
        if target is not None:
            face = 0.85 * face + 0.15 * target
        ref_faces.append(face)
        if sh.matte_needed(k / sh.FPS):
            ref_masks[k] = mask
    out = sys.argv[1] + ".t"
    bounds = sh.split(total, 3, 45)
    parts = [f"{out}{i}" for i in range(len(bounds) - 1)]
    for i in range(len(parts)):
        sh.track_range(bounds[i], bounds[i + 1], parts[i], str(i), progress=lambda *a: None)
    tr = sh.Track(parts, total)
    face_err = float(np.abs(np.array(ref_faces) - tr.faces[:len(ref_faces)]).max())
    mask_err = max(float(np.abs(sh.cv2.resize(ref_masks[k], (sh.W, sh.H), interpolation=sh.cv2.INTER_LINEAR) - tr.matte(k)).max())
                   for k in ref_masks)
    print(json.dumps({"parts": len(parts), "frames": len(ref_faces), "face_err": face_err, "mask_err": mask_err,
                      "masked": len(ref_masks), "missing": [k for k in ref_masks if tr.matte(k) is None],
                      "extra": [k for k in tr.where if k not in ref_masks]}))
''')


@unittest.skipUnless(PY and FFMPEG, "needs FFmpeg and a Python with OpenCV")
class TwoPassStyle(unittest.TestCase):
    """The new renderer must give the old picture: same face path, same mattes, whatever the number of workers."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="tb-style-"))
        cls.clip = cls.tmp / "clip.mp4"
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=96x160:r=30:d=6.2", "-pix_fmt", "yuv420p",
                        str(cls.clip)], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def plan(self, name, workers=1, track_workers=1, model=""):
        p = {"shots": [{"t0": 0, "t1": 2, "kind": "push"}, {"t0": 2, "t1": 4, "kind": "close"}, {"t0": 4, "t1": 6.2, "kind": "dutch"}],
             "transitions": [{"t": 0.0, "kind": "flash"}, {"t": 2.0, "kind": "invert"}, {"t": 4.0, "kind": "whip"}],
             "hits": [0.06], "punches": [3.0], "slams": [{"t0": 0.06, "t1": 1.4, "text": "HELLO", "style": "accent"},
                                                         {"t0": 3.5, "t1": 4.3, "text": "WORLD", "style": "ink"}],
             "chips": [], "logos": [], "grain": 0.02, "bloom": 0.3, "grade_ref": None, "input": str(self.clip),
             "output": str(self.tmp / f"{name}.mp4"), "width": 96, "height": 160, "fps": 30, "duration": 6.2,
             "palette": style.palette("midnight"), "fonts": str(ROOT / "video_studio" / "assets" / "fonts"), "matte_model": model,
             "ffmpeg": FFMPEG, "crf": 0, "preset": "ultrafast", "workers": workers, "track_workers": track_workers, "enc_threads": 0}
        path = self.tmp / f"{name}.json"
        path.write_text(json.dumps(p), encoding="utf-8")
        return path

    def test_parallel_tracking_matches_the_one_pass_loop(self):
        plan = self.plan("track")
        script = self.tmp / "driver.py"
        script.write_text(DRIVER.replace("HELPER", repr(str(HELPER))), encoding="utf-8")
        res = subprocess.run([*PY, "-E", str(script), str(plan)], capture_output=True, text=True, timeout=300)
        self.assertEqual(res.returncode, 0, res.stderr[-2000:])
        r = json.loads(res.stdout.strip().splitlines()[-1])
        self.assertEqual(r["parts"], 3)
        self.assertEqual(r["frames"], 186)
        self.assertGreater(r["masked"], 30)
        self.assertEqual(r["missing"], [])
        self.assertEqual(r["extra"], [])
        self.assertLess(r["face_err"], 1e-3)   # pixels
        self.assertLess(r["mask_err"], 1e-5)

    def test_one_worker_and_several_workers_give_the_same_video(self):
        outs = []
        for name, w, tw in (("one", 1, 1), ("many", 3, 2)):
            res = subprocess.run([*PY, "-E", "-P", str(HELPER), str(self.plan(name, w, tw))], capture_output=True, text=True,
                                 timeout=300)
            self.assertEqual(res.returncode, 0, (res.stdout + res.stderr)[-2000:])
            self.assertIn("STYLE_DONE 186", res.stdout)
            self.assertIn("STYLE_PROGRESS", res.stdout)  # progress is shown with one worker too
            outs.append(self.tmp / f"{name}.mp4")
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir() if ".track" in p.name or ".part" in p.name), [])
        raw = [subprocess.run([FFMPEG, "-v", "error", "-i", str(o), "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"],
                              capture_output=True, check=True).stdout for o in outs]
        self.assertEqual(len(raw[0]), 96 * 160 * 3 // 2 * 186)
        self.assertEqual(raw[0], raw[1])  # lossless test encode: identical pictures


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(PY and FFMPEG, "needs FFmpeg and a Python with OpenCV")
class ParallelSense(unittest.TestCase):
    def test_parallel_sense_gives_the_same_result(self):
        helper = ROOT / "video_studio" / "helpers" / "sense_helper.py"
        with tempfile.TemporaryDirectory() as tmp:
            clip = Path(tmp) / "clip.mp4"
            subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=21", "-f", "lavfi",
                            "-i", "sine=f=440:d=21", "-pix_fmt", "yuv420p", "-shortest", str(clip)], check=True)
            out = []
            for workers in (1, 2):
                dest = Path(tmp) / f"sense{workers}.json"
                res = subprocess.run([*PY, "-E", "-P", str(helper), str(clip), str(dest), "", FFMPEG, "0.9", str(workers)],
                                     capture_output=True, text=True, timeout=600)
                self.assertEqual(res.returncode, 0, res.stderr[-1500:])
                out.append(json.loads(dest.read_text(encoding="utf-8")))
            self.assertEqual(len(out[0]["timeline"]), 126)
            self.assertEqual(out[0], out[1])
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["clip.mp4", "sense1.json", "sense2.json"])
