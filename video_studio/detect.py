"""Detect installed production tools, check versions, and offer approved installs.

Nothing here installs or changes anything unless the user approves a specific
install plan (`video-studio install <tool>`), and installs always go through the
tool's official channel (winget, npm, pip).
"""

from __future__ import annotations

import glob
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path

from video_studio import config, toolbox
from video_studio.util import IS_WINDOWS, FROZEN, ToolError, console, run, which

HYPERFRAMES_VERSION = "0.9.52"
HOME = Path.home()
LOCAL = Path(os.environ.get("LOCALAPPDATA", HOME / "AppData" / "Local"))
ROAMING = Path(os.environ.get("APPDATA", HOME / "AppData" / "Roaming"))

# Fresh-PC test switch: see only Takebloom's own tools folder, as if nothing else were installed on this PC.
PORTABLE_ONLY = os.environ.get("VIDEO_STUDIO_PORTABLE_ONLY") == "1"

toolbox.activate()  # portable tools (if any) become visible to this process and its children only


@dataclass
class ToolInfo:
    key: str
    name: str
    role: str
    required: bool
    found: bool = False
    path: str = ""
    version: str = ""
    ok: bool = False
    problem: str = ""
    hint: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _version_tuple(text: str) -> tuple[int, ...]:
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text or "")
    if not match:
        return ()
    return tuple(int(g) for g in match.groups() if g is not None)


def _probe(cmd: list[str], timeout: float = 30) -> str:
    try:
        res = run(cmd, timeout=timeout, check=False)
        return (res.stdout or "") + (res.stderr or "")
    except (OSError, ToolError):
        return ""


# ------------------------------------------------------------------ locations


def _winget_links() -> list[Path]:
    return [LOCAL / "Microsoft" / "WinGet" / "Links"]


def _ffmpeg_dirs() -> list[Path]:
    dirs = _winget_links() + [HOME / "scoop" / "shims", Path("C:/ffmpeg/bin"), Path("C:/Program Files/ffmpeg/bin")]
    pattern = str(LOCAL / "Microsoft" / "WinGet" / "Packages" / "*FFmpeg*" / "*" / "bin")
    dirs += [Path(p) for p in glob.glob(pattern)]
    return dirs


def _blender_candidates() -> list[Path]:
    found: list[Path] = []
    for root in (os.environ.get("ProgramFiles", "C:/Program Files"), os.environ.get("ProgramFiles(x86)", "")):
        if root:
            found += [Path(p) for p in glob.glob(str(Path(root) / "Blender Foundation" / "*" / "blender.exe"))]
    found += [Path(p) for p in glob.glob(str(HOME / "scoop" / "apps" / "blender" / "current" / "blender.exe"))]
    steam = Path("C:/Program Files (x86)/Steam/steamapps/common/Blender/blender.exe")
    if steam.exists():
        found.append(steam)
    return found


def ffmpeg_path() -> Path | None:
    override = config.get("tools.ffmpeg")
    if override and Path(override).is_file():
        return Path(override)
    return toolbox.ffmpeg() or (None if PORTABLE_ONLY else which("ffmpeg", _ffmpeg_dirs()))


def ffprobe_path() -> Path | None:
    override = config.get("tools.ffprobe")
    if override and Path(override).is_file():
        return Path(override)
    ff = ffmpeg_path()
    if ff:
        sibling = ff.with_name("ffprobe" + ff.suffix)
        if sibling.exists():
            return sibling
    return None if PORTABLE_ONLY else which("ffprobe", _ffmpeg_dirs())


def blender_path() -> Path | None:
    override = config.get("tools.blender")
    if override and Path(override).is_file():
        return Path(override)
    portable = toolbox.blender()
    if portable or PORTABLE_ONLY:
        return portable
    on_path = which("blender")
    if on_path:
        return on_path
    candidates = _blender_candidates()
    if not candidates:
        return None
    return max(candidates, key=lambda p: _version_tuple(p.parent.name) or (0,))


def node_path() -> Path | None:
    override = config.get("tools.node")
    if override and Path(override).is_file():
        return Path(override)
    return toolbox.node() or (None if PORTABLE_ONLY else which("node", [Path("C:/Program Files/nodejs")]))


def npx_path() -> Path | None:
    node = node_path()
    if node:
        for name in ("npx.cmd", "npx"):
            candidate = node.parent / name
            if candidate.exists():
                return candidate
    return None if PORTABLE_ONLY else (which("npx.cmd") or which("npx"))


def system_python() -> Path | None:
    """A real Python interpreter for optional helpers (e.g. faster-whisper).

    Takebloom's private Python (made by setup, with every helper installed) always comes first."""
    private = toolbox.private_python()
    if private or PORTABLE_ONLY:
        return private
    if not FROZEN:
        return Path(sys.executable)
    for name in ("python", "py"):
        p = which(name)
        if p and "WindowsApps" not in str(p):
            return p
    # a Python that winget installed a moment ago is not on this process's PATH yet
    hits = sorted(glob.glob(str(LOCAL / "Programs" / "Python" / "Python3*" / "python.exe")), reverse=True)
    return Path(hits[0]) if hits else None


def source(path: str | Path | None) -> str:
    """Where a tool came from: 'portable' (Takebloom's own tools folder), 'custom' (set in config) or 'system'."""
    if not path:
        return ""
    if toolbox.is_portable(path):
        return "portable"
    overrides = [config.get(f"tools.{k}") for k in ("ffmpeg", "ffprobe", "blender", "node", "piper")]
    if any(o and Path(o) == Path(path) for o in overrides):
        return "custom"
    return "system"


def chrome_path() -> Path | None:
    for candidate in (
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
        LOCAL / "Google/Chrome/Application/chrome.exe",
    ):
        if candidate.exists():
            return candidate
    return None


def piper_path() -> Path | None:
    override = config.get("tools.piper")
    if override and Path(override).is_file():
        return Path(override)
    return which("piper")


# ------------------------------------------------------------------- checks


def check_ffmpeg() -> ToolInfo:
    info = ToolInfo("ffmpeg", "FFmpeg", "encoding, editing, audio mixing, captions, QA", True)
    path = ffmpeg_path()
    probe = ffprobe_path()
    if not path or not probe:
        info.problem = "FFmpeg/ffprobe not found"
        info.hint = "video-studio install ffmpeg"
        return info
    info.found, info.path = True, str(path)
    info.extra["source"] = source(path)
    out = _probe([str(path), "-hide_banner", "-version"])
    info.version = (re.search(r"ffmpeg version (\S+)", out) or [None, "?"])[1]
    filters = _probe([str(path), "-hide_banner", "-filters"])
    needed = ["xfade", "subtitles", "loudnorm", "sidechaincompress", "blackdetect", "ebur128"]
    missing = [f for f in needed if not re.search(rf"\s{f}\s", filters)]
    encoders = _probe([str(path), "-hide_banner", "-encoders"])
    info.extra = {**info.extra, "ffprobe": str(probe), "libx264": "libx264" in encoders, "missing_filters": missing,
                  "gpl_build": "--enable-gpl" in out}
    ver = _version_tuple(info.version)
    if ver and ver < (6, 0):
        info.problem = f"FFmpeg {info.version} is too old (need 6.0+)"
        info.hint = "video-studio install ffmpeg"
    elif missing or "libx264" not in encoders:
        info.problem = f"FFmpeg build lacks: {', '.join(missing + ([] if 'libx264' in encoders else ['libx264']))}"
        info.hint = "Install a full build: video-studio install ffmpeg"
    else:
        info.ok = True
    return info


def check_blender() -> ToolInfo:
    info = ToolInfo("blender", "Blender", "3D titles, objects, camera moves, 3D rendering", False)
    path = blender_path()
    if not path:
        info.problem = "Blender not found (3D scenes fall back to 2D motion graphics)"
        info.hint = "video-studio install blender"
        return info
    info.found, info.path = True, str(path)
    info.extra["source"] = source(path)
    out = _probe([str(path), "--factory-startup", "--background", "--version"], timeout=90)
    info.version = (re.search(r"Blender (\d+\.\d+(?:\.\d+)?)", out) or [None, "?"])[1]
    ver = _version_tuple(info.version)
    if ver and ver < (4, 2):
        info.problem = f"Blender {info.version} is older than 4.2 LTS"
        info.hint = "video-studio install blender"
    else:
        info.ok = bool(ver)
        if not ver:
            info.problem = "Could not read Blender version"
    return info


def check_node() -> ToolInfo:
    info = ToolInfo("node", "Node.js", "runtime for HyperFrames and AI CLIs", False)
    path = node_path()
    if not path:
        info.problem = "Node.js not found"
        info.hint = "video-studio install node"
        return info
    info.found, info.path = True, str(path)
    info.extra["source"] = source(path)
    info.version = _probe([str(path), "--version"]).strip()
    if _version_tuple(info.version) < (22, 0):
        info.problem = f"Node {info.version} < 22 (HyperFrames needs 22+)"
        info.hint = "video-studio install node"
    else:
        info.ok = True
    return info


def check_hyperframes() -> ToolInfo:
    info = ToolInfo("hyperframes", "HyperFrames", "HTML/GSAP motion graphics renderer (optional engine)", False)
    npx = npx_path()
    if not npx:
        info.problem = "needs Node.js 22+"
        info.hint = "video-studio install node"
        return info
    out = _probe([str(npx), "--no-install", f"hyperframes@{HYPERFRAMES_VERSION}", "--version"], timeout=60)
    if HYPERFRAMES_VERSION in out:
        info.found = info.ok = True
        info.version = HYPERFRAMES_VERSION
        info.path = f"npx hyperframes@{HYPERFRAMES_VERSION}"
        info.extra["source"] = source(npx)
        if not chrome_path():
            info.ok = False
            info.problem = "Chrome/Edge not found for rendering"
            info.hint = f"npx hyperframes@{HYPERFRAMES_VERSION} browser ensure"
    else:
        info.problem = "not in the local npm cache"
        info.hint = "video-studio install hyperframes"
    return info


def check_tts() -> ToolInfo:
    info = ToolInfo("tts", "Voice (TTS)", "offline voiceover", False)
    voices = sapi_voices()
    piper = piper_path()
    model = config.get("tools.piper_model")
    info.extra = {"sapi_voices": voices, "piper": str(piper) if piper else "", "piper_model": model}
    if piper and model and Path(model).exists():
        info.found = info.ok = True
        info.path, info.version = str(piper), "piper"
    if voices:
        info.found = info.ok = True
        info.path = info.path or "Windows SAPI (System.Speech)"
        info.version = info.version or f"{len(voices)} voice(s)"
    if not info.ok:
        info.problem = "No offline voice engine found"
        info.hint = "Windows Settings > Time & language > Speech > add a voice, or configure Piper"
    return info


def check_whisper() -> ToolInfo:
    info = ToolInfo("whisper", "faster-whisper", "transcribe / align user-recorded voiceovers", False)
    py = system_python()
    if not py:
        info.problem = "no Python for the AI helpers yet"
        info.hint = "video-studio setup (it installs a private Python for Takebloom)"
        return info
    out = _probe([str(py), "-c", "import faster_whisper,sys;print('FW',faster_whisper.__version__)"], timeout=60)
    m = re.search(r"FW (\S+)", out)
    if m:
        info.found = info.ok = True
        info.version, info.path = m.group(1), str(py)
        info.extra["source"] = "portable" if toolbox.is_portable(py) else "system"
    else:
        info.problem = "faster-whisper not installed"
        info.hint = "video-studio install whisper"
    return info


@lru_cache(maxsize=1)
def sapi_voices() -> list[str]:
    if not IS_WINDOWS:
        return []
    script = (
        "Add-Type -AssemblyName System.Speech;"
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        "$s.GetInstalledVoices()|?{$_.Enabled}|%{$_.VoiceInfo.Name}"
    )
    out = _probe(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], timeout=40)
    return [line.strip() for line in out.splitlines() if line.strip() and "Exception" not in line]


def check_all(include_ai: bool = True) -> list[ToolInfo]:
    tools = [check_ffmpeg(), check_blender(), check_node(), check_hyperframes(), check_tts(), check_whisper()]
    if include_ai:
        from video_studio.ai import providers

        for provider in providers():
            status = provider.status()
            tools.append(ToolInfo(
                key=f"ai:{provider.key}", name=provider.label, role="AI director (planning, creative review)",
                required=False, found=status.installed, path=status.path, version=status.version,
                ok=status.installed and status.signed_in, problem=status.problem, hint=status.hint,
                extra={"account": status.account},
            ))
    return tools


# ------------------------------------------------------------------ installs

# Portable tools come from `toolbox` (pinned download + SHA-256, no admin). winget is only the fallback, and is always
# run with the agreement flags so it never waits for a keypress (the Studio runs installs with no keyboard attached).
_WG = toolbox.WINGET_FLAGS
PORTABLE = {"ffmpeg", "blender", "node", "ollama"}
INSTALL_RECIPES: dict[str, dict] = {
    "ffmpeg": {"desc": toolbox.PACKAGES["ffmpeg"].label + " - portable copy in Takebloom's tools folder", "cmd": ["winget", "install", "--id", "Gyan.FFmpeg", *_WG], "license": "GPL-3.0"},
    "blender": {"desc": toolbox.PACKAGES["blender"].label + " - portable copy in Takebloom's tools folder", "cmd": ["winget", "install", "--id", "BlenderFoundation.Blender", *_WG], "license": "GPL-3.0-or-later"},
    "node": {"desc": toolbox.PACKAGES["node"].label + " - portable copy in Takebloom's tools folder", "cmd": ["winget", "install", "--id", "OpenJS.NodeJS.LTS", *_WG], "license": "MIT"},
    "hyperframes": {"desc": f"HyperFrames {HYPERFRAMES_VERSION} into the npm cache, plus its browser", "cmd": ["{npx}", "-y", f"hyperframes@{HYPERFRAMES_VERSION}", "--version"], "license": "Apache-2.0"},
    "python": {"desc": f"Private Python {toolbox.PY_VERSION} (via uv) with the offline AI helpers, only for Takebloom", "cmd": [], "license": "PSF (Python), MIT/Apache-2.0 (uv)"},
    "whisper": {"desc": "faster-whisper in Takebloom's private Python", "cmd": [], "license": "MIT"},
    "claude": {"desc": "Claude Code CLI (npm, official)", "cmd": ["{npm}", "install", "-g", "@anthropic-ai/claude-code"], "license": "proprietary CLI, your Anthropic account"},
    "codex": {"desc": "OpenAI Codex CLI (npm, official)", "cmd": ["{npm}", "install", "-g", "@openai/codex"], "license": "Apache-2.0, your OpenAI account"},
    "gemini": {"desc": "Google Gemini CLI (npm, official)", "cmd": ["{npm}", "install", "-g", "@google/gemini-cli"], "license": "Apache-2.0, your Google account"},
    "qwen": {"desc": "Qwen Code CLI (npm, official)", "cmd": ["{npm}", "install", "-g", "@qwen-code/qwen-code"], "license": "Apache-2.0"},
    "opencode": {"desc": "OpenCode CLI (npm, official) - free models, no account", "cmd": ["{npm}", "install", "-g", "opencode-ai"], "license": "MIT"},
    "ollama": {"desc": toolbox.PACKAGES["ollama"].label + f" plus the free {toolbox.OLLAMA_MODEL} model (Apache-2.0, ~1.4 GB)", "cmd": ["winget", "install", "--id", "Ollama.Ollama", *_WG], "license": "MIT"},
}


def install_plan(tool: str) -> list[str]:
    """The command for npm/winget recipes. Portable tools describe their download instead (see install_summary)."""
    if tool not in INSTALL_RECIPES:
        raise ToolError(f"No install recipe for '{tool}'.", "Known: " + ", ".join(INSTALL_RECIPES))
    if tool in PORTABLE or tool in ("python", "whisper"):
        return []
    cmd = []
    for part in INSTALL_RECIPES[tool]["cmd"]:
        if part == "{npx}":
            npx = npx_path()
            if not npx:
                raise ToolError("Node.js is needed first.", "video-studio install node")
            part = str(npx)
        elif part == "{npm}":
            npm = (node_path().parent / "npm.cmd") if node_path() else which("npm.cmd")
            if not npm or not Path(npm).exists():
                raise ToolError("npm is needed first.", "video-studio install node")
            part = str(npm)
        cmd.append(part)
    return cmd


def install_summary(tool: str) -> str:
    """One line saying exactly what an install does, shown before the user approves it."""
    if tool in PORTABLE:
        return toolbox.describe(tool)["command"]
    if tool in ("python", "whisper"):
        return f"Download uv {toolbox.PACKAGES['uv'].version} (SHA-256 checked), then `uv python install {toolbox.PY_VERSION}` "                f"and the helpers into {toolbox.venv_dir()}"
    return " ".join(install_plan(tool))


CHECKS = {"ffmpeg": lambda: check_ffmpeg(), "blender": lambda: check_blender(), "node": lambda: check_node(),
          "hyperframes": lambda: check_hyperframes(), "whisper": lambda: check_whisper(), "python": lambda: check_whisper()}


def install(tool: str, assume_yes: bool) -> bool:
    from video_studio.util import confirm, run_interactive

    if tool not in INSTALL_RECIPES:
        raise ToolError(f"No install recipe for '{tool}'.", "Known: " + ", ".join(INSTALL_RECIPES))
    recipe = INSTALL_RECIPES[tool]
    console.info(f"Install plan for {tool}: {recipe['desc']}  (licence: {recipe['license']})")
    console.info("  " + install_summary(tool))
    if not confirm("Run this install now?", assume_yes):
        console.warn("Skipped. Nothing was changed.")
        return False
    if tool in PORTABLE or tool in ("python", "whisper", "hyperframes"):
        from video_studio import setup

        if tool == "hyperframes" and not node_path():
            setup.install_item({"kind": "portable", "key": "node"})
        ok = setup.install_item({"kind": "portable" if tool in PORTABLE else tool, "key": tool})
        if tool not in CHECKS:
            return ok
        info = CHECKS[tool]()
        if info.ok:
            console.ok(f"{info.name} {info.version} verified at {info.path}")
            return True
        console.error(f"{info.name} is still not working: {info.problem}.")
        console.info(RECOVERY.get(tool, "Press Install again, or run `video-studio doctor`."))
        return False
    code = run_interactive(install_plan(tool))
    if code != 0:
        console.error(f"Installer exited with code {code}.")
        console.info(RECOVERY.get(tool, "Re-run with a new terminal, or install manually from the official site."))
        return False
    toolbox.refresh_path_from_registry()
    console.ok("Install command finished. Run `video-studio ai status` to check it.")
    return True


RECOVERY = {
    "ffmpeg": "Manual: download a 'full' build from https://www.gyan.dev/ffmpeg/builds/, unzip, then `video-studio config set tools.ffmpeg C:\\path\\to\\ffmpeg.exe`.",
    "blender": "Manual: install from https://www.blender.org/download/ then `video-studio config set tools.blender \"C:\\Program Files\\Blender Foundation\\Blender 4.2\\blender.exe\"`.",
    "node": "Manual: install Node.js 22+ LTS from https://nodejs.org, then open a new terminal.",
    "hyperframes": f"Check network access to registry.npmjs.org, then: npx -y hyperframes@{HYPERFRAMES_VERSION} doctor",
    "whisper": "Press Setup again (it continues where it stopped), or: py -m pip install --user faster-whisper.",
    "python": "Press Setup again (it continues where it stopped). Needs internet to github.com and pypi.org.",
}
