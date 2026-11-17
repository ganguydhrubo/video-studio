"""One-click setup 1.9.0: what the default plan holds, optional extras, speech model checks, fresh-PC test switch.

Everything here is offline: no download, no tool is started."""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_studio import detect, setup, toolbox
from video_studio.engines import ml

EMPTY = {"tools": {}, "python": "", "packages": {}, "models": {}, "ram_gb": 16}


class Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tb-oneclick-test-"))
        self.env = patch.dict(os.environ, {"VIDEO_STUDIO_HOME": str(self.tmp / "home"),
                                           "VIDEO_STUDIO_TOOLS": str(self.tmp / "tools"),
                                           "VIDEO_STUDIO_MODELS": str(self.tmp / "models"),
                                           "HF_HOME": str(self.tmp / "hf")})
        self.env.start()
        os.environ.pop("HF_HUB_CACHE", None)

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def whisper(self, folder: str, complete: bool = True) -> None:
        snap = self.tmp / "hf" / "hub" / folder / "snapshots" / "abc"
        snap.mkdir(parents=True)
        (snap / "config.json").write_text("{}")
        if complete:
            (snap / "model.bin").write_bytes(b"0")


class DefaultPlan(Temp):
    def keys(self, extra=()):
        with patch.object(setup.shutil, "which", return_value=None):
            return [i["key"] for i in setup.plan(EMPTY, extra=extra)]

    def test_default_plan_is_the_editing_engine_only(self):
        keys = self.keys()
        self.assertEqual(keys[0], "ffmpeg")
        for key in ("python", "node", "hyperframes", "whisper-model", "selfie_multiclass.tflite"):
            self.assertIn(key, keys)
        for key in ("blender", "ollama"):
            self.assertNotIn(key, keys)

    def test_no_video_generation_in_any_plan(self):
        """Takebloom edits the person's own footage: no PyTorch, CUDA or generation model is ever set up."""
        with patch.object(setup.shutil, "which", return_value=None):
            items = setup.plan(EMPTY, extra=setup.EXTRAS)
        text = repr(items).lower() + " ".join(setup.PIP_PACKAGES).lower()
        for word in ("torch", "cuda", "wan2", "diffusers", "text-to-video"):
            self.assertNotIn(word, text)

    def test_default_speech_model_is_small_and_large_is_optional(self):
        with patch.object(setup.shutil, "which", return_value=None):
            small = next(i for i in setup.plan(EMPTY) if i["key"] == "whisper-model")
            large = next(i for i in setup.plan(EMPTY, extra=("speech-large",)) if i["key"] == "whisper-model")
        self.assertEqual(small["model"], "small")
        self.assertLess(small["size"], 500_000_000)
        self.assertEqual(large["model"], "large-v3-turbo")
        self.assertGreater(large["size"], 1_500_000_000)

    def test_optional_extras_are_added_with_size_and_licence(self):
        with patch.object(setup.shutil, "which", return_value=None):
            items = {i["key"]: i for i in setup.plan(EMPTY, extra=("blender", "ollama"))}
        self.assertEqual(items["blender"]["license"], "GPL-3.0-or-later")
        self.assertGreater(items["ollama"]["size"], 1_000_000_000)

    def test_every_item_shows_size_and_licence_and_total(self):
        with patch.object(setup.shutil, "which", return_value=None):
            items = setup.plan(EMPTY)
        for it in items:
            self.assertGreater(it["size"], 0, it["key"])
            self.assertTrue(it["license"], it["key"])
        summary = setup.plan_summary(items)
        self.assertEqual(summary["total"], sum(i["size"] for i in items))
        self.assertTrue(summary["total_text"].endswith(("MB", "GB")))

    def test_ready_pc_with_small_speech_model_needs_nothing(self):
        self.whisper("models--Systran--faster-whisper-small")
        ok = {"ok": True}
        s = {"tools": {k: ok for k in ("ffmpeg", "node", "hyperframes")}, "python": sys.executable,
             "packages": {m: True for m in setup.MODULES}, "models": {m: True for m in setup.MODELS}, "ram_gb": 16}
        self.assertEqual(setup.plan(s), [])
        self.assertEqual([i["key"] for i in setup.plan(s, extra=("speech-large",))], ["whisper-model"])

    def test_kokoro_downloads_are_size_checked(self):
        it = next(i for i in setup.plan(EMPTY) if i["key"] == "voices-v1.0.bin")
        self.assertEqual(it["size"], setup.MODEL_BYTES["voices-v1.0.bin"])
        with patch.object(toolbox, "download") as dl:
            self.assertTrue(setup.install_item(it))
        self.assertEqual(dl.call_args.args[3], setup.MODEL_BYTES["voices-v1.0.bin"])


class SpeechModel(Temp):
    def test_half_downloaded_model_does_not_count(self):
        self.whisper("models--Systran--faster-whisper-small", complete=False)
        self.assertFalse(setup.whisper_cached())
        self.assertEqual(ml.best_whisper_model(), "small")  # the default, which the edit then downloads

    def test_hf_home_is_honoured(self):
        self.whisper("models--mobiuslabsgmbh--faster-whisper-large-v3-turbo")
        self.assertEqual(ml.hf_hub(), self.tmp / "hf" / "hub")
        self.assertTrue(setup.whisper_cached(large=True))
        self.assertEqual(ml.best_whisper_model(), "large-v3-turbo")

    def test_small_is_not_enough_when_large_is_asked_for(self):
        self.whisper("models--Systran--faster-whisper-small")
        self.assertTrue(setup.whisper_cached())
        self.assertFalse(setup.whisper_cached(large=True))

    def test_report_names_the_model_on_disk_and_never_promises_generation(self):
        s = {**EMPTY, "threads": 16, "free_disk_gb": 100, "nvidia": {"name": "RTX", "vram_gb": 24}, "gpus": [],
             "tools": {"ffmpeg": {"ok": True}}, "packages": {"faster_whisper": True}, "speech_model": "small"}
        r = setup.report(s)
        text = " ".join(r["can"] + r["limits"] + r["tips"]).lower()
        self.assertIn("'small' speech model", text)
        self.assertIn("speech-large", text)
        self.assertNotIn("text-to-video", text)
        self.assertNotIn("generated video", text)


class PortableOnly(Temp):
    """VIDEO_STUDIO_PORTABLE_ONLY=1 makes this PC look empty apart from Takebloom's own tools folder."""

    def test_system_tools_are_hidden(self):
        with patch.object(detect, "PORTABLE_ONLY", True), \
                patch.object(detect, "which", return_value=Path("C:/Windows/system-copy.exe")), \
                patch.object(detect, "_blender_candidates", return_value=[Path("C:/b/blender.exe")]):
            self.assertIsNone(detect.ffmpeg_path())
            self.assertIsNone(detect.ffprobe_path())
            self.assertIsNone(detect.node_path())
            self.assertIsNone(detect.npx_path())
            self.assertIsNone(detect.blender_path())
            self.assertIsNone(detect.system_python())

    def test_portable_copies_are_still_found(self):
        d = toolbox.tool_dir("ffmpeg") / "bin"
        d.mkdir(parents=True)
        (d / "ffmpeg.exe").write_bytes(b"")
        (d / "ffprobe.exe").write_bytes(b"")
        (toolbox.tool_dir("ffmpeg") / ".takebloom.json").write_text("{}")
        with patch.object(detect, "PORTABLE_ONLY", True):
            self.assertEqual(detect.ffmpeg_path(), d / "ffmpeg.exe")
            self.assertEqual(detect.ffprobe_path(), d / "ffprobe.exe")

    def test_switch_is_read_from_the_environment(self):
        with patch.dict(os.environ, {"VIDEO_STUDIO_PORTABLE_ONLY": "1"}):
            self.assertIsNone(toolbox._base_python_for_fallback())
        self.assertFalse(detect.PORTABLE_ONLY)  # off unless asked for

    def test_cli_accepts_the_optional_extras(self):
        from video_studio import cli

        a = cli.build_parser().parse_args(["setup", "--with", "blender", "speech-large", "--skip", "hyperframes"])
        self.assertEqual(a.extra, ["blender", "speech-large"])
        self.assertEqual(a.skip, ["hyperframes"])
        with self.assertRaises(SystemExit), patch("sys.stderr"):
            cli.build_parser().parse_args(["setup", "--with", "torch"])


if __name__ == "__main__":
    unittest.main()
