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
    (r"\b(cyberpunk|neon)\b", "grade", "cyberpunk", "Colour: neon cyberpunk"),
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
    (r"\b(lots of motion|zooms?|zoom ins?|camera moves?|dynamic|shake|movement|(?<!slow )motion)\b", "motion", "dynamic", "Motion: zooms and camera moves"),
    (r"\b(maximum motion|crazy motion|extreme motion)\b", "motion", "maximal", "Motion: maximum"),
    # transitions
    (r"\b(glitch)\b", "transition", "glitch", "Transitions: glitch"),
    (r"\b(smooth transitions?|soft transitions?)\b", "transition", "smooth", "Transitions: smooth"),
    (r"\b(cinematic|film(?:ic)?|movie like|movie style)\b", "transition", "cinematic", "Transitions: cinematic"),
    (r"\b(wipes?|slide transitions?)\b", "transition", "slide", "Transitions: slides and wipes"),
    (r"\b(flash|whip)\b", "transition", "flash", "Transitions: flash"),
    (r"\b(no transitions?|hard cuts?|jump cuts?)\b", "transition", "minimal", "Transitions: straight cuts"),
    (r"\b(hard cuts? only|only hard cuts?|no [jl][- ]?cuts?|no split edits?|no sound (?:lead|overlap)s?)\b", "hard_cuts", True,
     "Joins: hard cuts only, sound and picture change together (no J-cuts or L-cuts)"),
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
    (r"\b3d\b|\bthree[- ]d\b", "3D objects and 3D text are not added to edits of your own footage yet. "
                              "Your film gets motion titles, zooms and effects instead."),
    (r"\b(ai[- ]generated|generate (?:a |new )?(?:video|clip|scene|footage|image)s?|text to video|image to video|avatars?|create (?:new )?(?:scenes?|footage|clips?))\b",
     "Takebloom does not create new footage. It edits the videos and pictures you give it."),
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
    from video_studio import stock   # free stock B-roll: only with the person's own free key (stock.py)

    if stock.ASK.search(text):
        found = stock.keys() if stock.has_key() else {}
        if found:
            options["stock"] = True
            said.append(stock.WILL.format(sites=stock.sites_text(found)))
        else:
            cannot.append(stock.NO_KEY)
    return {"options": options, "understood": said, "cannot": cannot, "questions": intent.get("questions", []),
            "target_seconds": intent.get("target_seconds")}


FLAG = {"aspect": "--aspect", "template": "--template", "grade": "--grade", "pacing": "--pacing", "fx": "--fx",
        "motion": "--motion", "transition": "--transition", "captions": "--captions", "caption_script": "--caption-script",
        "music": "--music", "quality": "--quality", "target": "--target"}
SWITCH = {"no_music": "--no-music", "keep_silences": "--keep-silences", "no_reframe": "--no-reframe",
          "hard_cuts": "--hard-cuts", "stock": "--stock"}
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
