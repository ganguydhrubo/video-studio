/* Learned from your edits (System page): real counts from the signal file (GET /api/learned), what Takebloom will use
   when a prompt says nothing about a choice, the keep/cut model trained on your reviews, and "Forget what you learned".
   Also tells the app when a finished film is shared from the Share tab (Windows share, a site's page, Show in folder),
   because sharing a film is the clearest sign it was kept. See video_studio/signals.py. */
(() => {
  if (typeof api !== "function" || typeof S === "undefined") return;
  const taste = document.querySelector("#taste");
  const card = taste && taste.closest(".card");
  const esc = s => String(s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"})[c]);
  let box = null;

  function mount() {
    if (box || !card) return;
    box = document.createElement("div");
    box.id = "learnedEdits";
    box.innerHTML = `<h3>Learned from your edits</h3><div id="learnedBody" class="hint"><div class="skel" style="width:50%"></div></div>
      <div class="cta" style="margin-top:10px"><button class="btn" id="learnedForget">Forget what you learned</button><span class="hint" id="learnedMsg" role="status"></span></div>`;
    card.append(box);
    box.querySelector("#learnedForget").onclick = forget;
  }

  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
  async function load() {
    mount();
    if (!box) return;
    const body = box.querySelector("#learnedBody");
    let d;
    try { d = await api("/api/learned"); } catch (e) { body.textContent = "Could not load what was learned: " + e.message; return; }
    if (!d.events) {
      body.innerHTML = "Nothing yet. Every edit teaches Takebloom: what you ask for, refines, the lines you keep or cut, downloads, shares and ratings.";
      return;
    }
    const counts = [[d.edits, "edit", "edits"], [d.choices, "choice you made", "choices you made"], [d.refines, "refine", "refines"],
      [d.reviews, "transcript review", "transcript reviews"], [d.downloads, "download", "downloads"], [d.shares, "share", "shares"],
      [d.covers, "cover save", "cover saves"], [d.ratings, "rating", "ratings"], [d.stopped, "stopped edit", "stopped edits"]]
      .filter(([n]) => n).map(([n, a, b]) => `<span class="tag">${esc(plural(n, a, b))}</span>`).join("");
    const prefs = (d.preferences || []).map(p => `<span class="tag" title="${esc(p.reason)}">${esc(p.label)}
        <span class="tbar" style="width:70px"><i style="width:${Math.round(p.mean * 100)}%"></i></span> ${esc(p.reason)}${p.confident ? " · <b>used when your prompt is silent</b>" : " · still learning"}</span>`).join("");
    const k = d.keep_model || {};
    const keep = k.trained
      ? `Keep or cut advice: learned from ${esc(plural(k.reviewed, "reviewed edit", "reviewed edits"))} (${k.words} words). ` +
        (k.enabled ? "It is better than the built-in rules on edits it was not trained on, so its advice is shown." : "It is not better than the built-in rules yet, so the rules still decide.") +
        " Advice only: your ticks always decide."
      : `Keep or cut advice: review the lines of ${k.needs} edits on the Transcript tab and Takebloom learns what you keep and cut` +
        (d.reviews ? ` (${d.reviews} so far).` : ".");
    body.innerHTML = `<div class="tags" style="margin-top:6px">${counts}</div>
      ${prefs ? `<div class="tags" style="margin-top:8px">${prefs}</div>` : ""}
      <p style="margin:8px 0 0">${keep}</p>
      <p style="margin:6px 0 0">Kept only on this computer, in ${esc(d.file)}. What you write or pick for an edit always wins.</p>`;
  }

  async function forget() {
    const ok = typeof ask === "function"
      ? await ask("Forget what Takebloom learned?", "This clears what it learned from your edits and ratings, and the keep or cut advice trained on your reviews. Your films and projects are not touched.", "Forget")
      : confirm("Forget what Takebloom learned? Your films are not touched.");
    if (!ok) return;
    const msg = box.querySelector("#learnedMsg");
    try {
      const r = await api("/api/learned/forget", {method: "POST", body: "{}"});
      msg.textContent = `Forgotten: ${r.signals} signals.`;
      if (typeof loadTaste === "function") loadTaste();
      load();
    } catch (e) { msg.textContent = e.message; }
  }

  const onSystem = () => (location.hash || "").startsWith("#/system");
  addEventListener("hashchange", () => { if (onSystem()) load(); });
  if (onSystem()) load();

  // sharing a finished film: the Windows share panel, a site's upload page, or Show in folder (to drag it in)
  document.addEventListener("click", e => {
    const t = e.target && e.target.closest && e.target.closest("#shareBox [data-open], #shareBox #shNative, #shareBox #shReveal");
    if (!t || !S.root) return;
    const via = t.dataset.open ? (t.closest(".site")?.dataset.site || "site") : t.id === "shNative" ? "windows" : "folder";
    api("/api/signal", {method: "POST", body: JSON.stringify({root: S.root, kind: "share", via})}).catch(() => {});
  }, true);
})();
