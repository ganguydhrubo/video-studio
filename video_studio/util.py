"""Shared helpers: app directories, console output, subprocess execution, hashing."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

from video_studio import APP_NAME

IS_WINDOWS = os.name == "nt"
FROZEN = getattr(sys, "frozen", False)

# --------------------------------------------------------------------------- dirs


def app_data_dir() -> Path:
    """Per-user data (config, memory database). Never inside the install folder."""
    override = os.environ.get("VIDEO_STUDIO_HOME")
    if override:
        base = Path(override)
    elif IS_WINDOWS:
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / APP_NAME
    else:
        base = Path.home() / ".video-studio"
    base.mkdir(parents=True, exist_ok=True)
    return base


def cache_dir(*parts: str) -> Path:
    path = app_data_dir().joinpath("cache", *parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


def resource_path(*parts: str) -> Path:
    """Files shipped with the app (works from source and from a PyInstaller build)."""
    if FROZEN:
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).resolve().parent
    return base.joinpath(*parts)


# ----------------------------------------------------------------------- console

_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
if IS_WINDOWS and _COLOR:
    os.system("")  # enables ANSI escape processing on Windows 10+ consoles

_CODES = {"red": "31", "green": "32", "yellow": "33", "blue": "34", "magenta": "35", "cyan": "36", "dim": "2", "bold": "1"}


def paint(text: str, color: str) -> str:
    if not _COLOR:
        return text
    return f"\033[{_CODES[color]}m{text}\033[0m"


class Console:
    """Minimal console with an optional log file mirror."""

    def __init__(self) -> None:
        self.log_file: Path | None = None
        self.quiet = False

    def attach_log(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.log_file = path

    def _log(self, level: str, msg: str) -> None:
        if self.log_file:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            with self.log_file.open("a", encoding="utf-8") as fh:
                fh.write(f"{stamp} [{level}] {msg}\n")

    def _out(self, text: str) -> None:
        try:
            print(text, flush=True)
        except UnicodeEncodeError:
            print(text.encode("ascii", "replace").decode("ascii"), flush=True)

    def info(self, msg: str) -> None:
        self._log("INFO", msg)
        if not self.quiet:
            self._out(msg)

    def step(self, msg: str) -> None:
        self._log("STEP", msg)
        if not self.quiet:
            self._out(paint("> ", "cyan") + msg)

    def ok(self, msg: str) -> None:
        self._log("OK", msg)
        if not self.quiet:
            self._out(paint("[ok] ", "green") + msg)

    def warn(self, msg: str) -> None:
        self._log("WARN", msg)
        self._out(paint("[warn] ", "yellow") + msg)

    def error(self, msg: str) -> None:
        self._log("ERROR", msg)
        self._out(paint("[error] ", "red") + msg)

    def debug(self, msg: str) -> None:
        self._log("DEBUG", msg)
        if os.environ.get("VIDEO_STUDIO_DEBUG"):
            self._out(paint(msg, "dim"))

    def progress(self, label: str, done: int, total: int) -> None:
        if self.quiet or total <= 0:
            return
        width = 28
        filled = int(width * done / total)
        bar = "#" * filled + "-" * (width - filled)
        end = "\n" if done >= total else ""
        sys.stdout.write(f"\r  {label} [{bar}] {done}/{total}{end}")
        sys.stdout.flush()


console = Console()


def confirm(question: str, assume_yes: bool = False) -> bool:
    if assume_yes:
        return True
    if not sys.stdin or not sys.stdin.isatty():
        console.warn(f"{question} -> no interactive terminal; pass --yes to approve.")
        return False
    answer = input(f"{question} [y/N] ").strip().lower()
    return answer in {"y", "yes"}


# -------------------------------------------------------------------- processes


class ToolError(RuntimeError):
    """A tool or engine failed. `hint` tells the user how to recover."""

    def __init__(self, message: str, hint: str = "", stage: str = "") -> None:
        super().__init__(message)
        self.hint = hint
        self.stage = stage


_children: set[subprocess.Popen] = set()


def _below_normal_flags() -> int:
    if IS_WINDOWS:
        return 0x00004000  # BELOW_NORMAL_PRIORITY_CLASS: keeps the laptop responsive while rendering
    return 0


def run(
    cmd: Sequence[str | os.PathLike],
    *,
    cwd: Path | None = None,
    input_text: str | None = None,
    timeout: float | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
    low_priority: bool = False,
    log_name: str | None = None,
) -> subprocess.CompletedProcess:
    """Run a command, capture output, mirror a trimmed version into the log."""
    args = [str(c) for c in cmd]
    console.debug("$ " + " ".join(args)[:2000])
    flags = _below_normal_flags() if low_priority else 0
    if IS_WINDOWS:
        flags |= 0x08000000  # CREATE_NO_WINDOW
    proc = subprocess.Popen(
        args,
        cwd=str(cwd) if cwd else None,
        stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, **(env or {})},
        creationflags=flags,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    _children.add(proc)
    try:
        out, err = proc.communicate(input=input_text, timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(proc)
        out, err = proc.communicate()
        raise ToolError(f"{Path(args[0]).name} timed out after {timeout:.0f}s", "Retry, or raise the timeout.")
    finally:
        _children.discard(proc)
    result = subprocess.CompletedProcess(args, proc.returncode, out, err)
    if log_name:
        console.debug(f"[{log_name}] exit={proc.returncode}\n{(err or '')[-3000:]}")
    if check and proc.returncode != 0:
        tail = (err or out or "").strip().splitlines()[-12:]
        raise ToolError(f"{Path(args[0]).name} failed (exit {proc.returncode}):\n  " + "\n  ".join(tail))
    return result


def popen(cmd: Sequence[str | os.PathLike], *, low_priority: bool = True, **kw: Any) -> subprocess.Popen:
    flags = _below_normal_flags() if low_priority else 0
    if IS_WINDOWS:
        flags |= 0x08000000
    proc = subprocess.Popen([str(c) for c in cmd], creationflags=flags, **kw)
    _children.add(proc)
    return proc


def release(proc: subprocess.Popen) -> None:
    _children.discard(proc)


def kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if IS_WINDOWS:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.send_signal(signal.SIGTERM)


def kill_children() -> None:
    for proc in list(_children):
        kill_tree(proc)
    _children.clear()


def run_interactive(cmd: Sequence[str]) -> int:
    """Hand the terminal to a tool (used only for official login flows)."""
    return subprocess.call([str(c) for c in cmd])


# ------------------------------------------------------------------------- misc


def which(name: str, extra_dirs: Iterable[Path] = ()) -> Path | None:
    found = shutil.which(name)
    if found:
        return Path(found)
    for d in extra_dirs:
        for ext in ("", ".exe", ".cmd", ".bat"):
            candidate = Path(d) / f"{name}{ext}"
            if candidate.is_file():
                return candidate
    return None


def sha(data: Any, length: int = 16) -> str:
    if not isinstance(data, (bytes, str)):
        data = json.dumps(data, sort_keys=True, default=str)
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()[:length]


def file_sha(path: Path, length: int = 16) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:length]


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def fmt_seconds(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(round(seconds)), 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


def fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"
