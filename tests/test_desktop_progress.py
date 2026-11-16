import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from video_studio import launcher, studio

class DesktopProgress(unittest.TestCase):
    def test_footage_analysis_updates_the_current_stage(self):
        with tempfile.TemporaryDirectory() as folder:
            job=studio.Job('edit',[],Path(folder),'Test')
            job._line('> Watching the footage (shots, light, motion, people, music)')
            self.assertEqual(job.stage,'sense')

    def test_elapsed_time_stops_when_job_finishes(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(studio.time,'time',return_value=100):
                job=studio.Job('edit',[],Path(folder),'Test')
            job.proc=Mock(stdout=io.StringIO('last line without a newline'))
            job.proc.wait.return_value=0
            with patch.object(studio.time,'time',return_value=110):
                job._pump()
            with patch.object(studio.time,'time',return_value=500):
                self.assertEqual(job.info()['elapsed'],10)
            self.assertIn('last line without a newline',job.lines)

    def test_native_picker_returns_original_unicode_path(self):
        with tempfile.TemporaryDirectory() as folder:
            video=Path(folder)/'আমার ভিডিও.mp4'
            video.write_bytes(b'test')
            result=Mock(returncode=0,stdout=json.dumps([str(video)]))
            with patch.object(studio.subprocess,'run',return_value=result):
                picked=studio.pick_local_files()
            self.assertEqual(picked['files'][0]['path'],str(video))
            self.assertEqual(picked['files'][0]['size'],4)

    def test_app_window_uses_installed_edge(self):
        with patch.object(Path,'is_file',return_value=True), patch.object(launcher.subprocess,'Popen') as spawn:
            launcher.open_window('http://127.0.0.1:7860/')
        self.assertIn('--app=http://127.0.0.1:7860/',spawn.call_args.args[0])

if __name__=='__main__':
    unittest.main()
