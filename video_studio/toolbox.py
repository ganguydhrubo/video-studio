"""Portable tools: download pinned, checksum-verified copies of FFmpeg, Python (via uv), Blender, Node.js and Ollama
into Takebloom's own tools folder. No admin rights, no system PATH changes, no winget (winget is only a fallback).

Layout (all under `<app data>/tools`, or VIDEO_STUDIO_TOOLS):
    ffmpeg/bin/ffmpeg.exe       blender/blender.exe       node/node.exe       ollama/ollama.exe
    uv/uv.exe                   python/base (uv-managed)  python/venv (private venv with the AI helpers)
    npm/ (npm cache, HyperFrames lives here)               hyperframes/ (its headless browser)
    _downloads/*.part (resumable downloads)

Every download is pinned to one release and its SHA-256 (copied from the publisher's own checksum file, URL kept
beside it). A `.part` file resumes where it stopped; a damaged download is refused and deleted; unpacking goes to a
temporary folder and is renamed into place, so a half-unpacked tool is never used.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from video_studio import APP_NAME
from video_studio.util import IS_WINDOWS, console

USER_AGENT = "Takebloom-setup"
PY_VERSION = "3.12"
OLLAMA_MODEL = "qwen3:1.7b"  # Apache-2.0, about 1.4 GB


class ToolboxError(RuntimeError):
    """A plain-English install failure. `hint` says what to do next."""

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint


@dataclass(frozen=True)
class Package:
    key: str
    label: str
    version: str
    urls: tuple[str, ...]          # first is the official source, the rest are official mirrors
    sha256: str
    size: int                      # bytes of the download
    licence: str
    exe: str                       # path of the main program inside the unpacked folder
    checksum_url: str              # where the publisher lists the SHA-256 (for people who want to check)
    winget: str = ""               # winget id used only when the portable download fails

    @property
    def filename(self) -> str:
        return self.urls[0].rsplit("/", 1)[-1]


PACKAGES: dict[str, Package] = {
    "ffmpeg": Package(
        "ffmpeg", "FFmpeg 9.0.2 (essentials build by gyan.dev)", "9.0.2",
        ("https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.zip",
         "https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-9.0.2-essentials_build.zip"),
        "60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba", 114768076, "GPL-3.0",
        "bin/ffmpeg.exe", "https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-9.0.2-essentials_build.zip.sha256",
        "Gyan.FFmpeg"),
    "uv": Package(
        "uv", "uv 0.13.8 (Python installer by Astral)", "0.13.8",
        ("https://github.com/astral-sh/uv/releases/download/0.13.8/uv-x86_64-pc-windows-msvc.zip",),
        "529e78a9cec6cd7e3e014f7ecd8bda0c113969d4ed0f2efeedb85c30943325b8", 15529884, "MIT or Apache-2.0",
        "uv.exe", "https://github.com/astral-sh/uv/releases/download/0.13.8/uv-x86_64-pc-windows-msvc.zip.sha256"),
    "blender": Package(
        "blender", "Blender 5.2.4 LTS (portable)", "5.2.4",
        ("https://download.blender.org/release/Blender5.2/blender-5.2.4-windows-x64.zip",),
        "bbd78f8931dc940444da4ff2983374930f9f5c092ae0e330588e9760bb9d967c", 405779142, "GPL-3.0-or-later",
        "blender.exe", "https://download.blender.org/release/Blender5.2/blender-5.2.4.sha256",
        "BlenderFoundation.Blender"),
    "node": Package(
        "node", "Node.js 24.22.3 LTS (portable)", "24.22.3",
        ("https://nodejs.org/dist/v24.22.3/node-v24.22.3-win-x64.zip",),
        "e612af99f626ab415ea225af6bce5c971feb448cb5cae250bb1c3fa50c5f7d9f", 37762649, "MIT",
        "node.exe", "https://nodejs.org/dist/v24.22.3/SHASUMS256.txt", "OpenJS.NodeJS.LTS"),
    "ollama": Package(
        "ollama", "Ollama 0.40.16 (portable, runs AI models offline)", "0.40.16",
        ("https://github.com/ollama/ollama/releases/download/v0.40.16/ollama-windows-amd64.zip",),
        "50fcf5ad54277f2fa5d173ebfc93bcb017adbc0c9ef2314daabc96ca9e6aa64e", 1618244995, "MIT",
        "ollama.exe", "https://github.com/ollama/ollama/releases/download/v0.40.16/sha256sum.txt", "Ollama.Ollama"),
}

# The winget fallback must never wait for a keypress: the Studio runs setup with no keyboard attached.
WINGET_FLAGS = ["-e", "--source", "winget", "--accept-source-agreements", "--accept-package-agreements",
                "--silent", "--disable-interactivity"]


# ----------------------------------------------------------------------- places

def _base() -> Path:
    override = os.environ.get("VIDEO_STUDIO_TOOLS")
    if override:
        return Path(override)
    if os.environ.get("VIDEO_STUDIO_HOME"):
        return Path(os.environ["VIDEO_STUDIO_HOME"]) / "tools"
    if IS_WINDOWS:
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / APP_NAME / "tools"
    return Path.home() / ".video-studio" / "tools"


def tools_dir() -> Path:
    """The tools folder, created when something is installed (looking for tools never creates it)."""
    base = _base()
    base.mkdir(parents=True, exist_ok=True)
    return base


def tool_dir(key: str) -> Path:
    return _base() / key


def exe_path(key: str) -> Path | None:
    """The portable program if it is installed and complete (its stamp file is written last)."""
    pkg = PACKAGES[key]
    d = tool_dir(key)
    exe = d / pkg.exe
    return exe if exe.is_file() and (d / ".takebloom.json").is_file() else None


def ffmpeg() -> Path | None:
    return exe_path("ffmpeg")


def blender() -> Path | None:
    return exe_path("blender")


def node() -> Path | None:
    return exe_path("node")


def ollama() -> Path | None:
    return exe_path("ollama")


def npm_cache() -> Path:
    return _base() / "npm"


def venv_dir() -> Path:
    return _base() / "python" / "venv"


def private_python() -> Path | None:
    """The private Python with the AI helpers, only once its packages installed completely."""
    py = venv_dir() / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
    return py if py.is_file() and (venv_dir() / ".takebloom-ready").is_file() else None


def is_portable(path: str | Path | None) -> bool:
    if not path:
        return False
    try:
        return Path(path).resolve().is_relative_to(_base().resolve())
    except (OSError, ValueError):
        return False


def source_of(path: str | Path | None) -> str:
    if not path:
        return ""
    return "portable" if is_portable(path) else "system"


# ------------------------------------------------------------------ environment

_ACTIVE = {"done": False}


def activate(force: bool = False) -> None:
    """Make portable tools visible to this process and its children only (the system PATH is never changed)."""
    if _ACTIVE["done"] and not force:
        return
    _ACTIVE["done"] = True
    try:
        dirs = []
        n = node()
        if n:
            dirs.append(n.parent)
            os.environ.setdefault("npm_config_cache", str(npm_cache()))
            os.environ.setdefault("HYPERFRAMES_CACHE_DIR", str(_base() / "hyperframes"))
        f = ffmpeg()
        if f:
            dirs.append(f.parent)  # HyperFrames looks for ffmpeg/ffprobe on PATH
        if ollama():
            os.environ.setdefault("OLLAMA_MODELS", str(_base() / "ollama-models"))
        _prepend_path(dirs)
    except OSError:
        pass


def _prepend_path(dirs: Sequence[Path]) -> None:
    parts = os.environ.get("PATH", "").split(os.pathsep)
    low = [p.lower().rstrip("\\/") for p in parts]
    new = [str(d) for d in dirs if str(d).lower().rstrip("\\/") not in low]
    if new:
        os.environ["PATH"] = os.pathsep.join(new + parts)


def refresh_path_from_registry() -> None:
    """After a winget install, read the new user/machine PATH so this process sees the tool without a restart."""
    if not IS_WINDOWS:
        return
    try:
        import winreg
    except ImportError:
        return
    found = []
    for hive, sub in ((winreg.HKEY_CURRENT_USER, "Environment"),
                      (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
        try:
            with winreg.OpenKey(hive, sub) as key:
                value = winreg.QueryValueEx(key, "Path")[0]
            found += [os.path.expandvars(p) for p in value.split(os.pathsep) if p.strip()]
        except OSError:
            continue
    parts = os.environ.get("PATH", "").split(os.pathsep)
    low = {p.lower().rstrip("\\/") for p in parts}
    extra = [p for p in found if p.lower().rstrip("\\/") not in low]
    if extra:
        os.environ["PATH"] = os.pathsep.join(parts + extra)


# --------------------------------------------------------------------- download

urlopen: Callable = urllib.request.urlopen  # replaced in tests


def fmt_mb(n: int) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{max(1, round(n / 1e6))} MB"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _fetch(url: str, part: Path, size: int, label: str) -> None:
    have = part.stat().st_size if part.exists() else 0
    if size and have > size:
        part.unlink()
        have = 0
    if size and have == size:
        return
    headers = {"User-Agent": USER_AGENT}
    if have:
        headers["Range"] = f"bytes={have}-"
    with urlopen(urllib.request.Request(url, headers=headers), timeout=60) as resp:
        if have and getattr(resp, "status", 200) != 206:
            have = 0  # the server ignored the resume request: start again
        length = int(resp.headers.get("Content-Length") or 0)
        total = size or (have + length)
        if have:
            console.info(f"  Continuing {label} from {fmt_mb(have)}")
        shown = -1
        with part.open("ab" if have else "wb") as fh:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                fh.write(chunk)
                have += len(chunk)
                if total:
                    pct = min(100, int(have * 100 / total))
                    if pct != shown:
                        shown = pct
                        console.progress(f"Downloading {label} (MB)", min(have, total) // 1_000_000, max(1, total // 1_000_000))
    if size and have != size:
        raise urllib.error.URLError(f"the connection closed early ({fmt_mb(have)} of {fmt_mb(size)})")


def download(urls: Sequence[str], dest: Path, sha256: str = "", size: int = 0, label: str = "", tries: int = 3) -> Path:
    """Resumable, verified download to `dest`. Tries each mirror a few times; a damaged file is never kept."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    label = label or dest.name
    if dest.is_file() and (not sha256 or _sha256(dest) == sha256):
        return dest
    part = dest.with_name(dest.name + ".part")
    last = ""
    for url in urls:
        for attempt in range(1, tries + 1):
            try:
                _fetch(url, part, size, label)
                if sha256:
                    got = _sha256(part)
                    if got != sha256:
                        part.unlink(missing_ok=True)
                        raise ToolboxError(
                            f"The {label} download was damaged or changed (its SHA-256 check failed), so it was deleted "
                            "and not installed.", "Press Setup again to download it afresh.")
                part.replace(dest)
                return dest
            except ToolboxError as exc:
                last = str(exc)
                break  # a checksum mismatch from this source will not fix itself: try the next mirror
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                last = getattr(exc, "reason", None) or str(exc)
                if attempt < tries:
                    console.warn(f"Download of {label} stopped ({last}). Trying again in {2 * attempt} s.")
                    time.sleep(2 * attempt)
    raise ToolboxError(f"Could not download {label}: {last}.",
                       "Check the internet connection and press Setup again. Finished parts are kept, so it continues "
                       "where it stopped.")


# ----------------------------------------------------------------------- unpack

def _safe_extract(archive: Path, target: Path) -> None:
    root = target.resolve()
    with zipfile.ZipFile(archive) as z:
        for member in z.infolist():
            out = (target / member.filename).resolve()
            if not out.is_relative_to(root):
                raise ToolboxError(f"{archive.name} has an unsafe file path and was not unpacked.")
        z.extractall(target)


def unpack(pkg: Package, archive: Path) -> Path:
    """Unzip to a temporary folder, then rename into tools/<key> in one step."""
    base = tools_dir()
    tmp = base / f".{pkg.key}-unpack"
    old = base / f".{pkg.key}-old"
    for p in (tmp, old):
        shutil.rmtree(p, ignore_errors=True)
    console.step(f"Unpacking {pkg.label}")
    try:
        _safe_extract(archive, tmp)
    except zipfile.BadZipFile as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        archive.unlink(missing_ok=True)
        raise ToolboxError(f"The {pkg.label} file could not be opened ({exc}).", "Press Setup again.") from exc
    entries = list(tmp.iterdir())
    root = entries[0] if len(entries) == 1 and entries[0].is_dir() else tmp
    if not (root / pkg.exe).is_file():
        shutil.rmtree(tmp, ignore_errors=True)
        raise ToolboxError(f"{pkg.label} unpacked, but {pkg.exe} was not inside.", "Press Setup again.")
    (root / ".takebloom.json").write_text(json.dumps({"version": pkg.version, "sha256": pkg.sha256, "from": pkg.urls[0],
                                                      "installed": time.strftime("%Y-%m-%d %H:%M:%S")}), encoding="utf-8")
    dest = tool_dir(pkg.key)
    if dest.exists():
        dest.rename(old)
    root.rename(dest)
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(old, ignore_errors=True)
    return dest


def install_package(key: str) -> Path:
    """Download, verify and unpack one portable tool. Returns its main program. Skips it when already there."""
    pkg = PACKAGES[key]
    have = exe_path(key)
    if have:
        stamp = json.loads((tool_dir(key) / ".takebloom.json").read_text(encoding="utf-8") or "{}")
        if stamp.get("version") == pkg.version:
            console.ok(f"{pkg.label} is already here.")
            return have
    free = shutil.disk_usage(tools_dir()).free
    if free < pkg.size * 3:
        raise ToolboxError(f"Not enough free disk space for {pkg.label}: it needs about {fmt_mb(pkg.size * 3)}, "
                           f"and {fmt_mb(free)} is free.", "Free some space (run `video-studio clean`), then press Setup again.")
    console.step(f"Downloading {pkg.label} ({fmt_mb(pkg.size)}, licence {pkg.licence})")
    archive = download(pkg.urls, tools_dir() / "_downloads" / pkg.filename, pkg.sha256, pkg.size, pkg.label)
    console.ok(f"{pkg.label}: SHA-256 checked")
    unpack(pkg, archive)
    archive.unlink(missing_ok=True)
    activate(force=True)
    exe = exe_path(key)
    if not exe:
        raise ToolboxError(f"{pkg.label} did not install completely.", "Press Setup again.")
    return exe


# ------------------------------------------------------------------- processes

def stream(cmd: Sequence[str | os.PathLike], env: dict | None = None, timeout: float = 3600, cwd: Path | None = None) -> int:
    """Run a command and show its output line by line (the Studio job page shows the same lines)."""
    args = [str(c) for c in cmd]
    flags = 0x08000000 if IS_WINDOWS else 0
    try:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                env={**os.environ, **(env or {})}, cwd=str(cwd) if cwd else None, creationflags=flags,
                                text=True, encoding="utf-8", errors="replace")
    except OSError as exc:
        console.warn(f"Could not start {Path(args[0]).name}: {exc}")
        return 127
    start = time.time()
    assert proc.stdout
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            console.info("    " + line[:300])
        if time.time() - start > timeout:
            proc.kill()
            console.warn(f"{Path(args[0]).name} took longer than {timeout / 60:.0f} minutes and was stopped.")
            return 124
    return proc.wait()


def winget_path() -> Path | None:
    found = shutil.which("winget")
    if found:
        return Path(found)
    cand = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps" / "winget.exe"
    return cand if cand.exists() else None


def winget_cmd(winget_id: str) -> list[str]:
    return [str(winget_path() or "winget"), "install", "--id", winget_id, *WINGET_FLAGS]


def winget_install(winget_id: str) -> bool:
    """Fallback only. Always non-interactive; reads the new PATH afterwards so no restart is needed."""
    if not winget_path():
        console.warn("winget is not on this PC, so there is no second way to install this.")
        return False
    console.step(f"Trying Windows Package Manager (winget) for {winget_id}. Windows may ask for permission.")
    code = stream(winget_cmd(winget_id), timeout=1800)
    refresh_path_from_registry()
    return code == 0


def install_with_fallback(key: str) -> Path | None:
    """Portable copy first; winget only if the download fails and the tool has a winget package."""
    pkg = PACKAGES[key]
    try:
        return install_package(key)
    except ToolboxError as exc:
        console.error(str(exc))
        if exc.hint:
            console.info("  " + exc.hint)
        if pkg.winget and winget_install(pkg.winget):
            console.ok(f"{pkg.label} installed with winget.")
        return None


# ------------------------------------------------------------------------ python

def pip_install(py: Path, packages: Sequence[str]) -> int:
    """Add packages to the Python the helpers use: the private venv (no --user there) or the user's own Python."""
    if is_portable(py):
        uv = exe_path("uv")
        if uv:
            return stream([uv, "pip", "install", "--python", py, *packages], _uv_env(), timeout=5400)
        return stream([py, "-m", "pip", "install", "--disable-pip-version-check", *packages], timeout=5400)
    return stream([py, "-m", "pip", "install", "--user", "--upgrade", *packages], timeout=5400)


def _uv_env() -> dict:
    base = tools_dir() / "python"
    return {"UV_PYTHON_INSTALL_DIR": str(base / "base"), "UV_CACHE_DIR": str(tools_dir() / "uv-cache"),
            "UV_PYTHON_PREFERENCE": "only-managed", "UV_LINK_MODE": "copy", "UV_NO_CONFIG": "1",
            "UV_PYTHON_INSTALL_BIN": "0", "UV_PYTHON_INSTALL_REGISTRY": "0", "NO_COLOR": "1"}


def _venv_python() -> Path:
    return venv_dir() / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


def _base_python_for_fallback() -> Path | None:
    """A Python 3.10+ already on this PC (or just installed by winget) to build the private venv from."""
    import glob

    cands = [shutil.which("python"), shutil.which("py")]
    local = os.environ.get("LOCALAPPDATA", "")
    cands += sorted(glob.glob(os.path.join(local, "Programs", "Python", "Python31*", "python.exe")), reverse=True)
    for c in cands:
        if c and "WindowsApps" not in c and Path(c).is_file():
            return Path(c)
    return None


def install_python(packages: Sequence[str], modules: Sequence[str]) -> Path | None:
    """Private Python 3.12 + venv with the AI helpers. uv first; an existing or winget Python as the fallback."""
    vpy = _venv_python()
    made = False
    uv = None
    try:
        uv = install_package("uv")
    except ToolboxError as exc:
        console.warn(f"{exc} Trying another way.")
    if uv:
        env = _uv_env()
        console.step(f"Getting a private Python {PY_VERSION} (only for Takebloom)")
        if stream([uv, "python", "install", PY_VERSION], env) == 0 and \
                stream([uv, "venv", "--seed", "--allow-existing", "--python", PY_VERSION, venv_dir()], env) == 0:
            made = vpy.is_file()
    if not made:
        base = _base_python_for_fallback()
        if not base and winget_install("Python.Python.3.12"):
            base = _base_python_for_fallback()
        if base:
            console.step(f"Making a private Python from {base}")
            made = stream([base, "-m", "venv", venv_dir()]) == 0 and vpy.is_file()
    if not made:
        console.error("Could not set up Python for the AI helpers. Speech, voice and face features stay off.")
        console.info("  Check the internet connection and press Setup again.")
        return None
    console.step("Installing the offline AI helpers: " + ", ".join(packages) + " (this is the longest step)")
    if uv:
        code = stream([uv, "pip", "install", "--python", vpy, *packages], _uv_env(), timeout=5400)
    else:
        code = stream([vpy, "-m", "pip", "install", "--disable-pip-version-check", *packages], timeout=5400)
    if code != 0:
        console.error("Some AI helpers did not install. Press Setup again to retry; finished parts are kept.")
        return None
    probe = ("import importlib.util,json;print(json.dumps({m: importlib.util.find_spec(m) is not None for m in %r}))"
             % (list(modules),))
    res = subprocess.run([str(vpy), "-E", "-c", probe], capture_output=True, text=True, timeout=300,
                         creationflags=0x08000000 if IS_WINDOWS else 0)
    try:
        found = json.loads(res.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        found = {}
    missing = [m for m in modules if not found.get(m)]
    if missing:
        console.warn("Installed, but these helpers do not load: " + ", ".join(missing))
    (venv_dir() / ".takebloom-ready").write_text(json.dumps({"packages": list(packages), "missing": missing}), encoding="utf-8")
    if uv:
        stream([uv, "cache", "clean"], _uv_env(), timeout=600)  # wheels are copied into the venv; free the space
    return vpy


# --------------------------------------------------------------------- HyperFrames

def install_hyperframes(version: str) -> bool:
    """HyperFrames at the pinned version into the npm cache, then its headless browser."""
    activate(force=True)
    from video_studio import detect

    npx = detect.npx_path()
    if not npx:
        console.error("HyperFrames needs Node.js first.")
        return False
    console.step(f"Installing HyperFrames {version} (motion titles and the timeline renderer)")
    if stream([npx, "-y", f"hyperframes@{version}", "--version"], timeout=1800) != 0:
        console.error("HyperFrames did not install. Check the internet connection and press Setup again.")
        return False
    console.step("Getting HyperFrames' headless browser (one time)")
    if stream([npx, "-y", f"hyperframes@{version}", "browser", "ensure"], timeout=1800) != 0:
        console.warn("The HyperFrames browser could not be fetched; it will use Microsoft Edge or Chrome instead.")
    return True


# --------------------------------------------------------------------- Ollama

def ollama_up(timeout: float = 1.5) -> bool:
    try:
        with urlopen(urllib.request.Request("http://127.0.0.1:11434/api/version", headers={"User-Agent": USER_AGENT}),
                     timeout=timeout) as resp:
            return getattr(resp, "status", 200) == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def ensure_ollama_server(exe: Path | None = None, wait: float = 25) -> bool:
    """A portable Ollama has no tray app, so start `ollama serve` quietly when nothing is listening."""
    if ollama_up():
        return True
    exe = exe or ollama()
    if not exe:
        return False
    activate()
    env = {**os.environ, "OLLAMA_MODELS": os.environ.get("OLLAMA_MODELS") or str(tools_dir() / "ollama-models")}
    flags = (0x08000000 | 0x00000008 | 0x00000200) if IS_WINDOWS else 0  # no window, detached, own group
    try:
        subprocess.Popen([str(exe), "serve"], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True)
    except OSError:
        return False
    end = time.time() + wait
    while time.time() < end:
        if ollama_up():
            return True
        time.sleep(0.5)
    return False


def install_ollama(model: str = OLLAMA_MODEL) -> bool:
    exe = install_with_fallback("ollama")
    if not exe:
        return False
    if not ensure_ollama_server(exe):
        console.error("Ollama was installed but did not start.")
        return False
    console.step(f"Downloading the free offline AI model {model} (Apache-2.0, about 1.4 GB)")
    return stream([exe, "pull", model], timeout=5400) == 0


# ------------------------------------------------------------------- descriptions

def describe(key: str) -> dict:
    """What an install will do, for the confirmation shown before anything runs."""
    pkg = PACKAGES.get(key)
    if not pkg:
        return {}
    host = pkg.urls[0].split("/")[2]
    return {"desc": f"{pkg.label}: download {fmt_mb(pkg.size)} from {host}, check its SHA-256, unpack into Takebloom's "
                    f"own tools folder (no admin, no system changes)", "license": pkg.licence, "size": pkg.size,
            "command": f"Download {pkg.urls[0]}  ->  {tool_dir(key)}"}
