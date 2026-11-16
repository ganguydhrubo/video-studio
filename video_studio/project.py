"""Project workspace: layout, settings, resumable stage state."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

from video_studio import config
from video_studio.util import ToolError, console, read_json, sha, write_json

STAGES = ["analyze", "storyboard", "produce", "render", "validate", "export"]
EDIT_STAGES = ["ingest", "transcribe", "sense", "plan", "cut", "render", "validate", "export"]

ASPECTS = {"9:16": (9, 16), "16:9": (16, 9), "1:1": (1, 1), "4:5": (4, 5)}


def dims(aspect: str, height: int) -> tuple[int, int]:
    """Frame size for an aspect; `height` is the long side for 9:16, the short side otherwise."""
    a, b = ASPECTS[aspect]
    if aspect == "9:16":
        h = height
        w = int(round(h * a / b / 2)) * 2
    elif aspect == "16:9":
        h = height if height <= 1440 else 1080
        h = min(h, 2160)
        w = int(round(h * a / b / 2)) * 2
    else:
        w = height if aspect == "1:1" else int(round(height * a / b / 2)) * 2
        h = int(round(w * b / a / 2)) * 2
    return w, h


@dataclass
class Project:
    root: Path

    # ---------------------------------------------------------------- paths
    @property
    def file(self) -> Path:
        return self.root / "project.json"

    @property
    def build(self) -> Path:
        return self.root / "build"

    @property
    def output(self) -> Path:
        return self.root / "output"

    @property
    def exports(self) -> Path:
        return self.root / "exports"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def state_file(self) -> Path:
        return self.build / "state.json"

    @property
    def master(self) -> Path:
        return self.output / "master.mp4"

    # ------------------------------------------------------------- settings
    @property
    def data(self) -> dict:
        data = read_json(self.file)
        if not data:
            raise ToolError(f"No project at {self.root}", "Run `video-studio init <name>` or pass --project <folder>.")
        return data

    def save(self, data: dict) -> None:
        write_json(self.file, data)

    @property
    def settings(self) -> dict:
        return self.data["settings"]

    def update_settings(self, **changes) -> dict:
        data = self.data
        data["settings"].update({k: v for k, v in changes.items() if v is not None})
        data["updated"] = time.time()
        self.save(data)
        return data["settings"]

    @property
    def id(self) -> str:
        return self.data["id"]

    @property
    def kind(self) -> str:
        return self.data.get("kind", "script")

    @property
    def size(self) -> tuple[int, int]:
        s = self.settings
        return dims(s["aspect"], int(s["height"]))

    # ---------------------------------------------------------------- state
    def state(self) -> dict:
        return read_json(self.state_file, {}) or {}

    def mark(self, stage: str, status: str, **info) -> None:
        st = self.state()
        entry = st.get(stage, {})
        entry.update({"status": status, "at": time.strftime("%Y-%m-%d %H:%M:%S"), **info})
        st[stage] = entry
        write_json(self.state_file, st)

    def done(self, stage: str) -> bool:
        return self.state().get(stage, {}).get("status") == "done"

    def invalidate_from(self, stage: str) -> None:
        order = EDIT_STAGES if self.kind == "edit" else STAGES
        st = self.state()
        if stage in order:
            for later in order[order.index(stage):]:
                st.pop(later, None)
        write_json(self.state_file, st)

    def next_stage(self) -> str | None:
        order = EDIT_STAGES if self.kind == "edit" else STAGES
        for stage in order:
            if not self.done(stage):
                return stage
        return None

    # ---------------------------------------------------------------- files
    def read(self, name: str, default=None):
        return read_json(self.build / name, default)

    def write(self, name: str, data) -> Path:
        path = self.build / name
        write_json(path, data)
        return path

    def script_text(self) -> str:
        path = self.root / "script.txt"
        if not path.exists():
            raise ToolError("This project has no script.txt.", "Put your narration in script.txt or run `video-studio analyze <file>`.")
        return path.read_text(encoding="utf-8-sig")

    def instructions(self) -> str:
        path = self.root / "instructions.txt"
        return path.read_text(encoding="utf-8-sig").strip() if path.exists() else ""


def slug(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower()
    return s[:48] or "project"


def default_settings(**overrides) -> dict:
    s = {
        "aspect": config.get("defaults.aspect"),
        "height": int(config.get("defaults.height")),
        "fps": int(config.get("defaults.fps")),
        "quality": config.get("defaults.quality"),
        "voice": config.get("defaults.voice"),
        "voice_rate": int(config.get("defaults.voice_rate")),
        "captions_enabled": bool(config.get("defaults.captions")),
        "music_enabled": bool(config.get("defaults.music")),
        "music_file": "",
        "motion_engine": config.get("defaults.motion_engine"),
        "use_blender": bool(config.get("defaults.use_blender")),
        "style": "cinematic",
        "loudness": float(config.get("defaults.loudness")),
        "ai_provider": config.get("ai.provider"),
        "look": "", "pacing": "", "transition": "", "music": "", "captions": "", "motion": "",
    }
    s.update({k: v for k, v in overrides.items() if v is not None})
    return s


def create(root: Path, name: str, kind: str = "script", **settings) -> Project:
    root.mkdir(parents=True, exist_ok=True)
    proj = Project(root)
    if proj.file.exists():
        raise ToolError(f"A project already exists at {root}.", "Use another name or `cd` into it and continue.")
    for sub in ("build", "output", "exports", "logs", "assets"):
        (root / sub).mkdir(exist_ok=True)
    data = {
        "id": f"{slug(name)}-{sha([name, time.time()], 6)}",
        "name": name,
        "kind": kind,
        "created": time.time(),
        "updated": time.time(),
        "app_version": __import__("video_studio").__version__,
        "settings": default_settings(**settings),
    }
    proj.save(data)
    return proj


def find(path: str | None) -> Project:
    start = Path(path).resolve() if path else Path.cwd()
    for candidate in (start, *start.parents):
        if (candidate / "project.json").exists():
            proj = Project(candidate)
            console.attach_log(proj.logs / "run.log")
            return proj
    raise ToolError(f"No Video Studio project found at or above {start}.",
                    "Run `video-studio init my-video` first, or pass --project <folder>.")
