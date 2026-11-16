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

from video_studio import config
from video_studio.util import IS_WINDOWS, FROZEN, ToolError, console, run, which

HYPERFRAMES_VERSION = "0.9.52"
HOME = Path.home()
LOCAL = Path(os.environ.get("LOCALAPPDATA", HOME / "AppData" / "Local"))
ROAMING = Path(os.environ.get("APPDATA", HOME / "AppData" / "Roaming"))


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
    return which("ffmpeg", _ffmpeg_dirs())


def ffprobe_path() -> Path | None:
    override = config.get("tools.ffprobe")
    if override and Path(override).is_file():
        return Path(override)
    ff = ffmpeg_path()
    if ff:
        sibling = ff.with_name("ffprobe" + ff.suffix)
        if sibling.exists():
            return sibling
    return which("ffprobe", _ffmpeg_dirs())


def blender_path() -> Path | None:
    override = config.get("tools.blender")
    if override and Path(override).is_file():
        return Path(override)
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
    return which("node", [Path("C:/Program Files/nodejs")])


def npx_path() -> Path | None:
    node = node_path()
    if node:
        for name in ("npx.cmd", "npx"):
            candidate = node.parent / name
            if candidate.exists():
                return candidate
    return which("npx.cmd") or which("npx")


def system_python() -> Path | None:
    """A real Python interpreter for optional helpers (e.g. faster-whisper)."""
    if not FROZEN:
        return Path(sys.executable)
    for name in ("python", "py"):
        p = which(name)
        if p and "WindowsApps" not in str(p):
            return p
    return None


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
    out = _probe([str(path), "-hide_banner", "-version"])
    info.version = (re.search(r"ffmpeg version (\S+)", out) or [None, "?"])[1]
    filters = _probe([str(path), "-hide_banner", "-filters"])
    needed = ["xfade", "subtitles", "loudnorm", "sidechaincompress", "blackdetect", "ebur128"]
    missing = [f for f in needed if not re.search(rf"\s{f}\s", filters)]
    encoders = _probe([str(path), "-hide_banner", "-encoders"])
    info.extra = {"ffprobe": str(probe), "libx264": "libx264" in encoders, "missing_filters": missing,
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
        info.problem = "no system Python found"
        info.hint = "Install Python 3.10+ from python.org"
        return info
    out = _probe([str(py), "-c", "import faster_whisper,sys;print('FW',faster_whisper.__version__)"], timeout=60)
    m = re.search(r"FW (\S+)", out)
    if m:
        info.found = info.ok = True
        info.version, info.path = m.group(1), str(py)
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

INSTALL_RECIPES: dict[str, dict] = {
    "ffmpeg": {"desc": "FFmpeg full build (gyan.dev) via winget", "cmd": ["winget", "install", "--id", "Gyan.FFmpeg", "-e", "--source", "winget"], "license": "GPL-3.0 build"},
    "blender": {"desc": "Blender (Blender Foundation) via winget", "cmd": ["winget", "install", "--id", "BlenderFoundation.Blender", "-e", "--source", "winget"], "license": "GPL-3.0-or-later"},
    "node": {"desc": "Node.js LTS via winget", "cmd": ["winget", "install", "--id", "OpenJS.NodeJS.LTS", "-e", "--source", "winget"], "license": "MIT"},
    "hyperframes": {"desc": f"HyperFrames {HYPERFRAMES_VERSION} into the npm cache, plus its browser", "cmd": ["{npx}", "-y", f"hyperframes@{HYPERFRAMES_VERSION}", "--version"], "license": "Apache-2.0"},
    "whisper": {"desc": "faster-whisper for your user Python (pip --user)", "cmd": ["{python}", "-m", "pip", "install", "--user", "faster-whisper"], "license": "MIT"},
    "claude": {"desc": "Claude Code CLI (npm, official)", "cmd": ["{npm}", "install", "-g", "@anthropic-ai/claude-code"], "license": "proprietary CLI, your Anthropic account"},
    "codex": {"desc": "OpenAI Codex CLI (npm, official)", "cmd": ["{npm}", "install", "-g", "@openai/codex"], "license": "Apache-2.0, your OpenAI account"},
    "gemini": {"desc": "Google Gemini CLI (npm, official)", "cmd": ["{npm}", "install", "-g", "@google/gemini-cli"], "license": "Apache-2.0, your Google account"},
    "qwen": {"desc": "Qwen Code CLI (npm, official)", "cmd": ["{npm}", "install", "-g", "@qwen-code/qwen-code"], "license": "Apache-2.0"},
    "opencode": {"desc": "OpenCode CLI (npm, official) - free models, no account", "cmd": ["{npm}", "install", "-g", "opencode-ai"], "license": "MIT"},
    "ollama": {"desc": "Ollama local model runner via winget", "cmd": ["winget", "install", "--id", "Ollama.Ollama", "-e", "--source", "winget"], "license": "MIT"},
}


def install_plan(tool: str) -> list[str]:
    if tool not in INSTALL_RECIPES:
        raise ToolError(f"No install recipe for '{tool}'.", "Known: " + ", ".join(INSTALL_RECIPES))
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
        elif part == "{python}":
            py = system_python()
            if not py:
                raise ToolError("Python is needed first.", "Install Python 3.10+ from python.org")
            part = str(py)
        cmd.append(part)
    return cmd


def install(tool: str, assume_yes: bool) -> bool:
    from video_studio.util import confirm, run_interactive

    cmd = install_plan(tool)
    recipe = INSTALL_RECIPES[tool]
    console.info(f"Install plan for {tool}: {recipe['desc']}  (license: {recipe['license']})")
    console.info("  command: " + " ".join(cmd))
    if not confirm("Run this install now?", assume_yes):
        console.warn("Skipped. Nothing was changed.")
        return False
    code = run_interactive(cmd)
    if tool == "hyperframes" and code == 0:
        run_interactive([str(npx_path()), "-y", f"hyperframes@{HYPERFRAMES_VERSION}", "browser", "ensure"])
    check_fn = {"ffmpeg": check_ffmpeg, "blender": check_blender, "node": check_node,
                "hyperframes": check_hyperframes, "whisper": check_whisper}.get(tool)
    if code != 0:
        console.error(f"Installer exited with code {code}.")
        console.info(RECOVERY.get(tool, "Re-run with a new terminal, or install manually from the official site."))
        return False
    if check_fn:
        sapi_voices.cache_clear()
        info = check_fn()
        if info.ok:
            console.ok(f"{info.name} {info.version} verified at {info.path}")
            return True
        console.warn(f"Installed, but verification failed: {info.problem}. Open a new terminal (PATH refresh) and run `video-studio doctor`.")
        return False
    console.ok("Install command finished. Open a new terminal, then run `video-studio ai status`.")
    return True


RECOVERY = {
    "ffmpeg": "Manual: download a 'full' build from https://www.gyan.dev/ffmpeg/builds/, unzip, then `video-studio config set tools.ffmpeg C:\\path\\to\\ffmpeg.exe`.",
    "blender": "Manual: install from https://www.blender.org/download/ then `video-studio config set tools.blender \"C:\\Program Files\\Blender Foundation\\Blender 4.2\\blender.exe\"`.",
    "node": "Manual: install Node.js 22+ LTS from https://nodejs.org, then open a new terminal.",
    "hyperframes": f"Check network access to registry.npmjs.org, then: npx -y hyperframes@{HYPERFRAMES_VERSION} doctor",
    "whisper": "Manual: py -m pip install --user faster-whisper (needs internet once for the model download).",
}
