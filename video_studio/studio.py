"""Studio: the visual interface, served locally by the CLI (127.0.0.1 only, no cloud).

Jobs run as `video-studio` subprocesses, so the UI and the CLI share one engine
and one project format. The page is a single file (studio_ui/index.html).
"""

from __future__ import annotations

import json
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

from video_studio import __version__, config, memory
from video_studio.project import Project, slug
from video_studio.util import FROZEN, app_data_dir, console, kill_tree, read_json, resource_path, write_json

TOKEN = secrets.token_urlsafe(24)
JOBS: dict[str, dict] = {}
SELECTED_MEDIA: set[Path] = set()
LOCK = threading.Lock()
CHECK_LOCK = threading.Lock()
CHECKS: dict[str, dict] = {}


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
}


def engine_cmd() -> list[str]:
    if FROZEN:
        return [sys.executable]
    return [sys.executable, "-m", "video_studio"]


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
        self.finished = time.time()
        self.status = "done" if code == 0 else ("warning" if code == 2 else ("stopped" if self.status == "stopping" else "failed"))
        self._line(f"[exit {code}]")

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
            "hook_text": plan.get("hook_text", ""), "mode": plan.get("mode", "speech"),
            "understood": _understood(root),
            "effects": ((read_json(root / "build" / "style_plan.json", {}) or {}).get("reasons") or [])[:20],
            "music": (read_json(root / "build" / "edit_manifest.json", {}) or {}).get("music"),
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
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")
            self.end_headers()
            self.wfile.write(body)
        elif url.path == "/brand-logo.png":
            self._file(resource_path("assets", "takebloom-logo.png"))
        elif url.path == "/favicon.ico":
            self._file(resource_path("assets", "app-icon.ico"))
        elif url.path == "/api/health":
            self._json({"app": "VideoStudio", "version": __version__, "pid": os.getpid()})
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
        elif url.path == "/api/styles":
            from video_studio.engines import styledna

            out = []
            for n in styledna.available():
                p = styledna.load(n) or {}
                out.append({"name": n, "count": p.get("count", 0), "summary": styledna.describe(p) if p.get("shot_len") else ""})
            self._json({"styles": out})
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
            return self._json({"ok": True})
        if url.path == "/api/feedback":
            data = read_json(Path(body["root"]) / "project.json") or {}
            result = memory.add_feedback(data.get("id", ""), int(body["rating"]), body.get("notes", ""))
            return self._json(result)
        if url.path == "/api/refine":
            return self._json(refine(body))
        if url.path == "/api/ai/login":
            return self._json(ai_login(str(body.get("key", ""))))
        if url.path == "/api/ai/install":
            return self._json(ai_install(str(body.get("key", ""))))
        if url.path == "/api/ai/use":
            return self._json(ai_use(str(body.get("key", "")), str(body.get("model", ""))))
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


def _tool_job(kind: str, args: list[str], name: str) -> dict:
    root = workspace() / "_tools" / f"{kind}-{secrets.token_hex(3)}"
    root.mkdir(parents=True, exist_ok=True)
    job = Job(kind, args, root, name)
    JOBS[job.id] = job
    job.start()
    return {"job": job.id, "root": str(root)}


def start_job(body: dict) -> dict:
    mode = body.get("mode", "edit")
    o = body.get("options", {})
    name = (body.get("name") or "").strip() or ("Edit " if mode == "edit" else "Video ") + time.strftime("%d %b %H%M")
    root = workspace() / f"{slug(name)}-{secrets.token_hex(2)}"
    if mode == "edit":
        files = [f for f in body.get("files", []) if Path(f).is_file()]
        if not files:
            return {"error": "Add at least one video file."}
        args = ["edit", *files, "--brief-file", str(_write_brief(root, body.get("brief", ""))), "--out", str(root), "--name", name, *_opts(o)]
        args = [a for a in args if a != "--caption-script" or True]
    else:
        script = body.get("script", "").strip()
        if not script:
            return {"error": "Write or paste a script first."}
        root.mkdir(parents=True, exist_ok=True)
        (root.parent / f".{root.name}_script.txt").write_text(script, encoding="utf-8")
        args = ["make", str(root.parent / f".{root.name}_script.txt"), "--out", str(root), "--name", name,
                "--instructions", body.get("brief", "") or "", "--storyboard-frames",
                *_opts({k: v for k, v in o.items() if k not in ("fx", "like", "caption_script")})]
        if "--target" in args:
            i = args.index("--target")
            del args[i:i + 2]
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
        proj.save(data)
        job = Job("edit", ["resume", "--from", "plan", "--project", str(root)], root, data["name"] + " (your changes)")
        JOBS[job.id] = job
        job.start()
        return {"job": job.id, "root": str(root)}
    action = REFINES.get(body.get("action", ""))
    if not action:
        return {"error": "unknown action"}
    if "settings" in action:
        data["settings"].update(action["settings"])
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
