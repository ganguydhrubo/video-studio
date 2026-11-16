"""The local editor brain: every edit decision made on this computer, no LLM required.

Signals (all local):
  - prosody per word: loudness, pitch and stretch relative to the speaker's own running baseline
    (people stress what matters: louder, higher, slower)
  - lexical importance: content words, numbers, negations, questions, English loanwords inside Hindi
    (in Hinglish the key terms are usually English: product, launch, revenue, digital)
  - position: hooks come from strong claims early in the take
Weights live in brain_weights.json (app data) and are tuned by `video-studio learn` from your own past edits
and by your ratings, so the brain adapts to how *you* edit. Same input -> same output, on any machine.
"""

from __future__ import annotations

import json
import math
import re
import statistics
from pathlib import Path

from video_studio.util import app_data_dir, read_json, write_json

# Prosody weights follow the published cue ranking for stress (pitch > loudness > duration > pauses);
# lexical bonuses from editor practice. Tuned per creator by `video-studio learn` (see docs/research/editing_grammar.md).
DEFAULT_WEIGHTS = {"f0": 0.30, "db": 0.25, "stretch": 0.20, "pause_before": 0.10, "pause_after": 0.15,
                   "content": 1.0, "number": 0.8, "negation": 0.6, "loanword": 0.7, "cue": 0.5, "question": 0.4,
                   "long": 0.25, "repeat_topic": 0.3, "stop": -2.2, "bias": 0.0}
CUE_WORDS = {"lekin", "लेकिन", "but", "magar", "मगर", "sabse", "सबसे", "best", "worst", "only", "sirf", "सिर्फ",
             "never", "always", "hamesha", "हमेशा", "secret", "free", "₹", "rupees", "rupaye", "रुपये", "lakh", "crore",
             "biggest", "first", "last", "zero", "100%", "guarantee", "truth", "sach", "सच"}

HI_STOP = set("""है हैं हूँ हूं था थी थे के का की को में से पर और या तो ये यह वो वह जो जब तक भी ही ना न
कि कर करो करना करते करता कुछ एक इस उस इन उन हम आप तुम मैं मेरा मेरी तेरा अपना अपने अपनी
हो होगा होगी होता होते रहा रही रहे गया गई गए दिया लिए लिया वाला वाली वाले साथ बाद पहले फिर
अब यहाँ वहाँ क्या कैसे क्यों कौन जैसे ऐसा ऐसी ऐसे बस तो हाँ हां जी अरे
hai hain hoon tha thi the ke ka ki ko mein me se par aur ya to ye yeh wo woh jo jab tak bhi hi na ki kar karo
karna karte karta kuch ek is us in un hum aap tum main mera meri apna apne apni ho hoga hota hote raha rahi
rahe gaya gayi diya liye liya wala wali wale saath baad pehle phir ab yahan wahan kya kaise kyun kaun jaise
aisa aisi aise bas haan ji are aaj abhi kal bata batao batata batati batate bolo bolta bolti dekho dekh chalo
chal karte karti raha rahe log logo logon wali wale yaar bhai dosto doston matlab waise vaise isliye kyunki
sakte sakta sakti dono koi har sab sabhi kuch bahut zyada thoda sirf bilkul aapka aapke aapki apna apne hamara humara unka
कोई हर सब सभी कुछ बहुत ज्यादा ज़्यादा थोड़ा आपका आपके आपकी हमारा उनका
आज अभी बता बताओ बताता बताती बोलो देखो चलो यार भाई दोस्तों मतलब वैसे इसलिए क्योंकि लोग लोगों""".split())
EN_STOP = set("""a an the and or but if of to in on at for with from by is are was were be been being it this that
these those i you he she we they me my your our their his her its so do does did not no yes can will would should
could just very really also then than there here what when where who how why all any some more most much many
one two get got have has had going go""".split()) - {"not", "no", "never"}
NEGATIONS = {"nahi", "nahin", "नहीं", "नही", "मत", "mat", "never", "no", "not", "kabhi", "कभी", "bilkul", "बिल्कुल"}
NUMBER_WORDS = {"ek", "do", "teen", "char", "paanch", "das", "sau", "hazaar", "lakh", "crore", "percent", "pratishat",
                "एक", "दो", "तीन", "चार", "पांच", "दस", "सौ", "हज़ार", "हजार", "लाख", "करोड़", "प्रतिशत", "हंड्रेड", "थाउजेंड"}

# English vocabulary creators use inside Hindi speech: used to restore proper spelling after transliteration
LOANWORDS = """agentic agent agents multi working work founder product products launch revenue digital marketing market social media online offline business startup
brand brands content creator creators video videos reel reels short shorts youtube instagram facebook whatsapp google
app apps website software ai agent agents automate automation tool tools data money profit loss sales customer
customers client clients team office job jobs career skill skills course success successful strategy plan target
goal growth scale system ecosystem time right place wait start today tomorrow idea ideas problem solution question
answer mistake mistakes tips trick tricks secret secrets free paid price cost budget investment invest income salary
phone mobile laptop computer internet email channel subscribe follow like share comment viral trend trending
algorithm views reach audience brand motors motor company companies service services design editing editor edit
camera light sound music hook story value quality power energy focus mindset motivation hard work smart daily week
month year minute minutes second seconds hour hours percent level next step steps process result results challenge
explore future life game lucky luck dream dreams limit limits reality fake real power simple easy best better
tough tax taxes income window windows story stories hard soft simple sample example free share market stock stocks
bank banks loan loans credit card cards account accounts payment payments upi online shopping order orders delivery
offer offers discount sale deal deals review reviews rating feedback support help service guide course class classes
school college university exam exams result marks degree student students teacher teachers coach coaching training
practice project projects report reports file files folder document documents excel sheet sheets slide slides
presentation meeting meetings call calls message messages chat group groups post posts page pages profile followers
following subscriber subscribers creator influencer brand collab sponsor sponsorship campaign ads ad advertisement
engine model models chatgpt gemini claude prompt prompts code coding developer developers website websites domain
hosting server cloud storage backup password security privacy hack hacker update updates version feature features
setting settings option options button screen display battery charger charging speaker mic microphone headphone
camera lens photo photos picture pictures selfie reel shoot shooting edit editing render export upload download
install installer laptop desktop pc keyboard mouse gaming games gamer stream streaming live news trend trending
fashion style outfit makeup skin hair fitness gym workout diet protein health doctor hospital medicine
travel trip hotel flight ticket tickets train bus car bike petrol diesel electric ev motors motor
manager management leader leadership boss employee employees hr interview resume cv salary hike bonus
startup founder investor investors funding pitch revenue profit growth marketing sales strategy brand branding
customer client value price cost budget plan planning target targets goal goals mindset motivation discipline
consistency focus energy time management productivity habit habits routine morning night weekend holiday
problem problems solution solutions question questions answer answers idea ideas tip tips trick tricks hack hacks
mistake mistakes lesson lessons secret secrets truth fact facts myth myths reason reasons process system systems
""".split()

V_SIGNS = {"ा": "aa", "ि": "i", "ी": "ee", "ु": "u", "ू": "oo", "ृ": "ri", "े": "e", "ै": "ai", "ो": "o", "ौ": "au",
           "ॉ": "o", "ॅ": "e", "ॆ": "e", "ॊ": "o"}
VOWELS = {"अ": "a", "आ": "aa", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo", "ऋ": "ri", "ए": "e", "ऐ": "ai", "ओ": "o",
          "औ": "au", "ऑ": "o", "ऍ": "e"}
CONS = {"क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
        "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
        "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh",
        "ष": "sh", "स": "s", "ह": "h", "क़": "q", "ख़": "kh", "ग़": "g", "ज़": "z", "ड़": "r", "ढ़": "rh", "फ़": "f", "ळ": "l"}
NUKTA = {"क": "क़", "ख": "ख़", "ग": "ग़", "ज": "ज़", "ड": "ड़", "ढ": "ढ़", "फ": "फ़"}
DIGITS = {chr(0x966 + i): str(i) for i in range(10)}
COMMON = {"है": "hai", "हैं": "hain", "नहीं": "nahi", "में": "mein", "मैं": "main", "हूँ": "hoon", "हूं": "hoon",
          "क्या": "kya", "क्यों": "kyun", "और": "aur", "यह": "yeh", "वह": "woh", "आप": "aap", "हम": "hum",
          "कि": "ki", "की": "ki", "का": "ka", "के": "ke", "को": "ko", "से": "se", "भी": "bhi", "ही": "hi", "तो": "to",
          "ये": "ye", "वो": "wo", "जो": "jo", "कर": "kar", "करो": "karo", "एक": "ek", "हाँ": "haan", "हां": "haan"}


def weights() -> dict:
    return {**DEFAULT_WEIGHTS, **(read_json(app_data_dir() / "brain_weights.json", {}) or {})}


def save_weights(w: dict) -> None:
    write_json(app_data_dir() / "brain_weights.json", w)


# ----------------------------------------------------------------- transliteration

def _skeleton(word: str) -> str:
    """Phonetic consonant skeleton shared by English spellings and Hindi transliterations."""
    w = word.lower()
    w = re.sub(r"(?<=[aeiou])gh$", "f", w)    # tough ~ taf, enough ~ inaf
    w = re.sub(r"ow$", "o", w)                 # window ~ vindo, follow ~ folo
    w = re.sub(r"g(?=[eiy])", "j", w)          # soft g: digital ~ dijital, strategy ~ streteji
    w = re.sub(r"c(?=[eiy])", "s", w)          # soft c: success ~ sakses
    w = w.replace("ph", "f").replace("ck", "k").replace("c", "k").replace("q", "k").replace("x", "ks").replace("w", "v")
    w = w.replace("th", "t").replace("dh", "d").replace("bh", "b").replace("kh", "k").replace("gh", "g").replace("sh", "s")
    w = w.translate(str.maketrans("dgbz", "tkps"))  # voicing differences are common in loanwords
    w = re.sub(r"[aeiouyh]", "", w)
    return re.sub(r"(.)\1+", r"\1", w)


def _vocab() -> list[str]:
    from video_studio.util import resource_path

    base = set(LOANWORDS)
    try:
        base |= set(resource_path("assets", "english_words.txt").read_text(encoding="utf-8").split())
    except OSError:
        pass
    forms = set(base)
    for w in base:  # common inflections: working, agents, launched, founder, agentic
        stem = w[:-1] if w.endswith("e") else w
        forms |= {w + "s", stem + "ing", stem + "ed"}
    return sorted(forms)


CORE = set(LOANWORDS)  # what creators actually say; wins ties against rarer look-alikes (launch > lunch)
ENGLISH = set(_vocab())
LOANWORDS = sorted(CORE | ENGLISH)
_LOAN_INDEX: dict[str, list[str]] = {}
for _w in LOANWORDS:
    _LOAN_INDEX.setdefault(_skeleton(_w), []).append(_w)


def transliterate(word: str) -> str:
    """Devanagari -> casual Roman (Hinglish) with English loanwords restored ('प्रोडक्ट' -> 'product')."""
    core = word.strip()
    if not core or not any("ऀ" <= c <= "ॿ" for c in core):
        return word
    punct = " .,!?;:\"'()[]।…-"
    body = core.strip(punct)
    lead = core[:core.find(body)] if body else ""
    trail = core[len(lead) + len(body):]
    if body in COMMON:
        return lead + COMMON[body] + trail.replace("।", ".")
    out, i, chars = [], 0, list(body)
    while i < len(chars):
        c = chars[i]
        if i + 1 < len(chars) and chars[i + 1] == "़" and c in NUKTA:  # nukta
            c = NUKTA[c]
            i += 1
        nxt = chars[i + 1] if i + 1 < len(chars) else ""
        if c in CONS:
            out.append(CONS[c])
            if nxt == "्":  # halant: no vowel
                i += 2
                continue
            if nxt in V_SIGNS:
                out.append(V_SIGNS[nxt])
                i += 2
                continue
            out.append("a")  # inherent vowel (schwa), pruned below
        elif c in VOWELS:
            out.append(VOWELS[c])
        elif c in ("ं", "ँ"):
            out.append("n")
        elif c == "ः":
            out.append("h")
        elif c in DIGITS:
            out.append(DIGITS[c])
        elif c in V_SIGNS:
            out.append(V_SIGNS[c])
        elif c.isascii():
            out.append(c)
        i += 1
    roman = "".join(out)
    # schwa deletion: final 'a' after a consonant, and 'a' in C-a-C-V(owel) word-medial positions
    if len(roman) > 2 and roman.endswith("a") and not roman.endswith("aa") and len(body) > 1:
        roman = roman[:-1]
    roman = re.sub(r"(?<=[aeiou][bcdfghjklmnpqrstvwxyz])a(?=[bcdfghjklmnpqrstvwxyz][aeiou])", "", roman) if len(roman) > 5 else roman
    roman = re.sub(r"aa$", "a", roman)
    roman = roman.replace("ee", "i") if roman.endswith("ee") else roman
    sk = _skeleton(roman)
    cands = _LOAN_INDEX.get(sk) if len(sk) >= 2 and len(roman) >= 3 and (len(sk) >= 3 or any(ord(c) > 0x97f or c in "ॉ" for c in body)) else None
    if cands:
        from difflib import SequenceMatcher

        probe = roman.replace("ks", "x").replace("ph", "f").replace("aa", "a")
        best = max(cands, key=lambda c: (SequenceMatcher(None, probe, c).ratio() + (0.2 if c in CORE else 0.0), -len(c)))
        if SequenceMatcher(None, probe, best).ratio() >= (0.5 if best in CORE else 0.7):  # same sound AND similar spelling
            roman = best
    return lead + roman + trail.replace("।", ".")


def romanize_words(words: list[dict]) -> list[dict]:
    return [{**w, "text": transliterate(w["text"])} for w in words]


# ----------------------------------------------------------------- scoring

def _tok(text: str) -> str:
    return text.strip().strip(".,!?;:\"'()[]।…-").lower()


def lexical(text: str, roman: str) -> dict:
    t, r = _tok(text), _tok(roman)
    is_stop = t in HI_STOP or r in HI_STOP or r in EN_STOP or len(r) <= 1
    return {
        "stop": 1.0 if is_stop and t not in NEGATIONS and r not in NEGATIONS else 0.0,
        "content": 0.0 if is_stop else 1.0,
        "number": 1.0 if re.search(r"\d", t + r) or t in NUMBER_WORDS or r in NUMBER_WORDS else 0.0,
        "negation": 1.0 if t in NEGATIONS or r in NEGATIONS else 0.0,
        "loanword": 1.0 if (r in LOANWORDS or (text.isascii() and r.isalpha() and r not in EN_STOP)) and not is_stop else 0.0,
        "question": 1.0 if text.strip().endswith("?") else 0.0,
        "cue": 1.0 if t in CUE_WORDS or r in CUE_WORDS else 0.0,
        "long": 1.0 if len(r) >= 6 else 0.0,
    }


def _z(values: list[float | None], window: int = 12) -> list[float]:
    """Deviation from the speaker's running baseline (robust: median / MAD in a sliding window)."""
    out = []
    for i, v in enumerate(values):
        if v is None:
            out.append(0.0)
            continue
        ctx = [x for x in values[max(0, i - window):i + window + 1] if x is not None]
        if len(ctx) < 4:
            out.append(0.0)
            continue
        med = statistics.median(ctx)
        mad = statistics.median(abs(x - med) for x in ctx) or 1e-6
        out.append(max(-3.0, min(3.0, (v - med) / (1.4826 * mad))))
    return out


def score_words(words: list[dict], roman: list[dict], prosody: list[dict] | None, w: dict | None = None) -> list[dict]:
    w = w or weights()
    pr = prosody or [{} for _ in words]
    db = _z([p.get("db") for p in pr])
    f0 = _z([p.get("f0") for p in pr])
    stretch = _z([(p.get("dur") or (x["end"] - x["start"])) / max(1, len(_tok(r["text"]))) for p, x, r in zip(pr, words, roman)])
    seen: dict[str, int] = {}
    for r in roman:
        k = _tok(r["text"])
        seen[k] = seen.get(k, 0) + 1
    out = []
    for i, (x, r) in enumerate(zip(words, roman)):
        lx = lexical(x["text"], r["text"])
        gap_b = x["start"] - words[i - 1]["end"] if i else 0.0
        gap_a = words[i + 1]["start"] - x["end"] if i + 1 < len(words) else 0.0
        feats = {**lx, "db": db[i], "f0": f0[i], "stretch": stretch[i],
                 "pause_before": 1.0 if gap_b > 0.15 else 0.0, "pause_after": 1.0 if gap_a > 0.25 else 0.0,
                 "repeat_topic": 1.0 if seen.get(_tok(r["text"]), 0) >= 2 and lx["content"] else 0.0}
        s = w["bias"] + sum(w.get(k, 0.0) * v for k, v in feats.items())
        prob = float(x.get("p", x.get("prob", 1.0)))
        disp = _tok(r["text"])
        clean = prob >= (0.6 if disp in ENGLISH else 0.8) and not re.search(r"(\w{2,3})\1", disp) and len(disp) >= 2 and (
            disp in ENGLISH or lx["number"] or len(disp) >= 4)
        out.append({"i": i, "text": x["text"], "display": r["text"], "start": x["start"], "end": x["end"], "clean": clean,
                    "score": round(s, 3), "features": {k: round(v, 3) for k, v in feats.items()}})
    return out


# Hindi words a human editor would put on screen: they carry meaning on their own (not helper verbs or fillers)
POWER_HI = set("""zindagi zindagee paisa paise sapna sapne galti galtiyan sach jhooth dil dimaag mehnat kismat izzat dost
ghar duniya bharat desh naukri kamai crore karod lakh hazaar muft khatarnak zabardast kamaal asaan aasaan mushkil sabse
akela dhokha safalta junoon shuruaat badlaav azaadi samay waqt khushi dard darr dar himmat taakat raaz galat sahi kaam
sach paisa gyaan seekh sapno jeet haar bhavishya future maa papa pyaar bharosa vishwas imaandari sapna""".split())
# verb endings / auxiliaries that never go on screen alone
HI_AUX = ("ega", "egi", "enge", "unga", "ungi", "enge", "kar", "karo", "karna", "karke", "raha", "rahi", "rahe", "gaya",
          "gayi", "gaye", "dega", "degi", "denge", "lega", "legi", "lenge", "hoga", "hogi", "honge", "sakta", "sakte", "sakti")


def punchable(s: dict) -> bool:
    """Would a human editor slam this word on screen? English terms, numbers, or strong Hindi words only."""
    d = _tok(s["display"])
    if not s.get("clean", True) or s["features"].get("stop") or len(d) < 2:
        return False
    if s["features"].get("number"):
        return True
    if d in HI_AUX or d.endswith(HI_AUX[:6]):
        return False
    if d in POWER_HI:
        return True
    return d in ENGLISH and d not in EN_STOP and len(d) >= 3


def pick_emphasis(scored: list[dict], k: int, min_gap: float = 1.8, after: float = 0.0) -> list[dict]:
    picks = []
    for s in sorted(scored, key=lambda x: -(x["score"] + (0.8 if _tok(x["display"]) in ENGLISH else 0.0))):
        if len(picks) >= k:
            break
        if not punchable(s) or s["start"] < after or any(abs(s["start"] - p["start"]) < min_gap for p in picks):
            continue
        if any(_tok(p["display"]) == _tok(s["display"]) for p in picks):
            continue
        picks.append(s)
    return sorted(picks, key=lambda x: x["start"])


def utterance_strength(utts: list[dict], scored: list[dict]) -> dict[str, float]:
    out = {}
    for u in utts:
        ws = [s for s in scored if u["start"] - 0.01 <= s["start"] <= u["end"] + 0.01]
        if not ws:
            out[u["id"]] = -5.0
            continue
        top = sorted((s["score"] for s in ws), reverse=True)[:3]
        density = sum(1 for s in ws if s["features"]["content"]) / len(ws)
        out[u["id"]] = round(sum(top) / len(top) + 1.2 * density, 3)
    return out


def choose_hook(utts: list[dict], scored: list[dict], strength: dict[str, float], max_words: int = 4) -> dict:
    """The hook a human would write: a short CONTIGUOUS phrase (2-4 words) from the strongest early line that carries
    the claim - e.g. 'AI AGENTS KYA HAI?' - never scattered keywords. Every word must be cleanly heard; the phrase must
    contain at least one meaningful word and start on one."""
    if not utts:
        return {"utt": None, "words": [], "text": ""}
    horizon = utts[: max(2, math.ceil(len(utts) * 0.6))]
    cands = sorted(horizon, key=lambda u: -(strength.get(u["id"], -9) + (0.4 if u is utts[0] else 0.0)))[:3]
    best, best_val = None, -1e9
    for rank, u in enumerate(cands):
        ws = [x for x in scored if u["start"] - 0.01 <= x["start"] <= u["end"] + 0.01]
        for i in range(len(ws)):
            for n in range(2, max_words + 1):
                win = ws[i:i + n]
                if len(win) < n or not all(x.get("clean", True) for x in win):
                    continue
                if not punchable(win[0]) and not win[0]["features"].get("question"):
                    continue
                strong = [x for x in win if punchable(x)]
                if not strong:
                    continue
                stops = sum(1 for x in win if x["features"].get("stop"))
                last_ok = not win[-1]["features"].get("stop") or win[-1]["features"].get("question") \
                    or win[-1]["features"].get("negation") or _tok(win[-1]["display"]) in ("hai", "hain")
                val = (sum(max(0.0, x["score"]) + 1.0 for x in strong)
                       + 0.8 * any(x["features"].get("question") or x["features"].get("negation") for x in win)
                       - 0.35 * stops - 0.3 * abs(n - 3) - (0.0 if last_ok else 1.0) - 0.4 * rank)
                if val > best_val:
                    best, best_val = (u, win), val
    if best is None:  # nothing clean and meaningful: no hook text rather than a wrong one
        return {"utt": cands[0]["id"], "words": [], "text": ""}
    u, win = best
    text = " ".join(_tok(x["display"]) for x in win).upper()
    if any(x["features"].get("question") for x in win):
        text += "?"
    return {"utt": u["id"], "words": [x["i"] for x in win], "text": text}


# ---------------------------------------------------------------- meaning (any language, via the English translation)

HOOK_POWER = set("""change changed changing life money free secret never always why how mistake mistakes easy easier world
future job jobs business rich poor truth lie fail failure success win lose lost best worst only first last everything
nothing nobody everyone stop start quit save earn million billion crore lakh minutes hours days years zero hundred
thousand fast faster simple impossible dangerous shocking real fake hidden power powerful new revenue profit month week
today tomorrow overnight""".split())
HOOK_NEG = {"no", "not", "never", "nothing", "nobody", "don't", "won't", "can't", "isn't", "without"}
HOOK_PRONOUN_END = {"you", "your", "my", "his", "her", "their", "our", "today's", "such"}
SIGN_OFF = ("see you", "that's all", "thats all", "subscribe", "like and share", "bye", "thank you", "thanks for watching",
            "see you tomorrow", "see you next", "follow me", "comment below", "that's it for")
_LEAD_FILLER = {"but", "and", "so", "then", "now", "well", "because", "that", "this", "it", "the", "a", "an", "also",
                "just", "really", "actually", "basically", "i", "we", "or", "whether", "any", "there", "is", "of", "like", "here"}


def _stem(w: str) -> str:
    w = w.lower().strip(".,!?;:'\"()")
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > 4 and w.endswith(suf):
            w = w[: -len(suf)]
            break
    return w.rstrip("e") or w


def _en_tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[A-Za-z0-9']+", text)]


def verify_with_translation(scored: list[dict], words: list[dict], translation: list[dict]) -> None:
    """A word guessed as English from how it sounds ('lagta' -> 'light') only counts if the English translation of
    that moment actually says it. Works for any spoken language; Latin-script words are taken as spoken."""
    segs = [(t["start"], t["end"], {_stem(x) for x in _en_tokens(t["text"])}) for t in translation if t.get("text")]
    if not segs:
        return
    for s, w in zip(scored, words):
        native_latin = all(ord(c) < 0x250 for c in w["text"])
        d = _tok(s["display"])
        if native_latin or d not in ENGLISH:
            continue
        near = set().union(*[toks for a, b, toks in segs if a - 1.0 <= s["start"] <= b + 1.0]) if segs else set()
        if _stem(d) not in near and d not in near:
            s["clean"] = False  # a sound-alike, not what was said
            s["features"]["unverified"] = 1.0


def meaning_hook(translation: list[dict], utts: list[dict], strength: dict[str, float], total: float) -> dict:
    """The hook a human editor writes from what was MEANT: the strongest clause of the translated speech, cut to 2-5
    words. E.g. 'Job, business, everything is going to change completely' -> 'EVERYTHING IS GOING TO CHANGE'."""
    best, best_val = None, -1e9
    for t in translation:
        text = t.get("text", "").strip()
        low = text.lower()
        if not text or any(k in low for k in SIGN_OFF):
            continue
        u_str = [strength.get(u["id"], 0.0) for u in utts if u["start"] < t["end"] and u["end"] > t["start"]]
        for clause in re.split(r"[,.;:!?]| but | and ", text):
            toks = [x for x in _en_tokens(clause)]
            while toks and toks[0].lower() in _LEAD_FILLER:
                toks = toks[1:]
            if len(toks) < 2:
                continue
            for i in range(len(toks)):
                for n in range(2, 6):
                    win = toks[i:i + n]
                    if len(win) < n or (win[0].lower() in _LEAD_FILLER and win[0].lower() not in HOOK_NEG)                             or win[-1].lower() in EN_STOP - {"not", "no"}:
                        continue
                    content = [x for x in win if x.lower() not in EN_STOP]
                    power = sum(1 for x in win if _stem(x) in {_stem(p) for p in HOOK_POWER} or x.isdigit())
                    neg = any(x.lower() in HOOK_NEG for x in win)
                    if not neg and any(x.lower() in HOOK_NEG for x in _en_tokens(text)):
                        continue  # dropping the 'no' would flip the meaning
                    if any(x.lower() in HOOK_PRONOUN_END for x in win[-1:]):
                        continue
                    val = (1.0 * len(content) + 1.6 * power - 0.25 * abs(n - 4) + 0.4 * (max(u_str) if u_str else 0.0)
                           - 1.2 * (t["start"] / max(total, 1.0)) + (0.6 if "?" in text else 0.0) + (1.4 if neg else 0.0))
                    if val > best_val:
                        best, best_val = {"text": " ".join(win).upper() + ("?" if "?" in clause else ""),
                                          "start": t["start"], "end": t["end"], "score": round(val, 2)}, val
    return best or {}


def analyze(words: list[dict], prosody: list[dict] | None, utts: list[dict], roman: list[dict] | None = None,
            n_slams: int = 4, translation: list[dict] | None = None) -> dict:
    roman = roman or romanize_words(words)
    scored = score_words(words, roman, prosody)
    if translation:
        verify_with_translation(scored, words, translation)
    strength = utterance_strength(utts, scored)
    hook = choose_hook(utts, scored, strength)
    if translation:
        total = words[-1]["end"] if words else 1.0
        mh = meaning_hook(translation, utts, strength, total)
        idx = set(hook.get("words") or [])
        n_english = sum(1 for x in scored if x["i"] in idx and _tok(x["display"]) in ENGLISH and x.get("clean", True))
        # your own words when they are already a strong English phrase (Hinglish creators); otherwise the meaning
        if mh and n_english < 3:  # a 2-word topic label ('DIGITAL PRODUCT') loses to the actual claim
            hook = {**hook, "text": mh["text"], "source": "meaning", "meaning": mh}
        elif mh:
            hook = {**hook, "source": "spoken", "meaning": mh}
    hook_end = next((u["end"] for u in utts if u["id"] == hook["utt"]), 0.0)
    slams = pick_emphasis([s for s in scored if s["i"] not in hook["words"]], n_slams, min_gap=3.6, after=0.0)
    return {"scored": scored, "strength": strength, "hook": hook, "hook_end": hook_end,
            "slams": [{"word": s["text"], "text": _tok(s["display"]).upper(), "t": s["start"], "score": s["score"]} for s in slams],
            "emphasis": [_tok(s["text"]) for s in pick_emphasis(scored, 8, min_gap=1.0)]}


def save_debug(path: Path, result: dict) -> None:
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
