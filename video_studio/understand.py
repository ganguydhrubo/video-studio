"""Understand a free-text prompt: every engine choice it names, in plain words, and what cannot be done.

The person writes what they want ("black and white, slow and cinematic, no music, Hinglish subtitles, 30 sec reel").
`understand()` turns it into the same options the Fine-tune panel sets, so the prompt really controls the edit:
format, length, style recipe, colour look, pace, motion, effects, transitions, captions, music, silences, framing.
It also lists, honestly, what the request asks for that Takebloom cannot do, so nothing is silently ignored.

Local and instant (rules, no AI): it runs while the person types and again when the edit starts. A choice made in
Fine-tune always wins over the prompt. The AI editor still reads the whole prompt for the story itself.
"""

from __future__ import annotations

import re

from video_studio.briefing import local_intent

# (pattern, option, value, what to say). The first match for an option wins, so "no music" comes before "music".
RULES: list[tuple[str, str, object, str]] = [
    # format
    (r"\b(reels?|shorts|youtube short|tik ?tok|vertical|portrait|9[:x/]16|instagram stor(?:y|ies)|whatsapp status)\b", "aspect", "9:16", "Vertical 9:16"),
    (r"\b(youtube video|(?:for|on) youtube(?! shorts?)|youtube channel|landscape|horizontal|widescreen|16[:x/]9|long ?form|full ?video for youtube)\b", "aspect", "16:9", "Widescreen 16:9"),
    (r"\b(square|1[:x/]1)\b", "aspect", "1:1", "Square 1:1"),
    (r"\b(4[:x/]5|instagram (?:feed|post))\b", "aspect", "4:5", "Portrait post 4:5"),
    # style recipe (sets pace, captions, music and look together; the person's other words still win)
    (r"\bpodcast\b", "template", "podcast_clip", "Podcast clip style"),
    (r"\b(wedding|shaadi|marriage)\b", "template", "wedding", "Wedding film style"),
    (r"\b(travel|trip|vacation|holiday|tour) ?(vlog|montage|video|film)?\b", "template", "travel", "Travel style"),
    (r"\b(recipe|cooking|food|restaurant|kitchen)\b", "template", "food", "Food and recipe style"),
    (r"\b(gym|fitness|workout|sports?|yoga)\b", "template", "fitness", "Fitness and sport style"),
    (r"\b(testimonial|review from (?:a )?customer|client review)\b", "template", "testimonial", "Customer testimonial style"),
    (r"\b(tutorial|how ?to|step by step|walkthrough)\b", "template", "tutorial", "Tutorial style"),
    (r"\b(course|lesson|lecture|online class)\b", "template", "course_lesson", "Course lesson style"),
    (r"\b(product (?:video|ad|demo|launch)|advert|advertisement|ad film|promo|commercial)\b", "template", "product_ad", "Product advert style"),
    (r"\b(event (?:video|recap|film)|conference|launch event|recap)\b", "template", "event_recap", "Event recap style"),
    (r"\b(property|real estate|flat|apartment|house tour|home tour)\b", "template", "real_estate", "Property tour style"),
    (r"\b(documentary|docu)\b", "template", "documentary", "Mini documentary style"),
    (r"\bmusic video\b", "template", "music_video", "Music video style"),
    (r"\b(viral|trending)\b", "template", "shorts_viral", "Viral short style"),
    # colour look
    (r"\b(black (?:and|&|n) white|b ?& ?w|monochrome|greyscale|grayscale)\b", "grade", "noir", "Colour: black and white"),
    (r"\b(teal (?:and|&) orange|hollywood colou?r|blockbuster)\b", "grade", "teal_orange", "Colour: teal and orange film look"),
    (r"\b(vintage|retro|old ?school|90s|80s)\b", "grade", "vintage", "Colour: vintage"),
    (r"\b(sepia)\b", "grade", "sepia", "Colour: sepia"),
    (r"\b(warm (?:look|tone|colou?rs?|grade)|golden hour|golden (?:look|tone))\b", "grade", "golden", "Colour: warm golden"),
    (r"\b(cool (?:look|tone|colou?rs?)|cold (?:look|tone)|blue tone|night look)\b", "grade", "cool_night", "Colour: cool"),
    # "neon" in front of a graphic ("neon lower third", "neon title") is that graphic's style, not the film's colour
    (r"\bcyberpunk\b|\bneon\b(?! (?:lower|title|text|name|sign|glow|3d|end|outro|intro|tag|card|callouts?|words?|letters?|heading))",
     "grade", "cyberpunk", "Colour: neon cyberpunk"),
    (r"\b(pastel|soft colou?rs?|dreamy)\b", "grade", "pastel", "Colour: soft pastel"),
    (r"\b(vivid|vibrant|colou?rful|punchy colou?rs?|saturated)\b", "grade", "vivid", "Colour: vivid"),
    (r"\b(matte|faded|film fade)\b", "grade", "matte", "Colour: matte film"),
    (r"\b(moody|dark tone|dark look)\b", "grade", "moody_green", "Colour: moody"),
    (r"\b(natural colou?rs?|as filmed|no filter|no colou?r change|original colou?rs?)\b", "grade", "none", "Colour: as filmed"),
    # pace
    (r"\b(slow(?![ -]?mo)|calm|relaxed|peaceful|gentle|soothing|aaram se)\b", "pacing", "relaxed", "Pace: relaxed"),
    (r"\b(fast|quick|punchy|snappy|energetic|high energy|tez)\b", "pacing", "fast", "Pace: fast"),
    (r"\b(balanced|medium pace|normal pace)\b", "pacing", "balanced", "Pace: balanced"),
    # effects strength and motion
    (r"\b(no effects|plain cut|simple cut|no fx|minimal edit|just cut)\b", "fx", "off", "Effects: off, plain cuts"),
    (r"\b(lots of effects|heavy effects|crazy edit|maximum effects|full effects|all effects|max effects)\b", "fx", "viral", "Effects: maximum"),
    (r"\b(bold (?:edit|style|look|effects)|dramatic|impactful)\b", "fx", "bold", "Effects: bold"),
    (r"\b(clean (?:look|edit|style)|professional|corporate|minimal|subtle)\b", "fx", "clean", "Effects: clean"),
    (r"\b(no motion|static|steady frame|no zoom)\b", "motion", "subtle", "Motion: very little"),
    (r"\b(lots of motion|zooms?|zoom ins?|camera moves?|dynamic|shake|movement|(?<!slow )motion(?! graphics?| titles?| text))\b", "motion", "dynamic", "Motion: zooms and camera moves"),
    (r"\b(maximum motion|crazy motion|extreme motion)\b", "motion", "maximal", "Motion: maximum"),
    # transitions
    (r"\b(glitch)\b", "transition", "glitch", "Transitions: glitch"),
    (r"\b(smooth transitions?|soft transitions?)\b", "transition", "smooth", "Transitions: smooth"),
    (r"\b(cinematic|film(?:ic)?|movie like|movie style)\b", "transition", "cinematic", "Transitions: cinematic"),
    (r"\b(wipes?|slide transitions?)\b", "transition", "slide", "Transitions: slides and wipes"),
    (r"\b(flash|whip)\b", "transition", "flash", "Transitions: flash"),
    (r"\b(no transitions?|hard cuts?|jump cuts?)\b", "transition", "minimal", "Transitions: straight cuts"),
    # captions
    (r"\b(no (?:captions?|subtitles?|text)|without (?:captions?|subtitles?))\b", "captions", "none", "Captions: none"),
    (r"\b(karaoke|word by word|highlight(?:ed)? words?)\b", "captions", "karaoke", "Captions: word-by-word highlight"),
    (r"\b(clean (?:captions?|subtitles?)|simple (?:captions?|subtitles?)|small (?:captions?|subtitles?))\b", "captions", "clean", "Captions: clean"),
    (r"\b(big (?:captions?|subtitles?)|bold (?:captions?|subtitles?)|pop (?:captions?|subtitles?))\b", "captions", "bold_pop", "Captions: big and bold"),
    (r"\b(hinglish|roman (?:hindi|script)|english letters)\b", "caption_script", "roman", "Captions in English letters (Hinglish style)"),
    # music
    (r"\b(no (?:music|song|bgm|background music)|without (?:music|bgm)|only (?:my )?voice)\b", "no_music", True, "Music: none"),
    (r"\b(sad|emotional|tense|suspense|thriller)\b", "music", "tense", "Music: tense and emotional"),
    (r"\b(epic|grand|powerful|cinematic music)\b", "music", "epic", "Music: epic"),
    (r"\b(happy|fun|playful|funny|cheerful)\b", "music", "playful", "Music: playful"),
    (r"\b(uplifting|inspiring|motivational|positive)\b", "music", "uplifting", "Music: uplifting"),
    (r"\b(calm music|soft music|lofi|lo-fi|chill)\b", "music", "calm", "Music: calm"),
    # what to keep
    (r"\b(keep (?:the )?(?:pauses|silences)|don'?t cut pauses|keep everything|no cuts)\b", "keep_silences", True, "Keeps pauses and silences"),
    (r"\b(don'?t crop|no crop|without cropping|keep the full frame)\b", "no_reframe", True, "Never crops the picture (adds a blurred fill)"),
    (r"\b(4k|2160p|best quality|highest quality|high quality)\b", "quality", "high", "Quality: high"),
    (r"\b(draft|quick preview|fast render|low quality)\b", "quality", "draft", "Quality: draft (fast preview)"),
]

# Things people ask for that an edit of their own footage cannot do. Said plainly, never silently skipped.
CANNOT: list[tuple[str, str]] = [
    (r"\b(?:3d|three[- ]d) (?:scenes?|world|environment|model of|version of|avatars?|characters?|animation of me)\b|"
     r"\b(?:turn|convert|make) (?:my |the |this )?(?:video|footage|clips?|face|room) (?:into|in|to) (?:3d|three[- ]d)\b",
     "3D scenes built from your footage are not possible. 3D titles and simple 3D objects can be added on top of it."),
    (r"\b(ai[- ]generated|generate (?:a |new )?(?:video|clip|scene|footage|image)s?|text to video|image to video|avatars?|create (?:new )?(?:scenes?|footage|clips?))\b",
     "Takebloom does not create new footage. It edits the videos and pictures you give it."),
    (r"\b(stock (?:footage|video|clips?)|download (?:clips?|videos?|footage))\b",
     "Stock footage is not downloaded. Add your own extra clips or pictures to the drop and they are used."),
    (r"\b(remove (?:the )?background(?! noise| music| sound)|green ?screen|background removal|change (?:the )?background(?! music))\b",
     "Background removal is its own tool: choose Remove background, below the main editor."),
    (r"\b(voice ?over|narration|new voice|ai voice)\b",
     "A new voice-over is not added to your footage; your own voice is kept and cleaned."),
    (r"\bslow[ -]?mo(?:tion)?\b", "Slow motion is not added inside an edit yet. For a slowed-down clip use Restyle, below the main editor."),
    (r"\b(logo|watermark)\b", "A logo or watermark is not placed yet. Add it as a picture in the drop to show it in the film."),
]


def understand(brief: str) -> dict:
    """{"options": {...engine options...}, "understood": [plain lines], "cannot": [plain lines], "target_seconds"...}"""
    text = " " + (brief or "").lower().replace("’", "'") + " "
    options: dict[str, object] = {}
    said: list[str] = []
    for pattern, key, value, label in RULES:
        if key in options:
            continue
        m = re.search(pattern, text)
        if not m:
            continue
        if key == "music" and options.get("no_music"):
            continue
        options[key] = value
        said.append(f"{label} (you wrote “{m.group(0).strip()}”)")
    intent = local_intent(brief or "")
    if intent.get("target_seconds"):
        t = float(intent["target_seconds"])
        options["target"] = t
        said.insert(0, f"Length: about {t:g} seconds" if t < 120 else f"Length: about {t / 60:g} minutes")
    elif not intent.get("preserve_content"):
        said.insert(0, "Length: shorter, keeping the best parts")
    if intent.get("hook_seconds"):
        said.append(f"Opening hook of {intent['hook_seconds']:g} seconds (this does not shorten the film)")
    cannot = [msg for pattern, msg in CANNOT if re.search(pattern, text)]
    wish = motion_wishes(brief)
    if wish["off"]:
        options["graphics"] = "off"
    elif wish["kinds"]:
        options["graphics"] = ",".join(wish["kinds"])
    said += wish["said"]
    return {"options": options, "understood": said, "cannot": cannot, "questions": intent.get("questions", []),
            "target_seconds": intent.get("target_seconds")}


# ------------------------------------------------------------------ motion graphics and 3D (rendered by motion.py)

# (pattern, kind, what to say). Every match adds that piece; one title at most (a 3D title replaces the flat one).
MOTION_RULES: list[tuple[str, str, str]] = [
    (r"\b(?:3d|three[- ]d) (?:title|text|heading|name|words?|letters?|typography|intro)\b|"
     r"\b(?:title|text|name|heading) in (?:3d|three[- ]d)\b|\b(?:spinning|rotating|extruded|flying) (?:3d )?(?:title|text|heading|letters)\b",
     "title3d", "3D title"),
    (r"\b(?:3d|three[- ]d) (?:objects?|shapes?|cubes?|balls?|spheres?|gems?|diamonds?|stars?|hearts?|rings?|donuts?|knots?|elements?|icons?)\b|"
     r"\b(?:spinning|floating|rotating) (?:cubes?|shapes?|objects?|gems?|diamonds?|stars?|hearts?|rings?)\b",
     "shape3d", "3D object"),
    (r"\b(intro title|opening title|title card|animated title|big title|motion titles?|intro(?:/| and | & )outro titles?|"
     r"title at the (?:start|beginning)|titles?)\b", "title", "Animated title at the start"),
    (r"\b(lower[ -]?thirds?|name (?:tag|card|plate|strip|bar|title|super)|(?:show|put|add|with) my name|my name on screen|"
     r"location (?:tag|title|name)|place (?:tag|title|name))\b", "lower_third", "Name tag (lower third)"),
    (r"\b(kinetic (?:text|type|typography|words?)|text (?:callouts?|pop[- ]?ups?)|callouts?|pop[- ]?up (?:text|words)|"
     r"animated (?:text|words)|word pop[- ]?ups?)\b", "callout", "Kinetic text on key words"),
    (r"\b(end (?:card|screen|title|slate)|outro(?: title| card)?|closing (?:title|card)|thank(?:s| you) for watching|"
     r"subscribe (?:card|at the end))\b", "end_card", "End card"),
    (r"\b(confetti|party poppers?)\b", "confetti", "Confetti"),
    (r"\b(particles?|sparkles?|sparkly|glitter|bokeh|fireflies|magic dust|floating lights?)\b", "particles", "Sparkles"),
    (r"\b(light leaks?|lens flares?|film burns?|glow(?:ing)? transitions?)\b", "light_leak", "Light leaks on the cuts"),
]
MOTION_OFF = r"\b(no (?:titles?|graphics|motion graphics|animations?|text graphics)|without (?:titles?|graphics|animations?))\b"
SHAPES = [("heart", "heart"), ("star", "star"), ("diamond", "diamond"), ("gem", "gem"), ("cube", "cube"), ("box", "cube"),
          ("ring", "ring"), ("donut", "ring"), ("knot", "knot"), ("ball", "ball"), ("sphere", "ball")]
STYLE_WORDS = [(r"\b(neon|glow(?:ing|y)?|cyberpunk)\b", "neon"), (r"\b(gold(?:en)? (?:title|text|letters?|3d|name)|metallic|gold)\b(?! hour)", "gold"),
               (r"\b(elegant|classy|luxury|royal|graceful)\b", "elegant")]


def _quoted(brief: str, before: str) -> str:
    m = re.search(before + r"\s*(?:saying|that says|with|reading|text)?\s*[:=-]?\s*[\"“]([^\"”]{1,60})[\"”]", brief, re.I)
    return m.group(1).strip() if m else ""


def motion_wishes(brief: str) -> dict:
    """What motion graphics the prompt asks for, with their words: {"kinds", "style", "title", "name", "place", ...}."""
    raw = (brief or "").replace("’", "'")
    text = " " + raw.lower() + " "
    out: dict = {"kinds": [], "said": [], "off": False, "style": "", "spin": False, "shape": "", "count": 1,
                 "title": "", "name": "", "place": "", "end": "", "confetti_at": "end"}
    off = re.search(MOTION_OFF, text)
    if off:
        out["off"] = True
        out["said"].append(f"No titles or motion graphics (you wrote “{off.group(0).strip()}”)")
        return out
    hits: dict[str, str] = {}
    for pattern, kind, _label in MOTION_RULES:
        m = re.search(pattern, text)
        if m and kind not in hits:
            hits[kind] = m.group(0).strip()
    if "title3d" not in hits and "shape3d" not in hits and re.search(r"\b(?:3d|three[- ]d)\b", text) \
            and not re.search(CANNOT[0][0], text):
        hits["title3d"] = "3d"
    if "title3d" in hits:
        hits.pop("title", None)
    if re.search(r"\b(intro(?:/| and | & )outro|outro)\b", text) and "end_card" not in hits:
        hits["end_card"] = "outro"
    for pattern, style in STYLE_WORDS:
        if re.search(pattern, text):
            out["style"] = style
            break
    out["spin"] = bool(re.search(r"\b(spin(?:s|ning)?|rotat(?:e|es|ing)|turns? around|360)\b", text))
    for word, shape in SHAPES:
        sm = re.search(rf"\b{word}(e?s)?\b", text)
        if sm:
            out["shape"] = shape
            out["count"] = 3 if sm.group(1) else 1
            break
    if re.search(r"\b(?:shapes|objects)\b", text) and "shape3d" in hits:
        out["count"] = 3
    if re.search(r"confetti (?:at|in|on) the (?:start|beginning|intro|opening)|(?:start|begin|open)s? with (?:a )?confetti", text):
        out["confetti_at"] = "start"
    out["end"] = _quoted(raw, r"(?:end (?:card|screen)|outro)")
    quotes = [q.strip() for q in re.findall(r"[\"“]([^\"”]{1,60})[\"”]", raw) if q.strip() != out["end"]]
    out["title"] = _quoted(raw, r"(?:title|heading|called|titled|intro)") or (
        quotes[0] if quotes and ("title" in hits or "title3d" in hits) else "")
    name = re.search(r"\b(?:my name is|name\s*[:=]|name (?:tag|card) (?:for|with|of|saying)|lower third (?:for|with|saying)|"
                     r"(?:with|show|put|add) my name,?(?: as)?)\s*[\"“]?([A-Z][\w.'-]*(?:\s+[A-Z][\w.'-]*){0,3})", raw)
    name = name or re.search(r"\bname\s*[:=]\s*([^,.;\n\"”]{2,40})", raw, re.I)
    out["name"] = name.group(1).strip() if name else ""
    place = re.search(r"\b(?:place|location)\s*(?:[:=]|is|as)\s*([^,.;\n\"”]{2,40})", raw, re.I)
    out["place"] = place.group(1).strip() if place else ""
    for kind in ("title3d", "title", "lower_third", "callout", "shape3d", "end_card", "confetti", "particles", "light_leak"):
        if kind not in hits:
            continue
        out["kinds"].append(kind)
        label = dict((k, lab) for _p, k, lab in MOTION_RULES)[kind]
        extra = []
        if kind in ("title", "title3d") and out["title"]:
            extra.append(f"“{out['title']}”")
        if kind == "title3d" and out["spin"]:
            extra.append("spinning")
        if kind == "lower_third":
            extra.append(f"“{out['name']}”" if out["name"] else "your name (write “name: Your Name” to choose it)")
            if out["place"]:
                extra.append(f"place “{out['place']}”")
        if kind == "shape3d":
            extra.append(f"{out['count']} × {out['shape'] or 'gem'}")
        if kind == "end_card" and out["end"]:
            extra.append(f"“{out['end']}”")
        if kind == "confetti":
            extra.append("at the start" if out["confetti_at"] == "start" else "at the end")
        if out["style"] and kind in ("title", "title3d", "lower_third", "callout", "end_card", "shape3d"):
            extra.append(out["style"])
        out["said"].append(f"Motion: {label}{' ' + ', '.join(extra) if extra else ''} (you wrote “{hits[kind]}”)")
    return out


FLAG = {"aspect": "--aspect", "template": "--template", "grade": "--grade", "pacing": "--pacing", "fx": "--fx",
        "motion": "--motion", "transition": "--transition", "captions": "--captions", "caption_script": "--caption-script",
        "music": "--music", "quality": "--quality", "target": "--target", "graphics": "--graphics"}
SWITCH = {"no_music": "--no-music", "keep_silences": "--keep-silences", "no_reframe": "--no-reframe"}
EMPTY = (None, "", "auto")


def merge(chosen: dict, prompt: dict) -> dict:
    """The person's Fine-tune choices win; the prompt fills everything they left on Auto."""
    out = dict(chosen)
    for key, value in prompt.items():
        if out.get(key) in EMPTY:
            out[key] = value
    return out


def edit_args(prompt_options: dict, chosen: dict) -> list[str]:
    """Command-line switches for what the prompt set and the form left on Auto (the form's own flags come separately)."""
    args: list[str] = []
    for key, value in prompt_options.items():
        if chosen.get(key) not in EMPTY:
            continue
        if key in SWITCH and value:
            args.append(SWITCH[key])
        elif key in FLAG:
            args += [FLAG[key], str(value)]
    return args
