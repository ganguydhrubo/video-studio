import threading
import time
import unittest
from unittest.mock import patch

from video_studio import studio


class SystemChecks(unittest.TestCase):
    def setUp(self):
        with studio.CHECK_LOCK:
            studio.CHECKS.clear()

    def test_slow_check_returns_immediately_and_is_shared(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def slow():
            calls.append(1)
            entered.set()
            release.wait(2)
            return {"value": 42}
        try:
            started = time.monotonic()
            self.assertTrue(studio.background_check("slow", slow)["checking"])
            self.assertLess(time.monotonic() - started, .2)
            self.assertTrue(entered.wait(1))
            self.assertTrue(studio.background_check("slow", slow)["checking"])
            self.assertEqual(len(calls), 1)
        finally:
            release.set()
        for _ in range(100):
            result = studio.background_check("slow", slow)
            if not result["checking"]:
                break
            time.sleep(.01)
        self.assertEqual(result["value"], 42)
        self.assertEqual(len(calls), 1)

    def test_probe_error_is_visible(self):
        def fail():
            raise RuntimeError("cannot inspect tool")
        studio.background_check("bad", fail)
        for _ in range(100):
            result = studio.background_check("bad", fail)
            if not result["checking"]:
                break
            time.sleep(.01)
        self.assertEqual(result["error"], "cannot inspect tool")

    def test_doctor_does_not_probe_ai_again(self):
        from video_studio import detect
        info = detect.ToolInfo("test", "Test", "test", False)
        with patch.multiple(detect, **{name: lambda: info for name in
                ("check_ffmpeg", "check_blender", "check_node", "check_hyperframes", "check_tts", "check_whisper")}), \
                patch("video_studio.ai.providers", side_effect=AssertionError("duplicate AI scan")), \
                patch("video_studio.engines.ml.kokoro_ready", return_value=True):
            self.assertEqual(len(studio._doctor()["tools"]), 6)


if __name__ == "__main__":
    unittest.main()
