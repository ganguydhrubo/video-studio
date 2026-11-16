"""Background removal (cutout): FFmpeg graphs, output formats and the plain-English errors. No ML or FFmpeg runs."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("VIDEO_STUDIO_HOME", tempfile.mkdtemp(prefix="vs-unit-"))

from video_studio import cutout  # noqa: E402
from video_studio.util import ToolError  # noqa: E402

PROBE = {"format": {"duration": "6.0"},
         "streams": [{"codec_type": "video", "width": 1080, "height": 1920, "avg_frame_rate": "30/1",
                      "r_frame_rate": "30/1"},
                     {"codec_type": "audio", "codec_name": "pcm_s16le"}]}


class Graph(unittest.TestCase):
    def test_every_background_merges_the_mask_and_ends_in_v(self):
        for bg in cutout.BACKGROUNDS:
            g = cutout.build_graph(bg, 1080, 1920, "30/1")
            self.assertIn("[fg][m]alphamerge[person]", g, bg)
            self.assertIn("[1:v]setpts=PTS-STARTPTS,scale=1080:1920,format=gray[m]", g, bg)
            self.assertTrue(g.endswith("[v]"), bg)

    def test_blur_uses_the_source_twice_and_a_heavy_cheap_blur(self):
        g = cutout.build_graph("blur", 1080, 1920, "30/1")
        self.assertIn("split=2[src][back]", g)
        self.assertIn("[back]scale=134:240:flags=area,gblur=sigma=6,scale=1080:1920", g)
        self.assertIn("[bg][person]overlay=0:0:shortest=1", g)
        self.assertNotIn("[2:v]", g)

    def test_colour_is_a_generated_source(self):
        g = cutout.build_graph("colour", 1280, 720, "30000/1001", colour="#00b140")
        self.assertIn("color=c=0x00B140:s=1280x720:r=30000/1001", g)
        self.assertIn("overlay=0:0:shortest=1", g)

    def test_image_and_video_cover_the_frame(self):
        for bg in ("image", "video"):
            g = cutout.build_graph(bg, 1080, 1920, "30/1")
            self.assertIn("[2:v]", g)
            self.assertIn("scale=1080:1920:force_original_aspect_ratio=increase", g)
            self.assertIn("crop=1080:1920", g)
            self.assertIn("fps=30/1", g)
        self.assertIn("[2:v]setpts=PTS-STARTPTS,", cutout.build_graph("video", 1080, 1920, "30/1"))

    def test_transparent_keeps_alpha_and_has_no_background(self):
        g = cutout.build_graph("transparent", 1080, 1920, "30/1")
        self.assertTrue(g.endswith("[person]format=yuva420p[v]"))
        self.assertNotIn("overlay", g)
        g = cutout.build_graph("transparent", 1080, 1920, "30/1", alpha_format="yuva444p10le")
        self.assertTrue(g.endswith("[person]format=yuva444p10le[v]"))

    def test_background_inputs(self):
        self.assertEqual(cutout.background_inputs("image", Path("bg.png"), "30/1"),
                         ["-loop", "1", "-framerate", "30/1", "-i", "bg.png"])
        self.assertEqual(cutout.background_inputs("video", Path("bg.mp4"), "30/1"),
                         ["-stream_loop", "-1", "-i", "bg.mp4"])
        self.assertEqual(cutout.background_inputs("blur", None, "30/1"), [])

    def test_unknown_background_is_a_tool_error(self):
        with self.assertRaises(ToolError):
            cutout.build_graph("sparkles", 100, 100, "30/1")

    def test_colours(self):
        self.assertEqual(cutout.parse_colour("#00B140"), "0x00B140")
        self.assertEqual(cutout.parse_colour("0b4"), "0x00BB44")
        self.assertEqual(cutout.parse_colour("0xffffff"), "0xFFFFFF")
        self.assertEqual(cutout.parse_colour("White"), "white")
        with self.assertRaises(ToolError) as ctx:
            cutout.parse_colour("#12345")
        self.assertIn("#00B140", ctx.exception.hint)


class OutputPlan(unittest.TestCase):
    def test_transparent_webm_is_vp9_yuva420p(self):
        path, vcodec, acodec, alpha = cutout.output_plan("transparent", Path("o/out.webm"))
        self.assertEqual(path.suffix, ".webm")
        self.assertEqual(vcodec[vcodec.index("-c:v") + 1], "libvpx-vp9")
        self.assertEqual(vcodec[vcodec.index("-pix_fmt") + 1], "yuva420p")
        self.assertEqual(alpha, "yuva420p")
        self.assertIn("libopus", acodec)

    def test_transparent_mp4_becomes_webm(self):
        path, vcodec, _, _ = cutout.output_plan("transparent", Path("o/out.mp4"))
        self.assertEqual(path.name, "out.webm")
        self.assertIn("yuva420p", vcodec)

    def test_transparent_mov_is_prores_4444(self):
        path, vcodec, acodec, alpha = cutout.output_plan("transparent", Path("o/out.mov"))
        self.assertEqual(path.suffix, ".mov")
        self.assertEqual(vcodec[vcodec.index("-c:v") + 1], "prores_ks")
        self.assertEqual(vcodec[vcodec.index("-profile:v") + 1], "4444")
        self.assertEqual(vcodec[vcodec.index("-pix_fmt") + 1], "yuva444p10le")
        self.assertEqual(alpha, "yuva444p10le")

    def test_opaque_backgrounds_are_h264_yuv420p(self):
        path, vcodec, acodec, _ = cutout.output_plan("blur", Path("o/out.mp4"))
        self.assertEqual(path.suffix, ".mp4")
        self.assertIn("libx264", vcodec)
        self.assertEqual(vcodec[vcodec.index("-pix_fmt") + 1], "yuv420p")
        self.assertIn("aac", acodec)


class Run(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="vs-cutout-"))
        self.video = self.dir / "talk.mov"
        self.video.write_bytes(b"not really a video")
        self.model = self.dir / "selfie_multiclass.tflite"
        self.model.write_bytes(b"model")
        self.ff_calls = []
        self.ml_calls = []
        self.patches = [
            mock.patch.object(cutout.detect, "system_python", return_value=Path("python.exe")),
            mock.patch.object(cutout, "model_path", return_value=self.model),
            mock.patch.object(cutout.ff, "ffmpeg", return_value="ffmpeg.exe"),
            mock.patch.object(cutout.ff, "probe", return_value=PROBE),
            mock.patch.object(cutout.ff, "ff", side_effect=self._fake_ff),
            mock.patch.object(cutout.ml, "call", side_effect=self._fake_ml),
            mock.patch.object(cutout.resources, "ensure_memory", return_value=None),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def _fake_ff(self, args, **kw):
        self.ff_calls.append(args)
        Path(args[-1]).write_bytes(b"out")

    def _fake_ml(self, cmd, timeout=1800, **kw):
        self.ml_calls.append((cmd, kw))
        Path(kw["dest"]).write_bytes(b"mask")
        return {"ok": True, "dest": str(kw["dest"]), "engine": "mediapipe-selfie-multiclass", "fallback": "",
                "frames": 180, "fps": 30.0, "seconds": 12.0}

    def test_transparent_run_writes_webm_with_alpha_and_keeps_audio(self):
        res = cutout.run(self.video, self.dir / "out.mp4", "transparent")
        self.assertTrue(res["output"].endswith("out.webm"))
        self.assertTrue(Path(res["output"]).exists())
        args = self.ff_calls[-1]
        self.assertEqual(args[args.index("-pix_fmt") + 1], "yuva420p")
        self.assertIn("0:a:0?", args)
        self.assertEqual(res["engine"], "mediapipe-selfie-multiclass")
        self.assertEqual(res["frames"], 180)
        cmd, kw = self.ml_calls[0]
        self.assertEqual(cmd, "matte")
        self.assertEqual(kw["model"], self.model)
        self.assertEqual(kw["ffmpeg"], "ffmpeg.exe")
        self.assertEqual(kw["fps"], "30/1")
        self.assertEqual(kw["max_side"], 1280)

    def test_second_background_reuses_the_cached_mask(self):
        first = cutout.run(self.video, self.dir / "a.mp4", "blur")
        second = cutout.run(self.video, self.dir / "b.mp4", "colour", colour="#112233", keep_audio=False)
        self.assertEqual(len(self.ml_calls), 1)
        self.assertFalse(first["mask_cached"])
        self.assertTrue(second["mask_cached"])
        self.assertEqual(second["engine"], "mediapipe-selfie-multiclass")
        self.assertIn("-an", self.ff_calls[-1])
        self.assertIn("0x112233", " ".join(self.ff_calls[-1]))

    def test_video_background_loops_the_background_clip(self):
        bg = self.dir / "beach.mp4"
        bg.write_bytes(b"bg")
        cutout.run(self.video, self.dir / "o.mp4", "video", image=bg)
        args = self.ff_calls[-1]
        self.assertIn("-stream_loop", args)
        self.assertEqual(args[args.index("-t") + 1], "6.000")

    def test_no_python_is_a_plain_tool_error(self):
        with mock.patch.object(cutout.detect, "system_python", return_value=None):
            with self.assertRaises(ToolError) as ctx:
                cutout.run(self.video, self.dir / "o.mp4", "blur")
        self.assertIn("Python", str(ctx.exception))
        self.assertIn("python.org", ctx.exception.hint)
        self.assertEqual(self.ml_calls, [])

    def test_missing_model_is_a_plain_tool_error_with_the_setup_url(self):
        self.model.unlink()
        with self.assertRaises(ToolError) as ctx:
            cutout.run(self.video, self.dir / "o.mp4", "blur")
        self.assertIn("model", str(ctx.exception))
        self.assertIn("video-studio setup", ctx.exception.hint)
        self.assertIn("selfie_multiclass", ctx.exception.hint)
        self.assertEqual(self.ml_calls, [])

    def test_bad_inputs(self):
        with self.assertRaises(ToolError):
            cutout.run(self.dir / "missing.mov", self.dir / "o.mp4")
        with self.assertRaises(ToolError):
            cutout.run(self.video, self.dir / "o.mp4", "image")  # no image given
        with self.assertRaises(ToolError):
            cutout.run(self.video, self.dir / "o.mp4", "colour", colour="not a colour!")
        with self.assertRaises(ToolError):
            cutout.run(self.video, self.dir / "o.mp4", smooth=1.5)
        self.assertEqual(self.ml_calls, [])

    def test_available_explains_what_is_missing(self):
        self.model.unlink()
        with mock.patch.object(cutout.detect, "system_python", return_value=None), \
                mock.patch.object(cutout.detect, "ffmpeg_path", return_value=None):
            info = cutout.available()
        self.assertFalse(info["ok"])
        text = " ".join(info["reasons"])
        self.assertIn("Python", text)
        self.assertIn("FFmpeg", text)
        self.assertIn("model", text)


if __name__ == "__main__":
    unittest.main()
