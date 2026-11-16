"""Layered style compositor (runs in the system Python: numpy, opencv, mediapipe, pillow).

Reads the cut video frame by frame and builds a layered, motion-designed picture:
  real footage (contrast-only grade, the background is never replaced)
  -> virtual camera (wide / close / push / dutch per shot, centred on the face)
  -> words slammed BEHIND the speaker (person matte), background dimmed, rim light on the subject
  -> 3D logo sprites and keyword chips in front
  -> transitions on cuts (whip, zoom-through, flash, invert, glitch, blur-scale) and impact hits
     (shake, RGB split, flash), bloom, vignette, film grain
Usage: python style_helper.py plan.json   (plan written by video_studio/engines/style.py)
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

P = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
W, H, FPS = P["width"], P["height"], P["fps"]
FONTS = Path(P["fonts"])
RNG = np.random.default_rng(P.get("seed", 7))


def clamp(x, a=0.0, b=1.0):
    return a if x < a else b if x > b else x


def ease(u):  # ease-out cubic
    u = clamp(u)
    return 1 - (1 - u) ** 3


def ease_in(u):
    u = clamp(u)
    return u ** 3


def ease_io(u):
    u = clamp(u)
    return u * u * (3 - 2 * u)


def back_out(u, s=1.7):
    u = clamp(u) - 1
    return u * u * ((s + 1) * u + s) + 1


def hex_bgr(h):
    h = h.lstrip("#")
    return np.array([int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16)], np.float32) / 255


PAL = {k: hex_bgr(v) for k, v in P["palette"].items()}


# ------------------------------------------------------------------ text sprites

def _font(name, size):
    return ImageFont.truetype(str(FONTS / name), max(8, int(size)))


def _has_indic(text):
    return any(ord(c) > 0x900 for c in text)


@lru_cache(64)
def slam_sprite(text, max_w, style, max_h=100000):
    """Condensed bold italic, left-to-right gradient (dark accent -> bright accent), soft shadow. BGRA float."""
    indic = _has_indic(text)
    fname = "Mukta-ExtraBold.ttf" if indic else "BarlowCondensed-ExtraBoldItalic.ttf"
    words = text if indic else text.upper()
    lines = [words]
    if len(words) > 11 and " " in words:  # two balanced lines
        parts = words.split()
        best = min(range(1, len(parts)), key=lambda i: abs(len(" ".join(parts[:i])) - len(" ".join(parts[i:]))))
        lines = [" ".join(parts[:best]), " ".join(parts[best:])]
    size = 420
    f = _font(fname, size)
    while size > 60 and (max(f.getbbox(l)[2] - f.getbbox(l)[0] for l in lines) > max_w
                         or size * (1.02 if indic else 0.86) * len(lines) + size * 0.35 > max_h):
        size = int(size * 0.92)
        f = _font(fname, size)
    lh = int(size * (1.02 if indic else 0.86))
    tw = max(f.getbbox(l)[2] - f.getbbox(l)[0] for l in lines) + 40
    th = lh * len(lines) + int(size * 0.35)
    mask = Image.new("L", (tw, th), 0)
    d = ImageDraw.Draw(mask)
    for i, l in enumerate(lines):
        lw = f.getbbox(l)[2] - f.getbbox(l)[0]
        d.text(((tw - lw) // 2 - f.getbbox(l)[0], i * lh + 10), l, font=f, fill=255)
    a = np.asarray(mask, np.float32) / 255
    x = np.linspace(0, 1, tw, dtype=np.float32)[None, :, None]
    c0, c1 = (PAL["accent"], PAL["accent"] * 0.45 + 0.55) if style in ("accent", "popout") else (PAL["ink"] * 0.86, PAL["ink"])
    rgb = c0 * (1 - x) + c1 * x
    rgb = np.repeat(rgb, th, 0)
    out = np.dstack([rgb, a[..., None]])
    return out


@lru_cache(64)
def chip_sprite(text, size):
    f = _font("BarlowCondensed-BoldItalic.ttf" if not _has_indic(text) else "Mukta-ExtraBold.ttf", size)
    t = text if _has_indic(text) else text.upper()
    b = f.getbbox(t)
    pad_x, pad_y = int(size * 0.45), int(size * 0.22)
    w, h = b[2] - b[0] + pad_x * 2, b[3] - b[1] + pad_y * 2
    im = Image.new("RGBA", (w + 24, h + 24), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    acc = tuple(int(c * 255) for c in PAL["accent"][::-1])
    d.rounded_rectangle([12, 12, 12 + w, 12 + h], radius=int(h * 0.28), fill=acc + (255,))
    ink = tuple(int(c * 255) for c in PAL["chip_ink"][::-1])
    d.text((12 + pad_x - b[0], 12 + pad_y - b[1]), t, font=f, fill=ink + (255,))
    arr = np.asarray(im, np.float32) / 255
    return np.dstack([arr[..., 2], arr[..., 1], arr[..., 0], arr[..., 3]])


def over(dst, src, x, y, alpha=1.0):
    h, w = src.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if x0 >= x1 or y0 >= y1 or alpha <= 0:
        return
    s = src[y0 - y:y1 - y, x0 - x:x1 - x]
    a = s[..., 3:4] * alpha
    dst[y0:y1, x0:x1] = dst[y0:y1, x0:x1] * (1 - a) + s[..., :3] * a


def place(dst, src, cx, cy, scale=1.0, angle=0.0, alpha=1.0, blur=0.0):
    if scale <= 0.01 or alpha <= 0.005:
        return
    h, w = src.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, scale)
    nw, nh = int(w * scale * 1.3) + 8, int(h * scale * 1.3) + 8
    M[0, 2] += nw / 2 - w / 2
    M[1, 2] += nh / 2 - h / 2
    im = cv2.warpAffine(src, M, (nw, nh), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
    if blur > 0.4:
        im = soft_blur(im, blur)
    over(dst, im, int(cx - nw / 2), int(cy - nh / 2), alpha)


def shadow_of(sprite, blur=18, op=0.55):
    a = soft_blur(sprite[..., 3], blur) * op
    return np.dstack([np.zeros(sprite.shape[:2] + (3,), np.float32), a])


# ------------------------------------------------------------------ grade / texture

def make_lut():
    # contrast-only grade (owner-approved): no colour shift, highlights rolled off so a face never burns
    x = np.arange(256) / 255
    y = np.clip((x - 0.5) * P.get("contrast", 1.06) + 0.5, 0, 1)
    y = np.interp(y, [0, 0.6, 0.86, 1], [0.015, 0.6, 0.83, 0.94])
    return np.clip(y * 255 + 0.5, 0, 255).astype(np.uint8)


LUT = make_lut()
GRADE_REF = P.get("grade_ref")


def grade_like(img_u8):
    """Move this frame's colour toward the learned reference: a/b tint and spread (60%), L contrast only.
    Brightness is never raised (bright faces must not burn)."""
    if not GRADE_REF:
        return img_u8
    small = cv2.resize(img_u8, (W // 8, H // 8), interpolation=cv2.INTER_AREA)
    s_lab = cv2.cvtColor(small, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)
    mu, sd = s_lab.mean(0), s_lab.std(0) + 1e-3
    tmu, tsd = np.array(GRADE_REF["lab"], np.float32), np.array(GRADE_REF["lab_sd"], np.float32)
    lab = cv2.cvtColor(img_u8, cv2.COLOR_BGR2LAB).astype(np.float32)
    k = 0.6
    gL = float(np.clip(1 + k * (tsd[0] / sd[0] - 1), 0.85, 1.25))
    lab[..., 0] = (lab[..., 0] - mu[0]) * gL + mu[0] - max(0.0, (gL - 1) * 0.0)
    for c in (1, 2):
        g = float(np.clip(1 + k * (tsd[c] / sd[c] - 1), 0.8, 1.35))
        shift = float(np.clip(k * (tmu[c] - mu[c]), -8, 8))
        lab[..., c] = (lab[..., c] - mu[c]) * g + mu[c] + shift
    out = cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)
    return np.minimum(out, np.maximum(img_u8, out)) if gL <= 1 else out
yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
VIGN = (1 - 0.26 * np.clip(((xx - W / 2) / (W * 0.62)) ** 2 + ((yy - H * 0.45) / (H * 0.66)) ** 2, 0, 1))[..., None]
GRAIN = [cv2.resize(RNG.normal(0, 1, (H // 2, W // 2)).astype(np.float32), (W, H))[..., None] for _ in range(6)]
# Three-channel copies for OpenCV's multi-core maths (numpy broadcasting over a 1080x1920 frame is single-core).
# The grain strength is baked in once instead of being multiplied on every frame.
VIGN3 = np.ascontiguousarray(np.repeat(VIGN, 3, 2))
GRAIN3 = [np.ascontiguousarray(np.repeat(g * P.get("grain", 0.022), 3, 2)) for g in GRAIN]
del yy, xx


def soft_blur(img, sigma):
    """Gaussian blur for large radii, done at 1/f resolution (same look, ~f^2 faster)."""
    f = 8 if sigma >= 40 else (4 if sigma >= 12 else (2 if sigma >= 4 else 1))
    if f == 1:
        return cv2.GaussianBlur(img, (0, 0), sigma)
    h, w = img.shape[:2]
    small = cv2.resize(img, (w // f, h // f), interpolation=cv2.INTER_AREA)
    out = cv2.resize(cv2.GaussianBlur(small, (0, 0), sigma / f), (w, h), interpolation=cv2.INTER_LINEAR)
    return out if out.ndim == img.ndim else out[..., None]


def bloom(img, k):
    # same maths as clip(img - 0.6, 0, 1) -> blur at 1/8 -> add, but with OpenCV's multi-core operations
    hi = cv2.min(cv2.max(cv2.subtract(img, (0.6, 0.6, 0.6, 0.0)), 0.0), 1.0)
    small = cv2.resize(hi, (W // 8, H // 8), interpolation=cv2.INTER_AREA)
    g = cv2.add(cv2.GaussianBlur(small, (0, 0), 4), cv2.GaussianBlur(small, (0, 0), 12))
    return cv2.scaleAdd(cv2.resize(g, (W, H)), float(k), img)


def finish(img, k):
    """Vignette + film grain + clip to 8-bit, in three multi-core passes."""
    out = cv2.add(cv2.multiply(img, VIGN3), GRAIN3[k % 6])
    return cv2.convertScaleAbs(cv2.max(out, 0.0), alpha=255.0)


# ------------------------------------------------------------------ person matte + face

class Matte:
    def __init__(self, model):
        self.seg = None
        self.prev = None
        if model and Path(model).exists():
            import mediapipe as mp
            from mediapipe.tasks.python import BaseOptions, vision

            self.mp = mp
            self.seg = vision.ImageSegmenter.create_from_options(vision.ImageSegmenterOptions(
                base_options=BaseOptions(model_asset_path=str(model)), running_mode=vision.RunningMode.VIDEO,
                output_confidence_masks=True))
        self.face = np.array([W / 2, H * 0.42], np.float32)
        self.target = None  # latest measured face position; the camera eases toward it every frame
        self.last = -10**9

    def __call__(self, bgr, k, need=True):
        """Person matte at full size when `need` (words behind the speaker, invert glow), else None.
        Segmentation is the slowest step of the style pass, so outside those windows it runs only every
        FACE_STEP frames, just to keep the face position fresh; the per-frame camera smoothing is unchanged."""
        if self.seg is None:
            return np.zeros((H, W), np.float32) if need else None
        if need or k - self.last >= FACE_STEP:
            self.last = k
            small = cv2.cvtColor(cv2.resize(bgr, (W // 4, H // 4), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB)
            r = self.seg.segment_for_video(self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=np.ascontiguousarray(small)),
                                           int(k * 1000 / FPS))
            m = 1 - r.confidence_masks[0].numpy_view()
            self.prev = m if self.prev is None else 0.65 * m + 0.35 * self.prev
            self.mask = np.clip((self.prev - 0.35) / 0.4, 0, 1).astype(np.float32)
            # face: top of the person mass, horizontally its centre there
            m = self.mask
            rows = np.where(m.max(1) > 0.5)[0]
            if len(rows) > 10:
                top = rows[0]
                band = m[top:top + m.shape[0] // 7]
                cols = np.where(band.max(0) > 0.5)[0]
                if len(cols):
                    self.target = np.array([(cols[0] + cols[-1]) / 2 * 4, (top + m.shape[0] * 0.11) * 4], np.float32)
        if self.target is not None:
            self.face = 0.85 * self.face + 0.15 * self.target
        return cv2.resize(self.mask, (W, H), interpolation=cv2.INTER_LINEAR) if need else None


FACE_STEP = 4  # face position measured ~7 times a second outside the matte windows


def matte_needed(t):
    """Frames where the full person matte is drawn (plus a short lead-in so its smoothing has settled)."""
    for s in SLAMS:
        if s["t0"] - 0.4 <= t < s["t1"] + 0.3:
            return True
    for tr in TRANS:
        if tr["kind"] == "invert" and -0.4 <= t - tr["t"] < 0.2:
            return True
    return False


# ------------------------------------------------------------------ timeline helpers

SHOTS = P["shots"]  # [{t0,t1,kind}]
TRANS = P["transitions"]  # [{t, kind}]
HITS = P["hits"]  # [t]
PUNCHES = P.get("punches", [])
SLAMS = P["slams"]  # [{t0,t1,text,style}]
CHIPS = P["chips"]  # [{t0,t1,text}]
LOGOS = P["logos"]  # [{t0,t1,dir,frames,side}]
ZERO_MATTE = np.zeros((H, W), np.float32)  # stands in for the matte on frames that never draw it


def shot_at(t):
    for s in SHOTS:
        if s["t0"] <= t < s["t1"]:
            return s
    return SHOTS[-1] if SHOTS else {"t0": 0, "t1": 1e9, "kind": "wide"}


def camera(t, face):
    s = shot_at(t)
    u = (t - s["t0"]) / max(0.2, s["t1"] - s["t0"])
    kind = s["kind"]
    k, ang = 1.0, 0.0
    if kind == "wide":
        k = 1.0 + 0.035 * ease_io(u)
    elif kind == "close":
        k = 1.30 + 0.03 * u
    elif kind == "push":
        k = 1.0 + 0.14 * ease_io(u)
    elif kind == "dutch":
        k, ang = 1.14 + 0.02 * u, 4.0 * (1 if int(s["t0"] * 10) % 2 else -1)
    elif kind == "pull":
        k = 1.22 - 0.16 * ease_io(u)
    for h in HITS:  # impact punch
        if 0 <= t - h < 0.32:
            k *= 1 + 0.06 * (1 - ease((t - h) / 0.32))
    for p in PUNCHES:  # learned beat punch-in: 2-frame snap in, ~8-frame ease back
        d = t - p
        if 0 <= d < 2 / FPS:
            k *= 1 + 0.09 * (d * FPS / 2)
        elif 2 / FPS <= d < 10 / FPS:
            k *= 1 + 0.09 * (1 - ease((d - 2 / FPS) / (8 / FPS)))
    cx, cy = float(face[0]), float(face[1])
    M = cv2.getRotationMatrix2D((cx, cy), ang, k)
    # keep the frame filled: shift so the scaled image covers [0,W]x[0,H]
    return M, k


def trans_near(t, window=0.2):
    for tr in TRANS:
        if abs(t - tr["t"]) <= window:
            return tr, t - tr["t"]
    return None, 0.0


# ------------------------------------------------------------------ effects

def whip(img, d):
    # d in [-0.2, 0.2] around the cut: strong horizontal motion blur and slide
    k = 1 - abs(d) / 0.2
    shift = int(W * 0.18 * k * (1 if d < 0 else -1))
    img = np.roll(img, shift, 1)
    n = int(5 + 40 * k) | 1  # blur length at half resolution
    small = cv2.blur(cv2.resize(img, (W // 2, H // 2), interpolation=cv2.INTER_AREA), (n, 1))
    return cv2.resize(small, (W, H)) * k + img * (1 - k)


def zoom_through(img, d):
    k = 1 - abs(d) / 0.2
    sc = 1 + 0.35 * k
    acc = np.zeros_like(img)
    for i in range(4):
        s = sc * (1 + 0.04 * i * k)
        M = cv2.getRotationMatrix2D((W / 2, H / 2), 0, s)
        acc += cv2.warpAffine(img, M, (W, H), borderMode=cv2.BORDER_REFLECT)
    return acc / 4


def glitch(img, d, seed):
    r = np.random.default_rng(seed)
    k = 1 - abs(d) / 0.2
    out = img.copy()
    dsplit = int(26 * k)
    if dsplit:
        out[..., 2] = np.roll(img[..., 2], dsplit, 1)
        out[..., 0] = np.roll(img[..., 0], -dsplit, 1)
    for _ in range(int(10 * k)):
        y0 = int(r.integers(0, H - 40))
        hh = int(r.integers(8, 70))
        out[y0:y0 + hh] = np.roll(out[y0:y0 + hh], int(r.integers(-120, 120)), 1)
    return out


def invert_glow(img, matte):
    inv = 1 - img
    edge = np.clip(soft_blur(matte, 6) - matte * 0.7, 0, 1)[..., None]
    return inv * 0.85 + edge * np.array([1.0, 1.0, 0.7], np.float32) * 1.2


def blur_scale(img, d):
    if d < 0:
        u = 1 - abs(d) / 0.2
        M = cv2.getRotationMatrix2D((W / 2, H / 2), 0, 1 - 0.1 * u)
        img = cv2.warpAffine(img, M, (W, H), borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
        return soft_blur(img, 1 + 14 * u)
    return img


def hit_fx(img, t):
    for h in HITS:
        u = (t - h) / 0.26
        if 0 <= u < 1:
            k = 1 - u
            r = np.random.default_rng(int(h * 1000) + int(u * 9))
            sx, sy = int(r.normal(0, 14 * k)), int(r.normal(0, 14 * k))
            img = np.roll(np.roll(img, sy, 0), sx, 1)
            dd = int(16 * k)
            if dd:
                img = img.copy()
                img[..., 2] = np.roll(img[..., 2], dd, 1)
                img[..., 0] = np.roll(img[..., 0], -dd, 1)
            if u < 0.16:
                img = img * 0.72 + 0.28
    return img


# ------------------------------------------------------------------ layers

BG0, BG1 = PAL["bg0"], PAL["bg1"]
_g = np.linspace(0, 1, H, dtype=np.float32)[:, None, None]
BACKDROP = (BG0 * (1 - _g) + BG1 * _g) * np.ones((1, W, 1), np.float32)


def rim(matte, lvl):
    """Light on the inner edge of the subject only (never tints the face)."""
    m2 = cv2.resize(matte, (W // 2, H // 2), interpolation=cv2.INTER_AREA)
    inner = np.clip(m2 - cv2.GaussianBlur(m2, (0, 0), 2.5), 0, 1)
    glow = cv2.resize(cv2.GaussianBlur(np.clip(inner * 2.0, 0, 1), (0, 0), 2), (W, H), interpolation=cv2.INTER_LINEAR)
    return (glow * 0.55 * lvl)[..., None] * PAL["rim"]


_CARD = {}


def card_layers(x0, x1, y0, y1, r):
    """Rounded card mask + its shadow/stroke factors; cached because the card is still for most of the hook."""
    key = (x0, x1, y0, y1, r)
    if key not in _CARD:
        if len(_CARD) > 24:
            _CARD.clear()
        card = np.zeros((H, W), np.uint8)
        cv2.rectangle(card, (x0 + r, y0), (x1 - r, y1), 255, -1)
        cv2.rectangle(card, (x0, y0 + r), (x1, y1 - r), 255, -1)
        for cx, cy in ((x0 + r, y0 + r), (x1 - r, y0 + r), (x0 + r, y1 - r), (x1 - r, y1 - r)):
            cv2.circle(card, (cx, cy), r, 255, -1, cv2.LINE_AA)
        cm = cv2.GaussianBlur(card.astype(np.float32) / 255, (0, 0), 1.2)
        sh = soft_blur(cm, 28)
        stroke = np.clip(cv2.dilate(card, np.ones((9, 9), np.uint8)).astype(np.float32) / 255 - cm, 0, 1)
        F0 = (1 - sh * 0.55) * (1 - stroke) * (1 - cm)
        C0 = (stroke * (1 - cm))[..., None] * PAL["accent"]
        _CARD[key] = (cm, F0.astype(np.float32), C0.astype(np.float32))
    return _CARD[key]


def popout(base, matte, t, s, face):
    """Hook layout: footage shrinks into a framed card on a backdrop, giant words above it,
    the speaker's head breaks out over the card edge and over the words (real background stays in the card)."""
    a, b = s["t0"], s["t1"]
    u_in = ease((t - a) / 0.28)
    u_out = ease_in(clamp((t - b) / 0.22))
    sc = 1 - 0.24 * u_in * (1 - u_out)            # card scale
    if sc > 0.995:
        return base
    top_crop = H * 0.10 * u_in * (1 - u_out)       # card starts below the top of the head -> head pops out
    y0 = H * 0.33 * u_in * (1 - u_out) + top_crop * sc * (1 - u_in)
    # place footage: source point (W/2, top_crop) -> (W/2, y0)
    M = np.float32([[sc, 0, W / 2 - sc * W / 2], [0, sc, y0 - sc * top_crop]])
    foot = cv2.warpAffine(base, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    m = cv2.warpAffine(matte, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    cm, F0, C0 = card_layers(int(W / 2 - sc * W / 2), int(W / 2 + sc * W / 2), int(y0), int(y0 + sc * (H - top_crop)), int(46 * sc))
    drift = 1 + 0.03 * clamp((t - a) / 2.5)
    # soft glow blobs on the backdrop (motion in every frame), built at 1/8 resolution
    bd = cv2.resize(BACKDROP, (W // 8, H // 8), interpolation=cv2.INTER_AREA)
    for i, col in enumerate((PAL["accent"], PAL["rim"])):
        cx = (W * (0.25 + 0.5 * i) + 60 * math.sin(t * 1.3 + i)) / 8
        cy = (H * (0.18 + 0.6 * i) + 40 * math.cos(t * 1.1 + i)) / 8
        cv2.circle(bd, (int(cx), int(cy)), int(W * 0.42 / 8), (col * 0.35).tolist(), -1)
    out = cv2.resize(cv2.GaussianBlur(bd, (0, 0), 7.5), (W, H), interpolation=cv2.INTER_CUBIC)
    # giant words, behind everything that follows
    sprite = slam_sprite(s["text"], int(W * 0.94), "popout", int(H * 0.2))
    u = (t - a) / 0.22
    tsc = (2.0 - 1.0 * ease(u)) * drift * (1 + 0.5 * u_out)
    block = sprite.shape[0] * drift
    ty = H * 0.035 + block / 2
    if u < 1:
        for k in range(3, 0, -1):
            place(out, sprite, W / 2, ty, tsc * (1 + 0.15 * k * (1 - u)), 0, 0.16 * (1 - u))
    place(out, shadow_of(sprite, 22, 0.6), W / 2 + 6, ty + 16, tsc, 0, clamp(u * 3) * (1 - u_out))
    place(out, sprite, W / 2, ty, tsc, 0, clamp(u * 3) * (1 - u_out), 9 * (1 - ease(u * 1.4)))
    # card shadow + stroke + footage, then the speaker over the card edge and over the words (one fused blend)
    pm = m * (1 - cm)
    keep = (1 - pm)[..., None]
    out = out * (F0[..., None] * keep) + C0 * keep + foot * (1 - (1 - cm) * (1 - pm))[..., None]
    out = out + rim(m, 0.7)
    # blend in/out with the plain footage at the edges of the window
    k = clamp((1 - sc) / 0.24)
    return base * (1 - k) + out * k


def front_slam(base, matte, t, s, face):
    """Emphasis word: background dimmed (same real room), word slammed in front at chest height, rim on the speaker."""
    a, b = s["t0"], s["t1"]
    u = (t - a) / 0.18
    v = clamp((t - b) / 0.16)
    lvl = clamp(u * 3) * (1 - v)
    m = matte[..., None]
    g = cv2.cvtColor(base, cv2.COLOR_BGR2GRAY)[..., None]
    dim = (base * 0.45 + g * 0.55) * 0.42
    out = base * (1 - lvl * (1 - m)) + dim * lvl * (1 - m)
    out = out + rim(matte, lvl)
    sprite = slam_sprite(s["text"], int(W * 0.9), "ink")
    sc = (1.8 - 0.8 * back_out(clamp(u))) * (1 + 0.04 * clamp((t - a) / 1.0)) * (1 + 0.5 * ease_in(v))
    y = H * 0.6
    ang = -4 if int(a * 10) % 2 else 3
    place(out, shadow_of(sprite, 20, 0.7), W / 2 + 8, y + 16, sc, ang, lvl)
    place(out, sprite, W / 2, y, sc, ang, lvl, 7 * v)
    return out


def draw_slams(base, matte, t, face):
    out = base
    for s in SLAMS:
        if s["t0"] - 0.01 <= t < s["t1"] + 0.24:
            out = popout(out, matte, t, s, face) if s["style"] == "accent" else front_slam(out, matte, t, s, face)
    return out


def draw_chips(img, t, face):
    for i, c in enumerate(CHIPS):
        a, b = c["t0"], c["t1"]
        if not (a <= t < b + 0.2):
            continue
        u = (t - a) / 0.32
        v = clamp((t - b) / 0.2)
        sp = chip_sprite(c["text"], int(W * 0.075))
        side = -1 if face[0] > W / 2 else 1
        cx = W / 2 + side * W * 0.24
        cy = H * (0.17 if i % 2 == 0 else 0.22)  # clear background beside the head, away from captions
        place(img, shadow_of(sp, 12, 0.5), cx + 6, cy + 10, back_out(u) * (1 - 0.3 * v), side * -4, 1 - v)
        place(img, sp, cx, cy - 6 * math.sin(t * 3), back_out(u) * (1 - 0.3 * v), side * -4, 1 - v, 6 * v)


@lru_cache(256)
def logo_frame(d, i):
    im = cv2.imread(str(Path(d) / f"{i:04d}.png"), cv2.IMREAD_UNCHANGED)
    if im is None:
        return None
    return im.astype(np.float32) / 255


def draw_logos(img, t, face):
    for lg in LOGOS:
        a, b = lg["t0"], lg["t1"]
        if not (a <= t < b + 0.25):
            continue
        u = (t - a) / 0.4
        v = clamp((t - b) / 0.25)
        n = lg["frames"]
        fr = logo_frame(lg["dir"], int((t - a) * FPS) % n + 1)
        if fr is None:
            continue
        side = lg.get("side") or (-1 if face[0] > W / 2 else 1)
        size = W * 0.42
        sc = size / fr.shape[1] * back_out(u) * (1 - 0.5 * ease_in(v))
        cx = clamp(W / 2 + side * W * 0.27, W * 0.22, W * 0.78)
        cy = clamp(face[1] - H * 0.02, H * 0.25, H * 0.55) - 12 * math.sin(t * 2.2)
        glow = np.dstack([np.repeat(cv2.GaussianBlur(fr[..., 3], (0, 0), 22)[..., None], 3, 2) * PAL["accent"], cv2.GaussianBlur(fr[..., 3], (0, 0), 22) * 0.6])
        place(img, glow, cx, cy, sc * 1.05, 0, (1 - v))
        place(img, fr, cx, cy, sc, 0, clamp(u * 3) * (1 - v), 8 * v)


def flash_at(img, t):
    for tr in TRANS:
        if tr["kind"] == "flash":
            f = round((t - tr["t"]) * FPS)
            if 0 <= f < 4:
                a = [0.9, 0.55, 0.28, 0.1][f]
                img = img * (1 - a) + a
    return img


# ------------------------------------------------------------------ main loop

def render_range(start, end, dst, tag=""):
    """Render frames [start, end) to dst. A few frames before `start` are decoded to warm up matte smoothing."""
    warm = min(start, 12)
    first = start - warm
    cf = 0x08000000 if sys.platform == "win32" else 0
    seek = ["-ss", f"{first / FPS:.4f}"] if first else []
    dec = subprocess.Popen([P["ffmpeg"], "-v", "error", *seek, "-i", P["input"], "-vf", f"scale={W}:{H},fps={FPS}", "-f", "rawvideo",
                            "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE, creationflags=cf)
    enc = subprocess.Popen([P["ffmpeg"], "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(FPS),
                            "-i", "-", "-c:v", "libx264", "-preset", P.get("preset", "veryfast"), "-crf", str(P.get("crf", 16)),
                            "-pix_fmt", "yuv420p", "-threads", str(P.get("enc_threads", 0)), dst], stdin=subprocess.PIPE, creationflags=cf)
    matte_fn = Matte(P.get("matte_model"))
    n = W * H * 3
    k = first
    while k < end:
        buf = dec.stdout.read(n)
        if len(buf) < n:
            break
        raw = np.frombuffer(buf, np.uint8).reshape(H, W, 3)
        t = k / FPS
        need = matte_needed(t)
        matte = matte_fn(raw, k, need)
        if k < start:
            k += 1
            continue
        face = matte_fn.face
        M, _ = camera(t, face)
        graded = cv2.LUT(grade_like(raw), LUT)
        frame = cv2.warpAffine(graded, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        frame = np.multiply(frame, np.float32(1 / 255), dtype=np.float32)
        matte = cv2.warpAffine(matte, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT) if need else ZERO_MATTE
        fpos = M[:, :2] @ face + M[:, 2]
        img = draw_slams(frame, matte, t, fpos)
        draw_chips(img, t, fpos)
        draw_logos(img, t, fpos)
        tr, d = trans_near(t)
        if tr:
            kind = tr["kind"]
            if kind == "whip":
                img = whip(img, d)
            elif kind == "zoom":
                img = zoom_through(img, d)
            elif kind == "glitch" and abs(d) <= 0.14:
                img = glitch(img, d / 0.7, int(tr["t"] * 100) + k)
            elif kind == "invert" and 0 <= d < 2 / FPS:
                img = invert_glow(img, matte)
            elif kind == "blurscale":
                img = blur_scale(img, d)
        img = flash_at(img, t)
        img = hit_fx(img, t)
        img = bloom(np.ascontiguousarray(img, np.float32), P.get("bloom", 0.35))
        enc.stdin.write(finish(img, k).tobytes())
        k += 1
        if (k - start) % 10 == 0:
            print(f"PART_PROGRESS {tag} {k - start}", flush=True)
    dec.stdout.close()
    dec.kill()
    enc.stdin.close()
    enc.wait()
    return k - start


def main():
    total = int(round(P["duration"] * FPS))
    if "--part" in sys.argv:  # worker process
        i = sys.argv.index("--part")
        a, b, dst = int(sys.argv[i + 1]), int(sys.argv[i + 2]), sys.argv[i + 3]
        done = render_range(a, b, dst, sys.argv[i + 4])
        print(f"PART_DONE {done}", flush=True)
        return
    workers = max(1, min(int(P.get("workers", 1)), total // 45))
    if workers == 1:
        done = render_range(0, total, P["output"], "0")
        print(f"STYLE_DONE {done}", flush=True)
        return
    import threading

    out = Path(P["output"])
    bounds = [round(total * i / workers) for i in range(workers + 1)]
    parts = [out.with_name(f"{out.stem}.part{i}.mp4") for i in range(workers)]
    progress = [0] * workers
    lock = threading.Lock()
    cf = (0x08000000 | 0x00004000) if sys.platform == "win32" else 0

    def pump(i, proc):
        for line in proc.stdout:
            if line.startswith("PART_PROGRESS"):
                with lock:
                    progress[i] = int(line.split()[2])
                    print(f"STYLE_PROGRESS {sum(progress)} {total}", flush=True)
            elif line.strip() and not line.startswith(("PART_DONE", "INFO", "W0000", "WARNING")):
                print(line.rstrip(), flush=True)

    procs = [subprocess.Popen([sys.executable, "-E", "-P", __file__, sys.argv[1], "--part", str(bounds[i]), str(bounds[i + 1]),
                               str(parts[i]), str(i)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                              encoding="utf-8", errors="replace", creationflags=cf) for i in range(workers)]
    threads = [threading.Thread(target=pump, args=(i, pr), daemon=True) for i, pr in enumerate(procs)]
    for th in threads:
        th.start()
    codes = [pr.wait() for pr in procs]
    for th in threads:
        th.join(5)
    if any(codes) or not all(pt.exists() for pt in parts):
        print(f"STYLE_ERROR worker failed {codes}", flush=True)
        sys.exit(1)
    lst = out.with_name(out.stem + ".parts.txt")
    lst.write_text("".join(f"file '{pt.as_posix()}'\n" for pt in parts), encoding="utf-8")
    subprocess.run([P["ffmpeg"], "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)],
                   check=True, creationflags=cf)
    for pt in [*parts, lst]:
        pt.unlink(missing_ok=True)
    print(f"STYLE_DONE {total}", flush=True)


if __name__ == "__main__":
    main()
