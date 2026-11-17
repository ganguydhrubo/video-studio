"""Free stock B-roll: a few cutaways from Pexels or Pixabay, found with YOUR OWN free key. Optional and off by default.

When it runs
- Only when you asked for it (the prompt says "stock footage", "b-roll" and similar, or `--stock`) AND you have added
  your own free key. Without a key nothing is searched; the edit finishes as usual and the log says how to add a key.
- Never in a music montage (there are no spoken lines to match).

What leaves this PC
- Only 1 to 4 short search words picked from your edit (topic words of the kept lines, the title and your prompt) and
  your key, sent to the site whose key you added. No video, sound, transcript or file name is sent.

What it does
- Picks the search words, searches Pexels first, then Pixabay if needed, chooses a clip in the film's shape
  (portrait for a reel, landscape for a widescreen film) that is long enough, and downloads a small HD copy
  (never 4K: at most 1920 pixels on the long side, at most 50 MB, with a time limit).
- Search results are kept for 24 hours and clips are kept in the app folder (stock/), so a re-edit downloads nothing.
- Places each clip over the line where its word is said, with the same rules as your own B-roll (broll.py): never over
  the opening line or the last line, a giant-text moment, another cutaway or your pictures; at most one every 7 s;
  1 to 2.8 s each; your voice carries on underneath.
- Writes exports/credits.txt with every stock clip used: creator, clip page and licence.

Licences: the Pexels Licence and the Pixabay Content Licence allow free use in your videos, including commercial use,
without payment. Credit is not required by the licences, but Pexels asks apps to credit creators where possible, and
both sites ask that you say where the clips came from. Nothing from these sites is bundled with Takebloom.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from video_studio import config
from video_studio.util import app_data_dir, console, read_json, write_json

PEXELS_URL = "https://api.pexels.com/v1/videos/search"
PIXABAY_URL = "https://pixabay.com/api/videos/"
ENV = {"pexels": "PEXELS_API_KEY", "pixabay": "PIXABAY_API_KEY"}
UA = "Takebloom video editor"
TIMEOUT = 20                    # seconds for one search or one stalled download read
DOWNLOAD_LIMIT = 90             # seconds for one whole download
MAX_BYTES = 50 * 1024 * 1024    # one clip at most
MAX_SIDE, MIN_SIDE = 1920, 540  # small HD copies only: never 4K, never tiny
CACHE_HOURS = 24                # Pixabay asks apps to keep search results for 24 hours
MAX_WORDS = 4
GAP = 7.0                       # at most one cutaway per ~7 s (the same as your own B-roll)

ASK = re.compile(r"\b(stock (?:footage|videos?|clips?|shots?|b-?roll)|b-?roll|b roll|free footage|"
                 r"download (?:clips?|videos?|footage)|pexels|pixabay)\b")
NO_KEY = ("Free stock footage needs your own free key, and none is set, so no stock clips are added. "
          "Get a free key at pexels.com/api (or pixabay.com/api/docs), then run: "
          "video-studio config set stock.pexels_key YOUR_KEY (or stock.pixabay_key). No payment or app account is needed.")
WILL = ("Free stock clips for a few cutaways, from {sites} with your key. Only a few search words leave this PC. "
        "Credits go in exports/credits.txt")

# words about the edit itself, never a search word
EDIT_WORDS = set("""stock footage video videos clip clips broll roll cutaway cutaways shot shots pexels pixabay free download
add adding please make edit reel reels short shorts youtube instagram tiktok vertical landscape square cinematic music
song captions caption subtitles seconds second minute minutes long punchy viral style look transitions transition fast
slow film movie scene scenes footage effects hook title remove mistakes keep best part parts""".split())


class StockError(Exception):
    """A plain reason a search or download did not work. Never contains the key or a URL with the key."""


# ---------------------------------------------------------------- keys

def keys() -> dict[str, str]:
    """Your own free keys: from the app settings, else from PEXELS_API_KEY / PIXABAY_API_KEY."""
    import os

    out = {}
    for site, env in ENV.items():
        val = str(config.get(f"stock.{site}_key") or os.environ.get(env) or "").strip()
        if val:
            out[site] = val
    return out


def has_key() -> bool:
    try:
        return bool(keys())
    except Exception:  # noqa: BLE001 - an unreadable settings file means no key, never a crash while typing
        return False


def sites_text(found: dict) -> str:
    names = [n for n in ("Pexels", "Pixabay") if n.lower() in found]
    return " and ".join(names) or "Pexels or Pixabay"


def wanted(brief: str, opts: dict) -> bool:
    return bool(opts.get("stock")) or bool(ASK.search(" " + (brief or "").lower() + " "))


# ---------------------------------------------------------------- choosing words and places (pure)

def _words(text: str) -> list[str]:
    return [w.lower().strip("'-") for w in re.findall(r"[A-Za-z][A-Za-z'-]{2,}", text or "")]


def search_words(lines: list[dict], title: str, brief: str, limit: int = MAX_WORDS) -> list[dict]:
    """1-4 topic words: said in the kept lines, in the title or in your prompt. [{word, stem, said}]"""
    from video_studio import brain, broll

    stop = brain.EN_STOP | broll.GENERIC | EDIT_WORDS
    score: dict[str, float] = {}
    surface: dict[str, str] = {}
    said: set[str] = set()

    def add(word: str, weight: float, spoken: bool) -> None:
        w = word.split("'")[0]
        if len(w) < 3 or w in stop:
            return
        st = brain._stem(w)
        if st in {brain._stem(x) for x in ("stock", "footage", "video", "clip")}:
            return
        score[st] = score.get(st, 0.0) + weight
        surface.setdefault(st, w)
        if spoken:
            said.add(st)

    for ln in lines:
        named = broll._named(ln.get("en", ""))
        for w in _words(ln.get("en", "")):
            add(w, 1.0 + (1.5 if brain._stem(w) in named else 0.0), True)
    for w in _words(title):
        add(w, 1.0, False)
    for w in _words(brief):
        add(w, 2.0, False)
    ranked = sorted(score, key=lambda s: (-score[s], surface[s]))
    return [{"word": surface[s], "stem": s, "said": s in said} for s in ranked[:limit]]


def place(lines: list[dict], picks: list[dict], busy: list[tuple[float, float]], total: float,
          taken: list[tuple[float, float]] | None = None) -> list[dict]:
    """Where each search word's clip goes: over the line where it is said (else any free middle line).
    Same rules as your own B-roll: not the first or last line, 1-2.8 s, clear of busy spans, 7 s apart."""
    from video_studio import broll

    taken = list(taken or [])
    out = []
    middle = lines[1:-1]
    for pk in picks:
        said_in = [ln for ln in middle if pk["stem"] in broll._stems(ln.get("en", ""))]
        for ln in said_in or ([] if pk.get("said") else middle):
            a = ln["t0"] + 0.35
            b = min(ln["t1"] - 0.1, a + 2.8, total - 0.3)
            if b - a < 1.0 or any(a < y and b > x for x, y in busy):
                continue
            if any(abs(a - x) < GAP for x, _ in taken):
                continue
            taken.append((a, b))
            out.append({**pk, "t0": round(a, 3), "t1": round(b, 3), "line": ln.get("id")})
            break
    return sorted(out, key=lambda c: c["t0"])


def orientation(w: int, h: int) -> str:
    return "portrait" if h > w * 1.05 else "landscape" if w > h * 1.05 else "square"


def pexels_results(data: dict) -> list[dict]:
    out = []
    for v in data.get("videos") or []:
        files = [f for f in v.get("video_files") or [] if f.get("link") and f.get("width") and f.get("height")
                 and str(f.get("file_type", "video/mp4")) == "video/mp4"
                 and max(f["width"], f["height"]) <= MAX_SIDE and min(f["width"], f["height"]) >= MIN_SIDE]
        if not files:
            continue
        f = max(files, key=lambda x: x["width"] * x["height"])
        user = v.get("user") or {}
        out.append({"site": "Pexels", "id": v.get("id"), "page": v.get("url") or "", "author": user.get("name") or "a Pexels creator",
                    "author_url": user.get("url") or "", "duration": float(v.get("duration") or 0), "width": int(f["width"]),
                    "height": int(f["height"]), "link": f["link"], "size": 0,
                    "licence": "Pexels Licence", "licence_url": "https://www.pexels.com/license/"})
    return out


def pixabay_results(data: dict) -> list[dict]:
    out = []
    for hit in data.get("hits") or []:
        vids = hit.get("videos") or {}
        pick = None
        for name in ("medium", "small", "tiny"):   # 'large' is often 4K: never used
            r = vids.get(name) or {}
            if r.get("url") and r.get("width") and r.get("height") and max(r["width"], r["height"]) <= MAX_SIDE \
                    and min(r["width"], r["height"]) >= MIN_SIDE:
                pick = r
                break
        if not pick:
            continue
        user, uid = str(hit.get("user") or "a Pixabay creator"), hit.get("user_id")
        out.append({"site": "Pixabay", "id": hit.get("id"), "page": hit.get("pageURL") or "", "author": user,
                    "author_url": f"https://pixabay.com/users/{user}-{uid}/" if uid else "",
                    "duration": float(hit.get("duration") or 0), "width": int(pick["width"]), "height": int(pick["height"]),
                    "link": pick["url"], "size": int(pick.get("size") or 0),
                    "licence": "Pixabay Content Licence", "licence_url": "https://pixabay.com/service/license-summary/"})
    return out


def choose(cands: list[dict], want: str, need: float, used: set) -> dict | None:
    """Best clip for a cutaway: the film's shape first, then long enough, then not too long; never one used twice."""
    best, best_score = None, None
    for c in cands:
        if (c["site"], c["id"]) in used or (c["size"] and c["size"] > MAX_BYTES):
            continue
        if c["duration"] and c["duration"] < need + 0.3:
            continue
        sc = (2.0 if orientation(c["width"], c["height"]) == want else 0.0) + (1.0 if c["duration"] >= need + 1.0 else 0.0) \
            + (0.5 if 0 < c["duration"] <= 40 else 0.0)
        if best_score is None or sc > best_score:
            best, best_score = c, sc
    return best


# ---------------------------------------------------------------- network (standard library only)

def _open(req: urllib.request.Request, timeout: float):
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 - fixed https URLs only (tests patch this)


def _reason(code: int, site: str) -> str:
    if code in (401, 403):
        return f"{site} refused the key. Check it at {'pexels.com/api' if site == 'Pexels' else 'pixabay.com/api/docs'}"
    if code == 429:
        return f"{site}'s free search limit is reached for now. Try again later"
    if code == 400:
        return f"{site} did not accept the search"
    return f"{site} answered with an error ({code})"


def _get_json(url: str, headers: dict, site: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json", **headers})
    try:
        with _open(req, TIMEOUT) as r:
            body = r.read(5_000_000)
    except urllib.error.HTTPError as exc:
        raise StockError(_reason(exc.code, site)) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise StockError(f"{site} could not be reached (no internet, or it did not answer in time)") from None
    try:
        return json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise StockError(f"{site} sent an answer that could not be read") from None


def _cache_dir() -> Path:
    d = app_data_dir() / "stock"
    d.mkdir(parents=True, exist_ok=True)
    return d


def search(site: str, key: str, word: str, want: str) -> list[dict]:
    """Search one site for one word. Results are kept for 24 hours (asked by Pixabay, kind to both)."""
    slug = re.sub(r"[^a-z0-9]+", "-", word.lower()).strip("-")[:40] or "x"
    cache = _cache_dir() / "search" / f"{site}_{slug}_{want}.json"
    saved = read_json(cache) or {}
    if saved and time.time() - float(saved.get("at", 0)) < CACHE_HOURS * 3600:
        return saved.get("results") or []
    if site == "pexels":
        q = urllib.parse.urlencode({"query": word, "orientation": want, "size": "medium", "per_page": 15})
        results = pexels_results(_get_json(f"{PEXELS_URL}?{q}", {"Authorization": key}, "Pexels"))
    else:
        q = urllib.parse.urlencode({"key": key, "q": word, "video_type": "film", "safesearch": "true", "per_page": 20})
        results = pixabay_results(_get_json(f"{PIXABAY_URL}?{q}", {}, "Pixabay"))
    write_json(cache, {"at": time.time(), "word": word, "results": results})
    return results


def download(c: dict) -> Path:
    """A small HD copy of the clip, kept in the app folder (stock/clips). Size and time limited."""
    folder = _cache_dir() / "clips"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{c['site'].lower()}_{c['id']}_{c['width']}x{c['height']}.mp4"
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    part = dest.with_suffix(".part")
    req = urllib.request.Request(c["link"], headers={"User-Agent": UA})
    started, got = time.time(), 0
    try:
        with _open(req, TIMEOUT) as r, open(part, "wb") as out:
            size = int(r.headers.get("Content-Length") or 0) if getattr(r, "headers", None) else 0
            if size > MAX_BYTES:
                raise StockError(f"the {c['site']} clip is too big ({size // 1_000_000} MB)")
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                got += len(chunk)
                if got > MAX_BYTES:
                    raise StockError(f"the {c['site']} clip is too big (over {MAX_BYTES // 1_000_000} MB)")
                if time.time() - started > DOWNLOAD_LIMIT:
                    raise StockError(f"the {c['site']} clip took too long to download")
                out.write(chunk)
    except urllib.error.HTTPError as exc:
        part.unlink(missing_ok=True)
        raise StockError(_reason(exc.code, c["site"])) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        part.unlink(missing_ok=True)
        raise StockError(f"the {c['site']} clip could not be downloaded") from None
    except StockError:
        part.unlink(missing_ok=True)
        raise
    if got == 0:
        part.unlink(missing_ok=True)
        raise StockError(f"the {c['site']} clip was empty")
    part.replace(dest)
    return dest


# ---------------------------------------------------------------- credits

def credits_text(used: list[dict]) -> str:
    out = ["Stock footage in this film", "",
           "These clips were found on Pexels and Pixabay with your own free key. Their licences allow free use in your",
           "videos, including commercial use. Credit is not required by the licences, but it is polite, Pexels asks for",
           "it where possible, and both sites ask that you say where the clips came from. For example in your caption:",
           "  " + "; ".join(sorted({f"Video by {u['author']} on {u['site']}" for u in used})), ""]
    for i, u in enumerate(used, 1):
        out += [f"{i}. {u['t0']:.1f}-{u['t1']:.1f} s in the film, for the word '{u['word']}': video by {u['author']} on {u['site']}",
                f"   Clip page: {u['page'] or '-'}",
                f"   Creator:   {u['author_url'] or '-'}",
                f"   Licence:   {u['licence']}, {u['licence_url']}", ""]
    return "\n".join(out)


# ---------------------------------------------------------------- the editor's hook

def _lines(proj, m: dict, p: dict) -> list[dict]:
    from video_studio import story

    en = story.assign(p.get("utterances") or [], story.english(proj.read("transcript.json") or {}))
    lines: list[dict] = []
    for pc in m["pieces"]:
        end = pc["out_start"] + (pc["src_end"] - pc["src_start"])
        if lines and lines[-1]["id"] == pc["utt"]:
            lines[-1]["t1"] = end
        else:
            lines.append({"id": pc["utt"], "t0": pc["out_start"], "t1": end, "en": en.get(pc["utt"], "")})
    return lines


def apply_pass(proj, m: dict, p: dict, video: Path, splan: dict, w: int, h: int, total: float) -> Path:
    """Add free stock cutaways when asked and a key is set. Returns the new picture track (or the same one)."""
    from video_studio import broll
    from video_studio.engines import ff

    opts = proj.data.get("edit_options") or {}
    credits = proj.exports / "credits.txt"
    credits.unlink(missing_ok=True)   # a credits file from an earlier edit of this project could now be wrong
    if not wanted(proj.data.get("brief", ""), opts):
        return video
    if p.get("mode") == "montage":
        console.info("Stock footage: not used in a music montage (there are no spoken lines to match)")
        return video
    found = keys()
    if not found:
        console.info("Stock footage skipped. " + NO_KEY)
        m["stock"] = {"used": [], "skipped": "no key"}
        proj.write("edit_manifest.json", m)
        return video
    lines = _lines(proj, m, p)
    picks = search_words(lines, p.get("title", ""), proj.data.get("brief", ""))
    if not picks:
        console.info("Stock footage: no clear topic word to search for, so none was added")
        return video
    busy = [(s["t0"] - 0.2, s["t1"] + 0.2) for s in (splan or {}).get("slams", []) if "t0" in s]
    taken = [(c["t0"], c["t1"]) for c in m.get("broll") or []]
    busy += [(a - 0.3, b + 0.3) for a, b in taken]
    slots = place(lines, picks, busy, total, taken)
    if not slots:
        console.info("Stock footage: no free moment for a cutaway (the film is short or already full)")
        return video
    console.info(f"Stock footage: searching {sites_text(found)} for {', '.join(repr(s['word']) for s in slots)}. "
                 "Only these words leave this PC; no video, sound or transcript is sent.")
    want = orientation(w, h)
    cuts, used, seen, problems = [], [], set(), []
    for sl in slots:
        need = sl["t1"] - sl["t0"]
        clip = None
        for site in ("pexels", "pixabay"):
            if site not in found:
                continue
            try:
                c = choose(search(site, found[site], sl["word"], want), want, need, seen)
                if not c:
                    continue
                path = download(c)
                dur = ff.duration(path)
                if dur < need + 0.2:
                    continue
                clip = {**c, "path": str(path), "file_seconds": dur}
                break
            except StockError as exc:
                problems.append(str(exc))
            except Exception as exc:  # noqa: BLE001 - a broken download must never stop the edit
                problems.append(f"a {site} clip could not be used ({type(exc).__name__})")
        if not clip:
            continue
        seen.add((clip["site"], clip["id"]))
        start = round(min(1.0, max(0.0, (clip["file_seconds"] - need - 0.2) / 2)), 3)
        cuts.append({"t0": sl["t0"], "t1": sl["t1"], "path": clip["path"], "src": start, "stock": True,
                     "why": f"free stock clip for '{sl['word']}', video by {clip['author']} on {clip['site']}"})
        used.append({**{k: clip[k] for k in ("site", "id", "page", "author", "author_url", "licence", "licence_url")},
                     "word": sl["word"], "t0": sl["t0"], "t1": sl["t1"], "file": Path(clip["path"]).name})
    for msg in dict.fromkeys(problems):
        console.warn(f"Stock footage: {msg}.")
    m["stock"] = {"used": used, "words": [s["word"] for s in slots]}
    if not cuts:
        console.info("Stock footage: no suitable clip was found, so none was added")
        proj.write("edit_manifest.json", m)
        return video
    m["broll"] = sorted((m.get("broll") or []) + cuts, key=lambda c: c["t0"])   # your pictures keep clear of them
    proj.write("edit_manifest.json", m)
    proj.exports.mkdir(parents=True, exist_ok=True)
    credits.write_text(credits_text(used) + "\n", encoding="utf-8")
    for c in cuts:
        console.info(f"Stock {c['t0']:.1f}-{c['t1']:.1f}s: {c['why']}")
    console.ok(f"Stock footage: {len(cuts)} free clip(s) added; credits in {credits}")
    try:
        return broll.apply(video, cuts, w, h, m["fps"], proj.build / "with_stock.mp4", proj.settings["quality"])
    except Exception as exc:  # noqa: BLE001 - a clip FFmpeg cannot read must not stop the edit
        console.warn(f"Stock footage: the clips could not be laid over the film ({str(exc)[:100]}); the film is kept without them.")
        m["broll"] = [c for c in m["broll"] if not c.get("stock")]
        m["stock"]["used"] = []
        proj.write("edit_manifest.json", m)
        credits.unlink(missing_ok=True)
        return video
