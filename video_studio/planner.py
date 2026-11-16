"""Script understanding, scene breakdown, creative decisions and storyboard.

The AI director (one call) produces a full production plan as JSON. If no AI
is available, or the user passes --no-ai, a deterministic local planner does
the same job with rules. Either way the plan is normalised against the same
schema, so every downstream engine can rely on it.
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path
from typing import Any

from video_studio import memory
from video_studio.ai import Director
from video_studio.looks import LOOK_FOR_TONE, LOOKS, MUSIC_FOR_TONE, PACING, TRANSITIONS
from video_studio.util import ToolError, console

SCENE_TYPES = ["title3d", "object3d", "chart3d", "kinetic", "list", "stat", "quote", "media", "cta", "hyperframes"]
OBJECTS = ["orb", "cube", "ring", "gem", "coin", "rocket", "phone", "laptop", "globe", "pyramid", "capsule",
           "shield", "bulb", "clock", "chat", "heart", "star", "gear", "monkey"]
MATERIALS = ["chrome", "gold", "glass", "matte", "neon", "accent"]
CAMERAS = ["push_in", "pull_out", "orbit", "dolly_left", "dolly_right", "crane_up", "static", "handheld"]
SHOTS = ["wide", "medium", "close-up", "extreme close-up", "overhead", "low angle"]
TONES = list(MUSIC_FOR_TONE)
SFX = ["whoosh", "hit", "riser", "pop", "ding", "click", "none"]

KEYWORD_OBJECTS = {
    "rocket": "rocket", "launch": "rocket", "space": "rocket", "phone": "phone", "mobile": "phone", "app": "phone",
    "laptop": "laptop", "computer": "laptop", "software": "laptop", "code": "laptop", "world": "globe", "global": "globe",
    "earth": "globe", "money": "coin", "price": "coin", "cost": "coin", "free": "coin", "pay": "coin", "rupee": "coin",
    "secure": "shield", "security": "shield", "privacy": "shield", "safe": "shield", "data": "shield", "idea": "bulb",
    "think": "bulb", "learn": "bulb", "time": "clock", "fast": "clock", "hour": "clock", "minute": "clock",
    "chat": "chat", "message": "chat", "talk": "chat", "love": "heart", "health": "heart", "star": "star",
    "best": "star", "quality": "gem", "premium": "gem", "value": "gem", "engine": "gear", "tool": "gear",
    "work": "gear", "build": "gear", "ai": "orb", "brain": "orb", "future": "orb",
}

TONE_WORDS = {
    "inspiring": ["dream", "future", "believe", "change", "possible", "together", "grow", "imagine"],
    "urgent": ["now", "stop", "warning", "before", "never", "mistake", "risk", "danger", "today"],
    "educational": ["how", "why", "learn", "step", "explain", "works", "means", "guide", "tip"],
    "funny": ["lol", "funny", "joke", "weird", "crazy", "imagine if"],
    "serious": ["problem", "crisis", "truth", "data", "report", "fact"],
    "calm": ["slow", "breathe", "simple", "quiet", "peace", "gentle"],
    "upbeat": ["awesome", "amazing", "love", "excited", "best", "wow", "easy"],
}


# ------------------------------------------------------------------- parsing


def parse_script(text: str) -> dict:
    """Split a script into a title, paragraphs and inline directions.

    Supported markup (all optional):
      # Title line
      [3D: rocket] / [VISUAL: ...] / [STAT: 70% | of teams] / [QUOTE: ... - Author]
      [LIST] followed by lines starting with -, *, or 1.
      (stage directions in parentheses are not spoken)
    """
    title = ""
    blocks: list[dict] = []
    current: dict = {"lines": [], "directives": []}

    def flush() -> None:
        if current["lines"] or current["directives"]:
            blocks.append({"text": " ".join(current["lines"]).strip(), "directives": list(current["directives"]),
                           "items": list(current.get("items", [])), "lead": current.get("lead", "")})
        current.clear()
        current.update({"lines": [], "directives": [], "items": []})

    current["items"] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line:
            flush()
            continue
        if line.startswith("#") and not title:
            title = line.lstrip("#").strip()
            continue
        for d in re.findall(r"\[([^\]]+)\]", line):
            current["directives"].append(d.strip())
        line = re.sub(r"\[[^\]]+\]", "", line)
        line = re.sub(r"^(VO|NARRATOR|V\.O\.)\s*:\s*", "", line, flags=re.I)
        line = re.sub(r"\([^)]*\)", "", line).strip()
        bullet = re.match(r"^(?:[-*•]|\d+[.)])\s+(.+)", line)
        if bullet:
            current["items"].append(bullet.group(1).strip())
            current["lines"].append(bullet.group(1).strip().rstrip(".") + ".")
        elif line:
            if not current["lines"]:
                current["lead"] = line
            current["lines"].append(line)
    flush()
    blocks = [b for b in blocks if b["text"] or b["directives"]]
    return {"title": title, "blocks": blocks, "word_count": sum(len(b["text"].split()) for b in blocks)}


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])", text.strip())
    return [p.strip() for p in parts if p.strip()]


def detect_tone(text: str) -> str:
    low = text.lower()
    scores = {tone: sum(low.count(w) for w in words) for tone, words in TONE_WORDS.items()}
    tone, score = max(scores.items(), key=lambda kv: kv[1])
    return tone if score > 0 else "neutral"


STOP = {"the", "a", "an", "of", "to", "and", "that", "is", "are", "it", "this", "in", "on", "for", "with", "you", "your",
        "can", "will", "just", "so", "but", "or", "as", "at", "by", "be", "if", "its", "it's", "into", "every", "what",
        "how", "here", "there", "then", "than", "from", "they", "we", "our", "my", "me", "do", "does", "make", "makes"}


def headline_for(text: str, max_words: int = 7) -> str:
    """A short on-screen line cut at a natural clause boundary (never drops words mid-phrase)."""
    sents = sentences(text) or [text]
    first = re.sub(r"^(so|and|but|now|well|okay|look|here's the thing)[, ]+", "", sents[0].strip(), flags=re.I)
    question = first.rstrip().endswith("?")
    clauses = [c.strip() for c in re.split(r"[,;:\u2014]|\s-\s", first) if c.strip()]
    pick = first
    if len(first.split()) > max_words:
        fits = [c for c in clauses if 2 <= len(c.split()) <= max_words]
        pick = fits[0] if fits else " ".join(first.split()[:max_words])
    words = pick.rstrip(".!?:").split()
    while len(words) > 2 and words[-1].lower().strip(",") in STOP:
        words.pop()
    out = " ".join(words).strip(",")
    return out + ("?" if question and out == first.rstrip("?").rstrip() else "")


def emphasis_words(text: str, n: int = 1) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z'-]{2,}|\d[\d,.%]*", text)
    content = [w for w in words if w.lower() not in STOP]
    ranked = sorted(dict.fromkeys(content), key=lambda w: (not any(c.isdigit() for c in w), -len(w)))
    return ranked[:n]


# -------------------------------------------------------------- local planner


def _directive_visual(directives: list[str], text: str, items: list[str]) -> dict | None:
    for d in directives:
        key, _, val = d.partition(":")
        key, val = key.strip().lower(), val.strip()
        if key in ("3d", "object"):
            obj = val.lower() if val.lower() in OBJECTS else _object_for(val or text)
            return {"type": "object3d", "object": obj, "label": headline_for(text, 4)}
        if key in ("title", "title3d"):
            return {"type": "title3d", "text": val or headline_for(text, 4)}
        if key == "stat":
            num, _, label = val.partition("|")
            return _stat_from(num.strip(), label.strip() or headline_for(text, 5))
        if key == "quote":
            quote, _, author = val.rpartition(" - ")
            return {"type": "quote", "text": quote or val, "author": author if quote else ""}
        if key == "chart":
            nums = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", val)][:6]
            return {"type": "chart3d", "values": nums or [3, 5, 8], "labels": [], "title": headline_for(text, 5)}
        if key in ("image", "video", "media", "broll"):
            return {"type": "media", "asset": val, "headline": headline_for(text, 6), "motion": "kenburns_in"}
        if key == "list":
            return {"type": "list", "title": headline_for(text, 5), "items": items[:5] or [headline_for(s, 5) for s in sentences(text)[:4]]}
        if key == "cta":
            return {"type": "cta", "headline": val or headline_for(text, 5), "sub": ""}
        if key in ("hyperframes", "html"):
            return {"type": "hyperframes", "headline": val or headline_for(text, 6), "sub": ""}
    return None


def _object_for(text: str) -> str:
    low = text.lower()
    for kw, obj in KEYWORD_OBJECTS.items():
        if re.search(rf"\b{re.escape(kw)}", low):
            return obj
    return "orb"


def _stat_from(token: str, label: str) -> dict:
    m = re.match(r"([^\d-]*)(-?[\d,]+(?:\.\d+)?)(.*)", token)
    if not m:
        return {"type": "kinetic", "headline": label}
    prefix, num, suffix = m.groups()
    return {"type": "stat", "value": float(num.replace(",", "")), "prefix": prefix.strip(), "suffix": suffix.strip()[:6],
            "label": label}


def local_plan(script: str, instructions: str, settings: dict) -> dict:
    parsed = parse_script(script)
    if not parsed["blocks"]:
        raise ToolError("The script is empty.", "Write the narration in a .txt file, one paragraph per idea.")
    full = " ".join(b["text"] for b in parsed["blocks"])
    tone = detect_tone(full + " " + instructions)

    # Re-chunk long paragraphs so one scene carries about 10-28 spoken words.
    chunks: list[dict] = []
    for block in parsed["blocks"]:
        sents = sentences(block["text"]) or [block["text"]]
        if block["directives"] or block["items"] or len(block["text"].split()) <= 30:
            chunks.append(block)
            continue
        buf: list[str] = []
        for s in sents:
            buf.append(s)
            if len(" ".join(buf).split()) >= 18:
                chunks.append({"text": " ".join(buf), "directives": [], "items": []})
                buf = []
        if buf:
            if chunks and len(" ".join(buf).split()) < 8:
                chunks[-1]["text"] += " " + " ".join(buf)
            else:
                chunks.append({"text": " ".join(buf), "directives": [], "items": []})

    title = parsed["title"] or headline_for(chunks[0]["text"], 5)
    scenes = []
    used_objects: set[str] = set()
    for i, chunk in enumerate(chunks):
        text = chunk["text"]
        visual = _directive_visual(chunk["directives"], text, chunk.get("items", []))
        if visual is None:
            stat = re.search(r"(₹|\$|Rs\.?\s?)?(\d[\d,]*(?:\.\d+)?)\s?(%|x|k|K|M|lakh|crore|hours?|minutes?|days?)?", text)
            if i == 0:
                visual = {"type": "title3d", "text": title, "subtitle": headline_for(text, 6) if headline_for(text, 6) != title else ""}
            elif i == len(chunks) - 1:
                visual = {"type": "cta", "headline": headline_for(text, 5), "sub": title}
            elif chunk.get("items"):
                lead = (chunk.get("lead") or "").rstrip(":.")
                visual = {"type": "list", "title": headline_for(lead, 6) if lead else "Step by step", "items": chunk["items"][:5]}
            elif stat and stat.group(2) and (stat.group(3) or stat.group(1)):
                visual = _stat_from((stat.group(1) or "") + stat.group(2) + (stat.group(3) or ""), headline_for(text, 5))
            elif '"' in text or "“" in text:
                q = re.search(r"[\"“](.+?)[\"”]", text)
                visual = {"type": "quote", "text": q.group(1) if q else headline_for(text, 10), "author": ""}
            elif i % 3 == 1:
                obj = _object_for(text)
                if obj in used_objects:
                    obj = next((o for o in ("orb", "gem", "ring", "cube", "star") if o not in used_objects), "orb")
                used_objects.add(obj)
                visual = {"type": "object3d", "object": obj, "label": headline_for(text, 4)}
            else:
                visual = {"type": "kinetic", "headline": headline_for(text, 7), "emphasis": emphasis_words(text)}
        scenes.append({
            "narration": text,
            "visual": visual,
            "shot": "close-up" if visual["type"] in ("stat", "quote") else ("wide" if i == 0 else "medium"),
            "camera": ["push_in", "orbit", "dolly_left", "pull_out", "crane_up"][i % 5],
            "sfx": "whoosh" if i else "riser",
            "purpose": "hook" if i == 0 else ("call to action" if i == len(chunks) - 1 else "body"),
        })
    return {
        "title": title,
        "logline": headline_for(full, 14),
        "subject": title,
        "tone": tone,
        "audience": "general social-media viewers",
        "category": _category(full),
        "scenes": scenes,
        "planner": "local-rules",
    }


def _category(text: str) -> str:
    low = text.lower()
    for cat, words in {"tech": ["ai", "software", "app", "code", "computer", "data"], "finance": ["money", "invest", "price", "rupee", "cost"],
                       "education": ["learn", "how", "why", "explain"], "motivation": ["dream", "goal", "believe"],
                       "product": ["launch", "feature", "download", "product"]}.items():
        if sum(low.count(w) for w in words) >= 2:
            return cat
    return "general"


# ---------------------------------------------------------------- AI planner

AI_PROMPT = """You are the director of a short-form video. Turn the SCRIPT into a production plan.
Return ONLY one JSON object, no prose, matching this shape:
{{
 "title": str, "logline": str (<= 20 words), "subject": str, "tone": one of {tones},
 "audience": str, "category": one of ["tech","finance","education","motivation","product","general"],
 "scenes": [
  {{
   "narration": str  (the exact words spoken in this scene, copied from the script in order; together the scenes must cover the whole script, nothing invented),
   "purpose": "hook" | "body" | "proof" | "turn" | "call to action",
   "shot": one of {shots}, "camera": one of {cameras},
   "sfx": one of {sfx},
   "visual": one of:
     {{"type":"title3d","text": <=4 words,"subtitle": <=7 words,"material": one of {materials}}}
     {{"type":"object3d","object": one of {objects},"label": <=4 words,"material": one of {materials}}}
     {{"type":"chart3d","values":[numbers],"labels":[short strings],"title": <=5 words}}
     {{"type":"kinetic","headline": <=7 words,"emphasis":[1-2 words from headline]}}
     {{"type":"list","title": <=4 words,"items":[2-5 items, <=5 words each]}}
     {{"type":"stat","value": number,"prefix": str,"suffix": str,"label": <=6 words}}
     {{"type":"quote","text": <=18 words,"author": str}}
     {{"type":"cta","headline": <=5 words,"sub": <=8 words}}
  }}
 ]
}}
Rules: 5-12 scenes for 30-90 s of speech; each scene 8-30 spoken words. Scene 1 is a strong hook
(title3d or object3d). Use 3D (title3d/object3d/chart3d) for at most 40% of scenes. Never repeat the
same visual type twice in a row. On-screen text must be short and must not just repeat the narration.
Only use numbers that appear in the script. Last scene is the call to action.
{memory}
USER INSTRUCTIONS: {instructions}
SCRIPT:
<<<
{script}
>>>"""


def ai_plan(director: Director, script: str, instructions: str) -> dict:
    hints = memory.recall(script[:600] + " " + instructions, limit=4)
    prefs = memory.prefs()
    mem_lines = []
    if prefs:
        mem_lines.append("CREATIVE DIRECTOR'S SAVED PREFERENCES: " + "; ".join(f"{k}={v}" for k, v in prefs.items()))
    if hints:
        mem_lines.append("RELEVANT PAST PROJECTS / FEEDBACK: " + " | ".join(h["text"][:200] for h in hints))
    prompt = AI_PROMPT.format(tones=json.dumps(TONES), shots=json.dumps(SHOTS), cameras=json.dumps(CAMERAS),
                              sfx=json.dumps(SFX), materials=json.dumps(MATERIALS), objects=json.dumps(OBJECTS),
                              memory="\n".join(mem_lines), instructions=instructions or "none", script=script.strip())
    data, provider = director.ask_json("script analysis + storyboard", prompt)
    if not isinstance(data, dict) or not data.get("scenes"):
        raise ToolError("AI plan had no scenes.")
    data["planner"] = f"ai:{provider}"
    return data


# --------------------------------------------------------------- normalise


def _clip(text: Any, words: int) -> str:
    parts = str(text or "").replace("\n", " ").split()
    return " ".join(parts[:words])


def normalize(plan: dict, settings: dict, script: str) -> dict:
    """Coerce any plan (AI or local) into the strict schema the engines expect."""
    tone = plan.get("tone") if plan.get("tone") in TONES else detect_tone(script)
    scenes = []
    for i, raw in enumerate(plan.get("scenes") or []):
        narration = str(raw.get("narration") or "").strip()
        visual = dict(raw.get("visual") or {})
        vtype = visual.get("type") if visual.get("type") in SCENE_TYPES else "kinetic"
        if vtype == "hyperframes" or (settings.get("motion_engine") == "hyperframes" and vtype == "kinetic"):
            vtype = "hyperframes" if settings.get("hyperframes_ok") else "kinetic"
        if vtype in ("title3d", "object3d", "chart3d") and not settings.get("blender_ok"):
            vtype = {"title3d": "kinetic", "object3d": "kinetic", "chart3d": "list"}[vtype]
            visual.setdefault("headline", visual.get("text") or visual.get("label") or visual.get("title") or headline_for(narration))
            if vtype == "list":
                visual["items"] = [f"{l}: {v:g}" for l, v in zip(visual.get("labels") or [], visual.get("values") or [])] or [headline_for(narration, 5)]
        visual["type"] = vtype
        if vtype == "title3d":
            visual["text"] = _clip(visual.get("text") or plan.get("title"), 5)
            visual["subtitle"] = _clip(visual.get("subtitle"), 8)
        elif vtype == "object3d":
            visual["object"] = visual.get("object") if visual.get("object") in OBJECTS else _object_for(narration)
            visual["label"] = _clip(visual.get("label") or headline_for(narration, 4), 5)
        elif vtype == "chart3d":
            vals = [float(v) for v in visual.get("values") or [] if isinstance(v, (int, float)) or re.match(r"^-?\d", str(v))][:6]
            visual["values"] = vals or [3.0, 5.0, 8.0]
            visual["labels"] = [_clip(l, 2) for l in (visual.get("labels") or [])][: len(visual["values"])]
            visual["title"] = _clip(visual.get("title") or headline_for(narration, 5), 6)
        elif vtype in ("kinetic", "hyperframes"):
            visual["headline"] = _clip(visual.get("headline") or headline_for(narration), 8)
            visual["emphasis"] = [w for w in visual.get("emphasis") or emphasis_words(visual["headline"], 1) if isinstance(w, str)][:2]
        elif vtype == "list":
            visual["title"] = _clip(visual.get("title") or headline_for(narration, 4), 5)
            visual["items"] = [_clip(x, 6) for x in (visual.get("items") or [])][:5] or [headline_for(narration, 5)]
        elif vtype == "stat":
            try:
                visual["value"] = float(str(visual.get("value", 0)).replace(",", ""))
            except ValueError:
                visual = {"type": "kinetic", "headline": headline_for(narration), "emphasis": []}
            visual.setdefault("prefix", "")
            visual.setdefault("suffix", "")
            visual["label"] = _clip(visual.get("label") or headline_for(narration, 5), 7)
        elif vtype == "quote":
            visual["text"] = _clip(visual.get("text") or narration, 22)
            visual["author"] = _clip(visual.get("author"), 4)
        elif vtype == "cta":
            visual["headline"] = _clip(visual.get("headline") or headline_for(narration, 5), 6)
            visual["sub"] = _clip(visual.get("sub"), 9)
        elif vtype == "media":
            visual["headline"] = _clip(visual.get("headline") or headline_for(narration, 6), 7)
        if vtype in ("title3d", "object3d", "chart3d"):
            visual["material"] = visual.get("material") if visual.get("material") in MATERIALS else ["chrome", "accent", "gold", "glass"][i % 4]
        scenes.append({
            "id": f"s{i + 1:02d}",
            "narration": narration,
            "purpose": str(raw.get("purpose") or "body"),
            "shot": raw.get("shot") if raw.get("shot") in SHOTS else "medium",
            "camera": raw.get("camera") if raw.get("camera") in CAMERAS else "push_in",
            "sfx": raw.get("sfx") if raw.get("sfx") in SFX else "whoosh",
            "visual": visual,
        })
    if not scenes:
        raise ToolError("Plan has no scenes.")
    out = {k: plan.get(k) for k in ("title", "logline", "subject", "audience", "category", "planner")}
    out["title"] = _clip(out.get("title") or headline_for(script, 5), 10)
    out["category"] = out.get("category") or _category(script)
    out["tone"] = tone
    out["scenes"] = scenes
    _check_coverage(out, script)
    return out


def _check_coverage(plan: dict, script: str) -> None:
    """Warn if the planned narration drifted from the user's script."""
    spoken = set(re.findall(r"[a-z']+", " ".join(s["narration"] for s in plan["scenes"]).lower()))
    source = set(re.findall(r"[a-z']+", " ".join(b["text"] for b in parse_script(script)["blocks"]).lower()))
    if source:
        missing = len(source - spoken) / len(source)
        invented = len(spoken - source) / max(1, len(spoken))
        plan["coverage"] = {"script_words_missing": round(missing, 3), "words_not_in_script": round(invented, 3)}
        if missing > 0.15 or invented > 0.15:
            console.warn(f"Narration differs from the script ({missing:.0%} missing, {invented:.0%} new words). "
                         "Review plan.json or re-run `analyze --no-ai`.")


# ---------------------------------------------------------- creative choices


def decide(plan: dict, settings: dict, instructions: str, seed: int | None = None) -> dict:
    """Pick look/transition/pacing/captions/music/motion/hook with the taste model."""
    rng = random.Random(seed)
    tone, aspect = plan["tone"], settings["aspect"]
    low = instructions.lower()
    forced: dict[str, str] = {}
    for dim, arms in memory.DIMENSIONS.items():
        for arm in arms:
            if re.search(rf"\b{arm.replace('_', '[ _-]?')}\b", low):
                forced.setdefault(dim, arm)
    for key, value in memory.prefs().items():
        if key in memory.DIMENSIONS and value in memory.DIMENSIONS[key]:
            forced.setdefault(key, value)
    for key in ("look", "captions", "music", "pacing", "transition", "motion"):
        if settings.get(key) and settings[key] in memory.DIMENSIONS[key]:
            forced[key] = settings[key]
    priors = {
        "look": LOOK_FOR_TONE.get(tone, {}),
        "music": {MUSIC_FOR_TONE.get(tone, "calm"): 0.85},
        "pacing": {"fast": 0.7 if settings["aspect"] == "9:16" else 0.4, "balanced": 0.65},
        "captions": {"bold_pop": 0.7 if settings["aspect"] == "9:16" else 0.4, "clean": 0.6},
        "transition": {"smooth": 0.65, "cinematic": 0.6 if tone in ("dramatic", "epic", "serious") else 0.4},
        "motion": {"dynamic": 0.65},
        "hook": {"3d_reveal": 0.65 if settings.get("blender_ok") else 0.1, "bold_claim": 0.55},
    }
    decisions, reasons = {}, {}
    for dim in memory.DIMENSIONS:
        if dim in forced:
            decisions[dim], reasons[dim] = forced[dim], "your instruction/preference"
        else:
            decisions[dim], reasons[dim] = memory.choose(dim, tone, aspect, priors.get(dim), explore=True, rng=rng)
    if not settings.get("captions_enabled", True):
        decisions["captions"], reasons["captions"] = "none", "captions disabled"
    if settings.get("transition") and settings["transition"] not in memory.DIMENSIONS["transition"] and settings["transition"] in TRANSITIONS:
        decisions["transition"], reasons["transition"] = settings["transition"], "your instruction/template"
    return {"decisions": decisions, "reasons": reasons}


# ---------------------------------------------------------------- storyboard


def storyboard_markdown(plan: dict, creative: dict, timeline: list[dict] | None = None) -> str:
    d = creative["decisions"]
    lines = [f"# Storyboard - {plan['title']}", "",
             f"*{plan.get('logline') or ''}*", "",
             f"- Tone: **{plan['tone']}** | Audience: {plan.get('audience') or '-'} | Category: {plan.get('category')}",
             f"- Planner: `{plan.get('planner')}`",
             "- Creative decisions: " + ", ".join(f"{k}=**{v}** ({creative['reasons'][k]})" for k, v in d.items()),
             f"- Look palette: {', '.join(LOOKS[d['look']]['bg'] + [LOOKS[d['look']]['accent']])}",
             f"- Transitions: {', '.join(TRANSITIONS[d['transition']])} | Pacing: {d['pacing']} (rate {PACING[d['pacing']]['rate']:+d})",
             "", "| # | Time | Purpose | Visual | Shot / camera | On screen | Narration | SFX |",
             "|---|------|---------|--------|---------------|-----------|-----------|-----|"]
    times = {t["id"]: t for t in timeline or []}
    for s in plan["scenes"]:
        v = s["visual"]
        screen = v.get("text") or v.get("headline") or v.get("label") or v.get("title") or ""
        if v["type"] == "stat":
            screen = f"{v.get('prefix', '')}{v['value']:g}{v.get('suffix', '')} - {v['label']}"
        if v["type"] == "list":
            screen += ": " + "; ".join(v["items"])
        detail = v["type"] + (f" ({v.get('object')}, {v.get('material')})" if v["type"] == "object3d" else
                              f" ({v.get('material')})" if v["type"] in ("title3d", "chart3d") else "")
        t = times.get(s["id"])
        when = f"{t['start']:.1f}-{t['end']:.1f}s" if t else "-"
        narration = s["narration"].replace("|", "/")
        lines.append(f"| {s['id']} | {when} | {s['purpose']} | {detail} | {s['shot']} / {s['camera']} | {screen.replace('|', '/')} | {narration} | {s['sfx']} |")
    lines += ["", "Assets: all visuals are generated locally (Blender 3D, motion-graphics engine); music and SFX are synthesised; "
              "voice is generated offline. Add your own images/clips with `[IMAGE: path]` in the script."]
    return "\n".join(lines) + "\n"


def analyze(script: str, instructions: str, settings: dict, director: Director | None, use_ai: bool) -> dict:
    plan: dict | None = None
    if use_ai and director and director.available():
        try:
            plan = ai_plan(director, script, instructions)
        except ToolError as exc:
            console.warn(f"AI planning unavailable ({str(exc).splitlines()[0]}); using the local planner.")
    elif use_ai:
        console.warn("No signed-in AI CLI found; using the local rule-based planner (run `video-studio ai status`).")
    if plan is None:
        plan = local_plan(script, instructions, settings)
    try:
        return normalize(plan, settings, script)
    except (ToolError, KeyError, TypeError, ValueError) as exc:
        console.warn(f"AI plan was malformed ({exc}); using the local planner.")
        return normalize(local_plan(script, instructions, settings), settings, script)


def save_plan(path: Path, plan: dict) -> None:
    path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
