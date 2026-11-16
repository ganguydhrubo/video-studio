"""Built-in motion-graphics engine (Pillow + NumPy -> FFmpeg pipe).

Deterministic, offline and light: frames are drawn in memory and streamed to
FFmpeg, nothing is written per frame. Scene types: kinetic, list, stat, quote,
cta, title (2D fallback for 3D titles), media (Ken Burns on an image) and
`overlay` sprites used by the footage editor.
"""

from __future__ import annotations

import math
import subprocess
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from video_studio.engines import ff
from video_studio.looks import LOOKS, font_file, hex_rgb, is_light
from video_studio.util import ToolError, kill_tree, popen, release


# ----------------------------------------------------------------- easing

def ease_out(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return 1 - (1 - x) ** 3


def ease_in_out(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def ease_back(x: float) -> float:
    x = min(1.0, max(0.0, x))
    c1, c3 = 1.70158, 2.70158
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2


@lru_cache(maxsize=64)
def font(role: str, size: int) -> ImageFont.FreeTypeFont:
    size = max(1, int(size))
    path = font_file(role)
    if not path:
        return ImageFont.load_default(size=size)
    f = ImageFont.truetype(path, size)
    if path.lower().endswith("bahnschrift.ttf"):
        try:
            f.set_variation_by_name("Bold")
        except (OSError, ValueError):
            pass
    return f


# ------------------------------------------------------------- background

class Background:
    """Animated gradient with drifting light blobs and vignette.

    Everything is computed at 1/4 resolution (it is all low-frequency) and
    upscaled once per frame; film grain is added later by FFmpeg's C filter.
    """

    def __init__(self, w: int, h: int, look: str, seed: int = 0, intensity: float = 1.0) -> None:
        self.w, self.h = w, h
        self.sw, self.sh = max(64, w // 4), max(64, h // 4)
        pal = LOOKS[look]
        top, bottom = (np.array(hex_rgb(c), np.float32) for c in pal["bg"])
        ramp = np.linspace(0, 1, self.sh, dtype=np.float32)[:, None, None]
        base = top * (1 - ramp) + bottom * ramp
        ys, xs = np.mgrid[0:self.sh, 0:self.sw].astype(np.float32)
        self.gx, self.gy = xs / self.sw, ys / self.sh
        d = np.sqrt(((self.gx - 0.5) / 0.75) ** 2 + ((self.gy - 0.5) / 0.75) ** 2)
        light = is_light(look)
        vignette = 1 - np.clip(d - 0.35, 0, 1) * (0.25 if light else 0.55)
        self.base = (np.broadcast_to(base, (self.sh, self.sw, 3)) * vignette[..., None]).astype(np.float32)
        rng = np.random.default_rng(seed)
        self.blobs = []
        for i, color in enumerate(pal["blob"]):
            self.blobs.append({
                "color": np.array(hex_rgb(color), np.float32),
                "x": rng.uniform(0.15, 0.85), "y": rng.uniform(0.15, 0.85),
                "r": rng.uniform(0.35, 0.55), "sx": rng.uniform(0.04, 0.09) * (1 if i % 2 else -1),
                "sy": rng.uniform(0.03, 0.07), "phase": rng.uniform(0, 6.28),
            })
        self.blob_strength = (0.22 if light else 0.42) * intensity

    def frame(self, t: float, idx: int, zoom: float = 1.0) -> Image.Image:
        glow = np.zeros((self.sh, self.sw, 3), np.float32)
        aspect = self.sh / self.sw
        for b in self.blobs:
            cx = b["x"] + b["sx"] * math.sin(t * 0.35 + b["phase"])
            cy = b["y"] + b["sy"] * math.cos(t * 0.28 + b["phase"])
            dist = ((self.gx - cx) ** 2) + ((self.gy - cy) * aspect) ** 2
            glow += b["color"] * np.exp(-dist / (b["r"] ** 2 * 0.35))[..., None]
        k = self.blob_strength
        img = self.base * (1 - k) + (self.base * 0.4 + glow * 0.75) * k
        small = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
        if zoom != 1.0:
            cw, ch = self.sw / zoom, self.sh / zoom
            x0, y0 = (self.sw - cw) / 2, (self.sh - ch) / 2
            return small.resize((self.w, self.h), Image.BILINEAR, box=(x0, y0, x0 + cw, y0 + ch))
        return small.resize((self.w, self.h), Image.BILINEAR)


# ------------------------------------------------------------------ sprites

def text_sprite(text: str, size: int, color: str, role: str = "display", shadow: bool = True, stroke: int = 0,
                stroke_color: str = "#000000") -> Image.Image:
    """Render text on a transparent sprite with a shared baseline (font ascent/descent, not glyph box)."""
    f = font(role, size)
    ascent, descent = f.getmetrics()
    left = f.getbbox(text, stroke_width=stroke)[0]
    width = f.getlength(text) + stroke * 2
    pad = int(size * 0.35)
    w, h = int(width - left) + pad * 2, ascent + descent + pad * 2
    img = Image.new("RGBA", (max(1, w), max(1, h)), (0, 0, 0, 0))
    origin = (pad - left + stroke, pad)
    if shadow:
        sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).text((origin[0], origin[1] + size * 0.06), text, font=f, fill=(0, 0, 0, 120))
        img = Image.alpha_composite(img, sh.filter(ImageFilter.GaussianBlur(size * 0.08)))
    ImageDraw.Draw(img).text(origin, text, font=f, fill=hex_rgb(color) + (255,),
                             stroke_width=stroke, stroke_fill=hex_rgb(stroke_color) + (255,))
    img.info["pad"] = pad
    return img


def with_alpha(img: Image.Image, alpha: float) -> Image.Image:
    if alpha >= 0.999:
        return img
    out = img.copy()
    a = out.getchannel("A").point(lambda v: int(v * max(0.0, alpha)))
    out.putalpha(a)
    return out


def wrap_words(words: list[str], role: str, size: int, max_width: int) -> list[list[str]]:
    f = font(role, size)
    space = f.getlength(" ")
    lines, cur, width = [], [], 0.0
    for w in words:
        wl = f.getlength(w)
        if cur and width + space + wl > max_width:
            lines.append(cur)
            cur, width = [w], wl
        else:
            width = width + (space if cur else 0) + wl
            cur.append(w)
    if cur:
        lines.append(cur)
    return lines


def fit_size(words: list[str], role: str, max_width: int, max_lines: int, start: int, minimum: int) -> int:
    size = start
    while size > minimum:
        lines = wrap_words(words, role, size, max_width)
        if len(lines) <= max_lines and all(font(role, size).getlength(" ".join(l)) <= max_width for l in lines):
            return size
        size = int(size * 0.92)
    return minimum


# ------------------------------------------------------------------ scenes

class Scene:
    def __init__(self, visual: dict, w: int, h: int, fps: int, duration: float, look: str, motion: str, seed: int) -> None:
        self.v, self.w, self.h, self.fps, self.duration = visual, w, h, fps, duration
        self.look, self.pal = look, LOOKS[look]
        self.motion = {"subtle": 0.6, "dynamic": 1.0, "maximal": 1.4}.get(motion, 1.0)
        self.bg = Background(w, h, look, seed, intensity=self.motion)
        self.unit = min(w, h)
        self.portrait = h > w
        self.safe_w = int(w * (0.84 if self.portrait else 0.74))
        self.prepare()

    def prepare(self) -> None:
        pass

    def draw(self, canvas: Image.Image, t: float) -> None:
        pass

    def render(self, idx: int) -> Image.Image:
        t = idx / self.fps
        zoom = 1.0 + 0.04 * self.motion * (t / max(self.duration, 0.1))
        canvas = self.bg.frame(t, idx, zoom).convert("RGBA")
        self.draw(canvas, t)
        # global fade in/out keeps cuts clean even with "cut" transitions
        fade = min(1.0, t / 0.18, (self.duration - t) / 0.12)
        if fade < 1.0:
            black = Image.new("RGBA", canvas.size, (0, 0, 0, int(255 * (1 - max(0.0, fade)) * 0.6)))
            canvas = Image.alpha_composite(canvas, black)
        return canvas.convert("RGB")

    # shared building block: staggered word reveal
    def word_block(self, text: str, y_center: float, role: str, base_size: int, max_lines: int,
                   emphasis: list[str] | None = None, color: str | None = None, delay: float = 0.15) -> dict:
        words = text.split()
        size = fit_size(words, role, self.safe_w, max_lines, base_size, int(base_size * 0.4))
        lines = wrap_words(words, role, size, self.safe_w)
        emph = {e.lower().strip(".,!?") for e in emphasis or []}
        light = is_light(self.look)
        ink = color or self.pal["ink"]
        f = font(role, size)
        line_h = int(size * 1.12)
        total_h = line_h * len(lines)
        y0 = int(self.h * y_center - total_h / 2)
        items, k = [], 0
        for li, line in enumerate(lines):
            widths = [f.getlength(w) for w in line]
            space = f.getlength(" ")
            line_w = sum(widths) + space * (len(line) - 1)
            x = (self.w - line_w) / 2
            for w, ww in zip(line, widths):
                is_emph = w.lower().strip(".,!?") in emph
                fill = (self.pal["ink"] if light else "#111111") if is_emph and not light else ink
                if is_emph:
                    fill = "#FFFFFF" if light else "#0B0B0B"
                sprite = text_sprite(w, size, fill, role, shadow=not is_emph)
                items.append({"sprite": sprite, "x": x, "y": y0 + li * line_h, "w": ww, "emph": is_emph,
                              "start": delay + k * 0.075, "size": size})
                x += ww + space
                k += 1
        return {"items": items, "size": size, "height": total_h, "top": y0}

    def draw_words(self, canvas: Image.Image, block: dict, t: float, exit_at: float | None = None) -> None:
        exit_at = exit_at if exit_at is not None else self.duration - 0.35
        out = ease_in_out((t - exit_at) / 0.3) if t > exit_at else 0.0
        draw = ImageDraw.Draw(canvas)
        for it in block["items"]:
            p = ease_out((t - it["start"]) / 0.45)
            if p <= 0:
                continue
            size = it["size"]
            dy = (1 - p) * size * 0.55 * self.motion - out * size * 0.3
            alpha = p * (1 - out)
            if it["emph"]:
                pb = ease_out((t - it["start"] - 0.1) / 0.35)
                pad = size * 0.12
                bx0 = it["x"] - pad
                bx1 = bx0 + (it["w"] + pad * 2) * pb
                by0 = it["y"] + dy + size * 0.06
                if bx1 > bx0 + 1:
                    draw.rounded_rectangle((bx0, by0, bx1, by0 + size * 1.14), radius=int(size * 0.16),
                                           fill=hex_rgb(self.pal["accent"]) + (int(255 * alpha),))
            sp = with_alpha(it["sprite"], alpha)
            pad = it["sprite"].info.get("pad", 0)
            canvas.alpha_composite(sp, (int(it["x"] - pad), int(it["y"] + dy - pad)))


class Kinetic(Scene):
    def prepare(self) -> None:
        size = int(self.unit * (0.12 if self.portrait else 0.1))
        self.block = self.word_block(self.v.get("headline", ""), 0.40 if self.portrait else 0.45, "display", size,
                                     4, self.v.get("emphasis"))
        self.bar_w = int(self.w * 0.16)

    def draw(self, canvas: Image.Image, t: float) -> None:
        self.draw_words(canvas, self.block, t)
        p = ease_out((t - 0.5) / 0.5)
        y = self.block["top"] + self.block["height"] + int(self.unit * 0.05)
        if p > 0:
            d = ImageDraw.Draw(canvas)
            x0 = (self.w - self.bar_w) / 2
            d.rounded_rectangle((x0, y, x0 + self.bar_w * p, y + max(4, self.unit // 140)), radius=4,
                                fill=hex_rgb(self.pal["accent2"]) + (230,))


class Title2D(Kinetic):
    def prepare(self) -> None:
        size = int(self.unit * 0.15)
        text = self.v.get("text") or self.v.get("headline", "")
        from video_studio.planner import emphasis_words

        self.block = self.word_block(text, 0.38, "heavy", size, 3, emphasis_words(text))
        self.sub = None
        if self.v.get("subtitle"):
            self.sub = self.word_block(self.v["subtitle"], 0.38 + (self.block["height"] / self.h) / 2 + 0.07, "semibold",
                                       int(self.unit * 0.05), 2, color=self.pal["muted"], delay=0.6)
        self.bar_w = int(self.w * 0.2)

    def draw(self, canvas: Image.Image, t: float) -> None:
        super().draw(canvas, t)
        if self.sub:
            self.draw_words(canvas, self.sub, t)


class ListScene(Scene):
    def prepare(self) -> None:
        self.title = self.word_block(self.v.get("title", ""), 0.2 if self.portrait else 0.18, "display",
                                     int(self.unit * 0.085), 2, delay=0.1)
        items = self.v.get("items") or []
        n = max(1, len(items))
        self.rows = []
        size = int(self.unit * (0.07 if self.portrait else 0.055))
        size = min(size, fit_size(max(items, key=len, default="x").split(), "semibold", int(self.safe_w * 0.78), 2, size, 18))
        top = self.h * (0.33 if self.portrait else 0.36)
        span = self.h * (0.33 if self.portrait else 0.5)
        reveal = max(0.5, (self.duration - 1.2) / n)
        for i, text in enumerate(items):
            lines = wrap_words(text.split(), "semibold", size, int(self.safe_w * 0.78))
            self.rows.append({"lines": [text_sprite(" ".join(l), size, self.pal["ink"], "semibold") for l in lines],
                              "num": text_sprite(str(i + 1), int(size * 0.9), "#FFFFFF" if not is_light(self.look) else "#FFFFFF", "heavy", shadow=False),
                              "y": top + i * span / n, "start": 0.45 + i * reveal, "size": size})

    def draw(self, canvas: Image.Image, t: float) -> None:
        self.draw_words(canvas, self.title, t)
        d = ImageDraw.Draw(canvas)
        x0 = (self.w - self.safe_w) / 2
        exit_p = ease_in_out((t - (self.duration - 0.35)) / 0.3)
        for r in self.rows:
            p = ease_out((t - r["start"]) / 0.4)
            if p <= 0:
                continue
            a = p * (1 - exit_p)
            dx = (1 - p) * self.unit * 0.12
            size = r["size"]
            cx, cy = x0 + size * 0.75 - dx, r["y"] + size * 0.62
            rad = size * 0.68 * ease_back((t - r["start"]) / 0.4)
            d.ellipse((cx - rad, cy - rad, cx + rad, cy + rad), fill=hex_rgb(self.pal["accent"]) + (int(255 * a),))
            num = with_alpha(r["num"], a)
            pad = r["num"].info["pad"]
            canvas.alpha_composite(num, (int(cx - (num.width - 2 * pad) / 2 - pad), int(cy - (num.height - 2 * pad) / 2 - pad - size * 0.05)))
            for j, line in enumerate(r["lines"]):
                sp = with_alpha(line, a)
                canvas.alpha_composite(sp, (int(x0 + size * 1.8 - dx - line.info["pad"]), int(r["y"] + j * size * 1.15 - line.info["pad"] + size * 0.05)))


class StatScene(Scene):
    def prepare(self) -> None:
        self.value = float(self.v.get("value", 0))
        self.decimals = 0 if self.value == int(self.value) else 1
        self.num_size = int(self.unit * (0.24 if self.portrait else 0.2))
        final = f"{self.v.get('prefix', '')}{self.value:,.{self.decimals}f}{self.v.get('suffix', '')}"
        self.num_size = min(self.num_size, fit_size([final], "heavy", int(self.w * 0.62), 1, self.num_size, 24))
        num_w = font("heavy", self.num_size).getlength(final)
        self.ring_r = max(num_w / 2 + self.unit * 0.09, self.num_size * 0.95)
        cy = 0.38 if self.portrait else 0.4
        label_y = cy + (self.ring_r / self.h) + 0.07
        self.cy = self.h * cy
        self.label = self.word_block(self.v.get("label", ""), label_y, "semibold",
                                     int(self.unit * 0.06), 2, color=self.pal["muted"], delay=0.7)

    def draw(self, canvas: Image.Image, t: float) -> None:
        p = ease_out((t - 0.15) / 1.4)
        val = self.value * p
        text = f"{self.v.get('prefix', '')}{val:,.{self.decimals}f}{self.v.get('suffix', '')}"
        exit_p = ease_in_out((t - (self.duration - 0.35)) / 0.3)
        sp = text_sprite(text, self.num_size, self.pal["accent"], "heavy")
        sp = with_alpha(sp, min(1.0, t / 0.25) * (1 - exit_p))
        cy = self.cy
        canvas.alpha_composite(sp, (int((self.w - sp.width) / 2), int(cy - sp.height / 2)))
        # progress ring
        d = ImageDraw.Draw(canvas)
        r = self.ring_r
        box = (self.w / 2 - r, cy - r, self.w / 2 + r, cy + r)
        d.arc(box, -90, -90 + 360 * p, fill=hex_rgb(self.pal["accent2"]) + (int(200 * (1 - exit_p)),), width=max(4, self.unit // 90))
        self.draw_words(canvas, self.label, t)


class QuoteScene(Scene):
    def prepare(self) -> None:
        self.block = self.word_block(self.v.get("text", ""), 0.42, "serif", int(self.unit * 0.075), 6, delay=0.35)
        self.mark = text_sprite("“", int(self.unit * 0.3), self.pal["accent"], "serif", shadow=False)
        self.author = None
        if self.v.get("author"):
            self.author = text_sprite("- " + self.v["author"], int(self.unit * 0.042), self.pal["muted"], "semibold")

    def draw(self, canvas: Image.Image, t: float) -> None:
        p = ease_back(t / 0.5)
        m = self.mark.resize((max(1, int(self.mark.width * p)), max(1, int(self.mark.height * p)))) if p > 0.05 else None
        if m:
            canvas.alpha_composite(m, (int((self.w - m.width) / 2), int(self.block["top"] - m.height * 0.75)))
        self.draw_words(canvas, self.block, t)
        if self.author:
            a = ease_out((t - 1.0) / 0.5)
            canvas.alpha_composite(with_alpha(self.author, a), (int((self.w - self.author.width) / 2),
                                                                 int(self.block["top"] + self.block["height"] + self.unit * 0.04)))


class CTAScene(Scene):
    def prepare(self) -> None:
        from video_studio.planner import emphasis_words

        self.block = self.word_block(self.v.get("headline", ""), 0.36, "heavy", int(self.unit * 0.12), 3,
                                     emphasis_words(self.v.get("headline", "")))
        self.sub = text_sprite(self.v.get("sub") or "", int(self.unit * 0.045), self.pal["muted"], "semibold") if self.v.get("sub") else None

    def draw(self, canvas: Image.Image, t: float) -> None:
        self.draw_words(canvas, self.block, t, exit_at=self.duration + 1)
        y = self.block["top"] + self.block["height"] + self.unit * 0.06
        if self.sub:
            canvas.alpha_composite(with_alpha(self.sub, ease_out((t - 0.8) / 0.4)), (int((self.w - self.sub.width) / 2), int(y)))
            y += self.sub.height + self.unit * 0.02
        # pulsing button-like pill
        p = ease_back((t - 1.0) / 0.5)
        if p > 0:
            pulse = 1 + 0.04 * math.sin(t * 5)
            bw, bh = self.unit * 0.34 * p * pulse, self.unit * 0.1 * p * pulse
            d = ImageDraw.Draw(canvas)
            d.rounded_rectangle((self.w / 2 - bw / 2, y, self.w / 2 + bw / 2, y + bh), radius=int(bh / 2),
                                fill=hex_rgb(self.pal["accent"]) + (255,))
            arrow = None if bh < 8 else text_sprite("→", int(bh * 0.6), "#0B0B0B" if not is_light(self.look) else "#FFFFFF", "heavy", shadow=False)
            if p > 0.6 and arrow:
                canvas.alpha_composite(arrow, (int(self.w / 2 - arrow.width / 2), int(y + bh / 2 - arrow.height / 2)))


class MediaScene(Scene):
    """Ken Burns on a still image with a readable headline band."""

    def prepare(self) -> None:
        path = Path(self.v.get("asset", ""))
        if not path.exists():
            raise ToolError(f"Image not found: {path}", "Fix the [IMAGE: path] directive in the script.")
        img = Image.open(path).convert("RGB")
        scale = max(self.w / img.width, self.h / img.height) * 1.18
        self.img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
        self.block = self.word_block(self.v.get("headline", ""), 0.2, "display", int(self.unit * 0.085), 3)
        grad = np.linspace(0, 1, self.h, dtype=np.float32)
        alpha = (np.clip(0.75 - grad * 2.2, 0, 0.75) * 255).astype(np.uint8)
        self.shade = Image.fromarray(np.repeat(alpha[:, None], self.w, axis=1), "L")

    def render(self, idx: int) -> Image.Image:
        t = idx / self.fps
        p = t / max(self.duration, 0.1)
        zoom_in = self.v.get("motion", "kenburns_in") != "kenburns_out"
        z = 1.0 + 0.12 * (p if zoom_in else 1 - p)
        cw, ch = self.w / z * (self.img.width / (self.w * 1.18)), self.h / z * (self.img.height / (self.h * 1.18))
        cx = self.img.width / 2 + (p - 0.5) * self.img.width * 0.04
        cy = self.img.height / 2
        crop = self.img.crop((int(cx - cw / 2), int(cy - ch / 2), int(cx + cw / 2), int(cy + ch / 2))).resize((self.w, self.h), Image.BILINEAR)
        canvas = crop.convert("RGBA")
        canvas.alpha_composite(Image.merge("RGBA", (*Image.new("RGB", (self.w, self.h), (0, 0, 0)).split(), self.shade)))
        self.draw_words(canvas, self.block, t)
        return canvas.convert("RGB")


SCENES = {"kinetic": Kinetic, "title": Title2D, "title3d": Title2D, "object3d": Kinetic, "chart3d": ListScene,
          "list": ListScene, "stat": StatScene, "quote": QuoteScene, "cta": CTAScene, "media": MediaScene,
          "hyperframes": Kinetic}


def render_scene(visual: dict, dest: Path, w: int, h: int, fps: int, duration: float, look: str, motion: str,
                 quality: str, seed: int = 0) -> Path:
    cls = SCENES.get(visual["type"], Kinetic)
    scene = cls(visual, w, h, fps, duration, look, motion, seed)
    frames = max(1, int(round(duration * fps)))
    return pipe_frames((scene.render(i) for i in range(frames)), dest, w, h, fps, quality, frames)


def pipe_frames(frames, dest: Path, w: int, h: int, fps: int, quality: str, total: int,
                alpha: bool = False, grain: bool = False) -> Path:
    tmp = dest.with_suffix(".part" + dest.suffix)
    pix_in = "rgba" if alpha else "rgb24"
    codec = (["-c:v", "qtrle"] if alpha else ff.x264_args(quality, intermediate=True))
    vf = [] if alpha or not grain else ["-vf", "noise=alls=7:allf=t+u"]
    cmd = [ff.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", pix_in,
           "-s", f"{w}x{h}", "-r", str(fps), "-i", "-", *vf, *codec, "-r", str(fps), "-f", "mov" if alpha else "mp4", str(tmp)]
    proc = popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for frame in frames:
            proc.stdin.write(frame.tobytes())
        proc.stdin.close()
        err = proc.stderr.read().decode("utf-8", "replace")
        code = proc.wait()
    except (BrokenPipeError, OSError) as exc:
        err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        kill_tree(proc)
        raise ToolError(f"Frame encoding failed: {exc} {err[-400:]}")
    except BaseException:
        kill_tree(proc)
        raise
    finally:
        release(proc)
    if code != 0:
        raise ToolError(f"FFmpeg encode failed: {err[-600:]}")
    tmp.replace(dest)
    return dest


def overlay_png(kind: str, text: str, sub: str, w: int, h: int, look: str, dest: Path) -> Path:
    """Static RGBA graphics for the footage editor (title card, lower third, keyword pop)."""
    pal = LOOKS[look]
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    unit = min(w, h)
    d = ImageDraw.Draw(img)
    if kind == "lower_third":
        size = int(unit * 0.05)
        t1 = text_sprite(text, size, "#FFFFFF", "display")
        t2 = text_sprite(sub, int(size * 0.62), pal["accent"], "semibold") if sub else None
        bw = max(t1.width, t2.width if t2 else 0) + int(unit * 0.05)
        x, y = int(w * 0.06), int(h * (0.62 if h > w else 0.72))
        bh = t1.height + (t2.height if t2 else 0) - int(size * 0.3)
        d.rounded_rectangle((x, y, x + bw, y + bh), radius=int(size * 0.3), fill=(10, 10, 14, 205))
        d.rectangle((x, y, x + int(unit * 0.012), y + bh), fill=hex_rgb(pal["accent"]) + (255,))
        img.alpha_composite(t1, (x + int(unit * 0.025), y - int(size * 0.2)))
        if t2:
            img.alpha_composite(t2, (x + int(unit * 0.025), y + t1.height - int(size * 0.55)))
    elif kind == "keyword":
        size = int(unit * 0.12)
        sp = text_sprite(text.upper(), size, "#FFFFFF", "heavy", stroke=max(2, size // 18), stroke_color="#000000")
        img.alpha_composite(sp, ((w - sp.width) // 2, int(h * 0.22)))
    else:  # title card
        size = int(unit * 0.11)
        words = text.split()
        size = fit_size(words, "heavy", int(w * 0.86), 3, size, int(size * 0.5))
        y = int(h * 0.16)
        for line in wrap_words(words, "heavy", size, int(w * 0.86)):
            sp = text_sprite(" ".join(line).upper(), size, "#FFFFFF", "heavy", stroke=max(2, size // 16))
            img.alpha_composite(sp, ((w - sp.width) // 2, y))
            y += int(size * 1.05)
        if sub:
            band = text_sprite(sub, int(size * 0.42), "#0B0B0B", "semibold", shadow=False)
            bw, bh = band.width, band.height - band.info["pad"]
            x0 = (w - bw) // 2
            d.rounded_rectangle((x0, y + 10, x0 + bw, y + 10 + bh), radius=int(bh * 0.3), fill=hex_rgb(pal["accent"]) + (255,))
            img.alpha_composite(band, (x0, y + 10 - band.info["pad"] // 2))
    img.save(dest)
    return dest
