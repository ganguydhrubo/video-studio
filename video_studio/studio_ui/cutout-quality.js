/* 1.9.0 best-quality cut-out: shows the one-time download (size, licence) and a time estimate measured on this PC.
   Uses only the page's own api(), S and IMG_RE. The option itself is the "Edge quality" select in the cut-out options. */
(() => {
  const wrap = document.querySelector("#qCutQualityWrap"), select = document.querySelector("#qCutQuality");
  const dl = document.querySelector("#qCutDlWrap"), hint = document.querySelector("#qCutHint");
  if (!wrap || !select || !dl || !hint || typeof api !== "function") return;
  let lastKey = "", busy = false;
  function video() {
    const f = (S.files || []).find(x => x.path && !IMG_RE.test(x.name || ""));
    return f ? f.path : "";
  }
  async function sync(force) {
    const on = !wrap.hidden && select.value === "best";
    if (!on) { dl.hidden = hint.hidden = true; lastKey = ""; return; }
    const file = video(), key = "best|" + file;
    if ((key === lastKey && !force) || busy) return;
    busy = true;
    try {
      const r = await api("/api/cutout/quality?file=" + encodeURIComponent(file));
      if (wrap.hidden || select.value !== "best") return;
      lastKey = key;
      const lines = [];
      if (!r.model_present) lines.push(r.download || "Best quality needs a one-time model download.");
      lines.push(file ? (r.estimate_text || "") : "Add the video to see how long it will take on this PC.");
      const check = r.check || {}, known = r.estimate && r.estimate.known;
      lines.push(...(check.messages || []).filter(m => (/^Expected time/.test(m) ? file && !known : check.level !== "ok")
        && !lines.includes(m) && !/download/.test(m)));
      dl.hidden = !!r.model_present;
      hint.textContent = lines.filter(Boolean).join(" ");
      hint.hidden = !hint.textContent;
    } catch {
      hint.textContent = "Best quality is much slower than Fast on a laptop processor.";
      hint.hidden = false; dl.hidden = false;
    } finally { busy = false; }
  }
  select.addEventListener("change", () => sync(true));
  new MutationObserver(() => sync(true)).observe(wrap, { attributes: true, attributeFilter: ["hidden"] });
  setInterval(() => { if (!wrap.hidden && select.value === "best") sync(false); }, 2000);
  sync(true);
})();
