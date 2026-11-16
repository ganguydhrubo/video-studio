"""Your words: names, brands and terms the studio must hear and spell exactly (`video-studio vocab add ...`).

Used twice: as hotwords while transcribing (faster-whisper biased decoding), and to correct caption spelling afterwards
(phonetic match on the consonant skeleton, so 'sahkarmi', 'सहकर्मी' and 'sahakarmi' all become 'Sahkarmi').
"""

from __future__ import annotations

from difflib import SequenceMatcher

from video_studio.util import app_data_dir, read_json, write_json


def _file():
    return app_data_dir() / "vocab.json"


def words() -> list[str]:
    return list((read_json(_file(), {}) or {}).get("words", []))


def add(items: list[str]) -> list[str]:
    cur = words()
    for w in items:
        w = w.strip()
        if w and w.lower() not in {c.lower() for c in cur}:
            cur.append(w)
    write_json(_file(), {"words": cur})
    return cur


def remove(items: list[str]) -> list[str]:
    drop = {w.lower() for w in items}
    cur = [w for w in words() if w.lower() not in drop]
    write_json(_file(), {"words": cur})
    return cur


def correct(caption_words: list[dict]) -> list[dict]:
    """Replace near-misses of your words in (Roman) caption words with your exact spelling."""
    from video_studio import brain

    vocab = [v for v in words() if " " not in v]
    if not vocab:
        return caption_words
    sk = {v: brain._skeleton(v) for v in vocab}
    out = []
    for w in caption_words:
        t = w["text"]
        core = t.strip(".,!?;:\"'()")
        roman = brain.transliterate(core)
        best, score = None, 0.0
        for v in vocab:
            r = max(SequenceMatcher(None, roman.lower(), v.lower()).ratio(),
                    SequenceMatcher(None, brain._skeleton(roman), sk[v]).ratio() * 0.95)
            if r > score:
                best, score = v, r
        if best and score >= 0.8 and len(core) >= 3:
            t = t.replace(core, best)
        out.append({**w, "text": t})
    return out
