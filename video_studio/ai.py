"""AI provider hub: drives the official AI CLIs in headless mode.

Rules this module follows:
- Sign-in always happens through the provider's own CLI login command; we only
  ever read the CLI's yes/no status output. Token or credential files are never
  opened, copied or transmitted (for Gemini/Qwen only the *existence* of the
  CLI's sign-in file is checked, because those CLIs have no status command).
- AI is called only for planning, creative decisions and quality review.
  Responses are cached by prompt hash so re-runs cost nothing.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from video_studio import config
from video_studio.util import ToolError, cache_dir, console, read_json, run, run_interactive, sha, which, write_json

ROAMING_NPM = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "npm"


@dataclass
class Status:
    installed: bool = False
    signed_in: bool = False
    path: str = ""
    version: str = ""
    account: str = ""
    problem: str = ""
    hint: str = ""


@dataclass
class Provider:
    key: str
    label: str
    exe: str
    install_hint: str
    login_cmd: list[str] = field(default_factory=list)
    supports_images: bool = False

    def path(self) -> Path | None:
        return which(self.exe, [ROAMING_NPM, Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama"])

    def model(self) -> str:
        return (config.get("ai.models") or {}).get(self.key, "")

    # -- status -------------------------------------------------------------
    def status(self) -> Status:
        st = Status()
        exe = self.path()
        if not exe:
            st.problem, st.hint = "not installed", self.install_hint
            return st
        st.installed, st.path = True, str(exe)
        try:
            self._fill_status(exe, st)
        except (ToolError, OSError) as exc:
            st.problem = f"status check failed: {exc}"
        if st.installed and not st.signed_in and not st.problem:
            st.problem = "not signed in"
        health = (read_json(_health_file(), {}) or {}).get(self.key)
        if st.signed_in and health:  # kept until a later call succeeds
            st.problem = "last call failed: " + health["error"][:160]
            if _account_refused(health["error"]) or "not eligible" in health["error"].lower():
                st.hint = "Google no longer serves this personal login in Gemini CLI. Free option: create a key at aistudio.google.com and set GEMINI_API_KEY."
        if not st.signed_in and not st.hint:
            st.hint = f"video-studio ai login {self.key}"
        return st

    def _fill_status(self, exe: Path, st: Status) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    # -- login --------------------------------------------------------------
    def login(self) -> int:
        exe = self.path()
        if not exe:
            raise ToolError(f"{self.label} is not installed.", self.install_hint)
        cmd = [str(exe), *self.login_cmd]
        console.info(f"Opening {self.label}'s own sign-in: {' '.join(cmd)}")
        console.info("Video Studio never sees your password or tokens; the CLI stores its own login.")
        return run_interactive(cmd)

    # -- run ----------------------------------------------------------------
    def ask(self, prompt: str, timeout: float, images: list[Path] | None = None) -> str:  # pragma: no cover
        raise NotImplementedError


class Claude(Provider):
    def __init__(self) -> None:
        super().__init__("claude", "Claude Code", "claude", "video-studio install claude", ["auth", "login"], True)

    def _fill_status(self, exe: Path, st: Status) -> None:
        res = run([str(exe), "--version"], check=False, timeout=30)
        st.version = (res.stdout or "").strip().split(" ")[0]
        res = run([str(exe), "auth", "status", "--json"], check=False, timeout=30)
        try:
            data = json.loads(res.stdout or "{}")
        except ValueError:
            data = {}
        st.signed_in = data.get("loggedIn") is True
        if st.signed_in:
            st.account = " / ".join(str(x) for x in (data.get("authMethod"), data.get("subscriptionType")) if x)

    def ask(self, prompt: str, timeout: float, images: list[Path] | None = None) -> str:
        cmd = [str(self.path()), "-p", "--output-format", "json", "--no-session-persistence",
               "--disallowedTools", "Bash", "Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch", "Task"]
        if self.model():
            cmd += ["--model", self.model()]
        text = prompt
        workdir = Path(tempfile.mkdtemp(prefix="vs-ai-"))
        if images:
            # Claude Code reads images with its Read tool; give it read-only access to copies.
            for i, img in enumerate(images):
                target = workdir / f"image_{i}{img.suffix}"
                target.write_bytes(img.read_bytes())
            text += "\n\nImages to inspect (use the Read tool): " + ", ".join(f"image_{i}{p.suffix}" for i, p in enumerate(images))
        res = run(cmd, input_text=text, timeout=timeout, cwd=workdir, check=False)
        if res.returncode != 0:
            raise ToolError(f"Claude Code exited {res.returncode}: {(res.stderr or res.stdout)[-400:]}")
        try:
            data = json.loads(res.stdout)
            if data.get("is_error"):
                raise ToolError(f"Claude Code error: {str(data.get('result'))[:300]}")
            return str(data.get("result", ""))
        except ValueError:
            return res.stdout


class Codex(Provider):
    def __init__(self) -> None:
        super().__init__("codex", "OpenAI Codex", "codex", "video-studio install codex", ["login"], True)

    def _fill_status(self, exe: Path, st: Status) -> None:
        res = run([str(exe), "--version"], check=False, timeout=30)
        st.version = (res.stdout or "").strip().split()[-1] if res.stdout else ""
        res = run([str(exe), "login", "status"], check=False, timeout=30)
        text = (res.stdout or "") + (res.stderr or "")
        st.signed_in = res.returncode == 0 and "logged in" in text.lower()
        if st.signed_in:
            st.account = text.strip().splitlines()[-1].replace("Logged in using", "").strip()

    def ask(self, prompt: str, timeout: float, images: list[Path] | None = None) -> str:
        workdir = Path(tempfile.mkdtemp(prefix="vs-ai-"))
        out_file = workdir / "answer.txt"
        cmd = [str(self.path()), "exec", "--skip-git-repo-check", "--sandbox", "read-only", "--ephemeral",
               "-C", str(workdir), "--output-last-message", str(out_file)]
        if self.model():
            cmd += ["-m", self.model()]
        for img in images or []:
            cmd += ["-i", str(img)]
        res = run(cmd, input_text=prompt, timeout=timeout, cwd=workdir, check=False)
        if out_file.exists() and out_file.stat().st_size:
            return out_file.read_text(encoding="utf-8", errors="replace")
        raise ToolError(f"Codex exited {res.returncode}: {(res.stderr or res.stdout)[-400:]}")


class Gemini(Provider):
    settings_dir = ".gemini"

    def __init__(self, key: str = "gemini", label: str = "Google Gemini CLI", exe: str = "gemini") -> None:
        super().__init__(key, label, exe, f"video-studio install {key}", [], True)

    def _fill_status(self, exe: Path, st: Status) -> None:
        res = run([str(exe), "--version"], check=False, timeout=60)
        st.version = (res.stdout or "").strip().splitlines()[-1] if res.stdout else ""
        home = Path.home() / self.settings_dir
        settings = read_json(home / "settings.json", {}) or {}
        auth = (((settings.get("security") or {}).get("auth") or {}).get("selectedType")) or settings.get("selectedAuthType")
        env_key = any(os.environ.get(k) for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "DASHSCOPE_API_KEY", "OPENAI_API_KEY") if self.key == "qwen" or k.startswith("G"))
        # Existence check only: the credential file is never opened.
        has_oauth = (home / "oauth_creds.json").exists()
        st.signed_in = bool(env_key or (auth and (has_oauth or "api" in str(auth).lower() or "vertex" in str(auth).lower())))
        if st.signed_in:
            st.account = str(auth or "API key")
        else:
            st.hint = f"Run `{self.exe}` once in a terminal and pick 'Login with Google' (or set an API key)."

    def login(self) -> int:
        console.info(f"{self.label} signs in from its own first-run screen. Choose the Google/OAuth option, finish in the browser, then type /quit.")
        return super().login()

    def ask(self, prompt: str, timeout: float, images: list[Path] | None = None) -> str:
        workdir = Path(tempfile.mkdtemp(prefix="vs-ai-"))
        text = prompt
        if images:
            for i, img in enumerate(images):
                (workdir / f"image_{i}{img.suffix}").write_bytes(img.read_bytes())
            text += "\n\nImages to inspect: " + " ".join(f"@image_{i}{p.suffix}" for i, p in enumerate(images))
        cmd = [str(self.path()), "-p", "Answer the request above.", "--output-format", "json", "--approval-mode", "plan"]
        if self.model():
            cmd += ["-m", self.model()]
        res = run(cmd, input_text=text, timeout=timeout, cwd=workdir, check=False)
        raw = res.stdout or ""
        if res.returncode != 0 and _account_refused(raw + (res.stderr or "")):
            raise ToolError(ACCOUNT_REFUSED)
        try:
            start = raw.index("{")
            data = json.loads(raw[start:])
            if data.get("error"):
                raise ToolError(f"{self.label}: {data['error']}")
            return str(data.get("response", ""))
        except ValueError:
            if res.returncode != 0:
                raise ToolError(f"{self.label} exited {res.returncode}: {(res.stderr or raw)[-400:]}")
            return raw


class Qwen(Gemini):
    settings_dir = ".qwen"

    def __init__(self) -> None:
        super().__init__("qwen", "Qwen Code", "qwen")


class Ollama(Provider):
    def __init__(self) -> None:
        super().__init__("ollama", "Ollama (local models)", "ollama", "video-studio install ollama", [], False)

    def _fill_status(self, exe: Path, st: Status) -> None:
        res = run([str(exe), "--version"], check=False, timeout=30)
        st.version = (res.stdout or res.stderr or "").strip().split()[-1] if (res.stdout or res.stderr) else ""
        res = run([str(exe), "list"], check=False, timeout=30)
        models = [line.split()[0] for line in (res.stdout or "").splitlines()[1:] if line.strip()]
        st.signed_in = res.returncode == 0 and bool(models)
        st.account = ", ".join(models[:4])
        if not models:
            st.problem = "no local model pulled"
            st.hint = "ollama pull qwen2.5:7b   (free, runs offline; ~4.7 GB)"

    def login(self) -> int:
        console.info("Ollama needs no account. Pull a model once, e.g. `ollama pull qwen2.5:7b`.")
        return 0

    def model(self) -> str:
        chosen = super().model()
        if chosen:
            return chosen
        st = self.status()
        return st.account.split(", ")[0] if st.account else ""

    def ask(self, prompt: str, timeout: float, images: list[Path] | None = None) -> str:
        model = self.model()
        if not model:
            raise ToolError("No Ollama model available.", "ollama pull qwen2.5:7b")
        res = run([str(self.path()), "run", model], input_text=prompt, timeout=timeout, check=False)
        if res.returncode != 0:
            raise ToolError(f"Ollama exited {res.returncode}: {(res.stderr or '')[-300:]}")
        return res.stdout


class OpenCode(Provider):
    """OpenCode CLI with its FREE models (no paid plan needed). Tries the next free model when one is busy or retired."""

    FREE = ["opencode/deepseek-v4.1-flash-free", "opencode/glm-5.3-flash-free", "opencode/minimax-m3.1-flash-free",
            "opencode/mimo-v2.6-flash-free", "opencode/big-pickle"]

    def __init__(self) -> None:
        super().__init__("opencode", "OpenCode (free models)", "opencode", "video-studio install opencode", [], True)

    def _fill_status(self, exe: Path, st: Status) -> None:
        res = run([str(exe), "--version"], check=False, timeout=60)
        st.version = (res.stdout or "").strip().splitlines()[-1] if res.stdout else ""
        st.signed_in = res.returncode == 0  # the free models need no account; credentials are never read
        st.account = self.model() or self.FREE[0]

    def login(self) -> int:
        console.info("OpenCode's free models need no sign-in. Optional: `opencode auth login` for other providers.")
        return 0

    def ask(self, prompt: str, timeout: float, images: list[Path] | None = None) -> str:
        workdir = Path(tempfile.mkdtemp(prefix="vs-ai-"))  # empty folder: the agent has nothing to read or change
        files = []
        for i, img in enumerate(images or []):
            dst = workdir / f"image_{i}{img.suffix}"
            dst.write_bytes(img.read_bytes())
            files += ["-f", str(dst)]
        models = [self.model()] if self.model() else list(self.FREE)
        last = ""
        for model in models:
            # The prompt goes in on stdin: a command-line argument is cut at the first line break on Windows.
            res = run([str(self.path()), "run", "--pure", "-m", model, "--format", "json", *files],
                      input_text=prompt, timeout=timeout, cwd=workdir, check=False)
            text = []
            for line in (res.stdout or "").splitlines():
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("type") == "text":
                    text.append(ev.get("part", {}).get("text", ""))
                elif ev.get("type") == "error":
                    last = str(ev)[:300]
            if text:
                return "".join(text)
            last = last or (res.stderr or res.stdout or "")[-300:]
        raise ToolError(f"OpenCode free models did not answer: {last}")


def _health_file() -> Path:
    return cache_dir() / "ai_health.json"


def _record_health(key: str, error: str | None) -> None:
    health = read_json(_health_file(), {}) or {}
    if error is None:
        health.pop(key, None)
    else:
        health[key] = {"error": error, "at": time.time()}
    write_json(_health_file(), health)


PAID = {"claude", "codex"}
ACCOUNT_REFUSED = ("Google says this sign-in is not eligible to use Gemini CLI here (an account rule, not a Takebloom fault). "
                   "Pick another AI in AI editor, or set a free GEMINI_API_KEY from aistudio.google.com.")
HARD_FAILURE_HOURS = 6


def _account_refused(text: str) -> bool:
    low = text.lower()
    return "ineligible" in low or "antigravity.google" in low


def _hard_failure(key: str) -> bool:
    """The provider's last call failed for an account reason in the last few hours (not a timeout or a bad answer)."""
    health = (read_json(_health_file(), {}) or {}).get(key) or {}
    error = str(health.get("error", "")).lower()
    recent = time.time() - float(health.get("at", 0)) < HARD_FAILURE_HOURS * 3600
    return recent and any(k in error for k in ("not eligible", "ineligible", "antigravity", "not logged", "unauthor"))
_PROVIDERS: list[Provider] | None = None


def providers() -> list[Provider]:
    global _PROVIDERS
    if _PROVIDERS is None:
        _PROVIDERS = [OpenCode(), Claude(), Codex(), Gemini(), Qwen(), Ollama()]
    return _PROVIDERS


def get_provider(key: str) -> Provider:
    for p in providers():
        if p.key == key:
            return p
    raise ToolError(f"Unknown AI provider '{key}'.", "Choose one of: " + ", ".join(p.key for p in providers()))


# ---------------------------------------------------------------------- director


class Director:
    """Chooses a provider, enforces the per-project call budget, caches answers."""

    def __init__(self, preferred: str | None = None, budget: int | None = None, usage_file: Path | None = None) -> None:
        self.preferred = preferred or config.get("ai.provider")
        self.budget = budget if budget is not None else int(config.get("ai.max_calls_per_project"))
        self.usage_file = usage_file
        self.calls_made = 0
        self._status_cache: dict[str, Status] = {}
        if usage_file:
            self.calls_made = int((read_json(usage_file, {}) or {}).get("calls", 0))

    @property
    def enabled(self) -> bool:
        return self.preferred != "none"

    def _status(self, p: Provider) -> Status:
        if p.key not in self._status_cache:
            self._status_cache[p.key] = p.status()
        return self._status_cache[p.key]

    def _candidates(self) -> list[Provider]:
        if not self.enabled:
            return []
        order: list[str] = []
        if self.preferred and self.preferred != "auto":
            order.append(self.preferred)
        if self.preferred == "auto" or config.get("ai.allow_fallback"):
            order += [k for k in config.get("ai.fallback") if k not in order]
        if not config.get("ai.paid_fallback"):  # free first: a paid plan is spent only when the user picked it
            order = [k for k in order if k not in PAID or k == self.preferred]
        if len(order) > 1:  # an account that was just refused is tried last, so a working one answers first
            order = [k for k in order if not _hard_failure(k)] + [k for k in order if _hard_failure(k)]
        return [get_provider(k) for k in order]

    def chain(self) -> list[Provider]:
        return [p for p in self._candidates() if self._status(p).signed_in]

    def available(self) -> bool:
        return any(self._status(p).signed_in for p in self._candidates())

    def ask_json(self, purpose: str, prompt: str, *, images: list[Path] | None = None, use_cache: bool = True) -> tuple[dict | list, str]:
        """Ask for a JSON answer. Returns (data, provider_key). Raises ToolError if nobody can answer."""
        chain = self._candidates()
        if not chain:
            raise ToolError("No signed-in AI CLI available.", "Run `video-studio ai status`, then `video-studio ai login <provider>`.")
        img_key = [str(p.stat().st_size) + p.name for p in images or []]
        errors = []
        for provider in chain:
            if not self._status(provider).signed_in:
                continue
            key = sha([provider.key, provider.model(), prompt, img_key], 24)
            cached = cache_dir("ai") / f"{key}.json"
            if use_cache and cached.exists():
                data = read_json(cached)
                if data is not None:
                    console.info(f"  AI ({provider.label}) {purpose}: cached answer reused, 0 calls spent")
                    return data["answer"], provider.key
            if self.calls_made >= self.budget:
                raise ToolError(f"AI call budget for this project ({self.budget}) is used up.",
                                "Raise it with `video-studio config set ai.max_calls_per_project 8`, or continue with --no-ai.")
            console.step(f"AI director ({provider.label}) - {purpose}")
            started = time.time()
            try:
                raw = provider.ask(prompt, float(config.get("ai.timeout")), images)
                self._count(provider.key, purpose, len(prompt), len(raw))
                data = extract_json(raw)
                _record_health(provider.key, None)
                write_json(cached, {"answer": data, "provider": provider.key, "purpose": purpose, "at": time.time()})
                console.ok(f"{provider.label} answered in {time.time() - started:.0f}s")
                return data, provider.key
            except (ToolError, ValueError) as exc:
                _record_health(provider.key, _short_error(str(exc)))
                errors.append(f"{provider.label}: {exc}")
                console.warn(f"{provider.label} could not answer ({str(exc)[:160]}); trying the next provider.")
        raise ToolError("All AI providers failed:\n  " + "\n  ".join(errors), "Check `video-studio ai status` or continue with --no-ai.")

    def _count(self, provider: str, purpose: str, sent: int, received: int) -> None:
        self.calls_made += 1
        if self.usage_file:
            usage = read_json(self.usage_file, {}) or {}
            usage["calls"] = self.calls_made
            usage.setdefault("log", []).append({"provider": provider, "purpose": purpose, "chars_sent": sent,
                                                "chars_received": received, "at": time.strftime("%Y-%m-%d %H:%M:%S")})
            write_json(self.usage_file, usage)


def _short_error(text: str) -> str:
    for line in text.splitlines():
        low = line.lower()
        if any(k in low for k in ("not eligible", "ineligible", "antigravity", "quota", "unauthor", "rate limit", "not logged", "error:")):
            return line.strip()[:240]
    return text.strip().splitlines()[0][:240] if text.strip() else "unknown error"


def extract_json(text: str) -> dict | list:
    """Pull the first JSON object/array out of a model reply (tolerates code fences and prose)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    pairs = [(text.find(o), o, c) for o, c in (("{", "}"), ("[", "]")) if text.find(o) >= 0]
    for start, opener, closer in sorted(pairs):  # whichever structure opens first is the reply
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
    raise ValueError("reply contained no JSON")
