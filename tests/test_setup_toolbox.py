"""Portable one-click setup (1.9.0): downloads are mocked, so these run offline in about a second."""

import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from video_studio import detect, setup, toolbox


class FakeResp(io.BytesIO):
    def __init__(self, data: bytes, status: int = 200):
        super().__init__(data)
        self.status = status
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Server:
    """Serves one payload; honours Range like a real server unless told not to."""

    def __init__(self, payload: bytes, ranges: bool = True, fail_first: int = 0):
        self.payload, self.ranges, self.fail_first = payload, ranges, fail_first
        self.requests = []

    def __call__(self, req, timeout=None):
        self.requests.append(dict(req.header_items()))
        if self.fail_first:
            self.fail_first -= 1
            raise OSError("connection reset")
        rng = req.get_header("Range")
        if rng and self.ranges:
            start = int(rng.split("=")[1].rstrip("-"))
            return FakeResp(self.payload[start:], 206)
        return FakeResp(self.payload, 200)


def make_zip(files: dict, top: str = "") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr((top + "/" if top else "") + name, data)
    return buf.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tb-setup-test-"))
        self.env = patch.dict(os.environ, {"VIDEO_STUDIO_HOME": str(self.tmp / "home"),
                                           "VIDEO_STUDIO_TOOLS": str(self.tmp / "tools")})
        self.env.start()
        self.sleep = patch.object(toolbox.time, "sleep", lambda s: None)
        self.sleep.start()

    def tearDown(self):
        self.sleep.stop()
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def pkg(self, key: str, payload: bytes, exe: str) -> toolbox.Package:
        return toolbox.Package(key, f"Test {key}", "1.0", (f"https://example.test/{key}.zip",),
                               hashlib.sha256(payload).hexdigest(), len(payload), "MIT", exe, "", "Test.Winget")


class Downloads(Base):
    def test_checksum_mismatch_is_refused_and_deleted(self):
        data = b"x" * 5000
        dest = self.tmp / "dl" / "file.zip"
        with patch.object(toolbox, "urlopen", Server(data)):
            with self.assertRaises(toolbox.ToolboxError) as ctx:
                toolbox.download(["https://example.test/a"], dest, "0" * 64, len(data), "Test tool")
        self.assertIn("SHA-256", str(ctx.exception))
        self.assertFalse(dest.exists())
        self.assertFalse(dest.with_name("file.zip.part").exists())

    def test_resume_continues_from_part_file(self):
        data = os.urandom(300_000)
        dest = self.tmp / "dl" / "file.zip"
        dest.parent.mkdir(parents=True)
        dest.with_name("file.zip.part").write_bytes(data[:120_000])
        server = Server(data)
        with patch.object(toolbox, "urlopen", server):
            toolbox.download(["https://example.test/a"], dest, hashlib.sha256(data).hexdigest(), len(data))
        self.assertEqual(dest.read_bytes(), data)
        self.assertEqual(server.requests[0].get("Range"), "bytes=120000-")

    def test_server_without_range_restarts_cleanly(self):
        data = os.urandom(50_000)
        dest = self.tmp / "dl" / "file.zip"
        dest.parent.mkdir(parents=True)
        dest.with_name("file.zip.part").write_bytes(data[:10_000])
        with patch.object(toolbox, "urlopen", Server(data, ranges=False)):
            toolbox.download(["https://example.test/a"], dest, hashlib.sha256(data).hexdigest(), len(data))
        self.assertEqual(dest.read_bytes(), data)

    def test_retry_after_dropped_connection_then_mirror(self):
        data = b"hello" * 100
        server = Server(data, fail_first=3)  # first mirror fails three times, mirror two works
        dest = self.tmp / "dl" / "f.bin"
        with patch.object(toolbox, "urlopen", server):
            toolbox.download(["https://a.test/f", "https://b.test/f"], dest, hashlib.sha256(data).hexdigest(), len(data))
        self.assertEqual(dest.read_bytes(), data)
        self.assertEqual(len(server.requests), 4)

    def test_offline_gives_plain_english_error(self):
        server = Server(b"", fail_first=99)
        with patch.object(toolbox, "urlopen", server):
            with self.assertRaises(toolbox.ToolboxError) as ctx:
                toolbox.download(["https://a.test/f"], self.tmp / "f", "", 10, "FFmpeg")
        self.assertIn("Could not download FFmpeg", str(ctx.exception))
        self.assertIn("continues", ctx.exception.hint)


class Install(Base):
    def test_unpack_is_atomic_and_stamped_then_skipped(self):
        payload = make_zip({"bin/ffmpeg.exe": b"MZ", "bin/ffprobe.exe": b"MZ"}, top="ffmpeg-9.0.2-essentials_build")
        pkg = self.pkg("ffmpeg", payload, "bin/ffmpeg.exe")
        server = Server(payload)
        with patch.dict(toolbox.PACKAGES, {"ffmpeg": pkg}), patch.object(toolbox, "urlopen", server):
            exe = toolbox.install_package("ffmpeg")
            self.assertEqual(exe, toolbox.tool_dir("ffmpeg") / "bin" / "ffmpeg.exe")
            self.assertTrue((toolbox.tool_dir("ffmpeg") / ".takebloom.json").is_file())
            self.assertFalse(any(p.name.startswith(".ffmpeg-") for p in toolbox.tools_dir().iterdir()))
            self.assertFalse((toolbox.tools_dir() / "_downloads" / pkg.filename).exists())  # zip removed after unpack
            toolbox.install_package("ffmpeg")  # second run: already here, nothing downloaded
        self.assertEqual(len(server.requests), 1)

    def test_half_unpacked_tool_is_not_used(self):
        d = toolbox.tool_dir("node")
        d.mkdir(parents=True)
        (d / "node.exe").write_bytes(b"MZ")  # no stamp: an interrupted install
        self.assertIsNone(toolbox.node())

    def test_unsafe_zip_path_refused(self):
        payload = make_zip({"../evil.exe": b"x", "node.exe": b"MZ"})
        pkg = self.pkg("node", payload, "node.exe")
        with patch.dict(toolbox.PACKAGES, {"node": pkg}), patch.object(toolbox, "urlopen", Server(payload)):
            with self.assertRaises(toolbox.ToolboxError):
                toolbox.install_package("node")
        self.assertFalse((self.tmp / "evil.exe").exists())

    def test_winget_fallback_always_passes_agreement_flags(self):
        cmd = toolbox.winget_cmd("Gyan.FFmpeg")
        for flag in ("--accept-source-agreements", "--accept-package-agreements", "--silent", "--disable-interactivity"):
            self.assertIn(flag, cmd)
        for key, recipe in detect.INSTALL_RECIPES.items():
            if recipe["cmd"][:1] == ["winget"]:
                self.assertIn("--accept-package-agreements", recipe["cmd"], key)
                self.assertIn("--disable-interactivity", recipe["cmd"], key)

    def test_failed_download_falls_back_to_winget(self):
        calls = []
        with patch.object(toolbox, "install_package", side_effect=toolbox.ToolboxError("offline")), \
                patch.object(toolbox, "winget_path", return_value=Path("C:/winget.exe")), \
                patch.object(toolbox, "stream", side_effect=lambda cmd, **k: calls.append([str(c) for c in cmd]) or 0), \
                patch.object(toolbox, "refresh_path_from_registry") as refresh:
            toolbox.install_with_fallback("ffmpeg")
        self.assertEqual(calls[0][1:4], ["install", "--id", "Gyan.FFmpeg"])
        self.assertIn("--accept-source-agreements", calls[0])
        refresh.assert_called_once()

    def test_no_winget_is_explained(self):
        with patch.object(toolbox, "winget_path", return_value=None):
            self.assertFalse(toolbox.winget_install("Gyan.FFmpeg"))


class Detection(Base):
    def _stamp(self, key, exe):
        d = toolbox.tool_dir(key)
        (d / exe).parent.mkdir(parents=True, exist_ok=True)
        (d / exe).write_bytes(b"MZ")
        (d / ".takebloom.json").write_text("{}")

    def test_private_python_preferred_only_when_ready(self):
        vpy = toolbox.venv_dir() / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        vpy.parent.mkdir(parents=True)
        vpy.write_bytes(b"MZ")
        self.assertNotEqual(detect.system_python(), vpy)  # half-made venv is ignored
        (toolbox.venv_dir() / ".takebloom-ready").write_text("{}")
        self.assertEqual(detect.system_python(), vpy)
        with patch.object(detect, "FROZEN", True):
            self.assertEqual(detect.system_python(), vpy)

    def test_portable_tools_found_first_and_labelled(self):
        self._stamp("ffmpeg", "bin/ffmpeg.exe")
        self._stamp("node", "node.exe")
        self._stamp("blender", "blender.exe")
        with patch.object(detect.config, "get", return_value=None):
            self.assertEqual(detect.ffmpeg_path(), toolbox.tool_dir("ffmpeg") / "bin" / "ffmpeg.exe")
            self.assertEqual(detect.node_path(), toolbox.tool_dir("node") / "node.exe")
            self.assertEqual(detect.blender_path(), toolbox.tool_dir("blender") / "blender.exe")
            self.assertEqual(detect.source(detect.node_path()), "portable")
            self.assertEqual(detect.source(sys.executable), "system")

    def test_activate_is_process_only(self):
        self._stamp("node", "node.exe")
        with patch.dict(os.environ, {"PATH": "C:\\Windows\\System32"}):
            os.environ.pop("npm_config_cache", None)
            os.environ.pop("HYPERFRAMES_CACHE_DIR", None)
            toolbox.activate(force=True)
            self.assertTrue(os.environ["PATH"].startswith(str(toolbox.tool_dir("node"))))
            self.assertEqual(os.environ["npm_config_cache"], str(toolbox.npm_cache()))
            self.assertIn("hyperframes", os.environ["HYPERFRAMES_CACHE_DIR"])

    def test_ollama_provider_finds_portable_copy(self):
        from video_studio import ai

        self._stamp("ollama", "ollama.exe")
        self.assertEqual(ai.Ollama().path(), toolbox.tool_dir("ollama") / "ollama.exe")


class Plan(Base):
    EMPTY = {"tools": {}, "python": "", "packages": {}, "models": {}, "ram_gb": 16}

    def test_fresh_pc_plan_has_every_core_item_with_size_and_licence(self):
        with patch.object(setup, "whisper_cached", return_value=False):
            items = setup.plan(self.EMPTY)
        keys = [i["key"] for i in items]
        for key in ("ffmpeg", "python", "node", "hyperframes", "blender", "whisper-model"):
            self.assertIn(key, keys)
        self.assertNotIn("ollama", keys)  # optional, off by default
        self.assertLess(keys.index("node"), keys.index("hyperframes"))
        for it in items:
            self.assertGreater(it["size"], 0, it["key"])
            self.assertTrue(it["license"], it["key"])
        summary = setup.plan_summary(items)
        self.assertEqual(summary["total"], sum(i["size"] for i in items))

    def test_skip_and_optional_extra(self):
        with patch.object(setup, "whisper_cached", return_value=True), patch.object(setup.shutil, "which", return_value=None):
            keys = [i["key"] for i in setup.plan(self.EMPTY, skip=("blender",), extra=("ollama",))]
        self.assertNotIn("blender", keys)
        self.assertIn("ollama", keys)

    def test_ready_pc_needs_nothing(self):
        ok = {"ok": True}
        s = {"tools": {k: ok for k in ("ffmpeg", "node", "hyperframes", "blender")}, "python": sys.executable,
             "packages": {m: True for m in setup.MODULES}, "models": {m: True for m in setup.MODELS}, "ram_gb": 16}
        with patch.object(setup, "whisper_cached", return_value=True):
            self.assertEqual(setup.plan(s), [])

    def test_one_confirmation_and_failures_do_not_stop_the_rest(self):
        items = [{"kind": "portable", "key": "ffmpeg", "desc": "A", "size": 1, "license": "x"},
                 {"kind": "hyperframes", "key": "hyperframes", "desc": "B", "size": 1, "license": "y"}]
        done = []
        with patch.object(setup, "confirm", return_value=True) as conf, \
                patch.object(setup, "install_item", side_effect=lambda it: done.append(it["key"]) or it["key"] != "ffmpeg"):
            self.assertFalse(setup.install_all(items, False))
        conf.assert_called_once()
        self.assertEqual(done, ["ffmpeg", "hyperframes"])

    def test_declined_installs_nothing(self):
        with patch.object(setup, "confirm", return_value=False), patch.object(setup, "install_item") as inst:
            self.assertFalse(setup.install_all([{"kind": "model", "key": "m", "desc": "M", "size": 1}], False))
        inst.assert_not_called()

    def test_install_item_never_raises(self):
        with patch.object(toolbox, "install_hyperframes", side_effect=RuntimeError("boom")):
            self.assertFalse(setup.install_item({"kind": "hyperframes", "key": "hyperframes", "desc": "HF"}))


if __name__ == "__main__":
    unittest.main()
