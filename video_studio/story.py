"""Story editor: turns 'every line in recorded order' into a story, the way a human editor does.

Works in any spoken language: each line's role comes from its English meaning (the local Whisper translation) plus
the editor brain's voice/importance strength.

1. Cut dead weight: self-introductions, sign-offs ("see you tomorrow"), and lines that repeat a point already made.
2. Cold open: the strongest self-contained claim moves to the very start - unless it depends on the line before it
   ("but...", "so...") or is already first.
3. End on the payoff: trailing weak lines after the last conclusion line go.
4. Length: the requested target; for a reel with no target, about 55 s (weakest lines go first).
Every decision is written down in plain English (shown in the terminal, the plan and the Studio).
"""

from __future__ import annotations

import re

INTRO = re.compile(r"\bmy name is\b|\bi am (the )?(founder|ceo|creator)\b|\bwelcome( back)?\b|^\s*(hello|hi|hey)\b|"
                   r"\bin (today'?s|this) video\b|\btoday i (will|am going to)\b", re.I)
SIGN_OFF = re.compile(r"\bsee you\b|\bthat'?s all\b|\bsubscribe\b|\blike (and|&) share\b|\bthank(s| you)\b|\bbye\b|"
                      r"\bfollow (me|for)\b|\bcomment below\b|\bthat'?s it for\b", re.I)
PAYOFF = re.compile(r"\btry it\b|\bthat'?s why\b|\bgoing to change\b|\bremember\b|\bthe point is\b|\bin short\b|"
                    r"\bfinally\b|\bso (you|we|start|do)\b|\bwill make your\b|\bthe (truth|lesson|secret) is\b", re.I)
DEPENDENT = re.compile(r"^\s*(but|so|and|because|then|also|that|which|this is why|after that)\b", re.I)
NEG = re.compile(r"\b(no|not|never|nothing|nobody|don'?t|won'?t|can'?t|isn'?t|without)\b", re.I)


def english(transcript: dict) -> list[dict]:
    """The English meaning of a transcript, as timed sentences: the local translation, or - when the video is already
    in English - the transcript itself."""
    tr = transcript.get("translation") or []
    if not tr and transcript.get("language") == "en":
        tr = [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in transcript.get("segments") or [] if s.get("text")]
    return tr


def assign(utts: list[dict], translation: list[dict]) -> dict[str, str]:
    """Each translated sentence belongs to the ONE line it overlaps most (so 'My name is...' cannot leak into the
    question before it)."""
    out: dict[str, list[str]] = {u["id"]: [] for u in utts}
    for t in translation:
        best, ov = None, 0.0
        for u in utts:
            o = min(u["end"], t["end"]) - max(u["start"], t["start"])
            if o > ov:
                best, ov = u, o
        if best is not None and ov > 0.15:
            out[best["id"]].append(t["text"])
    return {k: " ".join(v) for k, v in out.items()}


def gist(text: str) -> str:
    """English terms said inside a non-English line ('GST फिल करना है, टैली भरना है' -> 'GST fill tally')."""
    from video_studio import brain

    out = []
    for tok in re.split(r"[\s,.!?;:\"()।|-]+", text):
        if not tok:
            continue
        if tok.isdigit() or (tok.isascii() and tok.isalpha() and tok.isupper() and len(tok) >= 2):
            out.append(tok)
            continue
        r = brain.transliterate(tok).lower()
        if r in brain.ENGLISH and r not in brain.EN_STOP and len(r) > 2:
            out.append(r.upper() if r in brain.CORE and len(r) <= 4 and r.isalpha() else r)
    return " ".join(out)


def meanings(utts: list[dict], translation: list[dict]) -> dict[str, str]:
    en = assign(utts, translation)
    return {u["id"]: en.get(u["id"]) or gist(u.get("text", "")) for u in utts}


def _english_of(u: dict, translation: list[dict]) -> str:
    return assign([u], translation).get(u["id"], "")


def _content(text: str) -> set[str]:
    from video_studio import brain

    out = set()
    for w in re.findall(r"[A-Za-z0-9']+", text.lower()):
        if w not in brain.EN_STOP and len(w) > 2:
            out.add(brain._stem(w))
    return out


def reliable(u: dict, en: str) -> bool:
    """False when the speech model clearly missed most of a line (far more meaning than words heard). Such a line
    cannot open the video: its captions would be missing words."""
    heard = len(u.get("text", "").split())
    meant = len(re.findall(r"[A-Za-z0-9']+", en))
    return not (heard < 8 and meant > 2.5 * max(1, heard) + 4)


def concrete(text: str, raw: str = "") -> float:
    """How much of a concrete example a line is: acronyms, numbers, product names, and the English terms said inside
    a non-English line ('GST', '3 hours', 'Excel', 'dashboard'). A human editor keeps examples; they are the proof."""
    hard = set(re.findall(r"\b[A-Z]{2,5}\b|\d+", text))
    names = {w for w in re.findall(r"(?<=[a-z,] )[A-Z][a-z]{3,}", text)}  # capitalised mid-sentence: Excel, Windows
    terms = set(gist(raw).lower().split()) if raw else set()
    return min(2.0, 0.6 * len(hard) + 0.4 * len(names) + 0.2 * len(terms))


def line_value(k: str, en: dict[str, str], by_id: dict[str, dict], strength: dict[str, float]) -> float:
    """Story value of a line: what it claims, how it was said, and how concrete it is."""
    text, raw = en.get(k, ""), by_id[k].get("text", "")
    if not text:  # no translation for this stretch: judge the line by its own words (numbers, English terms)
        from video_studio import brain

        digits = len(re.findall(r"\d+", raw))
        english = sum(1 for w in re.findall(r"[A-Za-z]+", raw) if w.lower() in brain.ENGLISH)
        return strength.get(k, 0.0) + 1.2 * digits + 0.3 * english - (0.0 if digits or english else 1.5)
    return claim_score(text, strength.get(k, 0.0)) + (1.0 if PAYOFF.search(text) else 0.0) + concrete(text, raw)


def fit(keep: list[str], target: float, en: dict[str, str], by_id: dict[str, dict], strength: dict[str, float],
        notes: list[str]) -> list[str]:
    """Cut the lines that add the least story value until the edit fits; never the opening line or the last one."""
    keep = list(keep)
    dur = lambda ids: sum(by_id[i]["end"] - by_id[i]["start"] for i in ids)  # noqa: E731
    while dur(keep) > target * 1.05 and len(keep) > 2:
        victim = min(keep[1:-1], key=lambda k: line_value(k, en, by_id, strength))
        keep.remove(victim)
        notes.append(f"Cut for length (least story value): \"{(en.get(victim) or by_id[victim]['text'])[:55]}\"")
    return keep


def claim_score(text: str, strength: float) -> float:
    from video_studio import brain

    toks = re.findall(r"[A-Za-z0-9']+", text)
    power = sum(1 for t in toks if brain._stem(t) in {brain._stem(p) for p in brain.HOOK_POWER} or t.isdigit())
    return strength + 0.8 * power + (1.0 if NEG.search(text) else 0.0) + (0.8 if "?" in text else 0.0) + 0.1 * len(_content(text))


def build(utts: list[dict], retakes: set[str], translation: list[dict], strength: dict[str, float], target: float | None,
          brief: str = "", reel: bool = True) -> tuple[list[str], list[str]]:
    """Returns (ordered utterance ids to keep, plain-English notes)."""
    notes: list[str] = []
    lines = [u for u in utts if u["id"] not in retakes]
    if not lines:
        return [], notes
    if not translation:  # no meaning available (old project): keep the recorded order, only fit the length
        return [u["id"] for u in lines], notes
    en = meanings(utts, translation)
    by_id = {u["id"]: u for u in lines}
    low_brief = brief.lower()
    keep = []
    for u in lines:
        text = en[u["id"]]
        if INTRO.search(text) and not any(k in low_brief for k in ("intro", "introduce", "name")) and len(lines) > 3:
            notes.append(f"Cut self-introduction: \"{text[:60]}\"")
            continue
        if SIGN_OFF.search(text) and not PAYOFF.search(text) and not any(k in low_brief for k in ("cta", "subscribe", "outro")) \
                and u is not lines[0]:
            notes.append(f"Cut sign-off: \"{text[:60]}\"")
            continue
        keep.append(u["id"])
    # repeats: the same point said twice -> keep the stronger take
    kept: list[str] = []
    for k in keep:
        c = _content(en[k])
        twin = next((j for j in kept if c and len(c & _content(en[j])) / max(1, len(c | _content(en[j]))) >= 0.5), None)
        if twin:
            weaker = k if strength.get(k, 0) <= strength.get(twin, 0) else twin
            if weaker == twin:
                kept[kept.index(twin)] = k
            notes.append(f"Cut a repeat of a point you make again later/earlier: \"{en[weaker][:60]}\"")
            continue
        kept.append(k)
    keep = kept or [lines[0]["id"]]
    # cold open: strongest self-contained claim first
    total = sum(by_id[k]["end"] - by_id[k]["start"] for k in keep)
    horizon = by_id[keep[0]]["start"] + 0.7 * (by_id[keep[-1]]["end"] - by_id[keep[0]]["start"])
    cands = [k for k in keep if by_id[k]["end"] - by_id[k]["start"] <= 7.0 and by_id[k]["start"] <= horizon
             and not DEPENDENT.search(en[k]) and en[k] and reliable(by_id[k], en[k])]
    if cands and len(keep) > 2:
        best = max(cands, key=lambda k: claim_score(en[k], strength.get(k, 0)))
        if best != keep[0] and claim_score(en[best], strength.get(best, 0)) >= claim_score(en[keep[0]], strength.get(keep[0], 0)) + 0.5:
            keep.remove(best)
            keep.insert(0, best)
            notes.append(f"Cold open: moved your strongest claim to the start - \"{en[best][:70]}\"")
    # end on the payoff
    pay = [i for i, k in enumerate(keep) if PAYOFF.search(en[k]) and i >= len(keep) * 0.6]
    if pay and pay[-1] < len(keep) - 1:
        tail = keep[pay[-1] + 1:]
        med = sorted(strength.get(k, 0) for k in keep)[len(keep) // 2]
        if all(strength.get(k, 0) <= med for k in tail):
            keep = keep[: pay[-1] + 1]
            notes.append(f"Ends on your payoff line - \"{en[keep[-1]][:70]}\" ({len(tail)} weaker closing line(s) cut)")
    # length: cut the lines that add the least STORY value (meaning + voice), never the cold open or the payoff
    if not target and reel and total > 62:
        target = 55.0
        notes.append("No length given: made it a ~55 s reel")
    if target:
        keep = fit(keep, target, en, by_id, strength, notes)
    return keep, notes


# ------------------------------------------------------------- AI senior editor (optional second opinion)

REVIEW_PROMPT = """You are a senior short-form video editor with a storyteller's ear. A creator recorded themselves
talking; below is every line they said. Each line has an id, its time and length, how strongly it was delivered
(voice, higher = more emphasis), the words the speech model heard (ORIGINAL - any language, may be mis-heard) and what
the line means in English (EN - trust this for meaning). Lines marked RETAKE are fumbled takes.

CREATOR'S BRIEF: {brief}
TARGET LENGTH: {target}
{memory}
A rule-based assistant drafted this cut: {draft}

Edit it the way an experienced human editor would. Think about the whole story first: what is the one idea, what
proves it, what is the payoff.
- Open on the single most arresting line that makes sense with NO context (a bold claim, a number, a question).
  Never open on a line the speech model clearly mangled.
- After the opening line, keep the remaining lines in their recorded order.
- Keep concrete examples, names and numbers, and the line that pays them off. Proof beats general talk.
- Cut self-introductions, sign-offs, repeats, filler, RETAKE lines and anything the story does not need.
- A line that starts mid-thought ("but", "so", "because", a list item) needs the line before it, or must go.
- End on the payoff or the call to try it, not on a trailing remark.
- Fit the target length (line lengths are given). Do not pad.

Return ONLY one JSON object:
{{
 "idea": "the video's one idea, <=14 words",
 "keep": ["u015", "u001", ...],            // ids in play order: the opening line first, then recorded order
 "reasons": {{"u007": "<=12 words: why you cut or kept this line"}},   // every line you treat differently from the draft
 "visuals": {{"u016": "tax form, GST invoice, accountant"}},   // for up to 6 kept lines: 2-4 concrete English nouns
                                            // of footage that would SHOW what is being said (not the speaker)
 "title": "<=6 word hook title",
 "hook_text": "1-3 punchy words slammed huge BEHIND the speaker in the first 2 seconds (caption language, Roman script for Hinglish)",
 "emphasis": ["word", ...],                // up to 8 single spoken words worth a punch-in zoom
 "slams": [{{"at": "u004", "word": "<a word actually spoken in that line, as transcribed>", "text": "1-2 words shown huge behind the speaker"}}],  // 2-5
 "brands": [{{"at": "u004", "name": "youtube"}}],   // products/platforms/companies actually mentioned, 0-4
 "graphics": [{{"at": "u004", "type": "keyword" | "lower_third", "text": "<=3 words", "sub": "<=5 words"}}],  // 0-5
 "look": one of {looks}, "music": one of {moods}, "captions": one of ["bold_pop","karaoke","clean","none"],
 "pacing": one of ["relaxed","balanced","fast"]
}}
LINES:
{lines}"""


def review_prompt(utts: list[dict], retakes: set[str], en: dict[str, str], strength: dict[str, float], draft: list[str],
                  target: float | None, brief: str, memory_text: str, looks: str, moods: str) -> str:
    rows = []
    for u in utts:
        rows.append(f"{u['id']} [{u['start']:.1f}s, {u['end'] - u['start']:.1f}s long, voice {strength.get(u['id'], 0.0):+.1f}]"
                    f"{' RETAKE' if u['id'] in retakes else ''}\n   ORIGINAL: {u['text']}\n   EN: {en.get(u['id']) or '(not translated)'}")
    return REVIEW_PROMPT.format(brief=brief or "make it engaging and tight",
                                target=f"about {target:.0f} seconds" if target else "as tight as possible, keep all good content",
                                memory=memory_text, draft=json_list(draft), looks=looks, moods=moods,
                                lines="\n".join(rows)[:60000])


def json_list(ids: list[str]) -> str:
    return "[" + ", ".join(f'"{i}"' for i in ids) + "]"


def accept_review(answer: dict, utts: list[dict], retakes: set[str], en: dict[str, str], strength: dict[str, float],
                  draft: list[str], target: float | None) -> tuple[list[str] | None, list[str]]:
    """Check the AI editor's cut against the rules a human editor never breaks. Returns (keep, notes); keep is None
    when the answer cannot be trusted (the rule-based draft is used instead)."""
    notes: list[str] = []
    by_id = {u["id"]: u for u in utts}
    raw = answer.get("keep") if isinstance(answer, dict) else None
    if not isinstance(raw, list):
        return None, ["AI editor gave no usable cut; kept the rule-based story"]
    keep = list(dict.fromkeys(k for k in raw if k in by_id and k not in retakes))
    dur = lambda ids: sum(by_id[i]["end"] - by_id[i]["start"] for i in ids)  # noqa: E731
    floor = 0.5 * (target or dur(draft) or 1.0)
    if len(keep) < 2 or dur(keep) < min(floor, 0.8 * dur(draft)):
        return None, ["AI editor's cut was too thin to be a story; kept the rule-based story"]
    opener, rest = keep[0], sorted(keep[1:], key=lambda k: by_id[k]["start"])
    first = min(keep, key=lambda k: by_id[k]["start"])
    if opener != first and (DEPENDENT.search(en.get(opener, "")) or not reliable(by_id[opener], en.get(opener, ""))):
        # the opening it chose leans on an earlier line, or the speech model missed most of it: use the draft's opening
        fallback = draft[0] if draft and draft[0] in by_id else first
        notes.append(f"AI editor's opening line could not stand alone; opening kept as \"{(en.get(fallback) or by_id[fallback]['text'])[:60]}\"")
        keep = [fallback] + sorted((k for k in keep if k != fallback), key=lambda k: by_id[k]["start"])
    else:
        keep = [opener] + rest
    if target and dur(keep) > target * 1.12:
        keep = fit(keep, target, en, by_id, strength, notes)
    reasons = answer.get("reasons") if isinstance(answer.get("reasons"), dict) else {}
    head = []
    if answer.get("idea"):
        head.append(f"The idea: {str(answer['idea'])[:110]}")
    if keep[0] != (draft[0] if draft else None):
        head.append(f"Opens on: \"{(en.get(keep[0]) or by_id[keep[0]]['text'])[:70]}\"")
    for u in utts:  # only what the AI editor changed, in recorded order
        k = u["id"]
        if (k in keep) != (k in draft):
            verb = "Kept" if k in keep else "Cut"
            why = str(reasons.get(k) or "").strip()[:90]
            head.append(f"{verb}: \"{(en.get(k) or u['text'])[:55]}\"" + (f" - {why}" if why else ""))
    return keep, head + notes


def visuals(answer: dict, keep: list[str]) -> dict[str, str]:
    """What footage would show each kept line (English nouns from the AI editor); used to find B-roll."""
    v = answer.get("visuals") if isinstance(answer, dict) else None
    if not isinstance(v, dict):
        return {}
    return {k: (" ".join(map(str, t)) if isinstance(t, list) else str(t))[:80]
            for k, t in v.items() if k in keep and isinstance(t, (str, list)) and t}


# ------------------------------------------------------------- decision assist: choices and a forecast

def _why_opening(text: str, strength: float) -> str:
    from video_studio import brain

    why = []
    if re.search(r"\d", text):
        why.append("has a number")
    if "?" in text:
        why.append("asks a question")
    if NEG.search(text):
        why.append("a bold 'no/never' claim")
    power = {brain._stem(p) for p in brain.HOOK_POWER}
    if any(brain._stem(t) in power for t in re.findall(r"[A-Za-z']+", text)):
        why.append("uses a hook word")
    if strength > 0.6:
        why.append("you said it with force")
    return ", ".join(why) or "clear and self-contained"


def openings(utts: list[dict], retakes: set[str], en: dict[str, str], strength: dict[str, float], keep: list[str],
             n: int = 3) -> list[dict]:
    """The best lines to open on, for the creator to choose from. The current opening is always among them.
    Scores come from editing rules (claim, number, question, delivery) - not from audience data."""
    cands = [u for u in utts if u["id"] not in retakes and en.get(u["id"]) and u["end"] - u["start"] <= 8.0
             and not DEPENDENT.search(en[u["id"]]) and not INTRO.search(en[u["id"]]) and not SIGN_OFF.search(en[u["id"]])
             and reliable(u, en[u["id"]])]
    ranked = sorted(cands, key=lambda u: claim_score(en[u["id"]], strength.get(u["id"], 0.0)), reverse=True)
    by_id = {u["id"]: u for u in utts}
    pick = [u["id"] for u in ranked[:n]]
    if keep and keep[0] not in pick and keep[0] in by_id:
        pick = [keep[0]] + pick[: n - 1]
    return [{"id": k, "en": en.get(k) or by_id[k]["text"], "text": by_id[k]["text"], "seconds": round(by_id[k]["end"] - by_id[k]["start"], 1),
             "score": round(claim_score(en.get(k, ""), strength.get(k, 0.0)), 1), "why": _why_opening(en.get(k, ""), strength.get(k, 0.0)),
             "chosen": bool(keep) and k == keep[0]} for k in pick]


def forecast(utts: list[dict], keep: list[str], en: dict[str, str], strength: dict[str, float], visuals: dict[str, str]) -> list[dict]:
    """What a human editor would warn about before publishing. Each item: {level: 'risk'|'good', text}. These are
    predictions from editing rules, stated as such; they are not measured audience retention."""
    by_id = {u["id"]: u for u in utts}
    out: list[dict] = []
    if not keep:
        return out
    first = by_id[keep[0]]
    f_en = en.get(first["id"], "")
    d = first["end"] - first["start"]
    if d > 6.0:
        out.append({"level": "risk", "text": f"The opening line runs {d:.0f} s. Viewers decide in about 3 s - consider a shorter opening (see the choices)."})
    if re.search(r"\d", f_en) or "?" in f_en or NEG.search(f_en):
        out.append({"level": "good", "text": "The opening has a number, a question or a bold claim - a reason to keep watching."})
    else:
        out.append({"level": "risk", "text": "The opening has no number, question or bold claim. It may not stop the scroll."})
    early, t = [], 0.0
    for k in keep:
        if t < 12.0:
            early.append(k)
        t += by_id[k]["end"] - by_id[k]["start"]
    if not any(concrete(en.get(k, ""), by_id[k].get("text", "")) >= 0.6 for k in early):
        out.append({"level": "risk", "text": "No concrete proof (a name, a number, an example) in the first 12 s."})
    shaky = [k for k in keep if en.get(k) and not reliable(by_id[k], en[k])]
    if shaky:
        out.append({"level": "risk", "text": f"The speech model missed words in {len(shaky)} kept line(s); captions will have gaps there. Check them in the Lines tab."})
    last = en.get(keep[-1], "")
    if PAYOFF.search(last) or re.search(r"\d", last):
        out.append({"level": "good", "text": "It ends on a payoff or a call to try it."})
    else:
        out.append({"level": "risk", "text": "The last line is not a clear payoff or call to action."})
    long_plain = [k for k in keep[1:] if by_id[k]["end"] - by_id[k]["start"] > 7.0 and k not in visuals]
    if len(long_plain) >= 2:
        out.append({"level": "risk", "text": f"{len(long_plain)} lines run over 7 s with nothing to cut away to. B-roll for them would hold attention."})
    total = sum(by_id[k]["end"] - by_id[k]["start"] for k in keep)
    if total > 62:
        out.append({"level": "risk", "text": f"About {total:.0f} s: over the 60 s limit for YouTube Shorts."})
    return out
