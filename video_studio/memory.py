"""Persistent project intelligence (SQLite, local only).

Three honest learning mechanisms, none of which retrain an AI model:

1. Taste model - a Bayesian (Beta-Bernoulli) Thompson-sampling bandit per
   creative decision (look, transitions, pacing, captions, music, motion,
   hook). Every rating/feedback updates the posteriors; future plans sample
   from them, so choices drift toward what the user approves while still
   exploring a little. Context-aware: posteriors are kept per (tone, aspect)
   bucket with backoff to the global posterior.
2. Retrieval memory - FTS5 full-text index over past projects, notes and
   feedback; the most relevant entries are given to the AI director.
3. Failure memory - error signatures with the fix that worked, so the
   pipeline applies a known fallback automatically next time.

Everything can be listed, corrected and deleted with `video-studio memory ...`.
"""

from __future__ import annotations

import json
import random
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from video_studio.util import app_data_dir

# Decision dimensions and their arms. Arms map to concrete renderer settings.
DIMENSIONS: dict[str, list[str]] = {
    "look": ["midnight", "studio", "sunset", "mono", "neon", "paper", "forest"],
    "transition": ["smooth", "punchy", "minimal", "cinematic"],
    "pacing": ["relaxed", "balanced", "fast"],
    "captions": ["bold_pop", "clean", "karaoke", "none"],
    "music": ["uplifting", "calm", "tense", "epic", "playful"],
    "motion": ["subtle", "dynamic", "maximal"],
    "hook": ["question", "bold_claim", "stat", "3d_reveal"],
}

FEEDBACK_KEYWORDS: dict[str, tuple[str, str, float]] = {
    # phrase -> (dimension, arm, reward)  reward 1 = liked, 0 = disliked
    "too fast": ("pacing", "fast", 0.0), "too slow": ("pacing", "relaxed", 0.0),
    "faster": ("pacing", "fast", 1.0), "slower": ("pacing", "relaxed", 1.0),
    "too busy": ("motion", "maximal", 0.0), "more energy": ("motion", "dynamic", 1.0),
    "calmer": ("motion", "subtle", 1.0), "no captions": ("captions", "none", 1.0),
    "captions too big": ("captions", "bold_pop", 0.0), "karaoke": ("captions", "karaoke", 1.0),
    "music too loud": ("music", "epic", 0.0), "love the 3d": ("hook", "3d_reveal", 1.0),
    "loved the 3d": ("hook", "3d_reveal", 1.0), "more 3d": ("hook", "3d_reveal", 1.0),
    "too dark": ("look", "midnight", 0.0), "brighter": ("look", "studio", 1.0),
    "cinematic": ("transition", "cinematic", 1.0), "jarring": ("transition", "punchy", 0.0),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects(
  id TEXT PRIMARY KEY, name TEXT, path TEXT, created REAL, updated REAL, status TEXT,
  aspect TEXT, tone TEXT, duration REAL, render_seconds REAL, qa_score REAL, rating INTEGER,
  decisions TEXT, summary TEXT);
CREATE TABLE IF NOT EXISTS feedback(
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, rating INTEGER, notes TEXT, created REAL);
CREATE TABLE IF NOT EXISTS preferences(
  key TEXT PRIMARY KEY, value TEXT, source TEXT, updated REAL);
CREATE TABLE IF NOT EXISTS taste(
  dimension TEXT, arm TEXT, bucket TEXT, alpha REAL, beta REAL, updated REAL,
  PRIMARY KEY(dimension, arm, bucket));
CREATE TABLE IF NOT EXISTS failures(
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, stage TEXT, signature TEXT, error TEXT,
  fix TEXT, fixed INTEGER DEFAULT 0, created REAL);
CREATE TABLE IF NOT EXISTS templates(
  name TEXT PRIMARY KEY, category TEXT, structure TEXT, score REAL, uses INTEGER, created REAL);
CREATE TABLE IF NOT EXISTS metrics(
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, stage TEXT, seconds REAL, detail TEXT, created REAL);
CREATE VIRTUAL TABLE IF NOT EXISTS recall USING fts5(kind, ref, text, tokenize='porter unicode61');
"""


def db_path() -> Path:
    return app_data_dir() / "memory.db"


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


# ----------------------------------------------------------------- projects


def upsert_project(pid: str, **fields: Any) -> None:
    fields = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in fields.items()}
    with connect() as c:
        row = c.execute("SELECT id FROM projects WHERE id=?", (pid,)).fetchone()
        fields["updated"] = time.time()
        if row:
            sets = ", ".join(f"{k}=?" for k in fields)
            c.execute(f"UPDATE projects SET {sets} WHERE id=?", (*fields.values(), pid))
        else:
            fields.setdefault("created", time.time())
            cols = ", ".join(["id", *fields])
            marks = ", ".join("?" * (len(fields) + 1))
            c.execute(f"INSERT INTO projects({cols}) VALUES({marks})", (pid, *fields.values()))
        if "summary" in fields and fields["summary"]:
            c.execute("DELETE FROM recall WHERE kind='project' AND ref=?", (pid,))
            c.execute("INSERT INTO recall(kind, ref, text) VALUES('project', ?, ?)", (pid, fields["summary"]))


def list_projects(limit: int = 50) -> list[dict]:
    with connect() as c:
        rows = c.execute("SELECT * FROM projects ORDER BY updated DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_project(pid: str) -> dict | None:
    with connect() as c:
        row = c.execute("SELECT * FROM projects WHERE id=? OR name=?", (pid, pid)).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------- feedback


def bucket_for(tone: str, aspect: str) -> str:
    return f"{(tone or 'any').lower()}|{aspect or 'any'}"


def add_feedback(pid: str, rating: int, notes: str = "") -> dict:
    """Store feedback and update the taste model. Returns what was learned."""
    rating = max(1, min(5, int(rating)))
    project = get_project(pid) or {}
    decisions = json.loads(project.get("decisions") or "{}")
    bucket = bucket_for(project.get("tone", ""), project.get("aspect", ""))
    reward = (rating - 1) / 4.0
    learned: list[str] = []
    with connect() as c:
        c.execute("INSERT INTO feedback(project_id, rating, notes, created) VALUES(?,?,?,?)",
                  (pid, rating, notes, time.time()))
        c.execute("UPDATE projects SET rating=?, updated=? WHERE id=?", (rating, time.time(), pid))
        for dim, arm in decisions.items():
            if dim in DIMENSIONS and arm in DIMENSIONS[dim]:
                _update(c, dim, arm, bucket, reward, weight=1.0)
                learned.append(f"{dim}={arm} {'+' if reward >= 0.5 else '-'}")
        low = notes.lower()
        for phrase, (dim, arm, r) in FEEDBACK_KEYWORDS.items():
            if phrase in low:
                _update(c, dim, arm, bucket, r, weight=2.0)
                learned.append(f"note '{phrase}' -> {dim}={arm} {'+' if r else '-'}")
        if notes:
            c.execute("INSERT INTO recall(kind, ref, text) VALUES('feedback', ?, ?)", (pid, f"rating {rating}/5: {notes}"))
    return {"bucket": bucket, "reward": reward, "learned": learned}


def _update(c: sqlite3.Connection, dim: str, arm: str, bucket: str, reward: float, weight: float) -> None:
    for b in (bucket, "global"):
        row = c.execute("SELECT alpha, beta FROM taste WHERE dimension=? AND arm=? AND bucket=?", (dim, arm, b)).fetchone()
        alpha, beta = (row["alpha"], row["beta"]) if row else (1.0, 1.0)
        alpha += weight * reward
        beta += weight * (1.0 - reward)
        c.execute("INSERT OR REPLACE INTO taste VALUES(?,?,?,?,?,?)", (dim, arm, b, alpha, beta, time.time()))


def taste_table() -> list[dict]:
    with connect() as c:
        rows = c.execute("SELECT * FROM taste ORDER BY dimension, bucket, alpha/(alpha+beta) DESC").fetchall()
    return [dict(r) | {"mean": r["alpha"] / (r["alpha"] + r["beta"])} for r in rows]


def choose(dimension: str, tone: str, aspect: str, prior: dict[str, float] | None = None,
           explore: bool = True, rng: random.Random | None = None) -> tuple[str, str]:
    """Thompson-sample an arm. `prior` gives the planner's own affinity (0..1) per arm."""
    rng = rng or random.Random()
    bucket = bucket_for(tone, aspect)
    arms = DIMENSIONS[dimension]
    with connect() as c:
        rows = c.execute("SELECT arm, bucket, alpha, beta FROM taste WHERE dimension=? AND bucket IN (?, 'global')",
                         (dimension, bucket)).fetchall()
    stats: dict[str, tuple[float, float]] = {}
    for r in rows:
        if r["bucket"] == bucket or r["arm"] not in stats:
            stats[r["arm"]] = (r["alpha"], r["beta"])
    best, best_score, reason = arms[0], -1.0, "default"
    for arm in arms:
        a, b = stats.get(arm, (1.0, 1.0))
        p = (prior or {}).get(arm, 0.5)
        # Planner affinity acts as a weak prior worth ~2 observations.
        a, b = a + 2 * p, b + 2 * (1 - p)
        score = rng.betavariate(a, b) if explore else a / (a + b)
        if score > best_score:
            best, best_score = arm, score
            reason = "learned from your ratings" if arm in stats else "planner suggestion"
    return best, reason


# ------------------------------------------------------------- preferences


def set_pref(key: str, value: str, source: str = "user") -> None:
    with connect() as c:
        c.execute("INSERT OR REPLACE INTO preferences VALUES(?,?,?,?)", (key, value, source, time.time()))


def prefs() -> dict[str, str]:
    with connect() as c:
        return {r["key"]: r["value"] for r in c.execute("SELECT key, value FROM preferences")}


def delete_pref(key: str) -> bool:
    with connect() as c:
        return c.execute("DELETE FROM preferences WHERE key=?", (key,)).rowcount > 0


# ---------------------------------------------------------------- recall


def recall(query: str, limit: int = 5) -> list[dict]:
    words = [w for w in re.findall(r"[A-Za-z0-9]{3,}", query.lower())][:24]
    if not words:
        return []
    match = " OR ".join(f'"{w}"' for w in words)
    with connect() as c:
        try:
            rows = c.execute("SELECT kind, ref, text, bm25(recall) AS score FROM recall WHERE recall MATCH ? "
                             "ORDER BY score LIMIT ?", (match, limit)).fetchall()
        except sqlite3.OperationalError:
            return []
    return [dict(r) for r in rows]


def add_note(text: str, ref: str = "user") -> None:
    with connect() as c:
        c.execute("INSERT INTO recall(kind, ref, text) VALUES('note', ?, ?)", (ref, text))


# --------------------------------------------------------------- failures

KNOWN_FIXES = [
    # (regex on error text, signature, fix key, explanation)
    (r"eevee|gpu|opengl|vulkan|EGL|gpu backend", "blender_gpu", "blender_engine=cycles", "Blender's GPU (EEVEE) renderer failed; use Cycles on the CPU."),
    (r"out of memory|MemoryError|bad_alloc|Cannot allocate", "out_of_memory", "quality=draft", "Ran out of memory; render at draft scale."),
    (r"No such filter: 'subtitles'|libass", "no_libass", "captions_burn=false", "FFmpeg has no subtitle burner; keep captions as a separate file."),
    (r"Chrome|chromium|browser", "hyperframes_browser", "motion_engine=canvas", "HyperFrames browser failed; use the built-in motion engine."),
    (r"SAPI|System.Speech|SpeechSynthesizer", "sapi_fail", "voice=none", "Windows voice failed; continue without a voiceover."),
]


def classify_failure(error: str) -> tuple[str, str, str]:
    for pattern, sig, fix, why in KNOWN_FIXES:
        if re.search(pattern, error, re.I):
            return sig, fix, why
    return "unknown:" + re.sub(r"\W+", "_", error[:60]).strip("_").lower(), "", ""


def record_failure(pid: str, stage: str, error: str) -> tuple[str, str]:
    sig, fix, _ = classify_failure(error)
    with connect() as c:
        c.execute("INSERT INTO failures(project_id, stage, signature, error, fix, created) VALUES(?,?,?,?,?,?)",
                  (pid, stage, sig, error[:4000], fix, time.time()))
    return sig, fix


def mark_fixed(signature: str) -> None:
    with connect() as c:
        c.execute("UPDATE failures SET fixed=1 WHERE signature=?", (signature,))


def learned_fixes() -> dict[str, str]:
    """Fixes that previously resolved a failure on this machine (applied automatically)."""
    with connect() as c:
        rows = c.execute("SELECT DISTINCT signature, fix FROM failures WHERE fixed=1 AND fix != ''").fetchall()
    return {r["signature"]: r["fix"] for r in rows}


def failures(limit: int = 20) -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM failures ORDER BY created DESC LIMIT ?", (limit,))]


# --------------------------------------------------------------- templates


def save_template(name: str, category: str, structure: list[dict], score: float) -> None:
    with connect() as c:
        row = c.execute("SELECT uses FROM templates WHERE name=?", (name,)).fetchone()
        c.execute("INSERT OR REPLACE INTO templates VALUES(?,?,?,?,?,?)",
                  (name, category, json.dumps(structure), score, (row["uses"] if row else 0), time.time()))


def best_template(category: str) -> dict | None:
    with connect() as c:
        row = c.execute("SELECT * FROM templates WHERE category=? ORDER BY score DESC, uses DESC LIMIT 1", (category,)).fetchone()
    if not row:
        return None
    data = dict(row)
    data["structure"] = json.loads(data["structure"])
    return data


def templates() -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT name, category, score, uses, created FROM templates ORDER BY score DESC")]


def use_template(name: str) -> None:
    with connect() as c:
        c.execute("UPDATE templates SET uses = uses + 1 WHERE name=?", (name,))


# ----------------------------------------------------------------- metrics


def add_metric(pid: str, stage: str, seconds: float, detail: dict | None = None) -> None:
    with connect() as c:
        c.execute("INSERT INTO metrics(project_id, stage, seconds, detail, created) VALUES(?,?,?,?,?)",
                  (pid, stage, seconds, json.dumps(detail or {}), time.time()))


def stage_estimates() -> dict[str, float]:
    """Median seconds-per-output-second per stage, from past runs on this machine."""
    with connect() as c:
        rows = c.execute("SELECT stage, seconds, detail FROM metrics ORDER BY created DESC LIMIT 400").fetchall()
    per: dict[str, list[float]] = {}
    for r in rows:
        dur = (json.loads(r["detail"] or "{}") or {}).get("output_seconds")
        if dur:
            per.setdefault(r["stage"], []).append(r["seconds"] / dur)
    return {k: sorted(v)[len(v) // 2] for k, v in per.items()}


# ----------------------------------------------------------------- deletion


def delete(kind: str, ref: str) -> int:
    tables = {"project": ("projects", "id"), "feedback": ("feedback", "id"), "pref": ("preferences", "key"),
              "failure": ("failures", "id"), "template": ("templates", "name")}
    if kind not in tables:
        raise KeyError(kind)
    table, col = tables[kind]
    with connect() as c:
        n = c.execute(f"DELETE FROM {table} WHERE {col}=?", (ref,)).rowcount
        if kind == "project":
            c.execute("DELETE FROM recall WHERE ref=?", (ref,))
            c.execute("DELETE FROM feedback WHERE project_id=?", (ref,))
    return n


def reset_taste(dimension: str | None = None) -> int:
    with connect() as c:
        if dimension:
            return c.execute("DELETE FROM taste WHERE dimension=?", (dimension,)).rowcount
        return c.execute("DELETE FROM taste").rowcount


def wipe() -> None:
    path = db_path()
    if path.exists():
        path.unlink()


def export_all() -> dict:
    with connect() as c:
        out = {}
        for table in ("projects", "feedback", "preferences", "taste", "failures", "templates", "metrics"):
            out[table] = [dict(r) for r in c.execute(f"SELECT * FROM {table}")]
        out["recall"] = [dict(r) for r in c.execute("SELECT kind, ref, text FROM recall")]
    return out
