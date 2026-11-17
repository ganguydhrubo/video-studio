"""Bridge to the optional ML worker (helpers/ml_helper.py) running on system Python."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from video_studio import detect, resources
from video_studio.util import ToolError, read_json, resource_path, run

HELPER = resource_path("helpers", "ml_helper.py")


def _temp(prefix: str) -> Path:
    import os

    fd, name = tempfile.mkstemp(prefix=prefix, suffix=".json")
    os.close(fd)  # Windows keeps an open handle locked otherwise
    return Path(name)


def call(cmd: str, timeout: float = 1800, **kwargs) -> dict:
    py = detect.system_python()
    if not py:
        raise ToolError("No system Python for ML helpers.", "Install Python 3.10+ (python.org) to enable ML features.")
    out = _temp(f"vs-{cmd}-")
    args = [str(py), "-E", "-P", str(HELPER), cmd, "--out", str(out)]
    for key, value in kwargs.items():
        args += [f"--{key.replace('_', '-')}", str(value)]
    res = run(args, timeout=timeout, check=False, low_priority=True)
    data = read_json(out, None)
    out.unlink(missing_ok=True)
    if not data:
        raise ToolError(f"ML helper '{cmd}' failed: {(res.stderr or '')[-600:]}")
    if not data.get("ok"):
        raise ToolError(f"ML helper '{cmd}' needs: {data.get('missing')}",
                        f"py -m pip install --user {data.get('missing')}")
    return data


def available(module: str) -> bool:
    py = detect.system_python()
    if not py:
        return False
    res = run([str(py), "-E", "-P", "-c", f"import importlib.util,sys;sys.exit(0 if importlib.util.find_spec('{module}') else 1)"],
              check=False, timeout=60)
    return res.returncode == 0


def models_dir() -> Path:
    """Shared model store (large downloads are never duplicated per profile)."""
    import os

    base = os.environ.get("VIDEO_STUDIO_MODELS") or str(Path(os.environ.get("LOCALAPPDATA", Path.home())) / "VideoStudio" / "models")
    path = Path(base)
    path.mkdir(parents=True, exist_ok=True)
    return path


def kokoro_dir() -> Path:
    return models_dir() / "kokoro"


def kokoro_ready() -> bool:
    d = kokoro_dir()
    return (d / "voices-v1.0.bin").exists() and any((d / n).exists() for n in ("kokoro-v1.0.int8.onnx", "kokoro-v1.0.onnx"))         and available("kokoro_onnx")


def best_whisper_model(requested: str = "auto") -> str:
    """Pick the most accurate faster-whisper model already on disk (no surprise downloads)."""
    if requested and requested != "auto":
        return requested
    hub = hf_hub()
    for name, folder in (("large-v3-turbo", "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo"),
                         ("large-v3", "models--Systran--faster-whisper-large-v3"),
                         ("medium", "models--Systran--faster-whisper-medium"),
                         ("small", "models--Systran--faster-whisper-small"),
                         ("base", "models--Systran--faster-whisper-base")):
        if model_on_disk(hub / folder):
            return name
    return "small"


def hf_hub() -> Path:
    """Where faster-whisper keeps its models: the Hugging Face cache (HF_HUB_CACHE / HF_HOME are honoured)."""
    import os

    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"])
    return Path(os.environ.get("HF_HOME") or (Path.home() / ".cache" / "huggingface")) / "hub"


def model_on_disk(folder: Path) -> bool:
    """A model download that finished (its model.bin is in a snapshot), not just a started one."""
    try:
        return any(folder.glob("snapshots/*/model.bin"))
    except OSError:
        return False


def transcribe(audio: Path, model: str = "small", language: str = "") -> dict:
    """Word-level transcript of any audio/video file (decoded to 16 kHz mono by FFmpeg first)."""
    from video_studio.engines import ff

    pcm = Path(tempfile.mkdtemp(prefix="vs-asr-")) / "audio16k.wav"
    ff.ff(["-i", str(audio), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(pcm)])
    try:
        from video_studio import vocab

        return call("transcribe", audio=pcm, model=model, language=language, threads=resources.render_threads(),
                    vocab="|".join(vocab.words()))
    finally:
        pcm.unlink(missing_ok=True)


def kokoro(items: list[dict], voice: str, speed: float, lang: str = "a") -> dict:
    tmp = _temp("vs-kokoro-")
    tmp.write_text(json.dumps(items), encoding="utf-8")
    try:
        return call("kokoro", items=tmp, voice=voice, speed=speed, lang=lang, model_dir=kokoro_dir())
    finally:
        tmp.unlink(missing_ok=True)
