"""Learning from every edit: what the person does with a film teaches Takebloom their taste. Local maths only.

Every action on an edit is one line in an append-only file in the app home (`edit_signals.jsonl`):

  kind      when                                             what it teaches (weight, see WEIGHTS)
  edit      an edit starts                                   choices from the prompt or Fine-tune: +1.0 for the value,
                                                             -1.0 shared out over the other values of that choice;
                                                             the same clips edited again with a different choice:
                                                             -1.0 for the value it replaced
  refine    a one-click refine ("Faster", "Less zoom", ...)  +1.5 for what was asked, -1.0 for what it replaced
  review    lines ticked or unticked on the transcript       keep/cut labels for the keep/cut model (learn.py)
  covers    covers saved by hand                             +0.25 for the film's choices
  download  the finished film downloaded                     +0.5 for the film's choices (once per film version,
  share     shared with apps / a site opened / folder shown    download and share count together)
  stopped   the person stopped the edit                      -0.5 for the choices Takebloom learned and applied
  rating    stars (and notes)                                (stars-1)/4 for the film's choices at 1.0; words in the
                                                             notes ("too fast", "no captions") count like a choice
  restyle   a restyle job with chosen options                +0.5 for each chosen value
  finished  an edit finished                                 nothing by itself; starts the keep/cut retrain check

The evidence is a Beta posterior per (choice, value), the same Beta-Bernoulli maths as the taste bandit in memory.py.
Old evidence fades: each signal counts GAMMA ** (edits since it) of its weight. The same signals also nudge the
bandit's own table (memory.db, "global" bucket, discounted by TABLE_GAMMA at every new edit), so choices Takebloom makes
by itself drift towards the person's taste too. Star ratings already update that table in memory.add_feedback and are
not counted twice there.

Suggestions (`suggest`) are used only when the prompt and Fine-tune say nothing about a choice, and only when the
confidence is real: posterior mean >= 0.7, 80% lower bound >= 0.5, clearly ahead of the next value, the person chose
it themselves at least once, chose or kept it in at least 2 of the last WINDOW edits, and their latest own choice
for it was the same. What the person writes or picks always wins.
"""

from __future__ import annotations

import functools
import hashlib
import json
import math
import threading
import time
from pathlib import Path

from video_studio.util import app_data_dir, console, read_json, write_json

FILE = "edit_signals.jsonl"
KEEP_FILE = "learned_keep.json"           # read by learn.keep_probabilities (advice shown on the Transcript tab)
REVIEWS_STATUS = "learned_keep_reviews.json"
SOURCE = "your reviews"

GAMMA = 0.85         # per edit: a signal from 5 edits ago counts 0.44, from 15 edits ago 0.09
TABLE_GAMMA = 0.97   # per edit, on the bandit table in memory.db (ratings included)
WINDOW = 6           # "you chose it in 4 of your last 6 edits"
MIN_MEAN, MIN_LOW, MIN_GAP, MIN_SUPPORT = 0.7, 0.5, 0.2, 2
MIN_REVIEWED = 2     # reviewed edits (different recordings) before the keep/cut model is trained

WEIGHTS = {"prompt": 1.0, "finetune": 1.0, "refine": 1.5, "refine_replaced": 1.0, "reedit_replaced": 1.0,
           "kept": 0.5, "covers": 0.25, "stopped": 0.5, "rating": 1.0, "rating_note": 1.0, "restyle": 0.5}


def _values() -> dict[str, list[str]]:
    from video_studio import library, memory

    return {"pacing": list(memory.DIMENSIONS["pacing"]), "captions": list(memory.DIMENSIONS["captions"]),
            "music": list(memory.DIMENSIONS["music"]), "music_enabled": ["on", "off"],
            "motion": list(memory.DIMENSIONS["motion"]), "transition": list(library.TRANSITION_FAMILIES),
            "grade": ["none", *library.GRADES], "fx": ["off", "clean", "bold", "viral"]}


LEARNABLE: dict[str, list[str]] = _values()
FLAG = {"pacing": "--pacing", "captions": "--captions", "music": "--music", "motion": "--motion",
        "transition": "--transition", "grade": "--grade", "fx": "--fx"}
NAMES = {"pacing": "Pace", "captions": "Captions", "music": "Music mood", "music_enabled": "Music", "motion": "Motion",
         "transition": "Transitions", "grade": "Colour", "fx": "Effects"}
TEMPLATE_SETS = {"pacing", "captions", "music", "music_enabled", "grade", "transition"}   # a style recipe sets these
LIKE_SETS = {"pacing", "transition", "motion", "fx"}                                       # "Edit like" sets these
REFINE_SETS = {"faster": {"pacing": "fast"}, "calmer": {"pacing": "relaxed", "music": "calm", "motion": "subtle"},
               "energy": {"pacing": "fast", "music": "uplifting", "motion": "dynamic"},
               "no_music": {"music_enabled": "off"}, "less_motion": {"motion": "subtle", "fx": "clean"},
               "no_captions": {"captions": "none"}}
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}

_LOCK = threading.Lock()
_RETRAIN = threading.Lock()


def path() -> Path:
    return app_data_dir() / FILE


def _safe(fn):
    """Learning must never stop an edit, a download or a page from working."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            console.debug(f"learning signal {fn.__name__} skipped: {exc}")
            return None
    return wrapper


# ------------------------------------------------------------------ the file

def events() -> list[dict]:
    p = path()
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue   # a half-written last line (power cut) is skipped, never fatal
        if isinstance(ev, dict) and ev.get("kind"):
            out.append(ev)
    return out


def record(kind: str, root: str | Path = "", **fields) -> dict:
    ev = {"v": 1, "t": round(time.time(), 3), "kind": kind, "root": str(root) if root else "", **fields}
    with _LOCK:
        with path().open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return ev


# ------------------------------------------------------------ what was chosen

def label(option: str, value: str) -> str:
    from video_studio import library, understand

    if option == "music_enabled":
        return "Music: none" if value == "off" else "Music: on"
    for _pattern, key, val, text in understand.RULES:
        if key == option and val == value:
            return text
    if option == "grade":
        return f"Colour: {(library.GRADES.get(value) or {}).get('name') or value}"
    if option == "transition":
        return f"Transitions: {((library.TRANSITION_FAMILIES.get(value) or {}).get('name') or value).lower()}"
    return f"{NAMES.get(option, option)}: {value.replace('_', ' ')}"


def _valid(option: str, value) -> bool:
    return option in LEARNABLE and isinstance(value, str) and value in LEARNABLE[option]


def explicit_choices(prompt_options: dict | None, chosen: dict | None) -> dict[str, tuple[str, str]]:
    """{choice: (value, "prompt" | "finetune")}. Fine-tune wins over the prompt, as in understand.merge."""
    out: dict[str, tuple[str, str]] = {}
    for key, value in (prompt_options or {}).items():
        if key == "no_music" and value:
            out["music_enabled"] = ("off", "prompt")
        elif _valid(key, value):
            out[key] = (value, "prompt")
    for key in ("pacing", "captions", "music", "motion", "transition", "grade", "fx"):
        value = (chosen or {}).get(key)
        if value not in (None, "", "auto") and _valid(key, value):
            out[key] = (value, "finetune")
    return out


def final_choices(root: str | Path) -> dict[str, str]:
    """What the finished film really used, read from the project's own files."""
    root = Path(root)
    d = (read_json(root / "build" / "creative.json", {}) or {}).get("decisions") or {}
    data = read_json(root / "project.json", {}) or {}
    s, eo = data.get("settings") or {}, data.get("edit_options") or {}
    out = {o: d[o] for o in ("pacing", "captions", "music", "motion", "transition") if _valid(o, d.get(o))}
    if _valid("grade", s.get("grade")):
        out["grade"] = s["grade"]
    fx = eo.get("fx")
    if fx == "auto" and data.get("kind") == "edit":
        from video_studio.engines import style

        fx = style.guess_intensity(data.get("brief", ""))
    if _valid("fx", fx):
        out["fx"] = fx
    if s.get("captions_enabled") is False:
        out["captions"] = "none"
    out["music_enabled"] = "off" if s.get("music_enabled") is False else "on"
    if out["music_enabled"] == "off":
        out.pop("music", None)
    return out


def _film_time(root: Path) -> float:
    m = root / "output" / "master.mp4"
    return round(m.stat().st_mtime, 1) if m.is_file() else 0.0


def _files_sig(files: list[str]) -> str:
    parts = []
    for f in sorted(files):
        p = Path(f)
        parts.append(f"{p.name}|{p.stat().st_size if p.is_file() else 0}")
    return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()[:16]


# -------------------------------------------------------- evidence per signal

def _choice(option: str, value: str, weight: float) -> list[tuple[str, str, float, float]]:
    """Choosing a value is a success for it, and one failure shared out over the values passed over."""
    others = [a for a in LEARNABLE[option] if a != value]
    return [(option, value, 1.0, weight)] + [(option, a, 0.0, weight / len(others)) for a in others]


def contributions(ev: dict) -> list[tuple[str, str, float, float]]:
    """(choice, value, reward 0..1, weight) for one signal. Duplicate approvals count nothing."""
    kind, out = ev.get("kind"), []
    if ev.get("dup"):
        return out
    if kind == "edit":
        for o, v in (ev.get("chosen") or {}).items():
            if _valid(o, v):
                out += _choice(o, v, WEIGHTS.get((ev.get("sources") or {}).get(o, "prompt"), 1.0))
        out += [(o, v, 0.0, WEIGHTS["reedit_replaced"]) for o, v in (ev.get("replaced") or {}).items() if _valid(o, v)]
    elif kind == "refine":
        for o, v in (ev.get("set") or {}).items():
            if _valid(o, v):
                out += _choice(o, v, WEIGHTS["refine"])
        out += [(o, v, 0.0, WEIGHTS["refine_replaced"]) for o, v in (ev.get("replaced") or {}).items() if _valid(o, v)]
    elif kind in ("download", "share", "covers"):
        w = WEIGHTS["covers" if kind == "covers" else "kept"]
        out += [(o, v, 1.0, w) for o, v in (ev.get("final") or {}).items() if _valid(o, v)]
    elif kind == "stopped":
        out += [(o, v, 0.0, WEIGHTS["stopped"]) for o, v in (ev.get("learned") or {}).items() if _valid(o, v)]
    elif kind == "rating":
        r = (max(1, min(5, int(ev.get("rating") or 3))) - 1) / 4.0
        out += [(o, v, r, WEIGHTS["rating"]) for o, v in (ev.get("final") or {}).items() if _valid(o, v)]
        for o, v in (ev.get("asked") or {}).items():
            if _valid(o, v):
                out += _choice(o, v, WEIGHTS["rating_note"])
        out += [(o, v, 0.0, WEIGHTS["rating_note"]) for o, v in (ev.get("disliked") or {}).items() if _valid(o, v)]
    elif kind == "restyle":
        for o, v in (ev.get("chosen") or {}).items():
            if _valid(o, v):
                out += _choice(o, v, WEIGHTS["restyle"])
    return out


def _approval_key(ev: dict) -> tuple | None:
    group = {"download": "kept", "share": "kept", "covers": "covers"}.get(ev.get("kind", ""))
    return (group, ev.get("root"), ev.get("film")) if group else None


def _nudge_table(contribs, discount: bool = False) -> None:
    """The same evidence, applied to the taste bandit that picks choices Takebloom makes by itself."""
    from video_studio import memory

    with memory.connect() as c:
        if discount:
            c.execute("UPDATE taste SET alpha = 1 + ? * (alpha - 1), beta = 1 + ? * (beta - 1)", (TABLE_GAMMA, TABLE_GAMMA))
        for o, v, r, w in contribs:
            if o in memory.DIMENSIONS and v in memory.DIMENSIONS[o]:
                row = c.execute("SELECT alpha, beta FROM taste WHERE dimension=? AND arm=? AND bucket='global'", (o, v)).fetchone()
                a, b = (row["alpha"], row["beta"]) if row else (1.0, 1.0)
                c.execute("INSERT OR REPLACE INTO taste VALUES(?,?,?,?,?,?)", (o, v, "global", a + w * r, b + w * (1 - r), time.time()))


def _add(kind: str, root: str | Path = "", discount: bool = False, table: bool = True, **fields) -> dict:
    ev = {"kind": kind, "root": str(root) if root else "", **fields}
    key = _approval_key(ev)
    if key and any(_approval_key(e) == key for e in events()):
        fields["dup"] = True
    ev = record(kind, root, **fields)
    if table:
        _nudge_table(contributions(ev), discount=discount)
    return ev


# ------------------------------------------------------------ signal sources

@_safe
def on_edit_started(root, files: list[str], prompt_options: dict, chosen: dict, learned: list[dict]) -> dict:
    explicit = explicit_choices(prompt_options, chosen)
    sig = _files_sig(files)
    replaced, previous = {}, ""
    for ev in reversed(events()):
        if ev.get("kind") == "edit" and ev.get("files_sig") == sig and ev.get("root") != str(root):
            previous = ev["root"]
            before = _latest_final(previous) or ev.get("chosen") or {}
            replaced = {o: before[o] for o, (v, _src) in explicit.items() if before.get(o) and before[o] != v}
            break
    return _add("edit", root, discount=True, files_sig=sig, chosen={o: v for o, (v, _s) in explicit.items()},
                sources={o: s for o, (_v, s) in explicit.items()}, learned={s["option"]: s["value"] for s in learned},
                replaces=previous, replaced=replaced)


def _latest_final(root: str) -> dict:
    for ev in reversed(events()):
        if ev.get("root") == root and ev.get("final"):
            return ev["final"]
    return final_choices(root) if root and Path(root, "project.json").is_file() else {}


@_safe
def on_finished(root, background: bool = True) -> dict:
    ev = _add("finished", root, table=False, final=final_choices(root), film=_film_time(Path(root)))
    if background:
        threading.Thread(target=maybe_retrain, daemon=True, name="tb-learn-keep").start()
    else:
        maybe_retrain()
    return ev


@_safe
def on_refine(root, action: str) -> dict:
    before = final_choices(root)
    sets = dict(REFINE_SETS.get(action, {}))
    replaced = {o: before[o] for o, v in sets.items() if before.get(o) and before[o] != v}
    if action == "new_look" and before.get("grade"):
        replaced["grade"] = before["grade"]
    return _add("refine", root, action=action, set=sets, replaced=replaced)


@_safe
def on_review(root, keep: list[str], opening: str = "", hook_text=None) -> dict:
    plan = read_json(Path(root) / "build" / "edit_plan.json", {}) or {}
    utts = {u["id"]: u for u in plan.get("utterances") or [] if "id" in u}
    keep = [k for k in keep if k in utts]
    auto = [k for k in plan.get("keep") or [] if k in utts]
    fields = {"opening": opening or "", "hook_changed": hook_text is not None and str(hook_text) != plan.get("hook_text", "")}
    if keep:
        fields.update(kept=keep, cut=[k for k in utts if k not in keep], spans=[[utts[k]["start"], utts[k]["end"]] for k in keep],
                      added=[k for k in keep if k not in auto], removed=[k for k in auto if k not in keep])
    return _add("review", root, table=False, **fields)


@_safe
def on_covers(root) -> dict:
    root = Path(root)
    return _add("covers", root, final=final_choices(root), film=_film_time(root))


def project_of(file: Path) -> Path | None:
    for parent in list(file.parents)[:3]:
        if (parent / "project.json").is_file():
            return parent
    return None


@_safe
def on_download(file) -> dict | None:
    file = Path(file)
    root = project_of(file)
    if not root or file.suffix.lower() not in VIDEO_EXT:
        return None   # covers, captions and other files are not a verdict on the film
    return _add("download", root, file=file.name, final=final_choices(root), film=_film_time(root))


@_safe
def on_share(root, via: str = "") -> dict:
    root = Path(root)
    return _add("share", root, via=str(via)[:40], final=final_choices(root), film=_film_time(root))


@_safe
def on_stopped(root) -> dict:
    learned = next((ev.get("learned") or {} for ev in reversed(events())
                    if ev.get("kind") == "edit" and ev.get("root") == str(root)), {})
    return _add("stopped", root, learned=learned)


# "too much X" in a note means less of X; these are read before the prompt rules, which would see only "X"
NOTE_PHRASES = {"less zoom": ("motion", "subtle"), "fewer zooms": ("motion", "subtle"), "too much zoom": ("motion", "subtle"),
                "too many zooms": ("motion", "subtle"), "too much motion": ("motion", "subtle"),
                "too many effects": ("fx", "clean"), "too much effects": ("fx", "clean"), "fewer effects": ("fx", "clean"),
                "less effects": ("fx", "clean"), "more effects": ("fx", "bold")}


def read_note(notes: str) -> tuple[dict, dict]:
    """(asked for, disliked) from a feedback note, e.g. "too fast" dislikes fast pacing, "no captions" asks for none."""
    from video_studio import memory, understand

    low, asked, disliked = " " + (notes or "").lower() + " ", {}, {}
    for phrase, (o, v) in NOTE_PHRASES.items():
        if phrase in low:
            asked.setdefault(o, v)
            low = low.replace(phrase, " ")
    for phrase, (o, v, r) in memory.FEEDBACK_KEYWORDS.items():
        if phrase in low and _valid(o, v):
            (asked if r else disliked).setdefault(o, v)
            low = low.replace(phrase, " ")
    for o, (v, _s) in explicit_choices(understand.understand(low)["options"], {}).items():
        asked.setdefault(o, v)
    return asked, {o: v for o, v in disliked.items() if asked.get(o) != v}


@_safe
def on_rating(root, rating: int, notes: str = "") -> dict:
    asked, disliked = read_note(notes)
    # the stars already update the bandit table in memory.add_feedback: not counted twice there
    return _add("rating", root, table=False, rating=int(rating), notes=(notes or "")[:300], final=final_choices(root),
                asked=asked, disliked=disliked, film=_film_time(Path(root)))


@_safe
def on_restyle(options: dict) -> dict:
    chosen = {o: v for o, (v, _s) in explicit_choices({}, options).items()}
    return _add("restyle", "", chosen=chosen) if chosen else {}


# ------------------------------------------------------------------- replay

def profile(evs: list[dict] | None = None) -> dict:
    """Replay the file: decayed Beta evidence per (choice, value), and one record per edit for honest counts."""
    evs = events() if evs is None else evs
    total = sum(1 for e in evs if e.get("kind") == "edit")
    seen, post, records, by_root, ever = 0, {}, [], {}, set()
    counts: dict[str, int] = {}
    for ev in evs:
        kind = ev.get("kind")
        counts[kind] = counts.get(kind, 0) + 1
        if kind == "edit":
            seen += 1
            rec = {"root": ev.get("root"), "chosen": dict(ev.get("chosen") or {}), "learned": dict(ev.get("learned") or {}),
                   "changed": set(ev.get("replaced") or {}), "kept": False, "rating": None, "stopped": False}
            records.append(rec)
            by_root[rec["root"]] = rec
            ever |= {(o, v) for o, v in rec["chosen"].items()}
        age = total - seen
        for o, v, r, w in contributions(ev):
            a, b = post.setdefault(o, {}).get(v, (1.0, 1.0))
            k = w * GAMMA ** age
            post[o][v] = (a + k * r, b + k * (1 - r))
        rec = by_root.get(ev.get("root"))
        if kind == "refine":
            ever |= {(o, v) for o, v in (ev.get("set") or {}).items()}
            if rec:
                rec["chosen"].update(ev.get("set") or {})
                rec["changed"] |= set(ev.get("set") or {}) | set(ev.get("replaced") or {})
        elif kind == "rating":
            ever |= {(o, v) for o, v in (ev.get("asked") or {}).items()}
            if rec:
                rec["rating"] = ev.get("rating")
                rec["chosen"].update(ev.get("asked") or {})
                rec["changed"] |= set(ev.get("disliked") or {})
        elif kind == "restyle":
            ever |= {(o, v) for o, v in (ev.get("chosen") or {}).items()}
        elif rec and kind in ("download", "share"):
            rec["kept"] = True
        elif rec and kind == "stopped":
            rec["stopped"] = True
    return {"post": post, "records": records, "ever": ever, "counts": counts, "edits": total, "events": len(evs)}


def _stats(a: float, b: float) -> tuple[float, float, float]:
    mean = a / (a + b)
    sd = math.sqrt(a * b / ((a + b) ** 2 * (a + b + 1)))
    return mean, mean - 1.2816 * sd, a + b - 2.0


def _accepted(rec: dict, option: str, value: str) -> bool:
    return (rec["learned"].get(option) == value and option not in rec["changed"] and not rec["stopped"]
            and (rec["kept"] or (rec["rating"] or 0) >= 4))


def _reason(chose: int, kept: int, n: int) -> str:
    edits = f"of your last {n} edits" if n > 1 else "of your edits"
    if chose and kept:
        return f"you chose it in {chose} and kept it in {kept} {edits}"
    return f"you chose it in {chose} {edits}" if chose else f"you kept it in {kept} {edits}"


def preference(option: str, prof: dict) -> dict | None:
    """The leading value for one choice, with its real counts and whether it is confident enough to use."""
    stats = {v: _stats(*prof["post"].get(option, {}).get(v, (1.0, 1.0))) for v in LEARNABLE[option]
             if not (option == "music_enabled" and v == "on")}
    seen = {v: s for v, s in stats.items() if s[2] > 0.05}
    if not seen:
        return None
    value = max(seen, key=lambda v: seen[v][0])
    mean, low, evidence = seen[value]
    runner = max((s[0] for v, s in stats.items() if v != value), default=0.5)
    window = prof["records"][-WINDOW:]
    chose = sum(1 for r in window if r["chosen"].get(option) == value)
    kept = sum(1 for r in window if r["chosen"].get(option) != value and _accepted(r, option, value))
    latest = next((r["chosen"][option] for r in reversed(window) if option in r["chosen"]), value)
    confident = (mean >= MIN_MEAN and low >= MIN_LOW and mean - runner >= MIN_GAP and (option, value) in prof["ever"]
                 and chose + kept >= MIN_SUPPORT and latest == value)
    reason = _reason(chose, kept, len(window))
    return {"option": option, "name": NAMES[option], "value": value, "label": label(option, value),
            "mean": round(mean, 3), "low": round(low, 3), "evidence": round(evidence, 2), "chose": chose, "kept": kept,
            "of": len(window), "confident": confident, "reason": reason,
            "line": f"{label(option, value)} (learned: {reason})"}


def suggest(prompt_options: dict | None = None, chosen: dict | None = None, has_song: bool = False,
            prof: dict | None = None) -> list[dict]:
    """Learned values for the choices the person left open. Never for a choice they made themselves."""
    prompt_options, chosen = prompt_options or {}, chosen or {}
    explicit = explicit_choices(prompt_options, chosen)
    skip = set(explicit)
    if prompt_options.get("template") or chosen.get("template") not in (None, "", "auto"):
        skip |= TEMPLATE_SETS
    if chosen.get("like"):
        skip |= LIKE_SETS
    if has_song or skip & {"music", "music_enabled"}:
        skip |= {"music", "music_enabled"}
    prof = prof if prof is not None else profile()
    if not prof["records"]:
        return []
    out = [p for o in LEARNABLE if o not in skip and (p := preference(o, prof)) and p["confident"]]
    if any(p["option"] == "music_enabled" for p in out):
        out = [p for p in out if p["option"] != "music"]
    return out


def flags(learned: list[dict]) -> list[str]:
    args: list[str] = []
    for s in learned:
        if s["option"] == "music_enabled":
            args += ["--no-music"] if s["value"] == "off" else []
        elif s["option"] in FLAG:
            args += [FLAG[s["option"]], s["value"]]
    return args


# --------------------------------------------------------- keep/cut retrain

def review_pairs(evs: list[dict] | None = None) -> list[dict]:
    """The person's latest review of each edit, as keep/cut labels on that recording's words."""
    latest: dict[str, dict] = {}
    for ev in events() if evs is None else evs:
        if ev.get("kind") == "review" and ev.get("spans"):
            latest[ev["root"]] = ev
    pairs = []
    for root, ev in latest.items():
        tr = read_json(Path(root) / "build" / "transcript.json", {}) or {}
        words = [w for w in tr.get("words") or [] if w.get("end", 0) > w.get("start", 0)]
        if len(words) >= 20:
            pairs.append({"name": Path(root).name, "video": root, "words": words,
                          "segs": [(float(a), float(b)) for a, b in ev["spans"]], "t": ev["t"]})
    return pairs


def maybe_retrain() -> dict:
    """Train the keep/cut model on the person's reviews when there is something new. Cheap (NumPy), never blocking."""
    from video_studio import learn

    if not _RETRAIN.acquire(blocking=False):
        return {"skipped": "already training"}
    try:
        pairs = review_pairs()
        sig = hashlib.sha1(json.dumps(sorted((p["video"], p["t"]) for p in pairs)).encode()).hexdigest()[:16]
        status = read_json(app_data_dir() / REVIEWS_STATUS, {}) or {}
        if len({p["video"] for p in pairs}) < MIN_REVIEWED:
            return {"skipped": f"needs {MIN_REVIEWED} reviewed edits", "reviewed": len(pairs)}
        if status.get("labels_sig") == sig:
            return {"skipped": "nothing new"}
        result = learn.train_pairs(pairs, save=False)
        if not result.get("ok"):
            return result
        result.update(source=SOURCE, labels_sig=sig, trained_at=round(time.time()), reviewed=len(pairs))
        current = read_json(app_data_dir() / KEEP_FILE, {}) or {}
        by_hand = current.get("ok") and current.get("source") != SOURCE
        # a model trained by hand (`video-studio learn`) is replaced only when it is off and this one is on
        result["in_use"] = not by_hand or (result["enabled"] and not current.get("enabled"))
        if result["in_use"]:
            write_json(app_data_dir() / KEEP_FILE, result)
        write_json(app_data_dir() / REVIEWS_STATUS, {k: v for k, v in result.items() if k != "weights"})
        return result
    finally:
        _RETRAIN.release()


# --------------------------------------------------------------- the view

def summary() -> dict:
    prof = profile()
    counts = prof["counts"]
    reviews = [e for e in events() if e.get("kind") == "review" and e.get("spans")]
    prefs = [p for o in LEARNABLE if (p := preference(o, prof))] if prof["records"] else []
    status = read_json(app_data_dir() / REVIEWS_STATUS, {}) or {}
    current = read_json(app_data_dir() / KEEP_FILE, {}) or {}
    return {"file": str(path()), "events": prof["events"], "edits": prof["edits"],
            "choices": sum(len(r["chosen"]) for r in prof["records"]),
            "refines": counts.get("refine", 0), "reviews": len(reviews),
            "review_lines": sum(len(e.get("kept") or []) + len(e.get("cut") or []) for e in reviews),
            "downloads": counts.get("download", 0), "shares": counts.get("share", 0), "covers": counts.get("covers", 0),
            "stopped": counts.get("stopped", 0), "ratings": counts.get("rating", 0), "restyles": counts.get("restyle", 0),
            "finished": counts.get("finished", 0),
            "preferences": [{k: v for k, v in p.items() if k != "line"} for p in prefs],
            "will_use": [p["line"] for p in prefs if p["confident"]],
            "keep_model": {"trained": bool(status.get("ok")), "reviewed": status.get("reviewed", 0),
                           "words": status.get("words", 0), "enabled": bool(status.get("enabled")),
                           "in_use": bool(status.get("in_use")) and current.get("source") == SOURCE,
                           "needs": MIN_REVIEWED, "trained_at": status.get("trained_at")}}


def forget() -> dict:
    """Forget what was learned: the signal file, the taste table and the keep/cut model trained on reviews.
    Films, projects and ratings shown on films are not touched."""
    from video_studio import memory

    n = len(events())
    with _LOCK:
        path().unlink(missing_ok=True)
    rows = memory.reset_taste()
    current = read_json(app_data_dir() / KEEP_FILE, {}) or {}
    if current.get("source") == SOURCE:
        (app_data_dir() / KEEP_FILE).unlink(missing_ok=True)
    (app_data_dir() / REVIEWS_STATUS).unlink(missing_ok=True)
    return {"ok": True, "signals": n, "taste_rows": rows}
