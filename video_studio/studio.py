"""Studio: the visual interface, served locally by the CLI (127.0.0.1 only, no cloud).

Jobs run as `video-studio` subprocesses, so the UI and the CLI share one engine
and one project format. The page is a single file (studio_ui/index.html).
"""

from __future__ import annotations

import json
import hashlib
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from video_studio import __version__, autopilot, config, memory, signals
from video_studio.project import Project, slug
from video_studio.util import FROZEN, app_data_dir, console, kill_tree, read_json, resource_path, write_json

TOKEN = secrets.token_urlsafe(24)
JOBS: dict[str, dict] = {}
SELECTED_MEDIA: set[Path] = set()
LOCK = threading.Lock()
CHECK_LOCK = threading.Lock()
CHECKS: dict[str, dict] = {}
BRIEF_RESULTS: dict[str, dict] = {}
BRIEF_SLOTS = threading.BoundedSemaphore(2)


def brief_assistance(body: dict) -> dict:
    """A bounded asynchronous AI request; never occupy an HTTP connection waiting for a model."""
    from video_studio import briefing

    brief = str(body.get("brief", ""))[:4000]
    preferred = str(body.get("provider") or config.get("ai.provider"))
    key = hashlib.sha256((preferred + "\0" + brief).encode()).hexdigest()
    with CHECK_LOCK:
        known = BRIEF_RESULTS.get(key)
        # A failed answer is retried after a minute, or at once when asked; a good answer is reused.
        if known and (known["checking"] or not (known.get("failed") and (body.get("fresh") or time.time() - known.get("at", 0) > 60))):
            return dict(known)
        local = briefing.local_intent(brief)
        if not brief.strip() or preferred == "none":
            return {**local, "checking": False, "request_id": key}
        if not BRIEF_SLOTS.acquire(blocking=False):
            return {**local, "checking": True, "waiting": True, "request_id": key}
        # Retain only recent results, without deleting in-flight work.
        for old in list(BRIEF_RESULTS):
            if len(BRIEF_RESULTS) < 32:
                break
            if not BRIEF_RESULTS[old]["checking"]:
                del BRIEF_RESULTS[old]
        BRIEF_RESULTS[key] = {**local, "checking": True, "request_id": key}

    def worker():
        try:
            result = briefing.interpret(brief, preferred)
        except Exception:
            result = {**local, "warnings": ["The AI assistant could not respond. Your original instructions are kept."]}
        finally:
            BRIEF_SLOTS.release()
        with CHECK_LOCK:
            BRIEF_RESULTS[key] = {**result, "checking": False, "request_id": key, "at": time.time()}

    threading.Thread(target=worker, daemon=True).start()
    return {**local, "checking": True, "request_id": key}


def background_check(key, check, fresh=False):
    """Return immediately; concurrent page requests share a single cached probe."""
    with CHECK_LOCK:
        state = CHECKS.setdefault(key, {"data": {}, "running": False, "finished": 0})
        if not state["running"] and (fresh or not state["finished"] or time.monotonic() - state["finished"] > 60):
            state["running"] = True

            def worker():
                try:
                    data = check()
                except Exception as exc:
                    data = {"error": str(exc)}
                with CHECK_LOCK:
                    state.update(data=data, running=False, finished=time.monotonic())

            threading.Thread(target=worker, daemon=True).start()
        return {**state["data"], "checking": state["running"]}
STAGE_WORDS = {
    "Ingesting": "ingest", "Transcribing": "transcribe", "edit decisions": "plan", "Edit plan": "plan", "Cutting": "cut",
    "Cleaning up": "cut", "Tracking faces": "cut", "Finishing": "render", "Mixing": "render", "Quality checks": "validate",
    "QA score": "validate", "Exporting": "export", "Reading the script": "analyze", "Plan ready": "analyze",
    "Drawing storyboard": "storyboard", "Voiceover": "produce", "Scene ": "produce", "3D scene": "produce",
    "Composing": "produce", "Assembling": "render", "Watching the footage": "sense",
    # photos, restyle, cut-out and AI clip jobs
    "Animating": "produce", "Joining pictures": "render", "Music:": "render", "Restyling": "produce", "Steadying": "produce",
    "Cutting out": "produce", "Generating": "produce",
}


def engine_cmd() -> list[str]:
    if FROZEN:
        return [sys.executable]
    return [sys.executable, "-m", "video_studio"]


AUTO_KINDS = ("edit", "script", "photos")   # finished films get covers by themselves
RETRY_KINDS = ("edit", "script")           # `resume --project` continues these from the stage that stopped


class Job:
    def __init__(self, kind: str, args: list[str], root: Path, title: str) -> None:
        self.id = uuid.uuid4().hex[:10]
        self.kind, self.args, self.root, self.title = kind, args, root, title
        self.lines: list[str] = []
        self.stage = "starting"
        self.status = "running"
        self.started = time.time()
        self.finished = None
        self.proc: subprocess.Popen | None = None
        # autopilot: edits and script videos retry once by themselves and finish with covers (video_studio/autopilot.py)
        self.retries = autopilot.RETRIES if kind in RETRY_KINDS else 0

    def start(self) -> None:
        env = {**os.environ, "NO_COLOR": "1", "PYTHONIOENCODING": "utf-8"}
        flags = 0x08000000 | 0x00004000 if os.name == "nt" else 0  # no window, below-normal priority
        self.proc = subprocess.Popen(engine_cmd() + self.args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, env=env, creationflags=flags, text=True,
                                     encoding="utf-8", errors="replace")
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self.proc and self.proc.stdout
        buf = ""
        while True:
            ch = self.proc.stdout.read(1)
            if not ch:
                break
            if ch in "\r\n":
                if buf.strip():
                    self._line(buf.strip())
                buf = ""
            else:
                buf += ch
        code = self.proc.wait()
        if buf.strip():
            self._line(buf.strip())
        if self._autopilot(code):
            return
        self.finished = time.time()
        self.status = "done" if code == 0 else ("warning" if code == 2 else ("stopped" if self.status == "stopping" else "failed"))
        self._line(f"[exit {code}]")
        if self.kind == "edit" and code in (0, 2):
            signals.on_finished(self.root)   # learning: what the film used; retrains keep/cut in the background

    def _autopilot(self, code: int) -> bool:
        """After the engine exits: one automatic retry from where it stopped, then covers. True = a retry started."""
        if self.status == "stopping" or self.kind not in AUTO_KINDS:
            return False
        if code not in (0, 2) and self.retries > 0 and (self.root / "project.json").exists():
            self.retries -= 1
            self._line(f"[exit {code}]")
            self._line("Trying once more by itself, from where it stopped. Finished work is reused.")
            time.sleep(2)
            if self.status == "stopping":
                return False
            self.args = ["resume", "--project", str(self.root)]
            self.start()
            return True
        if code in (0, 2):
            autopilot.finish(self.root, say=self._line)
        return False

    def _line(self, line: str) -> None:
        with LOCK:
            if line.startswith("  ") and "[" in line and "]" in line and self.lines and self.lines[-1].startswith("  ") and "[" in self.lines[-1]:
                self.lines[-1] = line  # progress bars update in place
            else:
                self.lines.append(line)
            for key, stage in STAGE_WORDS.items():
                if key in line:
                    self.stage = stage
                    break

    def info(self, since: int = 0) -> dict:
        with LOCK:
            state = read_json(self.root / "build" / "state.json", {}) or {}
            return {"id": self.id, "kind": self.kind, "title": self.title, "status": self.status, "stage": self.stage,
                    "elapsed": round((self.finished or time.time()) - self.started), "lines": self.lines[since:], "count": len(self.lines),
                    "root": str(self.root), "state": state}


# ---------------------------------------------------------------- projects

def workspace() -> Path:
    base = Path(config.get("workspace") or (Path.home() / "Videos" / "Video Studio"))
    base.mkdir(parents=True, exist_ok=True)
    return base


def project_summary(root: Path) -> dict | None:
    data = read_json(root / "project.json")
    if not data:
        return None
    state = read_json(root / "build" / "state.json", {}) or {}
    master = root / "output" / "master.mp4"
    qa = read_json(root / "output" / "qa" / "qa_report.json", {}) or {}
    exports = sorted(str(p) for p in (root / "exports").glob("*") if p.is_file()) if (root / "exports").exists() else []
    plan = read_json(root / "build" / "edit_plan.json") or read_json(root / "build" / "plan.json") or {}
    creative = read_json(root / "build" / "creative.json", {}) or {}
    rec = memory.get_project(data["id"]) or {}
    return {"id": data["id"], "name": data["name"], "kind": data.get("kind", "script"), "root": str(root),
            "updated": data.get("updated"), "settings": data.get("settings"), "brief": data.get("brief", ""),
            "state": state, "master": str(master) if master.exists() else "", "qa": {k: qa.get(k) for k in ("score", "fails", "warnings", "items")},
            "exports": exports, "title": plan.get("title", ""), "planner": plan.get("planner", ""),
            "decisions": creative.get("decisions", {}), "reasons": creative.get("reasons", {}), "rating": rec.get("rating"),
            "thumb": next((e for e in exports if e.endswith("_thumbnail.jpg")), ""),
            "lines": _review_lines(root, plan),
            "story": plan.get("story") or [], "assist": plan.get("assist") or {},
            "intent": plan.get("intent") or {}, "coverage": plan.get("coverage") or {},
            "learned_advice": plan.get("learned_advice") or {"available": False},
            "hook_text": plan.get("hook_text", ""), "mode": plan.get("mode", "speech"),
            "understood": _understood(root),
            "effects": ((read_json(root / "build" / "style_plan.json", {}) or {}).get("reasons") or [])[:20],
            "music": (read_json(root / "build" / "edit_manifest.json", {}) or {}).get("music"),
            "asked": read_json(root / "understood.json", {}) or {},
            "timeline": _timeline(root)}


def _timeline(root: Path) -> dict | None:
    """The finished edit as tracks for the Studio timeline: your lines in play order, B-roll cutaways, big text."""
    m = read_json(root / "build" / "edit_manifest.json", {}) or {}
    if not m.get("pieces") or not m.get("total"):
        return None
    sp = read_json(root / "build" / "style_plan.json", {}) or {}
    pieces = [{"t0": round(pc["out_start"], 2), "t1": round(pc["out_start"] + pc["src_end"] - pc["src_start"], 2),
               "utt": pc.get("utt", ""), "src0": round(pc["src_start"], 2), "src1": round(pc["src_end"], 2)} for pc in m["pieces"]]
    return {"total": m["total"], "pieces": pieces,
            "broll": [{"t0": c["t0"], "t1": c["t1"], "why": c.get("why", ""), "name": Path(c["path"]).name} for c in m.get("broll") or []],
            "slams": [{"t0": x["t0"], "t1": x["t1"], "text": x.get("text", "")} for x in sp.get("slams") or [] if "t0" in x],
            "cuts": [{"t": x["t"], "kind": x.get("kind", "")} for x in sp.get("transitions") or [] if "t" in x],
            "drop": ((m.get("music") or {}).get("drop_at"))}


FREE_NOTE = {"opencode": "Free models, no account needed", "ollama": "Free, runs offline on this PC",
             "gemini": "Free with a Google account (daily limits)", "qwen": "Free with a Qwen account",
             "claude": "Needs a paid Claude plan", "codex": "Needs a ChatGPT plan"}


def _ai_status() -> dict:
    from video_studio import ai

    out = []
    providers = ai.providers()
    with ThreadPoolExecutor(max_workers=6) as pool:
        statuses = list(pool.map(lambda p: p.status(), providers))
    for p, st in zip(providers, statuses):
        models = list(getattr(p, "FREE", []))
        out.append({"key": p.key, "label": p.label, "installed": st.installed, "signed_in": st.signed_in,
                    "ready": st.signed_in and not st.problem, "account": st.account, "problem": st.problem,
                    "hint": st.hint, "version": st.version, "install": p.install_hint, "cost": FREE_NOTE.get(p.key, ""),
                    "model": p.model(), "models": models, "needs_login": bool(p.login_cmd) or p.key in ("gemini", "qwen"),
                    "setup": None if st.installed else install_info(p.key)})
    return {"providers": out, "preferred": config.get("ai.provider"), "fallback": config.get("ai.fallback")}


def ai_status(fresh=False) -> dict:
    return {"providers": [], "preferred": config.get("ai.provider"), "fallback": config.get("ai.fallback"),
            **background_check("ai", _ai_status, fresh)}


def install_info(key: str) -> dict:
    """What an install would run, shown to the user before they approve it. `blocked` = a prerequisite is missing."""
    from video_studio import detect
    from video_studio.util import ToolError

    recipe = detect.INSTALL_RECIPES.get(key)
    if not recipe:
        return {"blocked": "No automatic install for this one."}
    try:
        cmd = detect.install_plan(key)
    except ToolError as exc:
        return {"blocked": str(exc), "hint": exc.hint}
    return {"desc": recipe["desc"], "license": recipe["license"], "command": " ".join(cmd)}


def ai_install(key: str) -> dict:
    """Runs `video-studio install <key> --yes` as a job. The UI asks first and shows the exact command."""
    from video_studio import ai

    try:
        p = ai.get_provider(key)
    except Exception:  # noqa: BLE001
        return {"error": "Unknown provider."}
    if p.path():
        return {"error": f"{p.label} is already installed."}
    info = install_info(key)
    if info.get("blocked"):
        return {"error": info["blocked"], "hint": info.get("hint", "")}
    return _tool_job("install", ["install", key, "--yes"], f"Install {p.label}")


def ai_login(key: str) -> dict:
    """Open the provider's OWN sign-in in a new terminal window (it opens the browser). We never see the login."""
    from video_studio import ai

    try:
        p = ai.get_provider(key)
    except Exception:  # noqa: BLE001
        return {"error": "Unknown provider."}
    exe = p.path()
    if not exe:
        return {"error": f"{p.label} is not installed.", "hint": p.install_hint}
    cmd = [str(exe), *p.login_cmd] if p.login_cmd else [str(exe)]
    if key == "opencode":
        cmd = [str(exe), "auth", "login"]
    import subprocess

    subprocess.Popen(["cmd", "/c", "start", f"Sign in - {p.label}", "cmd", "/k", *cmd], close_fds=True)
    return {"ok": True, "message": f"A window opened with {p.label}'s own sign-in. Finish it in your browser, then press 'Check again'."}


def ai_use(key: str, model: str) -> dict:
    from video_studio import ai

    if key not in ("auto", "none"):
        try:
            ai.get_provider(key)
        except Exception:  # noqa: BLE001
            return {"error": "Unknown provider."}
    config.set_value("ai.provider", key)
    if model:
        models = dict(config.get("ai.models") or {})
        models[key] = model
        config.set_value("ai.models", models)
    return {"ok": True, "message": f"AI brain set to {key}" + (f" ({model})" if model else "")}


def _review_lines(root: Path, plan: dict) -> list[dict]:
    """Every spoken line with its English meaning and its place in the edit (the cold open shows as #1)."""
    from video_studio import story

    utts = plan.get("utterances") or []
    tr = story.english(read_json(root / "build" / "transcript.json", {}) or {})
    en = story.meanings(utts, tr)
    keep = plan.get("keep") or []
    return [{"id": u["id"], "start": u["start"], "end": u["end"], "text": u["text"], "en": en.get(u["id"], ""),
             "kept": u["id"] in keep, "order": (keep.index(u["id"]) + 1) if u["id"] in keep else None,
             "retake": u["id"] in (plan.get("retakes") or [])} for u in utts]


def _understood(root: Path) -> str:
    from video_studio.engines import sense

    s = read_json(root / "build" / "sense.json", None)
    if not s:
        return ""
    notes = "; ".join(s.get("issues") or [])
    return sense.describe(s) + (f" (notes: {notes})" if notes else "")


def list_projects() -> list[dict]:
    out = []
    for root in sorted(workspace().iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if (root / "project.json").exists():
            s = project_summary(root)
            if s:
                out.append(s)
    return out[:60]


def _doctor() -> dict:
    from video_studio import detect
    from video_studio.engines import ml

    checks = [detect.check_ffmpeg, detect.check_blender, detect.check_node,
              detect.check_hyperframes, detect.check_tts, detect.check_whisper]
    with ThreadPoolExecutor(max_workers=6) as pool:
        tools = [t.to_dict() for t in pool.map(lambda check: check(), checks)]
    return {"tools": tools, "kokoro": ml.kokoro_ready(), "version": __version__}


def doctor() -> dict:
    return {"tools": [], "kokoro": False, "version": __version__, **background_check("doctor", _doctor)}


# ----------------------------------------------------------------- handler

def _safe_path(raw: str) -> Path | None:
    """Only files inside the workspace or a known project may be served."""
    try:
        p = Path(unquote(raw)).resolve()
    except (OSError, ValueError):
        return None
    with LOCK:
        if p in SELECTED_MEDIA and p.is_file():
            return p
    allowed = [workspace().resolve()] + [Path(j.root).resolve() for j in JOBS.values()]
    if any(str(p).lower().startswith(str(a).lower() + os.sep) for a in allowed) and p.is_file():
        return p
    return None


def copy_local_video(stream, size: int, folder: Path, name: str) -> dict:
    """Stream a browser-selected file to disk; memory use stays at 1 MiB."""
    if size <= 0:
        raise ValueError("The selected video is empty. Choose the original file again.")
    folder.mkdir(parents=True, exist_ok=True)
    if size > shutil.disk_usage(folder).free:
        raise OSError("There is not enough free disk space for a local working copy. Choose the file directly instead.")
    dest = folder / f"{uuid.uuid4().hex}_{name}"
    partial = dest.with_name(dest.name + '.part')
    try:
        with partial.open('xb') as target:
            left = size
            while left:
                chunk = stream.read(min(1 << 20, left))
                if not chunk:
                    raise ValueError("The local copy was interrupted. Reopen Takebloom and choose the file directly.")
                target.write(chunk)
                left -= len(chunk)
        partial.replace(dest)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return {"path": str(dest), "name": name, "size": size}


class Handler(BaseHTTPRequestHandler):
    server_version = f"VideoStudio/{__version__}"

    def log_message(self, fmt: str, *args) -> None:  # keep the terminal quiet
        return

    # -- helpers
    def _json(self, data, status: int = 200) -> None:
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def _authorised(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            return False
        if self.command == "POST" and self.headers.get("X-Studio-Token") != TOKEN:
            return False
        return True

    # -- routes
    def do_GET(self) -> None:  # noqa: N802
        if not self._authorised():
            return self._json({"error": "forbidden"}, 403)
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if url.path == "/":
            html = resource_path("studio_ui", "index.html").read_text(encoding="utf-8").replace("__TOKEN__", TOKEN).replace("__VERSION__", __version__)
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")  # after an update the window must load the new page and token
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")
            self.end_headers()
            self.wfile.write(body)
        elif url.path == "/brand-logo.png":
            self._file(resource_path("assets", "takebloom-logo.png"))
        elif url.path == "/favicon.ico":
            self._file(resource_path("assets", "app-icon.ico"))
        elif url.path == "/font/display.ttf":
            self._file(resource_path("assets", "fonts", "InstrumentSerif-Italic.ttf"))
        elif url.path == "/brief-assistant.js":
            self._file(resource_path("studio_ui", "brief-assistant.js"))
        elif url.path in ("/autopilot.js", "/understand.js", "/learned.js"):
            self._file(resource_path("studio_ui", url.path.lstrip("/")))
        elif url.path == "/api/health":
            self._json({"app": "VideoStudio", "version": __version__, "pid": os.getpid()})
        elif url.path == "/api/capabilities":
            trained = read_json(app_data_dir() / "learned_keep.json", {}) or {}
            self._json({"brief_assistant": True, "speech_model": "pretrained", "audience_prediction": False,
                        "adaptation": "saved preferences and ratings", "learned_keep": {
                            "trained": bool(trained.get("ok")), "validated_enabled": bool(trained.get("enabled")),
                            "role": "advisory_only", "recordings": trained.get("recordings", 0),
                            "held_out_metrics": trained.get("model", {})}})
        elif url.path == "/api/projects":
            self._json({"projects": list_projects(), "workspace": str(workspace())})
        elif url.path == "/api/project":
            s = project_summary(Path(q.get("root", [""])[0]))
            self._json(s or {"error": "not found"}, 200 if s else 404)
        elif url.path == "/api/doctor":
            self._json(doctor())
        elif url.path == "/api/ai":
            self._json(ai_status(q.get("fresh", ["0"])[0] == "1"))
        elif url.path == "/api/system":
            self._json(system_report(q.get("fresh", ["0"])[0] == "1"))
        elif url.path == "/api/machine":
            self._json(machine_report(q.get("fresh", ["0"])[0] == "1"))
        elif url.path == "/cutout-quality.js":
            self._file(resource_path("studio_ui", "cutout-quality.js"))
        elif url.path == "/api/cutout/quality":
            self._json(cutout_quality(q.get("file", [""])[0]))
        elif url.path == "/api/machine/check":
            self._json(machine_check(q.get("task", ["edit"])[0], q.get("seconds", [""])[0], q.get("short_side", [""])[0]))
        elif url.path == "/api/library":
            from video_studio import library

            self._json(library.catalog())
        elif url.path == "/api/update":
            self._json(update_report())
        elif url.path == "/api/share":
            info = share_info(q.get("root", [""])[0])
            self._json(info, 404 if info.get("error") else 200)
        elif url.path == "/api/styles":
            from video_studio.engines import styledna

            out = []
            for n in styledna.available():
                p = styledna.load(n) or {}
                out.append({"name": n, "count": p.get("count", 0), "summary": styledna.describe(p) if p.get("shot_len") else ""})
            self._json({"styles": out})
        elif url.path == "/api/learned":   # learning from your edits: real counts from the signal file
            self._json(signals.summary())
        elif url.path == "/api/taste":
            self._json({"taste": [r for r in memory.taste_table() if r["bucket"] == "global"], "prefs": memory.prefs()})
        elif url.path.startswith("/api/jobs/"):
            job = JOBS.get(url.path.rsplit("/", 1)[-1])
            if not job:
                return self._json({"error": "no such job"}, 404)
            self._json(job.info(int(q.get("since", ["0"])[0])))
        elif url.path == "/api/jobs":
            self._json({"jobs": [j.info(len(j.lines)) for j in JOBS.values()]})
        elif url.path == "/media":
            path = _safe_path(q.get("path", [""])[0])
            if not path:
                return self._json({"error": "not allowed"}, 403)
            if q.get("download", [""])[0] == "1" and not self.headers.get("Range", "").replace("bytes=0-", ""):
                signals.on_download(path)   # learning: a downloaded film is a film the person kept
            self._file(path)
        else:
            self._json({"error": "not found"}, 404)

    def _file(self, path: Path) -> None:
        size = path.stat().st_size
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        if rng and (m := re.match(r"bytes=(\d*)-(\d*)", rng)):
            if m.group(1):
                start = int(m.group(1))
            if m.group(2):
                end = min(int(m.group(2)), size - 1)
            self.send_response(HTTPStatus.PARTIAL_CONTENT)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        if path.suffix.lower() in (".js", ".html", ".css"):
            self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(end - start + 1))
        if self.path.find("download=1") != -1:
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.end_headers()
        with path.open("rb") as fh:
            fh.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    chunk = fh.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
                pass

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorised():
            return self._json({"error": "This page is from an earlier app session. Reopen Takebloom, then try again."}, 403)
        url = urlparse(self.path)
        if url.path == "/api/upload":
            name = re.sub(r"[^\w.\- ]", "_", unquote(self.headers.get("X-File-Name", "clip.mp4")))[:120]
            folder = workspace() / "_uploads"
            try:
                result = copy_local_video(self.rfile, int(self.headers.get("Content-Length") or 0), folder, name)
            except ValueError as exc:
                return self._json({"error": str(exc)}, 400)
            except OSError as exc:
                return self._json({"error": "Could not save a local working copy. " + str(exc)}, 507)
            return self._json(result)
        body = self._body()
        if url.path == "/api/brief/interpret":
            return self._json(brief_assistance(body))
        if url.path == "/api/brief/understand":   # instant, local: what the prompt will set, and what cannot be done
            from video_studio import understand

            seen = understand.understand(str(body.get("brief", ""))[:4000])
            # what Takebloom learned from your edits fills the choices the prompt and Fine-tune leave open
            seen["learned"] = signals.suggest(seen["options"], body.get("options") or {}, bool(body.get("has_song"))) or []
            return self._json(seen)
        if url.path == "/api/pick-files":
            return self._json(pick_local_files(), 410)
        if url.path == "/api/local-file":
            result = register_local_video(str(body.get("path", "")))
            return self._json(result, 400 if result.get("error") else 200)
        if url.path == "/api/jobs":
            return self._json(start_job(body))
        if url.path.startswith("/api/jobs/") and url.path.endswith("/cancel"):
            job = JOBS.get(url.path.split("/")[3])
            if job and job.proc and job.proc.poll() is None:
                job.status = "stopping"
                kill_tree(job.proc)
                if job.kind == "edit":
                    signals.on_stopped(job.root)
            return self._json({"ok": True})
        if url.path == "/api/feedback":
            data = read_json(Path(body["root"]) / "project.json") or {}
            result = memory.add_feedback(data.get("id", ""), int(body["rating"]), body.get("notes", ""))
            signals.on_rating(body["root"], int(body["rating"]), body.get("notes", ""))
            return self._json(result)
        if url.path == "/api/signal":   # learning: share actions happen in the window (Windows share, site pages)
            root = _project_root(str(body.get("root", "")))
            if not root or body.get("kind") != "share":
                return self._json({"error": "Only share actions on a project are recorded here."}, 400)
            return self._json({"ok": bool(signals.on_share(root, str(body.get("via", ""))))})
        if url.path == "/api/learned/forget":
            return self._json(signals.forget())
        if url.path == "/api/refine":
            return self._json(refine(body))
        if url.path == "/api/ai/login":
            return self._json(ai_login(str(body.get("key", ""))))
        if url.path == "/api/ai/install":
            return self._json(ai_install(str(body.get("key", ""))))
        if url.path == "/api/ai/use":
            return self._json(ai_use(str(body.get("key", "")), str(body.get("model", ""))))
        if url.path == "/api/reveal":
            result = reveal(str(body.get("path", "")))
            return self._json(result, 400 if result.get("error") else 200)
        if url.path == "/api/cover":
            result = save_covers(str(body.get("root", "")), body.get("images") or {})
            return self._json(result, 400 if result.get("error") else 200)
        if url.path == "/api/preview":
            result = preview_copy(str(body.get("path", "")))
            return self._json(result, 400 if result.get("error") else 200)
        if url.path.startswith("/api/update/"):
            result = update_action(url.path.rsplit("/", 1)[-1], body)
            self._json(result, 400 if result.get("error") else 200)
            if result.get("exit"):  # the installer or rollback swaps the program files once this copy has closed
                threading.Timer(1.5, lambda: os._exit(0)).start()
            return None
        if url.path == "/api/setup":
            return self._json(_tool_job("setup", ["setup", "--yes"], "Setup"))
        if url.path == "/api/learn-style":
            folder, name = str(body.get("folder", "")).strip().strip('"'), str(body.get("name", "")).strip()
            if not Path(folder).exists():
                return self._json({"error": "That folder or file does not exist."})
            if not re.fullmatch(r"[\w .-]{1,40}", name):
                return self._json({"error": "Use a short name (letters, numbers, - or _)."})
            return self._json(_tool_job("learn", ["learn-style", folder, "--name", name], f"Learn style {name}"))
        self._json({"error": "not found"}, 404)


# -------------------------------------------------------------------- jobs

def _opts(o: dict) -> list[str]:
    args = []
    for key, flag in (("aspect", "--aspect"), ("quality", "--quality"), ("look", "--look"), ("captions", "--captions"),
                      ("voice", "--voice"), ("music", "--music"), ("pacing", "--pacing"), ("ai", "--ai"), ("style", "--style")):
        if o.get(key):
            args += [flag, str(o[key])]
    if o.get("fx") and o.get("fx") != "auto":
        args += ["--fx", o["fx"]]
    if o.get("like"):
        args += ["--like", str(o["like"])]
    if o.get("caption_script") and o.get("caption_script") != "auto":
        args += ["--caption-script", o["caption_script"]]
    if o.get("target"):
        args += ["--target", str(o["target"])]
    for key, flag in (("template", "--template"), ("grade", "--grade"), ("transition", "--transition")):
        if o.get(key) and o.get(key) != "auto":
            args += [flag, str(o[key])]
    if o.get("no_ai"):
        args.append("--no-ai")
    if o.get("platforms"):
        args += ["--platform", *o["platforms"]]
    return args


def _system_report() -> dict:
    from video_studio import setup

    cached = read_json(app_data_dir() / "system_report.json", None)
    if not cached:
        s = setup.scan()
        cached = {"scan": s, "report": setup.report(s)}
        write_json(app_data_dir() / "system_report.json", cached)
    s = cached["scan"]
    missing = (not (s.get("tools", {}).get("ffmpeg", {}).get("ok"))) or (not s.get("python")) or not all((s.get("packages") or {}).values()) or not all((s.get("models") or {}).values())
    return {**cached, "missing": bool(missing)}


def system_report(fresh: bool = False) -> dict:
    if fresh:
        from video_studio import setup
        def rescan():
            s = setup.scan()
            write_json(app_data_dir() / "system_report.json", {"scan": s, "report": setup.report(s)})
            return _system_report()
        return background_check("system", rescan, fresh=True)
    if (app_data_dir() / "system_report.json").is_file():
        return {**_system_report(), "checking": False}
    return background_check("system", _system_report)


# ------------------------------------------------------------- sharing, covers, previews

# Upload pages. Takebloom never signs in or posts for you: posting by itself needs each site's own developer
# approval and your sign-in. It opens the site's upload page with the caption ready to paste.
SHARE_SITES = [
    {"key": "instagram", "name": "Instagram", "url": "https://www.instagram.com/", "how": "Press + (Create), choose Reel, pick the video. Paste the caption. Set the cover with Edit cover > Add from camera roll."},
    {"key": "youtube", "name": "YouTube", "url": "https://www.youtube.com/upload", "how": "Choose the video. Paste the title and description. Under Thumbnail press Upload file and pick the YouTube cover (long videos only; Shorts pick a frame)."},
    {"key": "tiktok", "name": "TikTok", "url": "https://www.tiktok.com/tiktokstudio/upload", "how": "Choose the video, paste the caption, then Edit cover to upload the cover picture."},
    {"key": "facebook", "name": "Facebook", "url": "https://www.facebook.com/reels/create/", "how": "Add the video, paste the caption and pick the cover."},
    {"key": "linkedin", "name": "LinkedIn", "url": "https://www.linkedin.com/feed/?shareActive=true", "how": "Press the video icon, add the video and paste the caption."},
    {"key": "x", "name": "X", "url": "https://x.com/compose/post", "how": "Add the video with the picture icon and paste the post (280 characters)."},
    {"key": "whatsapp", "name": "WhatsApp", "url": "https://web.whatsapp.com/", "how": "Use Share with apps on this PC if WhatsApp is installed, or open a chat and attach the video."},
]
COVER_SHAPES = autopilot.COVER_SHAPES


def _project_root(raw: str) -> Path | None:
    """A real project folder the person can reach from Studio (its project.json is servable)."""
    if not raw:
        return None
    root = Path(raw)
    return root if _safe_path(str(root / "project.json")) else None


def _post_sections(text: str) -> dict[str, str]:
    out, key, buf = {}, "", []
    for line in text.splitlines():
        if line.startswith("== ") and line.endswith(" =="):
            if key:
                out[key] = "\n".join(buf).strip()
            key, buf = line.strip("= ").lower(), []
        elif key and not line.startswith(("Cover image:", "Made locally")):
            buf.append(line)
    if key:
        out[key] = "\n".join(buf).strip()
    return out


def share_info(raw_root: str) -> dict:
    """What to share: the right video file, covers, and a ready caption per site (all made locally)."""
    root = _project_root(raw_root)
    if not root:
        return {"error": "Project not found."}
    data = read_json(root / "project.json", {}) or {}
    exports = root / "exports"
    files = sorted(exports.glob("*")) if exports.is_dir() else []
    master = root / "output" / "master.mp4"
    vertical = next((f for f in files if f.name.endswith(("_reels.mp4", "_shorts.mp4", "_tiktok.mp4"))), None)
    wide = next((f for f in files if f.name.endswith("_youtube.mp4")), None)
    video = vertical or wide or (master if master.exists() else None)
    post = next((f for f in files if f.name.endswith("_post.txt")), None)
    sections = _post_sections(post.read_text(encoding="utf-8")) if post else {}
    plan = read_json(root / "build" / "edit_plan.json") or read_json(root / "build" / "plan.json") or {}
    title = (plan.get("hook_text") or plan.get("title") or data.get("name") or "My video").strip()
    tags = next((ln for sec in sections.values() for ln in sec.splitlines() if ln.startswith("#")), "")
    insta = sections.get("instagram") or f"{title.title()}\n\n{tags}".strip()
    yt = sections.get("youtube", "")
    yt_title = next((ln.split(": ", 1)[1] for ln in yt.splitlines() if ln.startswith("Title")), title.title())
    yt_desc = "\n".join(ln.removeprefix("Description: ") for ln in yt.splitlines() if not ln.startswith("Title")).strip() or tags
    short = f"{title.title()} {' '.join(tags.split()[:5])}".strip()
    captions = {
        "instagram": insta, "youtube": f"{yt_title}\n\n{yt_desc}".strip(), "tiktok": sections.get("tiktok") or short,
        "facebook": insta, "linkedin": insta.replace(" 👇", ""), "x": short[:280], "whatsapp": title.title(),
    }
    covers = {k: str(exports / f"cover_{k}.jpg") for k in COVER_SHAPES if (exports / f"cover_{k}.jpg").is_file()}
    thumb = next((str(f) for f in files if f.name.endswith("_thumbnail.jpg")), "")
    return {"root": str(root), "title": title, "video": str(video) if video else "", "wide_video": str(wide) if wide else "",
            "master": str(master) if master.exists() else "", "thumbnail": thumb, "covers": covers,
            "captions_file": next((str(f) for f in files if f.suffix == ".srt"), ""),
            "sites": [{**site, "caption": captions[site["key"]]} for site in SHARE_SITES],
            "shapes": {k: list(v) for k, v in COVER_SHAPES.items()}}


def reveal(raw: str) -> dict:
    """Show a project file selected in File Explorer, ready to drag into an upload page."""
    path = _safe_path(raw)
    if not path:
        return {"error": "That file is not part of your Takebloom projects."}
    if os.name == "nt":
        subprocess.Popen(["explorer", f"/select,{path}"], creationflags=0x08000000)
    return {"ok": True}


def save_covers(raw_root: str, images: dict) -> dict:
    """Save the covers drawn in the window (JPEG data URLs) next to the exports. The reel cover also becomes the
    project's thumbnail; the automatic one is kept as *_thumbnail_auto.jpg."""
    import base64

    root = _project_root(raw_root)
    if not root:
        return {"error": "Project not found."}
    exports = root / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    saved = {}
    for key, url in images.items():
        if key not in COVER_SHAPES or not isinstance(url, str) or not url.startswith("data:image/jpeg;base64,"):
            continue
        raw = base64.b64decode(url.split(",", 1)[1], validate=True)
        if len(raw) > 15 * 1024 * 1024 or raw[:3] != b"\xff\xd8\xff":
            return {"error": "That cover image is not a JPEG picture."}
        dest = exports / f"cover_{key}.jpg"
        dest.write_bytes(raw)
        saved[key] = str(dest)
    if not saved:
        return {"error": "No cover was received."}
    signals.on_covers(root)   # learning: covers made by hand mean the film is going out
    (exports / autopilot.MARK).unlink(missing_ok=True)   # the person's own covers: autopilot never replaces them
    if "reel" in saved or "youtube" in saved:
        main = Path(saved.get("reel") or saved["youtube"])
        thumb = next(iter(sorted(exports.glob("*_thumbnail.jpg"))), None)
        if thumb:
            auto = thumb.with_name(thumb.stem + "_auto.jpg")
            if not auto.exists():
                shutil.copy2(thumb, auto)
            shutil.copy2(main, thumb)
    return {"ok": True, "covers": saved}


PREVIEWS: dict[str, dict] = {}
PLAYABLE = {"h264", "vp8", "vp9", "av1"}


def preview_copy(raw: str) -> dict:
    """A small H.264 copy for the preview when this window cannot show the picture (HEVC iPhone video, ProRes...).
    Made once in the background, kept in the projects folder (_previews), never used for the edit itself."""
    from video_studio.engines import ff

    path = _safe_path(raw)
    if not path:
        return {"error": "That file is not available to the preview."}
    st = path.stat()
    key = hashlib.sha1(f"{path}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()[:16]
    dest = workspace() / "_previews" / f"{key}.mp4"
    with CHECK_LOCK:
        known = PREVIEWS.get(key)
        if dest.is_file():
            return {"ready": True, "path": str(dest)}
        if known:
            return dict(known)
        try:
            codec = (ff.stream(ff.probe(path), "video") or {}).get("codec_name", "")
        except Exception:  # noqa: BLE001 - an unreadable file still gets one try at a preview copy
            codec = ""
        if codec in PLAYABLE:
            return {"ready": True, "path": str(path), "codec": codec, "playable": True}
        PREVIEWS[key] = {"making": True, "codec": codec}

    def worker():
        part = dest.with_suffix(".part.mp4")
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            ff.ff(["-y", "-i", str(path), "-map", "0:v:0", "-map", "0:a:0?", "-vf", "scale='if(gt(iw,ih),-2,min(540,iw))':'if(gt(iw,ih),min(540,ih),-2)'", "-c:v", "libx264",
                   "-preset", "veryfast", "-crf", "27", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k",
                   "-movflags", "+faststart", str(part)])
            part.replace(dest)
            with CHECK_LOCK:
                PREVIEWS[key] = {"ready": True, "path": str(dest)}
        except Exception as exc:  # noqa: BLE001
            part.unlink(missing_ok=True)
            with CHECK_LOCK:
                PREVIEWS[key] = {"error": "A preview copy could not be made. Takebloom can still edit this video. " + str(exc)[:160]}

    threading.Thread(target=worker, daemon=True).start()
    return {"making": True, "codec": codec}


def machine_report(fresh: bool = False) -> dict:
    """What this PC can make. The first scan (encoder tests) takes a few seconds, so it runs in the background."""
    from video_studio import machine

    if not fresh and (app_data_dir() / "machine.json").is_file():
        try:
            return {**machine.assess(), "checking": False}
        except Exception:  # noqa: BLE001 - fall through to a fresh background scan
            pass
    return background_check("machine", lambda: machine.assess(machine.scan(fresh=fresh)), fresh=fresh)


def cutout_quality(file: str = "") -> dict:
    """1.9.0 best-quality cut-out: model download facts, and a time estimate for the chosen video measured on this PC."""
    from video_studio import machine, matting

    try:
        seconds = fps = None
        if file and Path(file).is_file():
            from video_studio import cutout

            facts = cutout._video_facts(Path(file))
            seconds, fps = facts["duration"], facts["fps_value"]
        data = matting.info(seconds, fps)
        data["check"] = machine.check("cutout_best", seconds=seconds)
        return data
    except Exception as exc:  # noqa: BLE001 - advice only
        return {"error": str(exc)}


def machine_check(task: str, seconds: str = "", short_side: str = "") -> dict:
    from video_studio import machine

    def num(text):
        try:
            return float(text) if text else None
        except ValueError:
            return None

    try:
        side = num(short_side)
        return machine.check(task, seconds=num(seconds), short_side=int(side) if side else None)
    except Exception as exc:  # noqa: BLE001 - advice only
        return {"level": "ok", "messages": [], "error": str(exc)}


def update_report() -> dict:
    from video_studio import updates

    info = background_check("update", updates.check)
    try:
        previous = updates.previous_versions()
    except Exception:  # noqa: BLE001
        previous = []
    return {"current": __version__, "info": info, "download": updates.status(), "notice": updates.notice(),
            "auto": bool(config.get("updates.check")), "previous": previous, "running_jobs": running_jobs()}


def update_action(action: str, body: dict) -> dict:
    from video_studio import updates

    if action == "check":
        return background_check("update", lambda: updates.check(fresh=True), fresh=True)
    if action == "download":
        return updates.download()
    if action == "cancel":
        return updates.cancel()
    if action == "install":
        result = updates.install(running_jobs())
        return {**result, "exit": True} if result.get("ok") else result
    if action == "rollback":
        return updates.rollback(running_jobs())
    if action == "auto":
        return updates.set_auto(bool(body.get("on")))
    return {"error": "Unknown update action."}


def _tool_job(kind: str, args: list[str], name: str) -> dict:
    root = workspace() / "_tools" / f"{kind}-{secrets.token_hex(3)}"
    root.mkdir(parents=True, exist_ok=True)
    job = Job(kind, args, root, name)
    JOBS[job.id] = job
    job.start()
    return {"job": job.id, "root": str(root)}


QUICK_NAMES = {"photos": "Photos ", "restyle": "Restyle ", "cutout": "Cut-out "}
QUICK_OPTIONS = {"aspect", "quality", "look", "captions", "voice", "music", "template", "grade", "transition"}


def running_jobs() -> int:
    return sum(1 for j in JOBS.values() if j.status in ("running", "stopping") and j.proc and j.proc.poll() is None)


def _quick_args(mode: str, body: dict, o: dict, root: Path, name: str) -> list[str] | dict:
    """Command line for photos, restyle, cut-out and AI clip jobs (each has its own short option set)."""
    files = [f for f in body.get("files", []) if Path(f).is_file()]
    common = ["--out", str(root), "--name", name]
    if not files:
        return {"error": "Add at least one picture." if mode == "photos" else "Add a video file."}
    if mode == "cutout":
        bg = str(o.get("background") or "blur")
        args = ["cutout", files[0], *common, "--background", bg]
        if o.get("colour"):
            args += ["--colour", str(o["colour"])]
        if bg in ("image", "video") and len(files) > 1:
            args += ["--image" if bg == "image" else "--background-video", files[1]]
        if o.get("cutout_quality") == "best":  # 1.9.0 best-quality cut-out (download only when ticked)
            args.append("--best")
            if o.get("allow_download"):
                args.append("--allow-download")
        return args
    opts = _opts({k: v for k, v in o.items() if k in QUICK_OPTIONS})
    if o.get("no_music"):
        opts.append("--no-music")
    if mode == "photos":
        from video_studio import pictures

        dropped = pictures.split_media(files)
        files = [str(p) for p in dropped["pictures"]]
        if dropped["music"]:
            opts += ["--music-file", str(dropped["music"][0])]
        args = ["photos", *files, *common, *opts]
        for key, flag in (("seconds", "--seconds"), ("total", "--total")):
            if o.get(key):
                args += [flag, str(float(o[key]))]
        titles = [t.strip() for t in str(body.get("titles") or "").splitlines()]
        if any(titles):
            titles_file = root.parent / f".{root.name}_titles.txt"
            titles_file.write_text("\n".join(titles), encoding="utf-8")
            args += ["--titles-file", str(titles_file)]
            if o.get("narrate"):
                args.append("--narrate")
        return args
    args = ["restyle", files[0], *common, *opts, "--fit", "crop" if o.get("fit") == "crop" else "fill"]
    if o.get("speed") and float(o["speed"]) != 1.0:
        args += ["--speed", str(float(o["speed"]))]
    for key in ("steady", "denoise", "sharpen", "smooth_slow"):
        if o.get(key):
            args.append("--" + key.replace("_", "-"))
    if o.get("background") in ("blur", "colour", "image"):
        args += ["--background", o["background"]]
        if o["background"] == "image" and len(files) > 1:
            args += ["--background-image", files[1]]
    return args


def start_job(body: dict) -> dict:
    mode = body.get("mode", "edit")
    if mode not in ("edit", *QUICK_NAMES):
        return {"error": "Takebloom edits the videos you shot. Script videos and AI clips are no longer part of the app."}
    o = dict(body.get("options", {}) or {})
    if mode == "edit":
        # one drop for everything: clips are edited, pictures become cutaways, an audio file becomes the music.
        # Pictures with no clip at all make a photo film instead.
        from video_studio import pictures

        dropped = pictures.split_media([f for f in body.get("files", []) if Path(f).is_file()])
        if not dropped["videos"] and dropped["pictures"]:
            mode = "photos"
    if not o.get("quality"):
        from video_studio import machine

        o["quality"] = machine.default_quality()
    name = (body.get("name") or "").strip() or QUICK_NAMES.get(mode, "Edit " if mode == "edit" else "Video ") + time.strftime("%d %b %H%M")
    root = workspace() / f"{slug(name)}-{secrets.token_hex(2)}"
    if mode in QUICK_NAMES:
        root.parent.mkdir(parents=True, exist_ok=True)
        args = _quick_args(mode, body, o, root, name)
        if isinstance(args, dict):
            return args
        if mode == "restyle":
            signals.on_restyle(o)
        job = Job(mode, args, root, name)
        JOBS[job.id] = job
        job.start()
        return {"job": job.id, "root": str(root)}
    if not dropped["videos"]:
        return {"error": "Add at least one video clip. Pictures and a music file can go with it."}
    files = [str(p) for group in ("videos", "pictures", "music") for p in dropped[group]][:60]
    # the prompt sets every choice the person left on Auto (understand.py); their Fine-tune choices win
    from video_studio import understand

    seen = understand.understand(body.get("brief", ""))
    chosen = dict(body.get("options") or {})   # what the person picked themselves (before machine defaults)
    if not chosen.get("aspect_chosen"):
        chosen.pop("aspect", None)
    from_prompt = understand.edit_args(seen["options"], chosen)
    if "--aspect" in from_prompt:
        o.pop("aspect", None)
    if dropped["music"] and "--no-music" in from_prompt:
        from_prompt.remove("--no-music")   # a song in the drop is the music; "no music" then means no generated bed
    # learned from earlier edits: only for choices the prompt and Fine-tune leave open, only when confident (signals.py)
    learned = signals.suggest(seen["options"], chosen, bool(dropped["music"])) or []
    from_prompt += signals.flags(learned)
    args = ["edit", *files, "--brief-file", str(_write_brief(root, body.get("brief", ""))), "--out", str(root), "--name", name,
            *_opts(o), *from_prompt]
    root.mkdir(parents=True, exist_ok=True)
    write_json(root / "understood.json", {"brief": body.get("brief", ""), **seen, "learned": learned, "applied": from_prompt})
    signals.on_edit_started(root, files, seen["options"], chosen, learned)
    job = Job(mode, args, root, name)
    JOBS[job.id] = job
    job.start()
    return {"job": job.id, "root": str(root)}


def pick_local_files() -> dict:
    """Retired: HTTP requests must never wait for an external modal dialog."""
    return {"error": "Use Browse files in the current Takebloom window, or add an existing local file path."}


def register_local_video(raw: str) -> dict:
    try:
        path = Path(raw.strip().strip('"')).expanduser().resolve()
    except (OSError, ValueError, RuntimeError):
        return {"error": "That file path is not valid. Paste the full path to the original video."}
    if not path.is_file():
        return {"error": "That file was not found. Paste the full path to the original video."}
    if path.suffix.lower() not in (".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".mpeg", ".mpg", ".mts", ".m2ts"):
        return {"error": "Choose a video file such as MP4, MOV or MKV."}
    try:
        size = path.stat().st_size
        with path.open('rb') as source:
            source.read(1)
    except OSError:
        return {"error": "The video cannot be read. Check its file permissions or choose another copy."}
    with LOCK:
        SELECTED_MEDIA.add(path)
    return {"file": {"path": str(path), "name": path.name, "size": size}}

def _write_brief(root: Path, brief: str) -> Path:
    root.parent.mkdir(parents=True, exist_ok=True)
    path = root.parent / f".{root.name}_brief.txt"
    path.write_text(brief or "Make it engaging, tight and professional.", encoding="utf-8")
    return path


REFINES = {
    "shorter": {"edit": {"target_scale": 0.75}, "from": "plan"},
    "longer": {"edit": {"target_scale": 1.3}, "from": "plan"},
    "faster": {"settings": {"pacing": "fast"}, "from": "plan"},
    "calmer": {"settings": {"pacing": "relaxed", "music": "calm", "motion": "subtle"}, "from": "plan"},
    "energy": {"settings": {"pacing": "fast", "music": "uplifting", "motion": "dynamic"}, "from": "plan"},
    "no_music": {"settings": {"music_enabled": False}, "from": "render"},
    "new_look": {"settings": {"look": ""}, "from": "plan"},
    "less_motion": {"settings": {"motion": "subtle"}, "options": {"fx": "clean"}, "from": "plan"},
    "no_captions": {"settings": {"captions": "none", "captions_enabled": False}, "from": "plan"},
}


def refine(body: dict) -> dict:
    """One-click re-edits: adjust settings, then resume from the right stage (cached work is reused)."""
    root = Path(body["root"])
    proj = Project(root)
    data = proj.data
    if body.get("action") == "review" and data.get("kind") == "edit":
        keep = [str(k) for k in body.get("keep") or []]
        if not keep and data.get("edit_options", {}) and not body.get("hook_text") and not body.get("opening"):
            return {"error": "Keep at least one line."}
        opts = data.setdefault("edit_options", {})
        if keep:
            opts["manual_keep"] = keep
        if body.get("hook_text") is not None:
            opts["hook_override"] = str(body["hook_text"])[:40]
        if body.get("opening"):
            opts["opening"] = str(body["opening"])
        signals.on_review(root, keep, str(body.get("opening") or ""), body.get("hook_text"))   # keep/cut labels
        proj.save(data)
        job = Job("edit", ["resume", "--from", "plan", "--project", str(root)], root, data["name"] + " (your changes)")
        JOBS[job.id] = job
        job.start()
        return {"job": job.id, "root": str(root)}
    if body.get("action") == "resume":
        # Continue an interrupted project from its first unfinished stage. One job per project at a time.
        for running in list(JOBS.values()):
            if running.root == root and running.status in ("running", "stopping"):
                return {"job": running.id, "root": str(root)}
        job = Job(data.get("kind", "edit"), ["resume", "--project", str(root)], root, data["name"])
        JOBS[job.id] = job
        job.start()
        return {"job": job.id, "root": str(root)}
    action = REFINES.get(body.get("action", ""))
    if not action:
        return {"error": "unknown action"}
    signals.on_refine(root, body.get("action", ""))   # learning: what was asked for, and what it replaced
    if "settings" in action:
        data["settings"].update(action["settings"])
    if "options" in action and data.get("kind") == "edit":
        data.setdefault("edit_options", {}).update(action["options"])
    if "edit" in action and data.get("kind") == "edit":
        opts = data.setdefault("edit_options", {})
        m = read_json(root / "build" / "edit_manifest.json", {}) or {}
        base = opts.get("target") or m.get("total") or 60
        opts["target"] = round(base * action["edit"]["target_scale"], 1)
    proj.save(data)
    stage = action["from"] if data.get("kind") == "edit" else {"plan": "analyze", "render": "produce"}[action["from"]]
    job = Job(data.get("kind", "edit"), ["resume", "--from", stage, "--project", str(root)], root, data["name"] + " (refine)")
    JOBS[job.id] = job
    job.start()
    return {"job": job.id, "root": str(root)}


def serve(port: int = 7860, open_browser: bool = True, workspace_dir: str | None = None, **_) -> int:
    if workspace_dir:
        config.set_value("workspace", str(Path(workspace_dir).resolve()))
    for candidate in range(port, port + 20):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", candidate), Handler)
            break
        except OSError:
            continue
    else:
        console.error("No free port found.")
        return 1
    url = f"http://127.0.0.1:{candidate}/"
    try:
        from video_studio import updates

        updates.startup(running_jobs)
    except Exception:  # noqa: BLE001 - updates must never stop the app from opening
        pass
    console.ok(f"Studio running at {url}  (local only; Ctrl+C to stop)")
    console.info(f"Projects folder: {workspace()}")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        for job in JOBS.values():
            if job.proc and job.proc.poll() is None:
                kill_tree(job.proc)
        httpd.server_close()
    return 0
