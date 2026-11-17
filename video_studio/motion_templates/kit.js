/* Takebloom motion kit: shared helpers for every motion template (MIT, part of Takebloom).
   window.TB holds the template's values (text, colours, sizes), written by video_studio/motion.py.
   Everything is a function of timeline time: seeded random only, no clocks, no network. */
(function () {
  window.__timelines = window.__timelines || {};   // set by HyperFrames on the main page; a piece inside a batch frame makes its own
  const TB = window.TB || {};
  const root = document.documentElement;
  Object.entries(TB.colors || {}).forEach(([k, v]) => root.style.setProperty("--" + k, v));
  Object.entries(TB.sizes || {}).forEach(([k, v]) => root.style.setProperty("--" + k, v + "px"));

  // small deterministic random generator (LCG): the same seed draws the same confetti every render
  TB.rand = function (seed) {
    let s = (seed >>> 0) || 1;
    return () => { s = (Math.imul(s, 1664525) + 1013904223) >>> 0; return s / 4294967296; };
  };
  TB.clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  TB.ease = {
    outCubic: (x) => 1 - Math.pow(1 - TB.clamp(x, 0, 1), 3),
    inCubic: (x) => Math.pow(TB.clamp(x, 0, 1), 3),
    inOut: (x) => { x = TB.clamp(x, 0, 1); return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2; },
    outBack: (x) => { x = TB.clamp(x, 0, 1); const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2); },
  };

  // put the text in place: data-tb="key" fills from TB[key]; data-words splits it into .w spans for kinetic type
  TB.fill = function () {
    document.querySelectorAll("[data-tb]").forEach((el) => {
      const text = String(TB[el.dataset.tb] || "");
      el.textContent = "";
      if (!text) { el.classList.add("empty"); return; }
      if (el.hasAttribute("data-letters")) {
        Array.from(text).forEach((ch) => {
          const s = document.createElement("span");
          s.className = "l";
          s.textContent = ch === " " ? " " : ch;
          el.append(s);
        });
      } else if (el.hasAttribute("data-words")) {
        text.split(/\s+/).filter(Boolean).forEach((w, i) => {
          if (i) el.append(document.createTextNode(" "));
          const s = document.createElement("span");
          s.className = "w";
          s.textContent = w;
          el.append(s);
        });
      } else {
        el.textContent = text;
      }
    });
  };

  // shrink a line until it fits (after the fonts have loaded); registered as a build step so capture waits for it
  TB.fit = function (el, maxW, maxH) {
    if (!el) return;
    let px = parseFloat(getComputedStyle(el).fontSize) || 40;
    for (let i = 0; i < 40 && (el.scrollWidth > maxW || (maxH && el.scrollHeight > maxH)) && px > 14; i++) {
      px *= 0.94;
      el.style.fontSize = px + "px";
    }
  };
  TB.whenReady = function (fn) {
    window.__hf = window.__hf || {};
    window.__hf.buildReady = window.__hf.buildReady || {};
    const loads = (TB.fonts || []).map((f) => document.fonts.load(f).catch(() => null));
    const p = Promise.all(loads).then(() => (document.fonts ? document.fonts.ready : null)).then(fn).catch(() => null);
    window.__hf.buildReady.tb = p;
    return p;
  };
  window.TB = TB;
})();
