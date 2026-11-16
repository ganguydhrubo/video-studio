import hashlib
import io
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from video_studio import updates

PREFIX = updates.ASSET_PREFIX + "v1.8.0/"
SETUP = b"pretend installer bytes " * 1000


def release(version="1.8.0", prefix=PREFIX, draft=False):
    def asset(name, size=10):
        return {"name": name, "size": size, "browser_download_url": prefix + name}
    return {"tag_name": "v" + version, "draft": draft, "body": "What is new", "published_at": "2026-11-20T10:00:00Z",
            "html_url": f"https://github.com/{updates.REPO}/releases/tag/v{version}",
            "assets": [asset(f"Takebloom-Setup-{version}.exe", len(SETUP)), asset("Takebloom-Setup.exe", len(SETUP)),
                       asset(f"Takebloom-{version}-portable.zip"), asset("SHA256SUMS.txt")]}


class FakeResponse(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}


class SlowResponse(FakeResponse):
    """Hands out one small block, then waits until told to go on."""
    def __init__(self, data, go: threading.Event, started: threading.Event):
        super().__init__(data)
        self.go, self.started = go, started

    def read(self, n=-1):
        if self.tell() == 0:
            self.started.set()
            return super().read(10)
        self.go.wait(5)
        return super().read(n)


class FakeNet:
    def __init__(self, raw=None, setup=SETUP, sums=None, error=None):
        self.raw = raw if raw is not None else release()
        self.setup = setup
        digest = hashlib.sha256(SETUP).hexdigest()
        self.sums = sums if sums is not None else (
            f"{'0' * 64}  Takebloom-1.8.0-portable.zip\n{digest} *Takebloom-Setup-1.8.0.exe\n")
        self.error = error
        self.calls = []
        self.slow = None

    def __call__(self, url, timeout=10.0, accept=None):
        self.calls.append(url)
        if self.error:
            raise self.error
        if url == updates.LATEST:
            return FakeResponse(json.dumps(self.raw).encode())
        if url.endswith("SHA256SUMS.txt"):
            return FakeResponse(self.sums.encode())
        if url.endswith(".exe"):
            return self.slow or FakeResponse(self.setup)
        raise AssertionError("unexpected URL " + url)


def wait_for(states, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        state = updates.status()
        if state["state"] in states:
            return state
        time.sleep(0.01)
    return updates.status()


class UpdatesBase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="tb-updates-"))
        env = patch.dict(os.environ, {"VIDEO_STUDIO_HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(shutil.rmtree, self.home, True)
        updates._reset()
        self.addCleanup(updates._reset)
        for name, value in (("__version__", "1.7.0"), ("_windows_build", lambda: (10, 26200)),
                            ("_is_64bit", lambda: True), ("_free_bytes", lambda path: 500 * 1024 ** 3)):
            p = patch.object(updates, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.config = {}
        p = patch.object(updates.config, "get", side_effect=lambda key: self.config.get(key))
        p.start()
        self.addCleanup(p.stop)

    def use_net(self, net):
        p = patch.object(updates, "_get", net)
        p.start()
        self.addCleanup(p.stop)
        return net


class ReleaseAndVersions(UpdatesBase):
    def test_release_parsing_picks_versioned_setup_and_sums(self):
        info = updates.read_release(release())
        self.assertEqual(info["latest"], "1.8.0")
        self.assertTrue(info["available"])
        self.assertEqual(info["setup"]["name"], "Takebloom-Setup-1.8.0.exe")
        self.assertEqual(info["setup"]["size"], len(SETUP))
        self.assertTrue(info["sums"].endswith("SHA256SUMS.txt"))
        self.assertEqual(info["notes"], "What is new")
        self.assertIn("/releases/tag/v1.8.0", info["page"])

    def test_same_version_is_not_available(self):
        self.assertFalse(updates.read_release(release("1.7.0"))["available"])
        self.assertFalse(updates.read_release(release("1.6.9"))["available"])

    def test_foreign_asset_urls_are_refused(self):
        info = updates.read_release(release(prefix="https://example.com/evil/"))
        self.assertIsNone(info["setup"])
        self.assertIsNone(info["sums"])

    def test_draft_or_unnumbered_release_is_refused(self):
        with self.assertRaises(ValueError):
            updates.read_release(release(draft=True))
        with self.assertRaises(ValueError):
            updates.read_release({"tag_name": "nightly", "assets": []})

    def test_version_compare(self):
        v = updates._version
        self.assertGreater(v("v1.10.0"), v("1.9.9"))
        self.assertEqual(v("1.7"), v("1.7.0"))
        self.assertGreater(v("2.0.0"), v("1.99.99"))
        self.assertEqual(v(""), (0, 0, 0))


class Checking(UpdatesBase):
    def test_check_reads_github_and_caches_for_six_hours(self):
        net = self.use_net(FakeNet())
        first = updates.check()
        self.assertEqual(first["latest"], "1.8.0")
        self.assertTrue(first["available"])
        self.assertTrue(first["enabled"])  # missing setting counts as on
        self.assertEqual(first["error"], "")
        for key in ("current", "latest", "available", "notes", "page", "published", "setup", "sums",
                    "error", "checked", "enabled", "download"):
            self.assertIn(key, first)
        updates.check()
        self.assertEqual(len(net.calls), 1)
        updates.check(fresh=True)
        self.assertEqual(len(net.calls), 2)

    def test_offline_gives_plain_error_and_never_raises(self):
        self.use_net(FakeNet(error=urllib.error.URLError("no route")))
        info = updates.check(fresh=True)
        self.assertFalse(info["available"])
        self.assertIn("Could not reach GitHub", info["error"])

    def test_rate_limit_gives_plain_error(self):
        error = urllib.error.HTTPError(updates.LATEST, 403, "rate limited", {}, None)
        self.use_net(FakeNet(error=error))
        self.assertIn("limiting", updates.check(fresh=True)["error"])

    def test_disabled_check_sends_nothing(self):
        self.config["updates.check"] = False
        net = self.use_net(FakeNet())
        info = updates.check()
        self.assertFalse(info["enabled"])
        self.assertEqual(net.calls, [])

    def test_user_agent_names_only_the_app(self):
        seen = {}

        def fake_urlopen(request, timeout):
            seen.update(request.headers)
            return FakeResponse(json.dumps(release()).encode())
        with patch.object(updates.urllib.request, "urlopen", fake_urlopen):
            updates.check(fresh=True)
        self.assertEqual(seen.get("User-agent"), "Takebloom/1.7.0")


class Downloading(UpdatesBase):
    def test_download_verifies_and_renames(self):
        self.use_net(FakeNet())
        result = updates.download()
        self.assertIn("download", result)
        state = wait_for(("ready", "failed"))
        self.assertEqual(state["state"], "ready", state)
        self.assertEqual(state["version"], "1.8.0")
        folder = self.home / "updates"
        self.assertEqual((folder / "Takebloom-Setup-1.8.0.exe").read_bytes(), SETUP)
        self.assertEqual(list(folder.glob("*.part")), [])
        self.assertTrue((folder / "ready.json").exists())

    def test_checksum_mismatch_deletes_the_file(self):
        self.use_net(FakeNet(setup=b"tampered" + SETUP))
        updates.download()
        state = wait_for(("ready", "failed"))
        self.assertEqual(state["state"], "failed")
        self.assertIn("did not match", state["error"])
        self.assertEqual([p.name for p in (self.home / "updates").glob("Takebloom-Setup*")], [])

    def test_missing_checksum_line_fails(self):
        self.use_net(FakeNet(sums=f"{'a' * 64}  Something-else.exe\n"))
        updates.download()
        state = wait_for(("ready", "failed"))
        self.assertEqual(state["state"], "failed")
        self.assertIn("no checksum", state["error"])

    def test_cancel_stops_and_deletes_part(self):
        net = self.use_net(FakeNet())
        go, started = threading.Event(), threading.Event()
        net.slow = SlowResponse(SETUP, go, started)
        updates.download()
        self.assertTrue(started.wait(5))
        self.assertEqual(updates.status()["state"], "downloading")
        self.assertEqual(updates.cancel()["state"], "idle")
        go.set()
        time.sleep(0.2)
        self.assertEqual(updates.status()["state"], "idle")
        self.assertEqual(list((self.home / "updates").glob("Takebloom-Setup*")), [])

    def test_no_download_when_up_to_date(self):
        self.use_net(FakeNet(raw=release("1.7.0")))
        self.assertIn("already the latest", updates.download()["error"])

    def test_ready_download_is_remembered_after_restart(self):
        folder = self.home / "updates"
        folder.mkdir(parents=True)
        (folder / "Takebloom-Setup-1.8.0.exe").write_bytes(SETUP)
        (folder / "ready.json").write_text(json.dumps({"name": "Takebloom-Setup-1.8.0.exe", "version": "1.8.0",
                                                       "sha256": hashlib.sha256(SETUP).hexdigest()}))
        state = updates.status()
        self.assertEqual((state["state"], state["version"]), ("ready", "1.8.0"))

    def test_installed_download_is_tidied_after_restart(self):
        folder = self.home / "updates"
        folder.mkdir(parents=True)
        (folder / "Takebloom-Setup-1.7.0.exe").write_bytes(SETUP)
        (folder / "ready.json").write_text(json.dumps({"name": "Takebloom-Setup-1.7.0.exe", "version": "1.7.0"}))
        self.assertEqual(updates.status()["state"], "idle")
        self.assertFalse((folder / "Takebloom-Setup-1.7.0.exe").exists())


class Installing(UpdatesBase):
    def ready(self, sha=None):
        folder = self.home / "updates"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "Takebloom-Setup-1.8.0.exe"
        path.write_bytes(SETUP)
        updates._download.update(state="ready", path=str(path), version="1.8.0",
                                 sha256=sha or hashlib.sha256(SETUP).hexdigest())
        updates._restored = True
        return path

    def test_refused_while_jobs_run(self):
        self.ready()
        with patch.object(updates, "FROZEN", True):
            self.assertIn("still running", updates.install(1)["error"])

    def test_refused_when_not_frozen(self):
        self.ready()
        with patch.object(updates, "FROZEN", False):
            self.assertIn("source code", updates.install(0)["error"])

    def test_refused_when_not_downloaded(self):
        with patch.object(updates, "FROZEN", True):
            self.assertEqual(updates.install(0)["error"], "Download the update first.")

    def test_refused_and_deleted_when_hash_changed(self):
        path = self.ready(sha="0" * 64)
        with patch.object(updates, "FROZEN", True), patch.object(updates, "_start_detached") as start:
            result = updates.install(0)
        self.assertIn("changed on disk", result["error"])
        self.assertFalse(path.exists())
        start.assert_not_called()
        self.assertEqual(updates.status()["state"], "failed")

    def test_refused_when_not_compatible(self):
        self.ready()
        with patch.object(updates, "FROZEN", True), patch.object(updates, "_windows_build", lambda: (6, 9600)), \
                patch.object(updates, "_start_detached") as start:
            result = updates.install(0)
        self.assertIn("Windows 10", result["error"])
        start.assert_not_called()

    def test_starts_setup_detached(self):
        path = self.ready()
        with patch.object(updates, "FROZEN", True), patch.object(updates, "_start_detached") as start:
            result = updates.install(0)
        self.assertTrue(result["ok"])
        start.assert_called_once_with([str(path)], path.parent)


class Compatibility(UpdatesBase):
    INFO = {"latest": "1.8.0", "setup": {"name": "Takebloom-Setup-1.8.0.exe", "size": 200 * 1024 ** 2}}

    def test_fine_computer_passes(self):
        self.assertEqual(updates.compatibility(self.INFO), {"ok": True, "problems": []})

    def test_each_problem_is_reported(self):
        with patch.object(updates, "_windows_build", lambda: (10, 17134)), \
                patch.object(updates, "_is_64bit", lambda: False), \
                patch.object(updates, "_free_bytes", lambda path: 900 * 1024 ** 2):
            result = updates.compatibility(self.INFO)
        self.assertFalse(result["ok"])
        text = " ".join(result["problems"])
        self.assertIn("Windows 10", text)
        self.assertIn("64-bit", text)
        self.assertIn("not enough free space", text)
        self.assertIn("1.1 GB", text)  # 3 x 200 MB + 500 MB

    def test_missing_setup_and_not_newer(self):
        result = updates.compatibility({"latest": "1.7.0", "setup": None})
        self.assertEqual(len(result["problems"]), 2)
        self.assertIn("no Windows installer", result["problems"][0])
        self.assertIn("not newer", result["problems"][1])


class RollingBack(UpdatesBase):
    def make_install(self):
        programs = self.home / "Programs"
        install = programs / "VideoStudio"
        install.mkdir(parents=True)
        for name, version in (("VideoStudio-previous", "1.5.0"), ("VideoStudio-previous-abc123", "1.6.0"),
                              ("VideoStudio-previous-empty", None)):
            folder = programs / name
            folder.mkdir()
            if version:
                (folder / "video-studio.exe").write_text(version)
                (folder / "VideoStudio.exe").write_text("gui")
        unknown = programs / "VideoStudio-previous-old"
        unknown.mkdir()
        (unknown / "VideoStudio.exe").write_text("gui")
        (programs / "VideoStudio-rolledback-20261101-101010").mkdir()
        (programs / "VideoStudio-stage-xyz").mkdir()
        p = patch.object(updates, "install_dir", lambda: install)
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(updates, "_read_version", lambda exe: exe.read_text())
        p.start()
        self.addCleanup(p.stop)
        return install

    def test_previous_versions_lists_backups_newest_first(self):
        self.make_install()
        found = updates.previous_versions()
        self.assertEqual([e["folder"] for e in found],
                         ["VideoStudio-previous-abc123", "VideoStudio-previous", "VideoStudio-previous-old"])
        self.assertTrue(found[0]["restore"])
        self.assertEqual(found[0]["label"], "Takebloom 1.6.0")
        self.assertFalse(any(e["restore"] for e in found[1:]))
        self.assertTrue(found[2]["label"].startswith("The copy saved on "))
        self.assertEqual(found[2]["version"], "")

    def test_previous_versions_empty_from_source(self):
        with patch.object(updates, "FROZEN", False):
            self.assertEqual(updates.previous_versions(), [])

    def test_refusals(self):
        with patch.object(updates, "FROZEN", True):
            self.assertIn("still running", updates.rollback(2)["error"])
        with patch.object(updates, "FROZEN", False):
            self.assertIn("source code", updates.rollback(0)["error"])
        with patch.object(updates, "FROZEN", True), patch.object(updates, "previous_versions", lambda: []), \
                patch.object(updates, "install_dir", lambda: self.home / "Programs" / "VideoStudio"):
            self.assertIn("no earlier version", updates.rollback(0)["error"])

    def test_rollback_writes_script_with_right_folders(self):
        install = self.make_install()
        with patch.object(updates, "FROZEN", True), patch.object(updates, "_start_detached") as start:
            result = updates.rollback(0)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["version"], "1.6.0")
        script = self.home / "updates" / "rollback.ps1"
        text = script.read_text(encoding="utf-8-sig")
        self.assertIn(f"$install = '{install}'", text)
        self.assertIn(f"$previous = '{install.parent / 'VideoStudio-previous-abc123'}'", text)
        self.assertIn(f"$aside = '{install.parent}{os.sep}VideoStudio-rolledback-", text)
        self.assertIn("AddSeconds(60)", text)
        self.assertIn("Stop-Process", text)
        self.assertIn("VideoStudio.exe", text)
        self.assertIn("put back", text)
        args, cwd = start.call_args[0]
        self.assertEqual(args[0], "powershell.exe")
        self.assertEqual(args[-1], str(script))
        self.assertIn("1.7.0", updates._skipped())  # the version left behind is not downloaded again by itself

    def test_script_quotes_apostrophes(self):
        text = updates._rollback_script(Path("C:/Users/O'Neil/VideoStudio"), Path("C:/p"), Path("C:/a"), Path("C:/l"))
        self.assertIn("O''Neil", text)


class Background(UpdatesBase):
    def test_startup_starts_only_one_thread(self):
        calls, release_loop = [], threading.Event()

        def fake_loop(fn):
            calls.append(fn)
            release_loop.wait(5)
        self.addCleanup(release_loop.set)
        with patch.object(updates, "_auto_loop", fake_loop):
            starters = [threading.Thread(target=updates.startup, args=(lambda: 0,)) for _ in range(8)]
            for t in starters:
                t.start()
            for t in starters:
                t.join()
            updates.startup(lambda: 0)
            time.sleep(0.1)
        self.assertEqual(len(calls), 1)

    def test_auto_round_downloads_but_never_installs(self):
        self.use_net(FakeNet())
        with patch.object(updates, "download") as download, patch.object(updates, "install") as install:
            self.assertEqual(updates._auto_once(lambda: 0), updates.CHECK_EVERY)
        download.assert_called_once()
        install.assert_not_called()

    def test_auto_round_waits_while_edits_run(self):
        self.use_net(FakeNet())
        with patch.object(updates, "download") as download:
            self.assertEqual(updates._auto_once(lambda: 1), updates.BUSY_RETRY)
        download.assert_not_called()

    def test_auto_round_respects_setting_and_skips(self):
        net = self.use_net(FakeNet())
        self.config["updates.check"] = False
        with patch.object(updates, "download") as download:
            updates._auto_once(lambda: 0)
            self.assertEqual(net.calls, [])
            self.config["updates.check"] = True
            updates._set_skipped({"1.8.0"})
            updates._auto_once(lambda: 0)
        download.assert_not_called()

    def test_set_auto_saves_setting(self):
        with patch.object(updates.config, "set_value") as set_value:
            self.assertEqual(updates.set_auto(False), {"ok": True, "enabled": False})
        set_value.assert_called_once_with("updates.check", False)
        with patch.object(updates.config, "set_value", side_effect=KeyError("updates.check")):
            self.assertIn("error", updates.set_auto(True))

    def test_notice_text(self):
        self.assertEqual(updates.notice(), {"ready": False, "version": "", "text": ""})
        updates._download.update(state="ready", version="1.8.0")
        updates._restored = True
        self.assertEqual(updates.notice(), {
            "ready": True, "version": "1.8.0",
            "text": "Takebloom 1.8.0 is downloaded and checked. Install it when no edit is running."})


if __name__ == "__main__":
    unittest.main()
