"""Desktop entry: keep the local server alive independently of any terminal."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import traceback
import urllib.request
import webbrowser
from pathlib import Path


def health(port: int) -> bool:
    try:
        # Local launch must not depend on the user's HTTP proxy settings.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=0.25) as response:
            data = json.load(response)
        return data.get("app") == "VideoStudio"
    except (OSError, ValueError):
        return False


def open_window(url: str) -> None:
    """Use the installed Edge app window, without tabs or an address bar."""
    if os.name == "nt":
        candidates = [Path(os.environ.get(key, "C:/Program Files")) / "Microsoft/Edge/Application/msedge.exe"
                      for key in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
        for exe in candidates:
            if exe.is_file():
                try:
                    subprocess.Popen([str(exe), f"--app={url}", "--window-size=1360,900", "--no-first-run"],
                                     creationflags=subprocess.CREATE_NO_WINDOW)
                    return
                except OSError:
                    continue
    webbrowser.open(url)


def launch(open_browser: bool = True) -> str:
    from video_studio.util import app_data_dir

    for port in range(7860, 7880):
        if health(port):
            url = f"http://127.0.0.1:{port}/"
            if open_browser:
                open_window(url)
            return url
    logs = app_data_dir() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / "startup.log"
    if getattr(sys, "frozen", False):
        command = [str(Path(sys.executable).with_name("video-studio.exe"))]
    else:
        command = [sys.executable, "-m", "video_studio"]
    flags = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n--- Studio launch {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        log.flush()
        proc = subprocess.Popen(command + ["studio", "--no-browser"], stdin=subprocess.DEVNULL,
                                stdout=log, stderr=subprocess.STDOUT, creationflags=flags,
                                env={**os.environ, "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1"})
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        for port in range(7860, 7880):
            if health(port):
                url = f"http://127.0.0.1:{port}/"
                if open_browser:
                    open_window(url)
                return url
        if proc.poll() is not None:
            raise RuntimeError(f"Video Studio stopped during startup (exit {proc.returncode}). Details: {log_path}")
        time.sleep(0.15)
    raise RuntimeError(f"Video Studio did not become ready. Details: {log_path}")


def main() -> int:
    try:
        launch()
        return 0
    except Exception as exc:
        from video_studio.util import app_data_dir

        path = app_data_dir() / "logs" / "launcher-error.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(traceback.format_exc(), encoding="utf-8")
        if os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, f"{exc}\n\nFull details: {path}", "Video Studio could not open", 0x10)
        return 1
