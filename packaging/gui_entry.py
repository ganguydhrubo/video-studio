# PyInstaller's windowed bootloader has no standard streams. Set them before
# importing the shared CLI modules, which inspect stdout at import time.
import os
import sys

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = sys.stdout

from video_studio.launcher import main

if __name__ == "__main__":
    sys.exit(main())
