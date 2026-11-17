"""1.9.0 best-quality cut-out (BiRefNet lite): keyframe walk, model download rules, measured estimates, wiring.
Offline and fast: the model, downloads and FFmpeg are all mocked."""

import hashlib
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("VIDEO_STUDIO_HOME", tempfile.mkdtemp(prefix="vs-unit-"))
os.environ.setdefault("VIDEO_STUDIO_MODELS", tempfile.mkdtemp(prefix="vs-models-"))

from video_studio import cutout, machine, matting  # noqa: E402
from video_studio.util import ToolError, app_data_dir  # noqa: E402

HELPER = Path(__file__).resolve().parents[1] / "video_studio" / "helpers" / "matte_helper.py"
_spec = importlib.util.spec_from_file_location("matte_helper_under_test", HELPER)
mh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mh)

HAVE_CV = importlib.util.find_spec("cv2") is not None and importlib.util.find_spec("numpy") is not None


def fake_pc(ram=16.0, threads=16, free=8.0):
    return {"cpu": {"name": "Test CPU", "cores": threads // 2, "threads": threads}, "gpus": [], "ram_total_gb": ram,
            "ram_free_gb": free, "disk_free_gb": 200.0, "encoders": {"working": []}, "cuda": {"cuda": False},
            "models": {"tools": {"ffmpeg": True}, "packages": {}, "segmentation": True}, "speed": {"measured": False}}


def clear_speeds():
    (app_data_dir() / matting.SPEED_FILE).unlink(missing_ok=True)


class Walk(unittest.TestCase):
    def run_walk(self, n, gap, cuts=(), tail=True):
        keys, carries, emitted = [], [], []

        def key_fn(i):
            keys.append(i)
            return f"A{i}"

        def carry_fn(i, k0, k1, t):
            carries.append((i, k0[0], None if k1 is None else k1[0], round(t, 3)))
            return f"C{i}"

        stats = mh.walk(range(n), gap, key_fn, carry_fn, lambda i, a, is_key: emitted.append((i, a, is_key)),
                        is_cut=lambda prev, cur: cur in cuts, tail=tail)
        return keys, carries, emitted, stats

    def test_every_frame_is_emitted_once_in_order(self):
        keys, carries, emitted, stats = self.run_walk(10, 3)
        self.assertEqual([e[0] for e in emitted], list(range(10)))
        self.assertEqual(keys, [0, 3, 6, 9])
        self.assertEqual(stats, {"frames": 10, "keyframes": 4, "cuts": 0})
        for i, a, is_key in emitted:
            self.assertEqual(a, f"A{i}" if i in keys else f"C{i}")
            self.assertEqual(is_key, i in keys)

    def test_in_between_frames_blend_the_keys_either_side(self):
        _, carries, _, _ = self.run_walk(7, 3)
        self.assertIn((1, 0, 3, 0.333), carries)
        self.assertIn((2, 0, 3, 0.667), carries)
        self.assertIn((4, 3, 6, 0.333), carries)

    def test_a_shot_change_becomes_a_key_and_the_frames_before_only_look_back(self):
        keys, carries, _, stats = self.run_walk(10, 4, cuts={6})
        self.assertIn(6, keys)
        self.assertEqual(stats["cuts"], 1)
        self.assertIn((5, 4, None, 0.5), carries)  # 5 sits after key 4, before the cut: never blended across the cut

    def test_short_tail_is_carried_forwards_long_tail_ends_on_a_key(self):
        keys, carries, _, _ = self.run_walk(8, 6)  # keys 0, 6; tail = [7] (1 frame, under half a gap)
        self.assertEqual(keys, [0, 6])
        self.assertEqual(carries[-1][:3], (7, 6, None))
        keys, _, _, _ = self.run_walk(11, 6)  # tail = [7..10] (4 frames, over half a gap): last frame is a key
        self.assertEqual(keys, [0, 6, 10])

    def test_gap_of_one_runs_the_model_on_every_frame(self):
        keys, carries, _, _ = self.run_walk(5, 1)
        self.assertEqual(keys, list(range(5)))
        self.assertEqual(carries, [])

    @unittest.skipUnless(HAVE_CV, "needs numpy + OpenCV")
    def test_shot_change_threshold(self):
        import numpy as np

        a = np.full((36, 64), 100, np.uint8)
        self.assertFalse(mh.shot_change(a, a + 10))
        self.assertTrue(mh.shot_change(a, a + 90))
        self.assertFalse(mh.shot_change(None, a))


@unittest.skipUnless(HAVE_CV, "needs numpy + OpenCV")
class ModelMaths(unittest.TestCase):
    def test_prepare_is_normalised_nchw_1024(self):
        import numpy as np

        x = mh.BiRefNet.prepare(np.zeros((1920, 1080, 3), np.uint8))
        self.assertEqual(x.shape, (1, 3, 1024, 1024))
        self.assertEqual(x.dtype, np.float32)
        self.assertAlmostEqual(float(x[0, 0, 0, 0]), -0.485 / 0.229, places=4)

    def test_logits_become_probabilities_at_frame_size(self):
        import numpy as np

        logits = np.zeros((1, 1, 1024, 1024), np.float32)
        logits[..., :512] = 12.0
        logits[..., 512:] = -12.0
        a = mh.BiRefNet.to_alpha(logits, 108, 192)
        self.assertEqual(a.shape, (192, 108))
        self.assertGreater(a[10, 5], 0.99)
        self.assertLess(a[10, 100], 0.01)

    def test_zero_flow_warp_keeps_the_alpha(self):
        import numpy as np

        alpha = np.random.default_rng(0).random((40, 30)).astype(np.float32)
        out = mh.warp(alpha, np.zeros((20, 15, 2), np.float32))
        self.assertTrue(np.allclose(out, alpha, atol=1e-5))

    def test_mocked_model_runs_and_falls_back_from_graphics_card(self):
        import numpy as np

        class Sess:
            def __init__(self, providers):
                self.providers = providers

            def get_inputs(self):
                return [mock.Mock(name="in")]

            def get_providers(self):
                return self.providers

            def run(self, _names, _feed):
                if self.providers[0] == "DmlExecutionProvider":
                    raise RuntimeError("887A0020 driver's state is probably suspect")
                return [np.full((1, 1, 1024, 1024), 8.0, np.float32)]

        ort = mock.Mock()
        ort.get_available_providers.return_value = ["DmlExecutionProvider", "CPUExecutionProvider"]
        ort.InferenceSession.side_effect = lambda _m, _so, providers: Sess(providers)
        with mock.patch.dict("sys.modules", {"onnxruntime": ort}):
            net = mh.BiRefNet("model.onnx", 4)
            self.assertEqual(net.provider, "DmlExecutionProvider")
            alpha = net(np.zeros((64, 48, 3), np.uint8))
        self.assertEqual(net.provider, "CPUExecutionProvider")
        self.assertIn("graphics card failed", net.fallback)
        self.assertEqual(alpha.shape, (64, 48))
        self.assertGreater(float(alpha.min()), 0.99)


class Download(unittest.TestCase):
    def setUp(self):
        matting.model_path().unlink(missing_ok=True)

    def test_refuses_without_permission_and_says_size_and_licence(self):
        with mock.patch("urllib.request.urlretrieve") as get:
            with self.assertRaises(ToolError) as ctx:
                matting.ensure(allow_download=False)
        get.assert_not_called()
        self.assertIn("214 MB", str(ctx.exception))
        self.assertIn("MIT", str(ctx.exception))

    def test_bad_checksum_is_deleted(self):
        def fake(url, dest, hook=None):
            Path(dest).write_bytes(b"not the model")

        with mock.patch("urllib.request.urlretrieve", side_effect=fake):
            with self.assertRaises(ToolError) as ctx:
                matting.ensure(allow_download=True)
        self.assertIn("SHA-256", str(ctx.exception))
        self.assertFalse(matting.model_path().exists())
        self.assertFalse(matting.model_path().with_suffix(".onnx.part").exists())

    def test_good_checksum_lands_in_the_model_folder(self):
        data = b"pretend model bytes"
        model = {**matting.MODEL, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}

        def fake(url, dest, hook=None):
            Path(dest).write_bytes(data)

        with mock.patch.dict(matting.MODEL, model), mock.patch("urllib.request.urlretrieve", side_effect=fake) as get:
            path = matting.ensure(allow_download=True)
            self.assertTrue(matting.model_present())
            self.assertEqual(get.call_args[0][0], matting.MODEL["url"])
        self.assertEqual(path.parent.name, "birefnet")
        self.assertEqual(path.read_bytes(), data)
        path.unlink()

    def test_cutout_run_refuses_best_before_any_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            vid = Path(tmp, "v.mp4")
            vid.write_bytes(b"x")
            with mock.patch.object(cutout, "_video_facts") as facts:
                with self.assertRaises(ToolError):
                    cutout.run(vid, Path(tmp, "o.mp4"), "blur", quality="best")
                with self.assertRaises(ToolError):
                    cutout.run(vid, Path(tmp, "o.mp4"), "blur", quality="ultra")
            facts.assert_not_called()


class Estimate(unittest.TestCase):
    def setUp(self):
        clear_speeds()

    def tearDown(self):
        clear_speeds()

    def test_unknown_until_measured(self):
        est = matting.estimate(6, 30)
        self.assertFalse(est["known"])
        self.assertIn("not measured", matting.describe(est))

    @mock.patch.object(matting, "KEYS_PER_SECOND", 4.0)
    def test_keyframes_times_measured_key_speed(self):
        matting.record_speed("best", 1, 10.0, key_seconds=10.0, keyframes=1, source="bench")
        est = matting.estimate(6, 30)  # 180 frames, gap 8 -> 24 keys
        self.assertTrue(est["known"])
        self.assertEqual(est["keyframes"], matting.keyframe_count(180, 30))
        self.assertAlmostEqual(est["seconds"], est["keyframes"] * 10.0 + (180 - est["keyframes"]) * 0.06, places=1)
        self.assertIn("measured on this PC", matting.describe(est))

    @mock.patch.object(matting, "KEYS_PER_SECOND", 4.0)
    def test_real_runs_refine_the_in_between_cost(self):
        matting.record_speed("best", 180, 300.0, key_seconds=10.0, keyframes=24)
        est = matting.estimate(6, 30)
        between = (300.0 - 240.0) / (180 - 24)
        self.assertAlmostEqual(est["seconds"], est["keyframes"] * 10.0 + (180 - est["keyframes"]) * between, places=0)

    @mock.patch.object(matting, "KEYS_PER_SECOND", 4.0)
    def test_keyframe_count(self):
        self.assertEqual(matting.keyframe_count(1, 30), 1)
        self.assertEqual(matting.keyframe_count(180, 30), 24)  # gap round(30 / 4) = 8 -> 23 + the last frame
        self.assertEqual(matting.keyframe_count(100, 25), 18)  # gap 6

    def test_machine_check_warns_and_uses_the_measured_time(self):
        matting.record_speed("best", 1, 12.0, key_seconds=12.0, keyframes=1, source="bench")
        c = machine.check("cutout_best", seconds=6, s=fake_pc())
        msg = next(m for m in c["messages"] if m.startswith("Expected"))
        self.assertIn("measured on this PC", msg)
        weak = machine.check("cutout_best", seconds=6, s=fake_pc(ram=6, threads=4))
        self.assertEqual(weak["level"], "warn")
        self.assertTrue(any("very slow" in m for m in weak["messages"]))
        low = machine.check("cutout_best", s=fake_pc(free=3.0))
        self.assertTrue(any("free memory" in m for m in low["messages"]))
        self.assertEqual(machine.check("cutout_best", s=fake_pc(ram=8)).get("level"), "warn")

    def test_unmeasured_check_says_estimate(self):
        c = machine.check("cutout_best", seconds=6, s=fake_pc())
        msg = next(m for m in c["messages"] if m.startswith("Expected"))
        self.assertIn("estimate", msg)
        self.assertGreater(machine.TASK_WORK["cutout_best"], machine.TASK_WORK["cutout"])


class Wiring(unittest.TestCase):
    def test_fast_cache_key_is_unchanged_and_best_is_separate(self):
        import hashlib as h

        with tempfile.TemporaryDirectory() as tmp:
            vid = Path(tmp, "v.mp4")
            vid.write_bytes(b"x")
            st = vid.stat()
            old = h.sha1(f"{vid.resolve()}|{st.st_size}|{st.st_mtime_ns}|0.600|{cutout.MAX_SIDE}|{cutout.STRIDE}|"
                         f"{cutout.MASK_VERSION}".encode()).hexdigest()[:20]
            self.assertEqual(cutout._mask_key(vid, 0.6), old)
            self.assertNotEqual(cutout._mask_key(vid, 0.6, "best"), old)

    def test_server_passes_best_and_the_download_permission(self):
        from video_studio import studio

        with tempfile.TemporaryDirectory() as tmp:
            vid = Path(tmp, "v.mp4")
            vid.write_bytes(b"x")
            a = studio._quick_args("cutout", {"files": [str(vid)]}, {"cutout_quality": "best", "allow_download": True},
                                   Path(tmp, "p"), "C")
            self.assertIn("--best", a)
            self.assertIn("--allow-download", a)
            a = studio._quick_args("cutout", {"files": [str(vid)]}, {"allow_download": True}, Path(tmp, "p"), "C")
            self.assertNotIn("--best", a)
            self.assertNotIn("--allow-download", a)

    def test_cli_accepts_the_new_flags(self):
        from video_studio import cli

        args = cli.build_parser().parse_args(["cutout", "v.mp4", "--best", "--allow-download"])
        self.assertTrue(args.best and args.allow_download)
        self.assertFalse(cli.build_parser().parse_args(["cutout", "v.mp4"]).best)

    def test_quality_info_for_the_ui(self):
        info = matting.info()
        self.assertEqual(info["size"], "214 MB")
        self.assertIn("MIT", info["licence"])
        self.assertFalse(info["model_present"])
        self.assertEqual(info["estimate_text"], "")


if __name__ == "__main__":
    unittest.main()
