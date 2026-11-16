"""In-app updates from the public GitHub releases of ganguydhrubo/video-studio.

* Checking only reads the public "latest release" record. Nothing about you or your projects is sent;
  GitHub sees an ordinary request with the user agent "Takebloom/<version>".
* Downloading streams the setup file into the app data folder (``updates``) and checks it against the
  release's SHA256SUMS.txt before it may be run. It is written as ``.part`` and renamed only when the
  check passes.
* Installing starts the normal per-user setup (no admin). The setup closes Takebloom, swaps the files,
  keeps the previous copy as a backup and never touches projects or settings. It is refused while an
  edit is running, and the saved file is checked again right before it runs.
* Rolling back swaps the installed folder with the backup the setup kept beside it.
* ``startup()`` checks in the background (if updates.check is on) and downloads a compatible update by
  itself. It never installs by itself.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from video_studio import __version__, config
from video_studio.util import FROZEN, app_data_dir, read_json, write_json

REPO = "ganguydhrubo/video-studio"
LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
ASSET_PREFIX = f"https://github.com/{REPO}/releases/download/"
CHECK_EVERY = 6 * 3600          # cache a good answer (and re-check while the app runs) every 6 hours
ERROR_RETRY = 10 * 60           # a failed check is tried again after 10 minutes
BUSY_RETRY = 10 * 60            # the automatic download waits while an edit runs
START_DELAY = 20                # first automatic check, seconds after start-up
MIN_BUILD = 17763               # Windows 10 version 1809
EXTRA_SPACE = 500 * 1024 * 1024
MAX_SETUP = 2 * 1024 ** 3       # never write more than this, whatever the server says
SETUP_NAME = re.compile(r"Takebloom-Setup-(\d+\.\d+\.\d+)\.exe")
SUM_LINE = re.compile(r"^([0-9a-fA-F]{64})\s+\*?(.+?)\s*$")

_lock = threading.Lock()
_check: dict = {}
_download: dict = {"state": "idle"}
_restored = False
_token = 0
_started = False
_thread: threading.Thread | None = None
_wake = threading.Event()
_versions: dict = {}            # (exe path, mtime) -> version text, for previous_versions()


# ----------------------------------------------------------------------------- helpers

def _version(text: str) -> tuple[int, int, int]:
    parts = [int(x) for x in re.findall(r"\d+", str(text or ""))[:3]]
    return tuple(parts + [0] * (3 - len(parts)))  # type: ignore[return-value]


def _newer(version: str) -> bool:
    return bool(version) and _version(version) > _version(__version__)


def _get(url: str, timeout: float = 10.0, accept: str = "application/vnd.github+json"):
    request = urllib.request.Request(url, headers={"User-Agent": f"Takebloom/{__version__}", "Accept": accept})
    return urllib.request.urlopen(request, timeout=timeout)


def folder() -> Path:
    path = app_data_dir() / "updates"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _enabled() -> bool:
    try:
        value = config.get("updates.check")
    except Exception:
        value = None
    return True if value is None else bool(value)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _first_line(exc: BaseException) -> str:
    text = str(exc).strip()
    return text.splitlines()[0][:200] if text else exc.__class__.__name__


def _plain_error(exc: BaseException) -> str:
    """Turn a network failure into one plain sentence."""
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (403, 429):
            return "GitHub is limiting how often updates can be checked. Try again in an hour."
        if exc.code == 404:
            return "No Takebloom release has been published yet."
        return f"GitHub could not answer just now (error {exc.code}). Try again later."
    if isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError, OSError)):
        return "Could not reach GitHub to check for updates. Check your internet connection and try again."
    if isinstance(exc, ValueError):
        return "GitHub sent an answer that Takebloom could not read. Try again later."
    return "Could not check for updates just now. Try again later."


def _ps(text: str) -> str:
    """A PowerShell single-quoted string literal."""
    return "'" + str(text).replace("'", "''") + "'"


def _nice_date(stamp: float) -> str:
    return time.strftime("%d %b %Y", time.localtime(stamp)).lstrip("0")


def _start_detached(args: list[str], cwd: Path) -> None:
    """Start a process that keeps running after Takebloom closes."""
    devnull = subprocess.DEVNULL
    if os.name != "nt":
        subprocess.Popen(args, cwd=str(cwd), stdin=devnull, stdout=devnull, stderr=devnull,
                         close_fds=True, start_new_session=True)
        return
    detached, group, breakaway = 0x00000008, 0x00000200, 0x01000000
    try:
        subprocess.Popen(args, cwd=str(cwd), stdin=devnull, stdout=devnull, stderr=devnull,
                         close_fds=True, creationflags=detached | group | breakaway)
    except OSError:  # the app runs inside a job that does not allow breakaway
        subprocess.Popen(args, cwd=str(cwd), stdin=devnull, stdout=devnull, stderr=devnull,
                         close_fds=True, creationflags=detached | group)


# ----------------------------------------------------------------------------- checking

def _empty(error: str = "") -> dict:
    return {"current": __version__, "latest": "", "available": False, "notes": "", "page": RELEASES_PAGE,
            "published": None, "setup": None, "sums": None, "error": error, "checked": 0}


def read_release(raw: dict) -> dict:
    """Pick the versioned setup and the checksum file out of a GitHub release record."""
    if not isinstance(raw, dict) or raw.get("draft"):
        raise ValueError("not a release record")
    latest = str(raw.get("tag_name") or "").strip().lstrip("vV")
    if not re.fullmatch(r"\d+\.\d+\.\d+", latest):
        raise ValueError("release has no version number")
    assets = {str(a.get("name")): a for a in raw.get("assets") or [] if isinstance(a, dict) and a.get("name")}

    def safe(asset):
        return bool(asset) and str(asset.get("browser_download_url", "")).startswith(ASSET_PREFIX)

    # Prefer the setup named for this exact version, then any versioned one, then the plain copy.
    names = [f"Takebloom-Setup-{latest}.exe"]
    names += sorted((n for n in assets if SETUP_NAME.fullmatch(n) and n not in names),
                    key=lambda n: _version(n), reverse=True)
    names.append("Takebloom-Setup.exe")
    setup = next((assets[n] for n in names if n in assets and safe(assets[n])), None)
    sums = assets.get("SHA256SUMS.txt")
    return {"current": __version__, "latest": latest, "available": _newer(latest),
            "notes": str(raw.get("body") or "")[:4000],
            "page": str(raw.get("html_url") or RELEASES_PAGE) if str(raw.get("html_url") or "").startswith(
                f"https://github.com/{REPO}/") else RELEASES_PAGE,
            "published": raw.get("published_at"),
            "setup": {"name": setup["name"], "size": int(setup.get("size") or 0),
                      "url": setup["browser_download_url"]} if setup else None,
            "sums": sums["browser_download_url"] if safe(sums) else None,
            "error": ""}


def check(fresh: bool = False) -> dict:
    """The latest release, cached for 6 hours. Never raises."""
    enabled = _enabled()
    with _lock:
        _restore_locked()
        if _check and not fresh:
            age = time.time() - _check.get("checked", 0)
            if age < (ERROR_RETRY if _check.get("error") else CHECK_EVERY):
                return {**_check, "enabled": enabled, "download": _status_locked()}
        if not enabled and not fresh:
            return {**_empty(), "enabled": False, "download": _status_locked()}
    try:
        with _get(LATEST) as response:
            info = read_release(json.loads(response.read(4 << 20).decode("utf-8")))
    except Exception as exc:  # offline, rate limited, GitHub down: say so plainly, never block the app
        info = _empty(_plain_error(exc))
    info["checked"] = time.time()
    with _lock:
        _check.clear()
        _check.update(info)
        return {**info, "enabled": enabled, "download": _status_locked()}


# ----------------------------------------------------------------------------- compatibility

def _windows_build() -> tuple[int, int]:
    getter = getattr(sys, "getwindowsversion", None)
    if getter is None:
        return (0, 0)
    found = getter()
    return (found.major, found.build)


def _is_64bit() -> bool:
    machine = (platform.machine() or "").upper()
    wow = os.environ.get("PROCESSOR_ARCHITEW6432", "").upper()
    return machine.endswith("64") or wow.endswith("64")


def install_dir() -> Path | None:
    """The installed app folder, or None when Takebloom runs from source code."""
    return Path(sys.executable).resolve().parent if FROZEN else None


def _install_base() -> Path:
    base = install_dir()
    if base is None:
        base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Programs" / "VideoStudio"
    return base


def _free_bytes(path: Path) -> int:
    probe = path
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return shutil.disk_usage(str(probe)).free


def compatibility(info: dict) -> dict:
    """Can this computer install the release in ``info``? Plain-English problems if not."""
    problems: list[str] = []
    major, build = _windows_build()
    if major < 10 or build < MIN_BUILD:
        problems.append("Updates need Windows 10 (version 1809) or newer.")
    if not _is_64bit():
        problems.append("Updates need the 64-bit version of Windows.")
    setup = (info or {}).get("setup") or None
    if not setup:
        problems.append("This release has no Windows installer yet. Try again in a few minutes.")
    else:
        size = int(setup.get("size") or 0) or 300 * 1024 * 1024
        need = 3 * size + EXTRA_SPACE
        base = _install_base()
        try:
            free = _free_bytes(base)
        except OSError:
            free = None
        if free is not None and free < need:
            drive = base.drive or base.anchor or str(base)
            problems.append(f"There is not enough free space on drive {drive}. The update needs about "
                            f"{need / 1024 ** 3:.1f} GB free and there is {free / 1024 ** 3:.1f} GB.")
    latest = str((info or {}).get("latest") or "")
    if not _newer(latest):
        problems.append(f"Takebloom {latest or 'from this release'} is not newer than the version you have "
                        f"({__version__}).")
    return {"ok": not problems, "problems": problems}


# ----------------------------------------------------------------------------- downloading

def _skipped() -> set[str]:
    data = read_json(folder() / "skipped.json", {}) or {}
    return {str(v) for v in data.get("versions", [])} if isinstance(data, dict) else set()


def _set_skipped(versions: set[str]) -> None:
    write_json(folder() / "skipped.json", {"versions": sorted(versions, key=_version)})


def _status_locked() -> dict:
    return {"state": _download.get("state", "idle"), "done": int(_download.get("done") or 0),
            "total": int(_download.get("total") or 0), "version": str(_download.get("version") or ""),
            "error": str(_download.get("error") or "")}


def _restore_locked() -> None:
    """After a restart, remember an installer that was already downloaded and checked."""
    global _restored
    if _restored:
        return
    _restored = True
    try:
        record = read_json(folder() / "ready.json", None)
        if not isinstance(record, dict):
            return
        name, version = str(record.get("name") or ""), str(record.get("version") or "")
        path = folder() / name
        if not (SETUP_NAME.fullmatch(name) or name == "Takebloom-Setup.exe") or not path.is_file():
            (folder() / "ready.json").unlink(missing_ok=True)
            return
        if not _newer(version):  # already installed (or older): tidy up
            path.unlink(missing_ok=True)
            (folder() / "ready.json").unlink(missing_ok=True)
            return
        if _download.get("state", "idle") == "idle":
            size = path.stat().st_size
            _download.clear()
            _download.update(state="ready", path=str(path), sha256=str(record.get("sha256") or ""),
                             version=version, done=size, total=size, error="")
    except OSError:
        pass


def _current(token: int) -> bool:
    return _download.get("token") == token and not _download.get("cancel")


def _fetch(info: dict, token: int) -> None:
    setup = info["setup"]
    target = folder() / setup["name"]
    part = folder() / f"{setup['name']}.{token}.part"
    try:
        with _get(info["sums"], 20, accept="*/*") as response:
            sums = response.read(64 * 1024).decode("utf-8-sig", "replace")
        expected = ""
        for line in sums.splitlines():
            found = SUM_LINE.match(line.strip())
            if found and found.group(2).strip() == setup["name"]:
                expected = found.group(1).lower()
                break
        if not expected:
            raise ValueError("The release has no checksum for " + setup["name"] + ", so it was not downloaded.")
        with _get(setup["url"], 30, accept="application/octet-stream") as response, part.open("wb") as out:
            total = int(response.headers.get("Content-Length") or setup["size"] or 0)
            limit = min(MAX_SETUP, int(total * 1.1) + (1 << 20)) if total else MAX_SETUP
            with _lock:
                if not _current(token):
                    raise InterruptedError
                _download.update(total=total, done=0)
            while True:
                block = response.read(1 << 20)
                if not block:
                    break
                out.write(block)
                with _lock:
                    if not _current(token):
                        raise InterruptedError
                    _download["done"] = _download.get("done", 0) + len(block)
                    if _download["done"] > limit:
                        raise ValueError("The download was larger than expected, so it was stopped and deleted.")
        with _lock:
            if not _current(token):
                raise InterruptedError
            _download["state"] = "verifying"
        if _sha256(part) != expected:
            raise ValueError("The downloaded file did not match the release checksum, so it was deleted.")
        with _lock:
            if not _current(token):
                raise InterruptedError
            os.replace(part, target)
            write_json(folder() / "ready.json", {"name": setup["name"], "version": info["latest"], "sha256": expected})
            _download.update(state="ready", path=str(target), sha256=expected, version=info["latest"], error="")
        for old in folder().glob("Takebloom-Setup*"):  # older installers and stray parts
            if old.name != target.name and not old.name.endswith(".part"):
                old.unlink(missing_ok=True)
    except InterruptedError:
        part.unlink(missing_ok=True)
    except Exception as exc:
        part.unlink(missing_ok=True)
        if isinstance(exc, ValueError):
            message = str(exc)
        elif isinstance(exc, OSError) and getattr(exc, "errno", None) == 28:
            message = "There is not enough free space to save the update. Free some space and try again."
        elif isinstance(exc, urllib.error.HTTPError):
            message = f"GitHub stopped the download (error {exc.code}). Try again later."
        elif isinstance(exc, OSError):
            message = "The download stopped because the connection to GitHub was lost. Try again."
        else:
            message = "The download stopped: " + _first_line(exc)
        with _lock:
            if _download.get("token") == token:
                _download.update(state="failed", error=message)


def download() -> dict:
    """Start downloading the latest setup in the background."""
    info = check()
    if info.get("error"):
        return {"error": info["error"]}
    if not info.get("available"):
        return {"error": f"Takebloom {__version__} is already the latest version."}
    if not info.get("setup") or not info.get("sums"):
        return {"error": "This release has no installer to download yet. Try again in a few minutes."}
    fit = compatibility(info)
    if not fit["ok"]:
        return {"error": " ".join(fit["problems"]), "problems": fit["problems"]}
    global _token
    with _lock:
        _restore_locked()
        if _download.get("state") in ("downloading", "verifying") and _download.get("version") == info["latest"]:
            return {"download": _status_locked()}
        ready = folder() / info["setup"]["name"]
        if _download.get("state") == "ready" and _download.get("version") == info["latest"] and ready.exists():
            return {"download": _status_locked()}
        _token += 1
        token = _token
        _download.clear()
        _download.update(state="downloading", version=info["latest"], total=info["setup"]["size"], done=0,
                         error="", token=token)
        result = {"download": _status_locked()}
    skipped = _skipped()
    if info["latest"] in skipped:  # asked for again by hand, so stop skipping it
        _set_skipped(skipped - {info["latest"]})
    threading.Thread(target=_fetch, args=(info, token), name="takebloom-update-download", daemon=True).start()
    return result


def status() -> dict:
    """state (idle|downloading|verifying|ready|failed), done, total, version, error."""
    with _lock:
        _restore_locked()
        return _status_locked()


def cancel() -> dict:
    """Stop a running download. The half-written file is deleted."""
    with _lock:
        if _download.get("state") in ("downloading", "verifying"):
            _download["cancel"] = True
            _download.update(state="idle", done=0, total=0, version="", error="")
            _download.pop("token", None)
        return _status_locked()


# ----------------------------------------------------------------------------- installing

def install(running_jobs: int) -> dict:
    """Run the downloaded setup. It closes Takebloom, swaps the files and keeps the old copy."""
    if running_jobs:
        return {"error": "An edit is still running. Let it finish or stop it, then install the update."}
    if not FROZEN:
        return {"error": "This copy runs from source code, so update it with git. "
                         "The installer is only for the installed app."}
    with _lock:
        _restore_locked()
        state = dict(_download)
    path = Path(str(state.get("path") or ""))
    if state.get("state") != "ready" or not str(state.get("path") or "") or not path.is_file():
        return {"error": "Download the update first."}
    fit = compatibility({"latest": state.get("version"),
                         "setup": {"name": path.name, "size": path.stat().st_size}})
    if not fit["ok"]:
        return {"error": " ".join(fit["problems"]), "problems": fit["problems"]}
    if not state.get("sha256") or _sha256(path) != state.get("sha256"):  # checked again right before it runs
        path.unlink(missing_ok=True)
        (folder() / "ready.json").unlink(missing_ok=True)
        message = "The saved installer changed on disk, so it was deleted. Download the update again."
        with _lock:
            _download.update(state="failed", error=message, done=0)
        return {"error": message}
    try:
        _start_detached([str(path)], path.parent)
    except OSError as exc:
        return {"error": "The installer could not be started. " + _first_line(exc)}
    return {"ok": True, "version": state.get("version"),
            "text": f"The installer for Takebloom {state.get('version')} has started. Takebloom will close "
                    "while it updates. Open Takebloom again when the installer says it is finished."}


# ----------------------------------------------------------------------------- rolling back

def _read_version(exe: Path) -> str:
    """Ask an installed copy for its version (``video-studio.exe version``)."""
    try:
        key = (str(exe), exe.stat().st_mtime)
    except OSError:
        return ""
    if key in _versions:
        return _versions[key]
    found = ""
    try:
        done = subprocess.run([str(exe), "version"], cwd=str(exe.parent), capture_output=True, text=True,
                              timeout=8, creationflags=0x08000000 if os.name == "nt" else 0)
        match = re.search(r"(\d+\.\d+\.\d+)", (done.stdout or "") + (done.stderr or ""))
        found = match.group(1) if match and done.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        found = ""
    _versions[key] = found
    return found


def previous_versions() -> list[dict]:
    """The earlier copies the installer kept. The first entry (restore=True) is what rollback() would use."""
    base = install_dir()
    if base is None:
        return []
    found = []
    try:
        candidates = list(base.parent.glob(base.name + "-previous*"))
    except OSError:
        return []
    for path in candidates:
        cli, gui = path / "video-studio.exe", path / "VideoStudio.exe"
        if not path.is_dir() or not (cli.exists() or gui.exists()):
            continue
        try:
            modified = path.stat().st_mtime
        except OSError:
            continue
        version = _read_version(cli) if cli.exists() else ""
        label = f"Takebloom {version}" if version else f"The copy saved on {_nice_date(modified)}"
        found.append({"folder": path.name, "path": str(path), "version": version,
                      "modified": time.strftime("%Y-%m-%d", time.localtime(modified)),
                      "label": label, "restore": False, "_mtime": modified})
    # Prefer the newest copy that is older than this one; unknown versions fall back to the folder date.
    found.sort(key=lambda e: (bool(e["version"]) and _version(e["version"]) < _version(__version__),
                              _version(e["version"]) if e["version"] else (0, 0, 0), e["_mtime"]), reverse=True)
    for entry in found:
        entry.pop("_mtime")
    if found:
        found[0]["restore"] = True
    return found


def _rollback_script(install: Path, previous: Path, aside: Path, log: Path) -> str:
    return f"""# Takebloom rollback: swap the installed folder with the earlier copy the installer kept.
$ErrorActionPreference = 'Stop'
$install = {_ps(install)}
$previous = {_ps(previous)}
$aside = {_ps(aside)}
$log = {_ps(log)}
function Note([string]$text) {{ try {{ Add-Content -LiteralPath $log -Value ((Get-Date -Format s) + ' ' + $text) }} catch {{ }} }}
function Running {{ @(Get-Process video-studio,VideoStudio -ErrorAction SilentlyContinue | Where-Object {{ $_.Path -and ((Split-Path $_.Path -Parent) -eq $install) }}) }}
function Start-App {{
    $exe = Join-Path $install 'VideoStudio.exe'
    if (Test-Path -LiteralPath $exe) {{ Start-Process -FilePath $exe -WorkingDirectory $install }}
}}
function Move-Retry([string]$from, [string]$to) {{
    for ($i = 1; $i -le 10; $i++) {{
        try {{ Move-Item -LiteralPath $from -Destination $to; return }}
        catch {{ if ($i -eq 10) {{ throw }}; Start-Sleep -Seconds 1 }}
    }}
}}
Note 'Rollback started.'
$deadline = (Get-Date).AddSeconds(60)
while ((Running).Count -gt 0 -and (Get-Date) -lt $deadline) {{ Start-Sleep -Milliseconds 500 }}
if ((Running).Count -gt 0) {{ Note 'Takebloom did not close in 60 seconds, so it was stopped.'; Running | Stop-Process -Force -ErrorAction SilentlyContinue; Start-Sleep -Seconds 2 }}
if (-not (Test-Path -LiteralPath $previous)) {{ Note 'The earlier copy is missing. Nothing was changed.'; Start-App; exit 1 }}
$movedCurrent = $false
try {{
    if (Test-Path -LiteralPath $install) {{ Move-Retry $install $aside; $movedCurrent = $true }}
    Move-Retry $previous $install
}} catch {{
    Note ('Rollback failed: ' + $_.Exception.Message)
    try {{
        if ((Test-Path -LiteralPath $install) -and -not (Test-Path -LiteralPath $previous)) {{ Move-Retry $install $previous }}
        if ($movedCurrent -and -not (Test-Path -LiteralPath $install)) {{ Move-Retry $aside $install }}
        Note 'Everything was put back as it was.'
    }} catch {{ Note ('Could not put things back: ' + $_.Exception.Message) }}
    Start-App
    exit 1
}}
try {{
    $ver = & (Join-Path $install 'video-studio.exe') version 2>&1 | Out-String
    if ($ver -match '(\\d+\\.\\d+\\.\\d+)') {{
        $key = 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\VideoStudio'
        if (Test-Path $key) {{ Set-ItemProperty -Path $key -Name DisplayVersion -Value $Matches[1] }}
    }}
}} catch {{ }}
Note 'Rollback finished.'
Start-App
"""


def rollback(running_jobs: int) -> dict:
    """Go back to the earlier copy the installer kept. Takebloom closes and opens again."""
    if running_jobs:
        return {"error": "An edit is still running. Let it finish or stop it, then go back to the earlier version."}
    if not FROZEN:
        return {"error": "This copy runs from source code, so use git to go back to an earlier version."}
    base = install_dir()
    choices = previous_versions()
    if base is None or not choices:
        return {"error": "There is no earlier version to go back to. The installer keeps one only after an update."}
    chosen = choices[0]
    previous = Path(chosen["path"])
    stamp = time.strftime("%Y%m%d-%H%M%S")
    aside = base.parent / f"{base.name}-rolledback-{stamp}"
    n = 1
    while aside.exists():
        n += 1
        aside = base.parent / f"{base.name}-rolledback-{stamp}-{n}"
    script = folder() / "rollback.ps1"
    script.write_text(_rollback_script(base, previous, aside, folder() / "rollback.log"), encoding="utf-8-sig")
    try:
        _start_detached(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                         "-WindowStyle", "Hidden", "-File", str(script)], folder())
    except OSError as exc:
        return {"error": "Could not start going back to the earlier version. " + _first_line(exc)}
    try:  # do not download this version again by itself
        _set_skipped(_skipped() | {__version__})
    except OSError:
        pass
    return {"ok": True, "version": chosen["version"], "folder": chosen["folder"], "exit": True,
            "text": f"Going back to {chosen['label']}. Takebloom will close and open again in a minute. "
                    "Your projects and settings are not changed."}


# ----------------------------------------------------------------------------- background

def set_auto(on: bool) -> dict:
    """Turn the automatic check (and background download) on or off."""
    try:
        config.set_value("updates.check", bool(on))
    except (KeyError, OSError) as exc:
        return {"error": "Could not save the update setting. " + _first_line(exc)}
    if on:
        _wake.set()
    return {"ok": True, "enabled": bool(on)}


def _auto_once(running_jobs_fn: Callable[[], int] | None) -> float:
    """One automatic round. Returns the seconds to wait before the next one."""
    if not _enabled():
        return CHECK_EVERY
    info = check()
    if info.get("error"):
        return 3600
    if not info.get("available") or info.get("latest") in _skipped():
        return CHECK_EVERY
    if not compatibility(info)["ok"]:
        return CHECK_EVERY
    try:
        busy = int(running_jobs_fn() or 0) if running_jobs_fn else 0
    except Exception:
        busy = 0
    if busy:
        return BUSY_RETRY
    download()
    return CHECK_EVERY


def _auto_loop(running_jobs_fn: Callable[[], int] | None) -> None:
    wait: float = START_DELAY
    while True:
        _wake.wait(wait)
        _wake.clear()
        try:
            wait = _auto_once(running_jobs_fn)
        except Exception:
            wait = CHECK_EVERY


def startup(running_jobs_fn: Callable[[], int] | None = None) -> None:
    """Start the background checker once. It downloads by itself but never installs by itself."""
    global _started, _thread
    with _lock:
        if _started:
            return None
        _started = True
        _thread = threading.Thread(target=_auto_loop, args=(running_jobs_fn,), name="takebloom-updates", daemon=True)
        _thread.start()
    return None


def notice() -> dict:
    """A small message for the UI when an update is ready to install."""
    with _lock:
        _restore_locked()
        state = _status_locked()
    version = state["version"]
    ready = state["state"] == "ready" and _newer(version)
    if ready:
        try:
            ready = version not in _skipped()
        except OSError:
            pass
    text = (f"Takebloom {version} is downloaded and checked. Install it when no edit is running."
            if ready else "")
    return {"ready": ready, "version": version if ready else "", "text": text}


def _reset() -> None:
    """Forget everything in memory (used by tests)."""
    global _restored, _started, _thread
    with _lock:
        _check.clear()
        _download.clear()
        _download["state"] = "idle"
        _restored = False
        _started = False
        _thread = None
        _versions.clear()
