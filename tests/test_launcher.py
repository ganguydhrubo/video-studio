"""Desktop launch regression tests; no browser, installer or paid AI calls."""
import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from video_studio import launcher


class DesktopLaunch(unittest.TestCase):
    def test_no_arguments_opens_studio(self):
        from video_studio import cli
        with patch.object(cli, "cmd_studio", return_value=0) as studio:
            self.assertEqual(cli.main([]), 0)
            self.assertEqual(studio.call_args.args[0].port, 7860)

    def test_reuses_running_studio_without_spawning(self):
        with patch.object(launcher, "health", side_effect=lambda port: port == 7862), \
                patch.object(launcher.subprocess, "Popen") as spawn, \
                patch.object(launcher, "open_window") as browser:
            self.assertEqual(launcher.launch(), "http://127.0.0.1:7862/")
            spawn.assert_not_called()
            browser.assert_called_once_with("http://127.0.0.1:7862/")

    def test_unrelated_local_server_is_not_reused(self):
        response = Mock()
        response.__enter__ = Mock(return_value=io.BytesIO(b'{"app":"other-app"}'))
        response.__exit__ = Mock(return_value=False)
        with patch.object(launcher.urllib.request, "build_opener") as opener:
            opener.return_value.open.return_value = response
            self.assertFalse(launcher.health(7860))

    def test_failed_server_points_to_persistent_log(self):
        with TemporaryDirectory() as directory, \
                patch("video_studio.util.app_data_dir", return_value=Path(directory)), \
                patch.object(launcher, "health", return_value=False), \
                patch.object(launcher.subprocess, "Popen") as spawn:
            spawn.return_value.poll.return_value = 1
            spawn.return_value.returncode = 1
            with self.assertRaisesRegex(RuntimeError, "startup.log"):
                launcher.launch(open_browser=False)
            self.assertTrue((Path(directory) / "logs" / "startup.log").is_file())


if __name__ == "__main__":
    unittest.main()
