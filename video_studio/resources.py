"""Hardware sensing and resource-aware scheduling decisions (no psutil dependency)."""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from video_studio import config
from video_studio.util import IS_WINDOWS, ToolError, fmt_bytes


@dataclass
class Memory:
    total: int
    available: int

    @property
    def available_gb(self) -> float:
        return self.available / 1024**3


def memory() -> Memory:
    if IS_WINDOWS:
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        return Memory(stat.ullTotalPhys, stat.ullAvailPhys)
    try:
        info = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, val = line.split(":", 1)
            info[key] = int(val.split()[0]) * 1024
        return Memory(info["MemTotal"], info.get("MemAvailable", info["MemFree"]))
    except OSError:
        return Memory(0, 0)


def cpu_count() -> int:
    return os.cpu_count() or 2


def render_threads() -> int:
    configured = int(config.get("resources.max_threads") or 0)
    if configured > 0:
        return configured
    # Leave two logical cores for the user's desktop.
    return max(1, cpu_count() - 2)


@lru_cache(maxsize=1)
def gpus() -> list[str]:
    if not IS_WINDOWS:
        return []
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_VideoController).Name"],
            capture_output=True, text=True, timeout=20, creationflags=0x08000000,
        ).stdout
        return [line.strip() for line in out.splitlines() if line.strip()]
    except (OSError, subprocess.SubprocessError):
        return []


def has_discrete_gpu() -> bool:
    return any(k in g.lower() for g in gpus() for k in ("nvidia", "geforce", "rtx", "radeon rx", "arc a"))


def disk_free(path: Path) -> int:
    path = Path(path)
    while not path.exists() and path.parent != path:
        path = path.parent
    return shutil.disk_usage(path).free


def ensure_disk(path: Path, needed_bytes: int, stage: str) -> None:
    floor = float(config.get("resources.min_free_disk_gb")) * 1024**3
    free = disk_free(path)
    if free - needed_bytes < floor:
        raise ToolError(
            f"Not enough disk space for {stage}: {fmt_bytes(free)} free, about {fmt_bytes(needed_bytes)} needed "
            f"plus a {fmt_bytes(floor)} safety margin.",
            "Free some space, run `video-studio clean`, or lower quality with `--quality draft`.",
            stage,
        )


def ensure_memory(min_gb: float, stage: str) -> None:
    mem = memory()
    if mem.total and mem.available_gb < min_gb:
        raise ToolError(
            f"Only {mem.available_gb:.1f} GB RAM free; {stage} needs about {min_gb:.1f} GB.",
            "Close heavy apps (browsers, editors) and run `video-studio resume`.",
            stage,
        )


def summary() -> dict:
    mem = memory()
    return {
        "cpu_threads": cpu_count(),
        "render_threads": render_threads(),
        "ram_total_gb": round(mem.total / 1024**3, 1),
        "ram_free_gb": round(mem.available_gb, 1),
        "gpus": gpus(),
        "discrete_gpu": has_discrete_gpu(),
    }
