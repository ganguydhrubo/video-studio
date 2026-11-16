"""HyperFrames engine (optional): HTML + GSAP motion graphics rendered by `npx hyperframes render`.

HyperFrames (Apache-2.0) is used when the user picks `motion_engine = hyperframes` or a script
block asks for [HYPERFRAMES]. Everything stays offline: GSAP is copied from the copy HyperFrames
itself vendors in the npm cache, never fetched from a CDN at render time.
"""

from __future__ import annotations

import html
import json
import os
import shutil
from pathlib import Path

from video_studio import detect
from video_studio.engines import ff
from video_studio.looks import LOOKS
from video_studio.util import ToolError, console, run

QUALITY = {"draft": "draft", "standard": "looks", "high": "delivery"}


def _vendored_gsap() -> Path | None:
    roots = [Path(os.environ.get("LOCALAPPDATA", "")) / "npm-cache" / "_npx", Path.home() / ".npm" / "_npx"]
    for root in roots:
        if root.exists():
            hits = sorted(root.glob("*/node_modules/hyperframes/dist/vendor/npm/gsap@*/dist/gsap.min.js"), reverse=True)
            if hits:
                return hits[0]
    return None


def composition(visual: dict, w: int, h: int, fps: int, duration: float, look: str) -> str:
    pal = LOOKS[look]
    bg = f"linear-gradient(160deg,{pal['bg'][0]},{pal['bg'][1]})"
    blobs = pal["blob"]
    headline = visual.get("headline") or visual.get("text") or ""
    sub = visual.get("sub") or ""
    words = headline.split()
    emphasis = {e.lower() for e in visual.get("emphasis", [])}
    spans = "".join(
        f'<span class="w{" em" if wd.lower().strip(".,!?") in emphasis else ""}">{html.escape(wd)}</span> ' for wd in words)
    portrait = h > w
    size = int((w if portrait else h) * (0.105 if len(headline) < 28 else 0.08))
    out = max(0.4, duration - 0.45)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width={w}, height={h}">
<script src="gsap.min.js"></script>
<style>
body{{margin:0;background:{pal['bg'][0]};font-family:"Bahnschrift","Segoe UI",system-ui,sans-serif}}
#root{{position:relative;width:{w}px;height:{h}px;overflow:hidden;background:{bg}}}
.clip{{position:absolute;inset:0}}
.blob{{position:absolute;border-radius:50%;filter:blur({int(w * 0.08)}px);opacity:.55}}
#b1{{width:{int(w * .9)}px;height:{int(w * .9)}px;left:-{int(w * .25)}px;top:-{int(w * .2)}px;background:{blobs[0]}}}
#b2{{width:{int(w * .7)}px;height:{int(w * .7)}px;right:-{int(w * .2)}px;bottom:-{int(w * .1)}px;background:{blobs[-1]}}}
.stage{{position:absolute;inset:0;display:flex;flex-direction:column;justify-content:center;align-items:flex-start;
  padding:0 {int(w * .09)}px;box-sizing:border-box;gap:{int(size * .35)}px}}
#bar{{display:block;width:{int(w * .16)}px;height:{max(6, int(size * .1))}px;background:{pal['accent']};border-radius:99px;transform-origin:left center}}
h1{{margin:0;font-size:{size}px;line-height:1.04;font-weight:800;color:{pal['ink']};letter-spacing:-.02em;max-width:{int(w * .82)}px}}
.w{{display:inline-block}}
.em{{color:{pal['accent']}}}
p{{margin:0;font-size:{int(size * .38)}px;color:{pal['muted']};max-width:{int(w * .8)}px;font-weight:500}}
</style></head>
<body>
<div id="root" data-composition-id="main" data-start="0" data-width="{w}" data-height="{h}" data-duration="{duration:.3f}" data-fps="{fps}">
  <section class="clip" data-start="0" data-duration="{duration:.3f}" data-track-index="0">
    <div class="blob" id="b1"></div><div class="blob" id="b2"></div>
  </section>
  <section class="clip" data-start="0" data-duration="{duration:.3f}" data-track-index="1">
    <div class="stage" id="stage"><span id="bar"></span><h1 id="hl">{spans}</h1>{f'<p id="sub">{html.escape(sub)}</p>' if sub else ''}</div>
  </section>
</div>
<script>
const tl = gsap.timeline({{ paused: true }});
tl.fromTo("#b1", {{x: 0, y: 0, scale: 1}}, {{x: {int(w * .12)}, y: {int(h * .05)}, scale: 1.15, duration: {duration:.3f}, ease: "sine.inOut"}}, 0);
tl.fromTo("#b2", {{x: 0, y: 0}}, {{x: -{int(w * .1)}, y: -{int(h * .06)}, duration: {duration:.3f}, ease: "sine.inOut"}}, 0);
tl.from("#bar", {{scaleX: 0, duration: 0.45, ease: "power3.out"}}, 0.05);
tl.from("#hl .w", {{y: {int(size * .7)}, opacity: 0, rotate: 4, duration: 0.55, ease: "back.out(1.6)", stagger: 0.07}}, 0.12);
tl.from("#hl .em", {{scale: 1.35, duration: 0.4, ease: "power2.out"}}, 0.45);
{'tl.from("#sub", {y: 30, opacity: 0, duration: 0.5, ease: "power2.out"}, 0.55);' if sub else ''}
tl.to("#stage", {{opacity: 0, y: -{int(size * .3)}, duration: 0.35, ease: "power2.in"}}, {out:.3f});
window.__timelines["main"] = tl;
</script>
</body></html>
"""


def render_scene(visual: dict, dest: Path, w: int, h: int, fps: int, duration: float, look: str, work_dir: Path,
                 quality: str = "standard") -> Path:
    npx = detect.npx_path()
    gsap = _vendored_gsap()
    if not npx:
        raise ToolError("HyperFrames needs Node.js.", "video-studio install node")
    if not gsap:
        raise ToolError("HyperFrames is not in the npm cache yet.", "video-studio install hyperframes")
    work_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(gsap, work_dir / "gsap.min.js")
    (work_dir / "index.html").write_text(composition(visual, w, h, fps, duration, look), encoding="utf-8")
    (work_dir / "spec.json").write_text(json.dumps(visual), encoding="utf-8")
    raw = work_dir / "render.mp4"
    console.info(f"    HyperFrames rendering {duration:.1f}s ({QUALITY.get(quality, 'looks')})")
    res = run([str(npx), "--no-install", f"hyperframes@{detect.HYPERFRAMES_VERSION}", "render", str(work_dir),
               "--output", str(raw), "--quality", QUALITY.get(quality, "looks"), "--fps", str(fps), "--workers", "2", "--quiet"],
              check=False, timeout=max(300, duration * 90), cwd=work_dir, low_priority=True)
    if res.returncode != 0 or not raw.exists() or raw.stat().st_size < 1000:
        tail = ((res.stderr or "") + (res.stdout or "")).strip().splitlines()[-4:]
        raise ToolError("HyperFrames render failed: " + " | ".join(tail)[:400], "npx hyperframes doctor")
    # normalise to the pipeline's intermediate format (exact size, fps, length)
    ff.ff(["-i", str(raw), "-vf", f"scale={w}:{h},fps={fps},tpad=stop_mode=clone:stop_duration=1,format=yuv420p",
           "-t", f"{duration:.3f}", "-an", *ff.x264_args(quality, intermediate=True), str(dest)])
    return dest
