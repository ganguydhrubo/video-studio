"""Built-in production library: templates, transitions and colour looks.

Everything here is made by Takebloom itself, so it can be bundled in an open-source app:
* transitions are FFmpeg's own `xfade` transitions, grouped into families with plain names;
* colour looks are 3D LUTs generated in code (no third-party LUT packs);
* templates are production recipes that set the editor's existing choices (format, pacing, captions, music,
  look, colour, transitions, effects) and add a short instruction to the brief.
"""
from __future__ import annotations

import colorsys
import math
from pathlib import Path

from video_studio.util import app_data_dir

# ------------------------------------------------------------------ transitions

XFADE = ["fade", "fadeblack", "fadewhite", "fadegrays", "fadefast", "fadeslow", "dissolve", "distance",
         "wipeleft", "wiperight", "wipeup", "wipedown", "wipetl", "wipetr", "wipebl", "wipebr",
         "slideleft", "slideright", "slideup", "slidedown", "smoothleft", "smoothright", "smoothup", "smoothdown",
         "coverleft", "coverright", "coverup", "coverdown", "revealleft", "revealright", "revealup", "revealdown",
         "circlecrop", "rectcrop", "circleopen", "circleclose", "vertopen", "vertclose", "horzopen", "horzclose",
         "diagtl", "diagtr", "diagbl", "diagbr", "hlslice", "hrslice", "vuslice", "vdslice",
         "hlwind", "hrwind", "vuwind", "vdwind", "radial", "pixelize", "hblur", "zoomin", "squeezeh"]

TRANSITION_FAMILIES: dict[str, dict] = {
    "smooth": {"name": "Smooth", "mood": "friendly, modern", "seconds": 0.45, "xfade": ["smoothup", "fade", "smoothleft", "circleopen"]},
    "punchy": {"name": "Punchy", "mood": "fast reels, energy", "seconds": 0.30, "xfade": ["slideleft", "zoomin", "wipeup", "slideup"]},
    "minimal": {"name": "Minimal", "mood": "calm, corporate", "seconds": 0.50, "xfade": ["fade", "fade", "dissolve"]},
    "cinematic": {"name": "Cinematic", "mood": "film, drama", "seconds": 0.70, "xfade": ["fadeblack", "fade", "radial", "hblur"]},
    "slide": {"name": "Slide", "mood": "product, lists", "seconds": 0.40, "xfade": ["slideleft", "slideright", "slideup", "slidedown"]},
    "reveal": {"name": "Cover and reveal", "mood": "stories, tutorials", "seconds": 0.45, "xfade": ["coverleft", "revealright", "coverup", "revealdown"]},
    "wipe": {"name": "Wipe", "mood": "news, sport", "seconds": 0.35, "xfade": ["wipeleft", "wiperight", "wipetl", "wipebr"]},
    "geometric": {"name": "Shapes", "mood": "playful, kids, events", "seconds": 0.50, "xfade": ["circleopen", "rectcrop", "diagtl", "vertopen", "horzclose"]},
    "glitch": {"name": "Glitch", "mood": "tech, gaming", "seconds": 0.30, "xfade": ["pixelize", "hlslice", "hrwind", "vdslice"]},
    "soft": {"name": "Soft", "mood": "weddings, travel, beauty", "seconds": 0.80, "xfade": ["dissolve", "fadegrays", "hblur", "fadeslow"]},
    "zoom": {"name": "Zoom", "mood": "hype, sales", "seconds": 0.35, "xfade": ["zoomin", "squeezeh", "distance", "zoomin"]},
    "dark": {"name": "Dip to black", "mood": "documentary, serious", "seconds": 0.60, "xfade": ["fadeblack", "fadeblack", "fade"]},
    "flash": {"name": "Dip to white", "mood": "fashion, music", "seconds": 0.40, "xfade": ["fadewhite", "fadefast", "fadewhite"]},
}


def transitions(family: str) -> list[str]:
    return list(TRANSITION_FAMILIES.get(family, TRANSITION_FAMILIES["smooth"])["xfade"])


def transition_seconds(family: str) -> float:
    return float(TRANSITION_FAMILIES.get(family, TRANSITION_FAMILIES["smooth"])["seconds"])


# ------------------------------------------------------------------ colour looks (generated 3D LUTs)

def _clip(v: float) -> float:
    return 0.0 if v < 0 else 1.0 if v > 1 else v


def _luma(r, g, b):
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _sat(r, g, b, k):
    y = _luma(r, g, b)
    return y + (r - y) * k, y + (g - y) * k, y + (b - y) * k


def _scurve(v, k):
    """Contrast around mid-grey; k > 1 adds contrast."""
    return _clip(0.5 + (v - 0.5) * k) if k <= 1 else _clip(0.5 + math.atan((v - 0.5) * k * 2.2) / (2 * math.atan(1.1 * k)))


def _lift(v, black, white=1.0):
    return black + v * (white - black)


def _split(r, g, b, shadow, highlight, amount):
    y = _luma(r, g, b)
    ws, wh = (1 - y) ** 2, y ** 2
    return (r + amount * (shadow[0] * ws + highlight[0] * wh), g + amount * (shadow[1] * ws + highlight[1] * wh),
            b + amount * (shadow[2] * ws + highlight[2] * wh))


def _mono(r, g, b, tint=(0, 0, 0)):
    y = _luma(r, g, b)
    return y + tint[0], y + tint[1], y + tint[2]


GRADES: dict[str, dict] = {
    "natural": {"name": "Clean natural", "mood": "safe for any footage",
                "fn": lambda r, g, b: tuple(_scurve(c, 1.06) for c in _sat(r, g, b, 1.06))},
    "teal_orange": {"name": "Teal and orange", "mood": "blockbuster, travel",
                    "fn": lambda r, g, b: tuple(_scurve(c, 1.12) for c in _split(*_sat(r, g, b, 1.1), (-0.12, 0.04, 0.10), (0.10, 0.03, -0.10), 1.0))},
    "warm_film": {"name": "Warm film", "mood": "lifestyle, memories",
                  "fn": lambda r, g, b: tuple(_lift(_scurve(c, 1.08), 0.045, 0.97) for c in _sat(r * 1.04 + 0.01, g * 1.0, b * 0.92, 0.9))},
    "cool_night": {"name": "Cool night", "mood": "city, thriller",
                   "fn": lambda r, g, b: tuple(_scurve(c, 1.15) for c in _sat(r * 0.88, g * 0.95, b * 1.1 + 0.02, 0.85))},
    "bleach": {"name": "Bleach bypass", "mood": "gritty, sport, war",
               "fn": lambda r, g, b: tuple(_scurve(c, 1.3) for c in _sat(r, g, b, 0.45))},
    "vintage": {"name": "Faded vintage", "mood": "retro, nostalgia",
                "fn": lambda r, g, b: tuple(_lift(_scurve(c, 0.9), 0.09, 0.93) for c in _split(*_sat(r, g, b, 0.75), (0.0, 0.03, 0.04), (0.06, 0.05, -0.06), 1.0))},
    "noir": {"name": "Noir black and white", "mood": "drama, fashion",
             "fn": lambda r, g, b: tuple(_scurve(c, 1.45) for c in _mono(r, g, b))},
    "soft_mono": {"name": "Soft black and white", "mood": "portraits, weddings",
                  "fn": lambda r, g, b: tuple(_lift(_scurve(c, 0.95), 0.04, 0.98) for c in _mono(r, g, b, (0.008, 0.004, -0.004)))},
    "pastel": {"name": "Soft pastel", "mood": "beauty, kids, food",
               "fn": lambda r, g, b: tuple(_lift(_scurve(c, 0.88), 0.06) for c in _split(*_sat(r, g, b, 1.08), (0.02, 0.0, 0.04), (0.05, 0.02, 0.03), 1.0))},
    "golden": {"name": "Golden hour", "mood": "outdoor, romance",
               "fn": lambda r, g, b: tuple(_scurve(c, 1.05) for c in _split(r * 1.05, g * 1.01, b * 0.88, (0.02, 0.0, -0.02), (0.10, 0.05, -0.06), 1.0))},
    "vivid": {"name": "Vivid pop", "mood": "ads, YouTube, food",
              "fn": lambda r, g, b: tuple(_scurve(c, 1.12) for c in _sat(r, g, b, 1.35))},
    "moody_green": {"name": "Moody green", "mood": "nature, mystery",
                    "fn": lambda r, g, b: tuple(_scurve(c, 1.12) for c in _split(*_sat(r, g, b, 0.8), (-0.04, 0.06, 0.02), (0.02, 0.03, -0.04), 1.0))},
    "sepia": {"name": "Sepia", "mood": "history, heritage",
              "fn": lambda r, g, b: (_luma(r, g, b) * 1.07 + 0.03, _luma(r, g, b) * 0.95 + 0.01, _luma(r, g, b) * 0.74)},
    "cyberpunk": {"name": "Cyberpunk", "mood": "tech, gaming, nightlife",
                  "fn": lambda r, g, b: tuple(_scurve(c, 1.18) for c in _split(*_sat(r, g, b, 1.2), (-0.02, 0.08, 0.14), (0.14, -0.02, 0.10), 1.0))},
    "matte": {"name": "Matte film", "mood": "indie, interviews",
              "fn": lambda r, g, b: tuple(_lift(_scurve(c, 1.04), 0.07, 0.94) for c in _split(*_sat(r, g, b, 0.92), (-0.02, 0.0, 0.03), (0.02, 0.01, -0.02), 1.0))},
    "rich_classic": {"name": "Rich classic", "mood": "brand films, products",
                     "fn": lambda r, g, b: tuple(_scurve(c, 1.15) for c in _sat(r * 1.02, g, b * 1.02, 1.18))},
}
LUT_SIZE = 33
LUT_VERSION = 1


def lut_file(key: str) -> Path:
    """The .cube file for a look, generated once into the app data folder."""
    if key not in GRADES:
        raise KeyError(key)
    folder = app_data_dir() / "luts"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{key}-v{LUT_VERSION}.cube"
    if path.exists() and path.stat().st_size > 1000:
        return path
    fn, n = GRADES[key]["fn"], LUT_SIZE
    lines = [f"TITLE \"Takebloom {GRADES[key]['name']}\"", f"LUT_3D_SIZE {n}", "DOMAIN_MIN 0 0 0", "DOMAIN_MAX 1 1 1"]
    step = 1 / (n - 1)
    for bi in range(n):  # .cube order: red changes fastest
        for gi in range(n):
            for ri in range(n):
                r, g, b = (_clip(c) for c in fn(ri * step, gi * step, bi * step))
                lines.append(f"{r:.5f} {g:.5f} {b:.5f}")
    tmp = path.with_suffix(".part")
    tmp.write_text("\n".join(lines) + "\n", encoding="ascii")
    tmp.replace(path)
    return path


def _ff_path(path: Path) -> str:
    """A path inside an FFmpeg filter graph (drive colons and quotes escaped)."""
    return str(path).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def grade_filter(key: str | None) -> str:
    """FFmpeg filter for a look, or '' for none/unknown."""
    if not key or key not in GRADES:
        return ""
    return f"lut3d=file='{_ff_path(lut_file(key))}':interp=tetrahedral"


def preview_rgb(key: str, samples: int = 5) -> list[str]:
    """A few hex colours showing what a look does to a hue sweep (for small swatches in the UI)."""
    fn, out = GRADES[key]["fn"], []
    for i in range(samples):
        r, g, b = colorsys.hsv_to_rgb(i / samples, 0.55, 0.8)
        r, g, b = (_clip(c) for c in fn(r, g, b))
        out.append("#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255)))
    return out


# ------------------------------------------------------------------ templates (production recipes)

def _t(key, name, category, modes, aspect, brief, **settings) -> dict:
    return {"key": key, "name": name, "category": category, "modes": modes, "aspect": aspect, "brief": brief, "settings": settings}


TEMPLATES: list[dict] = [
    _t("reel_talking", "Talking-head reel", "Social", ["edit"], "9:16",
       "Vertical reel. Keep the whole message, strong first line, bold captions, light energetic music.",
       pacing="fast", captions="bold_pop", music="uplifting", transition="punchy", fx="bold", grade="natural"),
    _t("shorts_viral", "Viral short", "Social", ["edit", "script"], "9:16",
       "Fast YouTube Short or reel. Hook in the first two seconds, cut pauses, big captions, lots of energy.",
       pacing="fast", captions="karaoke", music="uplifting", transition="zoom", fx="viral", grade="vivid", motion="maximal"),
    _t("youtube_long", "YouTube video", "YouTube", ["edit"], "16:9",
       "Landscape YouTube video. Keep the full talk, remove mistakes and long pauses, clean captions, calm music.",
       pacing="balanced", captions="clean", music="calm", transition="minimal", fx="clean", grade="natural"),
    _t("podcast_clip", "Podcast clip", "YouTube", ["edit"], "9:16",
       "Vertical podcast clip. Keep complete sentences, clean captions, no music under speech.",
       pacing="balanced", captions="clean", music="calm", transition="minimal", fx="clean", grade="matte", music_enabled=False),
    _t("product_ad", "Product advert", "Marketing", ["script", "edit", "photos"], "9:16",
       "Product advert: problem, product, three benefits, offer, clear call to action at the end.",
       pacing="fast", captions="bold_pop", music="uplifting", transition="slide", fx="bold", grade="rich_classic", look="studio"),
    _t("promo_sale", "Sale promotion", "Marketing", ["script", "photos"], "1:1",
       "Short sale promotion with the offer in big text, a deadline and a call to action.",
       pacing="fast", captions="bold_pop", music="uplifting", transition="zoom", grade="vivid", look="sunset", motion="dynamic"),
    _t("brand_film", "Brand film", "Marketing", ["edit", "photos", "script"], "16:9",
       "Cinematic brand film. Slow, confident pacing, wide shots first, minimal text, emotional music.",
       pacing="relaxed", captions="clean", music="epic", transition="cinematic", fx="clean", grade="teal_orange", look="midnight"),
    _t("testimonial", "Customer testimonial", "Marketing", ["edit"], "4:5",
       "Customer testimonial. Keep their own words, name card at the start, warm colour, gentle music.",
       pacing="balanced", captions="clean", music="calm", transition="soft", fx="clean", grade="warm_film"),
    _t("explainer", "Explainer", "Education", ["script"], "16:9",
       "Explainer: question, simple answer, three steps with examples, short recap.",
       pacing="balanced", captions="clean", music="calm", transition="reveal", grade="natural", look="paper", motion="subtle"),
    _t("tutorial", "Tutorial or how-to", "Education", ["edit", "script"], "16:9",
       "Step-by-step tutorial. Number each step on screen, keep instructions complete, no fast cuts.",
       pacing="relaxed", captions="clean", music="calm", transition="minimal", fx="clean", grade="natural"),
    _t("course_lesson", "Course lesson", "Education", ["edit"], "16:9",
       "Course lesson. Keep everything the teacher says, remove only mistakes and long silences.",
       pacing="relaxed", captions="clean", music="calm", transition="minimal", fx="off", grade="natural", music_enabled=False),
    _t("news_update", "News update", "Business", ["script", "edit"], "9:16",
       "Short news update: headline first, three facts, source on screen.",
       pacing="balanced", captions="bold_pop", music="tense", transition="wipe", grade="natural", look="mono"),
    _t("company_update", "Company announcement", "Business", ["script", "edit"], "16:9",
       "Company announcement. Clear and calm, logo at the start, key facts as titles, warm ending.",
       pacing="balanced", captions="clean", music="calm", transition="minimal", grade="natural", look="studio"),
    _t("event_recap", "Event recap", "Events", ["photos", "edit"], "16:9",
       "Event highlights: arrivals, key moments, people smiling, finish on the group photo.",
       pacing="fast", captions="none", music="uplifting", transition="geometric", grade="vivid", seconds_per_photo=2.2),
    _t("wedding", "Wedding film", "Events", ["photos", "edit"], "16:9",
       "Wedding film: gentle and emotional, slow dissolves, soft colour, music carries the story.",
       pacing="relaxed", captions="none", music="calm", transition="soft", grade="pastel", seconds_per_photo=3.5),
    _t("birthday", "Birthday slideshow", "Events", ["photos"], "9:16",
       "Birthday slideshow with names and wishes as titles, happy music.",
       pacing="balanced", captions="bold_pop", music="playful", transition="geometric", grade="golden", seconds_per_photo=2.8),
    _t("travel", "Travel montage", "Lifestyle", ["photos", "edit"], "9:16",
       "Travel montage: wide places, details, people, quick rhythm to the music.",
       pacing="fast", captions="none", music="uplifting", transition="smooth", grade="teal_orange", seconds_per_photo=2.0),
    _t("real_estate", "Property tour", "Business", ["photos", "edit"], "16:9",
       "Property tour: outside first, then each room, price and contact at the end.",
       pacing="relaxed", captions="clean", music="calm", transition="slide", grade="natural", seconds_per_photo=3.2),
    _t("food", "Food and recipe", "Lifestyle", ["edit", "photos"], "9:16",
       "Recipe video: finished dish first, then steps with quantities as titles, close-ups.",
       pacing="fast", captions="bold_pop", music="playful", transition="punchy", grade="vivid", seconds_per_photo=2.0),
    _t("fitness", "Fitness and sport", "Lifestyle", ["edit"], "9:16",
       "Workout or sport clip: high energy, beat-matched cuts, bold captions.",
       pacing="fast", captions="bold_pop", music="tense", transition="zoom", fx="viral", grade="bleach"),
    _t("music_video", "Music video look", "Creative", ["edit", "photos"], "9:16",
       "Music video style: cuts on the beat, strong colour, flashes on changes.",
       pacing="fast", captions="none", music="epic", transition="flash", fx="viral", grade="cyberpunk", seconds_per_photo=1.2),
    _t("documentary", "Mini documentary", "Creative", ["edit", "script"], "16:9",
       "Mini documentary: calm narration, slow cuts, titles for places and dates, serious tone.",
       pacing="relaxed", captions="clean", music="tense", transition="dark", fx="clean", grade="matte"),
    _t("retro", "Retro memories", "Creative", ["photos", "restyle"], "4:5",
       "Retro memories: faded colour, slow pan and zoom, nostalgic music.",
       pacing="relaxed", captions="none", music="calm", transition="soft", grade="vintage", seconds_per_photo=3.0),
    _t("noir_story", "Noir story", "Creative", ["edit", "restyle", "script"], "16:9",
       "Black-and-white noir: hard contrast, slow fades to black, tense music.",
       pacing="relaxed", captions="clean", music="tense", transition="dark", grade="noir", look="mono"),
]
_BY_KEY = {t["key"]: t for t in TEMPLATES}


def template(key: str | None) -> dict | None:
    return _BY_KEY.get(key or "")


def apply_template(key: str | None, options: dict, brief: str = "") -> tuple[dict, str]:
    """Merge a template under the person's own choices (their choices always win). Returns (options, brief)."""
    t = template(key)
    if not t:
        return dict(options), brief
    merged = {"aspect": t["aspect"], **{k: v for k, v in t["settings"].items() if k not in ("music_enabled", "seconds_per_photo")}}
    merged.update({k: v for k, v in options.items() if v not in (None, "", "auto")})
    if t["settings"].get("music_enabled") is False and "music_enabled" not in options:
        merged["no_music"] = True
    if t["settings"].get("seconds_per_photo") and not options.get("seconds_per_photo"):
        merged["seconds_per_photo"] = t["settings"]["seconds_per_photo"]
    text = brief.strip()
    if t["brief"].lower() not in text.lower():
        text = (text + "\n" if text else "") + "Template - " + t["name"] + ": " + t["brief"]
    return merged, text


def catalog() -> dict:
    """Everything the UI needs to show the library."""
    return {
        "templates": [{k: t[k] for k in ("key", "name", "category", "modes", "aspect", "brief")} | {"settings": t["settings"]}
                      for t in TEMPLATES],
        "transitions": [{"key": k, "name": v["name"], "mood": v["mood"], "count": len(set(v["xfade"]))} for k, v in TRANSITION_FAMILIES.items()],
        "grades": [{"key": k, "name": v["name"], "mood": v["mood"], "swatch": preview_rgb(k)} for k, v in GRADES.items()],
        "counts": {"templates": len(TEMPLATES), "transition_families": len(TRANSITION_FAMILIES), "transitions": len(XFADE),
                   "looks": len(GRADES)},
    }
