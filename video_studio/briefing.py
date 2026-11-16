"""Turn a creator's rough brief into a validated editing intent.

Component timing is never silently used as the whole video's duration.
AI wording and suggestions remain separate from the immutable original brief.

Rules for a time in the brief (seconds or minutes):
* next to a part of the video (hook, intro, outro, transition, title, caption, fade, pause, zoom),
  including common misspellings such as "hok" or "intor", it times that part only;
* "remove/cut/trim N seconds from ..." is a trim, not a length;
* it becomes the length of the whole video only when the words around it say so ("45 second reel",
  "keep it under 30 sec", "total 2 min"), or when it is 10 seconds or more and nothing else claims it;
* a short time with no clear owner ("make it 3 sec") is NOT used to cut the video. A question is asked
  instead, because cutting a whole recording down to a few seconds by mistake destroys the message.
"""
from __future__ import annotations

import difflib
import json
import re

from video_studio.ai import Director, _short_error
from video_studio.util import ToolError

# Hindi (Devanagari) and Bengali digits read as ordinary digits.
_DIGITS = str.maketrans("०१२३४५६७८९০১২৩৪৫৬৭৮৯", "01234567890123456789")
_WORD_NUMBERS = {
    "half": .5, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "fortyfive": 45,
    "forty-five": 45, "fifty": 50, "sixty": 60, "ninety": 90,
}
_UNIT = (r"seconds?|secs?|sec\.|s|minutes?|mins?|min\.|m"
         r"|(?:सेकंड|सेकेंड|सेकण्ड|मिनट|সেকেন্ড|সেকেণ্ড|মিনিট)[ऀ-৿]*")  # with case endings: সেকেন্ডের
DURATION = re.compile(
    r"(?<![\wऀ-৿.])(?P<n>\d+(?:\.\d+)?|" + "|".join(sorted(map(re.escape, _WORD_NUMBERS), key=len, reverse=True)) + r")"
    r"\s*[-–]?\s*(?P<u>" + _UNIT + r")(?![\wऀ-৿])", re.I)
COMPONENTS = ("hook", "intro", "opening", "outro", "ending", "transition", "transitions", "pause", "pauses",
              "zoom", "title", "titles", "caption", "captions", "fade", "gap", "gaps", "silence", "silences", "cta",
              "हुक", "इंट्रो", "আউটরো", "হুক", "ইন্ট্রো")
COMPONENT = "|".join(COMPONENTS)
_HOOKISH = {"hook", "intro", "opening", "हुक", "इंट्रो", "হুক", "ইন্ট্রো"}
_VIDEO_AFTER = re.compile(r"\s*(?:long\s+|total\s+|max(?:imum)?\s+)?(?:\w+\s+)?"
                          r"(?:video|reel|reels|clip|short|shorts|edit|film|story|version|cut|ad|promo|teaser|trailer|long|total"
                          r"|वीडियो|ভিডিও|রিল)\b", re.I)
_VIDEO_BEFORE = re.compile(r"(?:under|below|within|max(?:imum)?|at\s+most|no\s+more\s+than|about|around|roughly|approx\w*"
                           r"|total|length|duration|runtime|make\s+it|keep\s+it|cut\s+(?:it\s+)?(?:down\s+)?to|trim\s+(?:it\s+)?to"
                           r"|shorten\s+(?:it\s+)?to|down\s+to)\s*[:=]?\s*$", re.I)
_TRIM = re.compile(r"\b(?:remove|cut|trim|skip|drop|delete)\s*(?:the\s+)?(?:first|last)?\s*$", re.I)


def _seconds(match: re.Match) -> float:
    raw = match["n"].lower()
    value = _WORD_NUMBERS[raw] if raw in _WORD_NUMBERS else float(raw)
    unit = match["u"].lower()
    return value * (60 if unit.startswith("m") or unit.startswith(("मिनट", "মিনিট")) else 1)


def _component(word: str) -> str | None:
    """A part of the video named by this word, allowing small typos ("hok", "intor", "trasition")."""
    word = word.lower().strip(".,:;!?\"'")
    if not word:
        return None
    if word in COMPONENTS:
        return word
    if len(word) >= 3 and word.isascii():
        close = difflib.get_close_matches(word, [c for c in COMPONENTS if c.isascii()], n=1, cutoff=.74)
        if close:
            return close[0]
    return None


def local_intent(brief: str) -> dict:
    text = (brief or "").translate(_DIGITS)
    target, hook, questions = None, None, []
    unclear: list[float] = []
    for match in DURATION.finditer(text):
        seconds = _seconds(match)
        if not 0 < seconds <= 86400:
            continue
        before = text[max(0, match.start() - 45):match.start()]
        after = text[match.end():match.end() + 40]
        next_words = re.findall(r"[\wऀ-৿']+", after)[:3]
        # "2 sec hook", "2 second long intro", "3 sec (opening) title"
        owner = next((_component(w) for w in next_words[:2] if _component(w)), None)
        if owner is None and next_words[:1] and next_words[0].lower() in ("long", "of", "for"):
            owner = _component(next_words[1]) if len(next_words) > 1 else None
        if owner is None:  # "hook of 2 seconds", "outro: 3 sec", "intro about 2s"
            prev = re.search(r"([\wऀ-৿']+)(?:\s+(?:of|for|about|around|lasting|length|duration|is|a|an|should\s+be|max))*\s*[:=]?\s*$",
                             before)
            owner = _component(prev[1]) if prev else None
        if owner:
            if owner in _HOOKISH and hook is None:
                hook = seconds
            continue
        if _TRIM.search(before) or re.match(r"\s*(?:from|off|of\s+the\s+(?:start|end|beginning))\b", after, re.I) and _TRIM.search(before[-25:] + " "):
            continue
        says_video = bool(_VIDEO_AFTER.match(after) or _VIDEO_BEFORE.search(before))
        if target is None and (says_video or seconds >= 10):
            target = seconds
        elif target is None:
            unclear.append(seconds)
    if unclear and target is None:
        n = unclear[0]
        shown = f"{n:g} seconds" if n < 60 else f"{n / 60:g} minutes"
        questions.append(f"Is {shown} the length of the whole video, or of one part such as the hook? "
                         "Until you say, the whole recording is kept.")
    shorten = bool(re.search(r"\b(shorten|condense|summari[sz]e|highlights?|shorter|short\s+(?:video|reel|clip|edit)|cut\s+it\s+down)\b",
                             text, re.I))
    return {"original_brief": brief, "normalized_brief": (brief or "").strip(), "target_seconds": target,
            "hook_seconds": hook, "preserve_content": target is None and not shorten,
            "source": "local", "suggestions": [], "questions": questions, "warnings": []}


PROMPT = """You help a creator who typed a rough instruction for editing their OWN video. The footage is already
imported and transcribed, so never ask whether they have footage or a script.

1. "normalized_brief": the SAME instruction with spelling and grammar fixed. Keep it about as long as the original.
   Do not add explanations, notes, assumptions or new requests. Keep every number and what it belongs to.
   Example: "best taliking head wit 2 sec hook" -> "Best talking-head video with a 2-second hook".
2. "summary": one plain sentence saying what the edit will do.
3. "suggestions": up to 3 short, concrete, OPTIONAL ideas for this video (for example caption style or music mood).
4. "questions": only real ambiguities in the instruction, at most 2. If no total length is given, the whole
   recording is kept; you may ask whether they want it shorter, but do not ask about platform unless it matters.

Rules: a time next to hook/intro/title/transition is that part only, never the whole video. Do not invent a duration,
do not promise views or audience results, do not claim any training. Reply in the language of the instruction.
Return JSON only: {"normalized_brief":"...","summary":"...","suggestions":["..."],"questions":["..."]}
Checked separately (must not change): """


def interpret(brief: str, preferred=None, use_ai=True, usage_file=None) -> dict:
    result = local_intent(brief)
    if not use_ai or preferred == "none" or not brief.strip():
        return result
    prompt = PROMPT + json.dumps({"instruction": brief, "target_seconds": result["target_seconds"],
                                  "hook_seconds": result["hook_seconds"], "keep_whole_recording": result["preserve_content"]},
                                 ensure_ascii=False)
    try:
        # During an edit the call counts against the project's AI budget; a live check while typing is one call.
        director = Director(preferred, budget=None if usage_file else 2, usage_file=usage_file)
        answer, provider = director.ask_json("understand editing brief", prompt)
        if not isinstance(answer, dict):
            raise ValueError("AI returned no structured interpretation")
        normalized = str(answer.get("normalized_brief") or brief).strip()
        corrected = local_intent(normalized)
        if any(corrected[field] != result[field] for field in ("target_seconds", "hook_seconds", "preserve_content")):
            normalized = brief.strip()
            result["warnings"].append("The AI's corrected wording changed a time or a shortening instruction, "
                                      "so your own wording is used.")
        elif len(normalized) > 2 * len(brief.strip()) + 40:  # a spelling fix does not double the length
            normalized = brief.strip()
            result["warnings"].append("The AI's corrected wording added too much, so your own wording is used.")
        result.update(normalized_brief=normalized[:4000], summary=str(answer.get("summary") or "")[:600], source="ai:" + provider)
        result["suggestions"] = [str(x)[:300] for x in answer.get("suggestions", [])[:3]] if isinstance(answer.get("suggestions"), list) else []
        asked = [str(x)[:300] for x in answer.get("questions", [])[:2]] if isinstance(answer.get("questions"), list) else []
        result["questions"] = result["questions"] + [q for q in asked if q not in result["questions"]]
    except (ToolError, ValueError, OSError) as error:
        result["warnings"] = ["The AI check could not run, so the safe reading below is used. " + _short_error(str(error))]
        result["failed"] = True
    return result
