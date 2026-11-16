"""`video-studio watch <folder> "<brief>"`: drop raw clips in a folder, get finished drafts in <folder>/edited/.

A file is picked up once its size has stopped changing (copy finished). Each clip is edited once (a .done marker is
written); failures are logged and skipped, never retried in a loop. Stop with Ctrl+C.
"""

from __future__ import annotations

import time
from pathlib import Path

from video_studio.util import console

MEDIA = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm", ".mts"}


def run(folder: Path, brief: str, passthrough: list[str], interval: float = 10.0, once: bool = False) -> int:
    from video_studio import cli

    folder = folder.resolve()
    if not folder.is_dir():
        console.error(f"Not a folder: {folder}")
        return 1
    out = folder / "edited"
    out.mkdir(exist_ok=True)
    console.ok(f"Watching {folder} - drop clips here; drafts appear in {out}. Ctrl+C to stop.")
    sizes: dict[Path, int] = {}
    try:
        while True:
            for f in sorted(folder.iterdir()):
                if f.suffix.lower() not in MEDIA or not f.is_file():
                    continue
                marker = out / f"{f.stem}.done"
                failed = out / f"{f.stem}.failed"
                if marker.exists() or failed.exists():
                    continue
                size = f.stat().st_size
                if sizes.get(f) != size:  # still copying (or first sight): wait one more round
                    sizes[f] = size
                    if not once:
                        continue
                console.step(f"New clip: {f.name}")
                code = cli.main(["edit", str(f), brief, "--out", str(out / f.stem), *passthrough])
                (marker if code in (0, 2) else failed).write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
                if code in (0, 2):
                    console.ok(f"Draft ready: {out / f.stem / 'output' / 'master.mp4'}")
                else:
                    console.warn(f"{f.name} failed; see {out / f.stem / 'logs'}. Delete {failed.name} to retry.")
            if once:
                return 0
            time.sleep(interval)
    except KeyboardInterrupt:
        console.info("Stopped watching.")
        return 0
