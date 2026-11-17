"""`video-studio learn`: train the keep/cut model on real edits (raw transcript + the segments the final video kept).

Each training pair is a folder with words.json (source-timed words) and edl*.json ({"segs": [{"src0", "src1"}, ...]}),
or a finished Video Studio project (transcript.json + edit_manifest.json). Every word becomes one example: kept or cut.
Model: L2-regularised logistic regression on local features (no LLM). It is evaluated leave-one-video-out against the
built-in rules and only switched on when it beats them. Weights are a small JSON file read by the editor brain.
"""

from __future__ import annotations

import json
import math
import re
from difflib import SequenceMatcher
from pathlib import Path

from video_studio import brain
from video_studio.util import app_data_dir, console, read_json, write_json

FEATURES = ["bias", "dur", "gap_before", "gap_after", "prob", "pos", "stop", "content", "number", "negation", "loanword",
            "cue", "filler", "repeated_later", "repeated_earlier", "utt_len", "utt_pos", "utt_repeated_later", "first_utt",
            "low_prob_run"]
FILLERS = {"um", "uh", "umm", "hmm", "matlab", "basically", "actually", "toh", "to", "so", "like", "acha", "accha", "haan",
           "ok", "okay", "मतलब", "तो", "अच्छा", "हां", "हाँ", "उम्म", "अं", "आं", "हम्म"}


def _text(w: dict) -> str:
    return str(w.get("text") or w.get("word") or "").strip()


def _key(w: dict) -> str:
    return re.sub(r"[^\wऀ-ॿ]", "", _text(w).lower())


def utterances(words: list[dict]) -> list[list[int]]:
    out, cur = [], []
    for i, w in enumerate(words):
        if cur and (w["start"] - words[cur[-1]]["end"] > 0.6 or len(cur) >= 14):
            out.append(cur)
            cur = []
        cur.append(i)
        if re.search(r"[.?!।]$", _text(w)):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def features(words: list[dict]) -> list[dict]:
    n = len(words)
    total = words[-1]["end"] if words else 1.0
    keys = [_key(w) for w in words]
    utts = utterances(words)
    utt_of = {}
    for ui, u in enumerate(utts):
        for i in u:
            utt_of[i] = ui
    utt_text = [" ".join(keys[i] for i in u) for u in utts]
    utt_rep_later = []
    for ui, u in enumerate(utts):
        t_end = words[u[-1]]["end"]
        later = [uj for uj in range(ui + 1, len(utts)) if words[utts[uj][0]]["start"] - t_end < 12]
        best = max((SequenceMatcher(None, utt_text[ui], utt_text[uj]).ratio() for uj in later), default=0.0)
        utt_rep_later.append(1.0 if best > 0.6 and len(u) >= 3 else 0.0)
    rows = []
    for i, w in enumerate(words):
        lx = brain.lexical(_text(w), brain.transliterate(_text(w)))
        tri = tuple(keys[i:i + 3])
        later = any(tuple(keys[j:j + 3]) == tri for j in range(i + 1, min(n - 2, i + 40))) if len(tri) == 3 else False
        earlier = any(tuple(keys[j:j + 3]) == tri for j in range(max(0, i - 40), i)) if len(tri) == 3 else False
        u = utts[utt_of[i]]
        lowrun = sum(1 for j in range(max(0, i - 2), min(n, i + 3)) if float(words[j].get("p", words[j].get("prob", 1.0))) < 0.5)
        rows.append({
            "bias": 1.0, "dur": min(2.0, w["end"] - w["start"]),
            "gap_before": min(2.0, w["start"] - words[i - 1]["end"]) if i else 0.5,
            "gap_after": min(2.0, words[i + 1]["start"] - w["end"]) if i + 1 < n else 0.5,
            "prob": float(w.get("p", w.get("prob", 0.9))), "pos": w["start"] / max(1.0, total),
            "stop": lx["stop"], "content": lx["content"], "number": lx["number"], "negation": lx["negation"],
            "loanword": lx["loanword"], "cue": lx["cue"], "filler": 1.0 if keys[i] in FILLERS else 0.0,
            "repeated_later": 1.0 if later else 0.0, "repeated_earlier": 1.0 if earlier else 0.0,
            "utt_len": min(1.0, len(u) / 14), "utt_pos": utt_of[i] / max(1, len(utts) - 1),
            "utt_repeated_later": utt_rep_later[utt_of[i]], "first_utt": 1.0 if utt_of[i] == 0 else 0.0,
            "low_prob_run": lowrun / 5,
        })
    return rows


def _labels(words: list[dict], segs: list[tuple[float, float]]) -> list[int]:
    return [1 if any(a - 0.02 <= (w["start"] + w["end"]) / 2 <= b + 0.02 for a, b in segs) else 0 for w in words]


def find_pairs(roots: list[Path]) -> list[dict]:
    pairs = []
    for root in roots:
        for edl in sorted(root.rglob("edl*.json")):
            wf = edl.parent / "words.json"
            if not wf.exists():
                continue
            try:
                words = [w for w in json.loads(wf.read_text(encoding="utf-8")) if w.get("end", 0) > w.get("start", 0)]
                d = json.loads(edl.read_text(encoding="utf-8"))
                segs = [(float(s["src0"]), float(s["src1"])) for s in (d["segs"] if isinstance(d, dict) else d)]
            except (ValueError, KeyError, TypeError):
                continue
            if len(words) >= 30 and segs:
                pairs.append({"name": str(edl.relative_to(root)), "video": str(edl.parent), "words": words, "segs": segs})
        for man in root.rglob("edit_manifest.json"):
            tr = man.parent / "transcript.json"
            if tr.exists():
                m, t = read_json(man, {}), read_json(tr, {})
                segs = [(s["start"], s["end"]) for s in m.get("segments", [])]
                if len(t.get("words", [])) >= 30 and segs:
                    pairs.append({"name": str(man.parent.parent.name), "video": str(man.parent), "words": t["words"], "segs": segs})
    return pairs


def _sigmoid(z: float) -> float:
    return 1 / (1 + math.exp(-max(-30.0, min(30.0, z))))


def fit(X: list[list[float]], y: list[int], l2: float = 1.0, iters: int = 600, lr: float = 0.3) -> list[float]:
    """Plain gradient-descent logistic regression with class balancing. NumPy (already in the app) makes it about
    a hundred times faster, so retraining after an edit is cheap; the pure-Python loop below is the same maths."""
    try:
        import numpy as np
    except ImportError:
        np = None
    if np is not None:
        xa, ya = np.asarray(X, dtype=float), np.asarray(y, dtype=float)
        n = len(ya)
        pos, neg = (ya.sum() or 1.0), ((n - ya.sum()) or 1.0)
        cw = np.where(ya == 1, n / (2 * pos), n / (2 * neg))
        reg = np.full(xa.shape[1], l2)
        reg[0] = 0.0
        w = np.zeros(xa.shape[1])
        for _ in range(iters):
            p = 1 / (1 + np.exp(-np.clip(xa @ w, -30.0, 30.0)))
            w -= lr * ((xa.T @ ((p - ya) * cw)) / n + reg * w / n)
        return [float(v) for v in w]
    d = len(X[0])
    w = [0.0] * d
    pos = sum(y) or 1
    neg = (len(y) - sum(y)) or 1
    cw = {1: len(y) / (2 * pos), 0: len(y) / (2 * neg)}
    for _ in range(iters):
        g = [0.0] * d
        for xi, yi in zip(X, y):
            p = _sigmoid(sum(a * b for a, b in zip(w, xi)))
            e = (p - yi) * cw[yi]
            for k in range(d):
                g[k] += e * xi[k]
        for k in range(d):
            w[k] -= lr * (g[k] / len(y) + (l2 * w[k] / len(y) if k else 0.0))
    return w


def _rules_predict(words: list[dict]) -> list[int]:
    """The built-in editor rules (retakes + fillers), for comparison."""
    from video_studio import editor

    utts = editor._utterances([{"text": _text(w), "start": w["start"], "end": w["end"]} for w in words])
    retakes = editor.find_retakes(utts)
    out = []
    norm = [{"text": _text(w), "start": w["start"], "end": w["end"]} for w in words]
    for i, w in enumerate(words):
        u = next((x for x in utts if x["start"] - 0.01 <= w["start"] <= x["end"] + 0.01), None)
        out.append(0 if (u and u["id"] in retakes) or editor._is_filler(norm, i) else 1)
    return out


def _metrics(y: list[int], pred: list[int]) -> dict:
    tp = sum(1 for a, b in zip(y, pred) if a == 1 and b == 1)
    tn = sum(1 for a, b in zip(y, pred) if a == 0 and b == 0)
    fp = sum(1 for a, b in zip(y, pred) if a == 0 and b == 1)
    fn = sum(1 for a, b in zip(y, pred) if a == 1 and b == 0)
    acc = (tp + tn) / max(1, len(y))
    cut_recall = tn / max(1, tn + fp)       # of the words you cut, how many we also cut
    keep_recall = tp / max(1, tp + fn)      # of the words you kept, how many we kept
    return {"accuracy": round(acc, 3), "cut_recall": round(cut_recall, 3), "keep_recall": round(keep_recall, 3),
            "balanced": round((cut_recall + keep_recall) / 2, 3)}


def train(roots: list[Path], save: bool = True) -> dict:
    return train_pairs(find_pairs(roots), save)


def train_pairs(pairs: list[dict], save: bool = True) -> dict:
    """Train on {"name", "video", "words", "segs"} pairs (from folders, or from your reviews in signals.py)."""
    if len(pairs) < 2:
        console.warn("Need at least 2 finished edits (words.json + edl*.json, or Video Studio projects) to learn from.")
        return {"ok": False, "pairs": len(pairs)}
    # one group per recording (IG and YT cuts of the same footage must not leak across folds)
    data = []
    for pr in pairs:
        feats = features(pr["words"])
        data.append({"group": pr["video"], "name": pr["name"], "X": [[r[f] for f in FEATURES] for r in feats],
                     "y": _labels(pr["words"], pr["segs"]), "words": pr["words"]})
    groups = sorted({d["group"] for d in data})
    console.step(f"Learning from {len(data)} edits of {len(groups)} recordings ({sum(len(d['y']) for d in data)} words)")
    y_all, p_model, p_rules = [], [], []
    for g in groups:
        tr = [d for d in data if d["group"] != g]
        te = [d for d in data if d["group"] == g]
        w = fit([x for d in tr for x in d["X"]], [v for d in tr for v in d["y"]])
        for d in te:
            y_all += d["y"]
            p_model += [1 if _sigmoid(sum(a * b for a, b in zip(w, x))) >= 0.5 else 0 for x in d["X"]]
            p_rules += _rules_predict(d["words"])
    m_model, m_rules = _metrics(y_all, p_model), _metrics(y_all, p_rules)
    w = fit([x for d in data for x in d["X"]], [v for d in data for v in d["y"]])
    # switched on only if it is better at BOTH: finding what you cut, and not cutting what you keep
    better = (m_model["balanced"] > m_rules["balanced"] + 0.05 and m_model["accuracy"] >= m_rules["accuracy"]
              and m_model["keep_recall"] >= 0.85)
    result = {"ok": True, "edits": len(data), "recordings": len(groups), "words": len(y_all),
              "kept_share": round(sum(y_all) / max(1, len(y_all)), 3), "model": m_model, "rules": m_rules,
              "enabled": better, "features": FEATURES, "weights": [round(v, 4) for v in w]}
    console.info(f"  held-out test (each recording unseen during training):")
    console.info(f"    learned model : {m_model}")
    console.info(f"    built-in rules: {m_rules}")
    if save:
        write_json(app_data_dir() / "learned_keep.json", result)
        console.ok("Learned model " + ("ON: it beats the built-in rules on unseen videos." if better
                                       else "saved but OFF: it does not beat the built-in rules yet (needs more edits)."))
    return result


def keep_probabilities(words: list[dict]) -> list[float] | None:
    """Per-word keep probability from the learned model, or None when no model is enabled."""
    m = read_json(app_data_dir() / "learned_keep.json", {}) or {}
    if not m.get("enabled") or m.get("features") != FEATURES or not words:
        return None
    rows = features([{**w, "text": _text(w)} for w in words])
    return [_sigmoid(sum(a * r[f] for a, f in zip(m["weights"], FEATURES))) for r in rows]
