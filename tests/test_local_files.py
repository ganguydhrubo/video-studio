import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_studio import studio

class GeneratedVideoStream:
    def __init__(self,size):
        self.remaining=size
        self.largest_read=0
    def read(self,size):
        self.largest_read=max(self.largest_read,size)
        count=min(self.remaining,size)
        self.remaining-=count
        return b'x'*count

class LocalFiles(unittest.TestCase):
    def test_113_megabyte_file_has_no_arbitrary_upload_cap(self):
        size=113*1024*1024
        stream=GeneratedVideoStream(size)
        with tempfile.TemporaryDirectory() as folder:
            result=studio.copy_local_video(stream,size,Path(folder),'new.MOV')
            self.assertEqual(Path(result['path']).stat().st_size,size)
            self.assertEqual(result['size'],size)
            self.assertLessEqual(stream.largest_read,1024*1024)

    def test_interrupted_copy_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as folder:
            keep=Path(folder)/'original.mov';keep.write_bytes(b'original')
            with self.assertRaisesRegex(ValueError,'interrupted'):
                studio.copy_local_video(io.BytesIO(b'partial'),1000,Path(folder),'new.MOV')
            self.assertEqual(list(Path(folder).iterdir()),[keep])
            self.assertEqual(keep.read_bytes(),b'original')

    def test_native_preview_does_not_grant_access_to_other_files(self):
        with tempfile.TemporaryDirectory() as folder:
            selected=Path(folder)/'chosen.mov';selected.write_bytes(b'video')
            other=Path(folder)/'private.txt';other.write_text('keep private')
            with patch.object(studio,'workspace',return_value=Path(folder)/'projects'), \
                    patch.object(studio,'SELECTED_MEDIA',{selected.resolve()}), \
                    patch.dict(studio.JOBS,{},clear=True):
                self.assertEqual(studio._safe_path(str(selected)),selected.resolve())
                self.assertIsNone(studio._safe_path(str(other)))

if __name__=='__main__':
    unittest.main()
