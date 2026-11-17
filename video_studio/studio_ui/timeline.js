/* Timeline editor: after the automatic edit, fix anything by hand, then make the film again.
   The edit lives in the project as a HyperFrames composition (timeline/index.html). This panel reads it with
   GET /api/timeline, saves every change with POST /api/timeline, and "Make the film again" starts
   POST /api/timeline/render (only changed pieces are cut again; speech is never heard again).
   Built only from the existing look: .card .tl .tl-head .tl-body .trk .lane .clip .btn .hint .viewer. */
(() => {
  if (typeof api !== "function" || typeof S === "undefined" || !document.querySelector("#resultWrap")) return;
  const css = document.createElement("style");
  css.textContent = `#tle{outline:none;scroll-margin-top:78px}
#tle .clip{animation:none;user-select:none;touch-action:none}
#tle .clip.sel{box-shadow:0 0 0 2px var(--coral);z-index:4}
#tle .clip .h{position:absolute;top:0;bottom:0;width:8px;cursor:ew-resize;background:rgba(255,255,255,.45)}
#tle .clip .h.l{left:0;border-radius:6px 0 0 6px}#tle .clip .h.r{right:0;border-radius:0 6px 6px 0}
#tle .clip.cap{background:linear-gradient(180deg,var(--gold-soft),#f3d9a0);color:#5b3d05}
#tle .clip.pic{background:linear-gradient(180deg,var(--coral-soft),#f9c6b8);color:var(--coral-ink)}
#tle .clip.mus{background:linear-gradient(180deg,var(--violet-soft),#d6cff6);color:var(--violet)}
#tle .clip.ro{opacity:.55;cursor:default}#tle .clip.off{opacity:.35}
#tle .tle-drop{position:absolute;top:-4px;bottom:-4px;width:3px;margin-left:-1px;background:var(--coral);border-radius:2px;pointer-events:none;z-index:5}
#tle .tle-ws{display:grid;grid-template-columns:minmax(220px,340px) minmax(0,1fr);gap:18px;align-items:start;padding:16px}
#tle .tle-frame{position:relative;margin:0 auto;background:#000;border-radius:14px;overflow:hidden;max-width:100%}
#tle .tle-frame video,#tle .tle-frame img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
#tle .tle-frame img{display:none}
#tle .tle-cap{position:absolute;left:8%;right:8%;text-align:center;font:800 clamp(12px,2.2vw,20px)/1.15 var(--display);color:#fff;text-transform:uppercase;text-shadow:0 0 6px #000,0 2px 3px #000;pointer-events:none}
#tle .tle-tag{position:absolute;left:10px;bottom:10px;font:650 10.5px var(--display);color:#fff;background:rgba(0,0,0,.6);padding:3px 8px;border-radius:999px}
#tle .tle-wait{position:absolute;inset:0;display:grid;place-items:center;color:var(--ivory);font:600 12.5px var(--display);padding:16px;text-align:center}
#tle .tle-insp h3{margin:0 0 8px;font:650 15px var(--display)}
#tle .tle-insp label{display:block;margin:0 0 10px;font-size:12.5px;color:var(--muted)}
#tle .tle-insp label input,#tle .tle-insp label textarea{margin-top:4px}
#tle .tle-insp textarea{min-height:64px}
#tle .tle-two{display:grid;grid-template-columns:1fr 1fr;gap:10px}
#tle .tle-keys{margin:8px 0 0;padding-left:18px;color:var(--muted);font-size:12.5px;line-height:1.6}
#tle input[type=range]{width:100%;accent-color:var(--teal)}
@media (max-width:900px){#tle .tle-ws{grid-template-columns:1fr}}`;
  document.head.append(css);

  const wrapEl = document.querySelector("#resultWrap");
  const KIND = {cut: "cuts", cap: "captions", pic: "pictures", br: "broll", ov: "overlays"};
  let T = null;   // the open timeline: {root, model, sel, undo, redo, ...}

  /* ---- the button on the finished-edit screen ---- */
  new MutationObserver(() => {
    const head = wrapEl.querySelector("#tl .tl-head");
    if (!head || head.querySelector("#tlFix")) return;
    const b = document.createElement("button");
    b.className = "btn primary"; b.id = "tlFix"; b.type = "button"; b.textContent = "Fix on the timeline";
    b.title = "Trim, move, delete or change any piece, then make the film again";
    b.onclick = () => open(S.root);
    head.append(b);
  }).observe(wrapEl, {childList: true});

  /* ---- model helpers (the same rules as video_studio/timeline.py layout) ---- */
  const r3 = x => Math.round(x * 1000) / 1000;
  function layout(m) {
    let t = 0; const by = {};
    for (const c of m.cuts) { c.start = r3(t); c.len = r3(c.out - c.in); by[c.id] = c; t += c.out - c.in; }
    m.total = r3(t);
    for (const k of m.captions) {
      const c = by[k.cut]; const a = c ? Math.max(k.at, c.in) : 0, b = c ? Math.min(k.at + k.dur, c.out) : 0;
      if (!c || b - a < .05) { k.start = null; k.len = 0; } else { k.start = r3(c.start + a - c.in); k.len = r3(b - a); }
    }
    for (const key of ["pictures", "broll"]) m[key] = m[key].filter(x => x.t0 < m.total - .3).map(x => {
      const len = x.t1 - x.t0, t0 = Math.max(0, Math.min(x.t0, m.total - len)); return {...x, t0: r3(t0), t1: r3(Math.min(m.total, t0 + len))}; });
    return m;
  }
  const slim = m => ({cuts: m.cuts.map(c => ({id: c.id, in: c.in, out: c.out})),
    captions: m.captions.map(k => ({id: k.id, cut: k.cut, at: k.at, dur: k.dur, text: k.text, orig: k.orig, odur: k.odur, words: k.words})),
    pictures: m.pictures.map(p => ({id: p.id, t0: p.t0, t1: p.t1, move: p.move})), broll: m.broll.map(b => ({id: b.id, t0: b.t0, t1: b.t1})),
    music: m.music ? {volume: m.music.volume, on: m.music.on} : null});
  const find = (kind, id) => kind === "mus" ? T.model.music : (T.model[KIND[kind]] || []).find(x => x.id === id);
  const cutAt = t => T.model.cuts.find(c => t >= c.start - 1e-6 && t < c.start + c.len) || null;

  /* ---- changes, undo, saving ---- */
  function change(fn) {
    T.undo.push(JSON.stringify(slim(T.model))); if (T.undo.length > 60) T.undo.shift(); T.redo = [];
    fn(T.model); layout(T.model); draw(); save();
  }
  function restore(from, to) {
    if (!from.length) return toast(from === T.undo ? "Nothing to undo." : "Nothing to redo.");
    to.push(JSON.stringify(slim(T.model)));
    const snap = JSON.parse(from.pop()), m = T.model, cat = T.catalogue;
    m.cuts = snap.cuts.map(c => ({...cat.cuts[c.id], ...c}));
    m.captions = snap.captions.map(k => ({...k}));
    m.pictures = snap.pictures.map(p => ({...cat.pictures[p.id], ...p}));
    m.broll = snap.broll.map(b => ({...cat.broll[b.id], ...b}));
    if (m.music && snap.music) Object.assign(m.music, snap.music);
    layout(m); draw(); save();
  }
  let saveTimer = 0, saveSeq = 0, saving = null;
  function save() {
    clearTimeout(saveTimer); status("Saving…");
    saveTimer = setTimeout(() => { saving = flush(); }, 450);
  }
  async function flush() {
    clearTimeout(saveTimer); const my = ++saveSeq, root = T.root;
    try {
      const r = await api("/api/timeline", {method: "POST", body: JSON.stringify({root, model: slim(T.model)})});
      if (!T || T.root !== root) return true;
      if (my === saveSeq && !T.dragging) { keepCatalogue(r); T.model = layout(r); T.file = r.file; draw(); status("Saved in your project"); }
      return true;
    } catch (e) { status("Not saved"); toast(e.message); return false; }
  }
  function keepCatalogue(m) {
    for (const c of m.cuts) T.catalogue.cuts[c.id] = {...c};
    for (const p of m.pictures) T.catalogue.pictures[p.id] = {...p};
    for (const b of m.broll) T.catalogue.broll[b.id] = {...b};
  }
  const status = text => { const s = T && T.el.querySelector("#tleState"); if (s) s.textContent = text; };

  /* ---- open / close ---- */
  async function open(root) {
    if (!root) return;
    const tl = wrapEl.querySelector("#tl");
    let model;
    try { model = await api("/api/timeline?root=" + encodeURIComponent(root)); }
    catch (e) { return toast(e.message); }
    close();
    const el = document.createElement("div");
    el.className = "card tl"; el.id = "tle"; el.tabIndex = -1;
    tl.after(el); tl.hidden = true;
    T = {root, el, model: layout(model), sel: null, undo: [], redo: [], t: 0, playing: false, file: model.file,
         catalogue: {cuts: {}, pictures: {}, broll: {}}};
    keepCatalogue(model);
    el.innerHTML = shell();
    wire();
    draw(); preview(model.preview);
    el.focus({preventScroll: true}); el.scrollIntoView({block: "start", behavior: "smooth"});
    document.addEventListener("keydown", keys, true);
  }
  function close() {
    if (!T) return;
    cancelAnimationFrame(T.raf); clearTimeout(T.waitTimer); document.removeEventListener("keydown", keys, true);
    const v = T.el.querySelector("#tleVid"); if (v) v.pause();
    T.el.remove(); const tl = wrapEl.querySelector("#tl"); if (tl) tl.hidden = false; T = null;
  }

  function shell() {
    const m = T.model, tall = m.height > m.width;
    return `<div class="tl-head"><b>Fix your edit</b><span class="hint" id="tleInfo"></span><span class="hint" id="tleState">Saved in your project</span><span class="spacer"></span>
        <button class="btn ghost" id="tleUndo" type="button" title="Undo (Ctrl+Z)">↶ Undo</button>
        <button class="btn primary" id="tleMake" type="button">Make the film again</button>
        <button class="btn ghost" id="tleClose" type="button">Close</button></div>
      <div class="tle-ws">
        <div><div class="viewer"><div class="tle-frame" id="tleFrame" style="aspect-ratio:${m.width}/${m.height};${tall ? "height:min(52vh,460px)" : "width:100%"}">
          <video id="tleVid" playsinline preload="auto"></video><img id="tlePic" alt=""><div class="tle-cap" id="tleCap" style="bottom:${(100 - m.caption_y * 100).toFixed(1)}%"></div>
          <span class="tle-tag" id="tleTag" hidden></span><div class="tle-wait" id="tleWait">Making a quick preview copy of your footage…</div></div>
          <span class="tc" id="tleTc">0:00.0</span></div>
          <div class="transport"><button class="btn primary" id="tlePlay" type="button" title="Play / pause (Space)">Play</button>
            <span class="hint">Preview of your pieces and captions. Music, effects and colour come in the film.</span></div></div>
        <div class="tle-insp" id="tleInsp"></div></div>
      <div class="tl-body" id="tleBody"></div>`;
  }

  /* ---- drawing ---- */
  function draw() {
    if (!T) return;
    const m = T.model, el = T.el, total = Math.max(m.total, .01), pct = t => (100 * t / total).toFixed(3) + "%";
    const step = total > 150 ? 30 : total > 70 ? 10 : total > 20 ? 5 : 1; let ticks = "";
    for (let t = 0; t <= total; t += step) ticks += `<i style="left:${pct(t)}">${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}</i>`;
    const sel = (k, id) => T.sel && T.sel.kind === k && T.sel.id === id ? " sel" : "";
    const box = (k, x, a, len, cls, label, tip, handles) => `<div class="clip ${cls}${sel(k, x.id)}" data-k="${k}" data-id="${esc(x.id)}" style="left:${pct(a)};width:calc(${pct(len)} - 2px)" title="${esc(tip)}">${handles ? '<i class="h l" data-h="l"></i>' : ""}<span style="overflow:hidden;text-overflow:ellipsis;padding:0 4px">${esc(label)}</span>${handles ? '<i class="h r" data-h="r"></i>' : ""}</div>`;
    const capOf = c => m.captions.filter(k => k.cut === c.id && k.start != null).map(k => k.text).join(" ");
    const lane = (name, kind, html, tall) => `<div class="trk"><span>${name}</span><div class="lane${tall ? " tall" : ""}" data-lane="${kind}">${html}</div></div>`;
    const groups = [...new Set(m.overlays.map(o => o.group))];
    el.querySelector("#tleBody").innerHTML =
      `<div class="trk"><span></span><div class="lane ruler" data-lane="ruler">${ticks}</div></div>`
      + lane("Video", "cut", m.cuts.map((c, i) => box("cut", c, c.start, c.len, "edit", `${i + 1} ${capOf(c)}`, `Piece ${i + 1} · ${tc(c.start)} · ${c.len.toFixed(1)} s · drag the edges to trim, drag the middle to move`, true)).join(""), true)
      + (m.broll.length || T.catalogue.broll && Object.keys(T.catalogue.broll).length ? lane("B-roll", "br", m.broll.map(b => box("br", b, b.t0, b.t1 - b.t0, "br", b.name.replace(/\.[^.]+$/, ""), "B-roll · " + (b.why || b.name), false)).join("")) : "")
      + (Object.keys(T.catalogue.pictures).length ? lane("Pictures", "pic", m.pictures.map(p => box("pic", p, p.t0, p.t1 - p.t0, "pic", p.name, "Your picture " + p.name + " · drag to move", false)).join("")) : "")
      + groups.map(g => lane(esc(g), "ov", m.overlays.filter(o => o.group === g).map(o => box("ov", o, o.t0, Math.max(.2, o.t1 - o.t0), "tx ro", o.text, g + " · placed by the automatic edit", false)).join(""))).join("")
      + lane("Captions", "cap", m.captions.filter(k => k.start != null).map(k => box("cap", k, k.start, k.len, "cap", k.text, "Caption · " + tc(k.start) + " · click to change the words", true)).join(""))
      + (m.music ? lane("Music", "mus", box("mus", {id: "music"}, 0, total, "mus" + (m.music.on ? "" : " off"), `${m.music.name} · ${Math.round(100 * m.music.volume / m.music.default)}%`, "Music · click to change the level", false)) : "")
      + `<div class="head" id="tleHead"></div>`;
    el.querySelector("#tleInfo").textContent = `${m.cuts.length} piece${m.cuts.length === 1 ? "" : "s"} · ${tc(m.total)}`;
    el.querySelector("#tleUndo").disabled = !T.undo.length;
    inspector(); head();
  }

  function head() {
    if (!T) return;
    const body = T.el.querySelector("#tleBody"), lane = body.querySelector('[data-lane="cut"]'), h = body.querySelector("#tleHead");
    if (!lane || !h) return;
    const lr = lane.getBoundingClientRect(), br = body.getBoundingClientRect();
    h.style.transform = `translateX(${lr.left - br.left + lr.width * Math.min(1, T.t / Math.max(.01, T.model.total))}px)`;
    T.el.querySelector("#tleTc").textContent = tc(T.t);
    const cap = T.model.captions.find(k => k.start != null && T.t >= k.start && T.t < k.start + k.len);
    T.el.querySelector("#tleCap").textContent = cap ? cap.text : "";
    const pic = T.model.pictures.find(p => T.t >= p.t0 && T.t < p.t1), img = T.el.querySelector("#tlePic");
    if (pic) { const src = media(pic.path); if (img.dataset.src !== src) { img.src = src; img.dataset.src = src; } img.style.display = "block"; } else img.style.display = "none";
    const br2 = T.model.broll.find(b => T.t >= b.t0 && T.t < b.t1), tag = T.el.querySelector("#tleTag");
    tag.hidden = !br2; if (br2) tag.textContent = "B-roll: " + br2.name;
  }

  function inspector() {
    const box = T.el.querySelector("#tleInsp"), m = T.model, s = T.sel, x = s && find(s.kind, s.id);
    const num = (id, label, v, step = .05) => `<label>${label}<input type="number" id="${id}" step="${step}" value="${(+v).toFixed(2)}"></label>`;
    if (!x) {
      box.innerHTML = `<h3>Click anything on the timeline to change it</h3>
        <ul class="tle-keys"><li>Drag the edges of a piece to trim it. Drag its middle to move it.</li>
        <li>Click a caption to change its words or timing.</li><li>Drag a picture to show it at another moment.</li>
        <li>Space plays and pauses. Delete removes what you picked. Ctrl+Z undoes.</li></ul>
        <p class="hint" style="margin-top:10px">Every change is saved in your project as a HyperFrames file:<br><span style="font:11.5px var(--mono)">${esc(T.file || "")}</span></p>`;
      return;
    }
    if (s.kind === "cut") {
      const i = m.cuts.indexOf(x);
      box.innerHTML = `<h3>Piece ${i + 1} of ${m.cuts.length}</h3>
        <p class="hint">Plays at ${tc(x.start)} for ${x.len.toFixed(2)} s. Taken from ${tc(x.in)} to ${tc(x.out)} in your recording.</p>
        <div class="tle-two">${num("tleIn", "Starts in your recording (s)", x.in)}${num("tleOut", "Ends in your recording (s)", x.out)}</div>
        <p class="hint">${x.max - x.min > x.out - x.in + .3 ? `You can take this piece from anywhere between ${tc(x.min)} and ${tc(x.max)} of your recording.` : "This piece can be made shorter, not longer."}</p>
        <div class="row"><button class="btn" id="tleEarlier" type="button" ${i ? "" : "disabled"}>◀ Play earlier</button><button class="btn" id="tleLater" type="button" ${i < m.cuts.length - 1 ? "" : "disabled"}>Play later ▶</button>
          <button class="btn stop" id="tleDel" type="button" ${m.cuts.length > 1 ? "" : "disabled"}>Delete piece</button></div>`;
      const set = () => change(() => { const a = +box.querySelector("#tleIn").value, b = +box.querySelector("#tleOut").value;
        x.in = r3(Math.max(x.min, Math.min(a, x.max - .25))); x.out = r3(Math.max(x.in + .25, Math.min(b, x.max))); });
      box.querySelector("#tleIn").onchange = set; box.querySelector("#tleOut").onchange = set;
      box.querySelector("#tleEarlier").onclick = () => move(x, -1); box.querySelector("#tleLater").onclick = () => move(x, 1);
    } else if (s.kind === "cap") {
      box.innerHTML = `<h3>Caption</h3><label>Words on screen<textarea id="tleText" maxlength="240">${esc(x.text)}</textarea></label>
        <div class="tle-two">${num("tleStart", "Shows at (s)", x.start)}${num("tleLen", "For (s)", x.dur)}</div>
        <div class="row"><button class="btn stop" id="tleDel" type="button">Delete caption</button></div>
        <p class="hint" style="margin-top:8px">The caption stays with its piece when you trim or move pieces.</p>`;
      box.querySelector("#tleText").onchange = e => { const v = e.target.value.trim(); if (!v) return remove(); change(() => { x.text = v; }); };
      box.querySelector("#tleStart").onchange = e => change(() => retime(x, +e.target.value));
      box.querySelector("#tleLen").onchange = e => change(() => { x.dur = r3(Math.max(.2, Math.min(15, +e.target.value))); });
    } else if (s.kind === "pic" || s.kind === "br") {
      box.innerHTML = `<h3>${s.kind === "pic" ? "Your picture" : "B-roll"}: ${esc(x.name)}</h3>${x.why ? `<p class="hint">${esc(x.why)}</p>` : ""}
        <div class="tle-two">${num("tleT0", "Shows at (s)", x.t0)}${num("tleLen", "For (s)", x.t1 - x.t0)}</div>
        <div class="row"><button class="btn stop" id="tleDel" type="button">Delete</button></div>`;
      const set = () => change(() => { const len = Math.max(.5, Math.min(8, +box.querySelector("#tleLen").value)); const t0 = Math.max(0, Math.min(+box.querySelector("#tleT0").value, m.total - len)); x.t0 = r3(t0); x.t1 = r3(t0 + len); });
      box.querySelector("#tleT0").onchange = set; box.querySelector("#tleLen").onchange = set;
    } else if (s.kind === "mus") {
      const pc = Math.round(100 * x.volume / x.default);
      box.innerHTML = `<h3>Music: ${esc(x.name)}</h3>
        <label>Level: <b id="tleVolN">${pc}%</b> of what Takebloom chose<input type="range" id="tleVol" min="0" max="250" step="5" value="${pc}"></label>
        <label style="display:flex;gap:8px;align-items:center"><input type="checkbox" id="tleOn" ${x.on ? "checked" : ""} style="width:auto;margin:0;accent-color:var(--teal)">Music in the film</label>
        <p class="hint">The voice still gets louder than the music by itself.</p>`;
      const vol = box.querySelector("#tleVol");
      vol.oninput = () => box.querySelector("#tleVolN").textContent = vol.value + "%";
      vol.onchange = () => change(() => { x.volume = r3(x.default * vol.value / 100); });
      box.querySelector("#tleOn").onchange = e => change(() => { x.on = e.target.checked; });
    } else {
      box.innerHTML = `<h3>${esc(x.group)}: ${esc(x.text)}</h3><p class="hint">Placed by the automatic edit at ${tc(x.t0)}. After your changes it is placed again to fit the new cut.</p>`;
    }
    const del = box.querySelector("#tleDel"); if (del) del.onclick = remove;
  }

  /* ---- edits ---- */
  function move(c, d) {
    change(m => { const i = m.cuts.indexOf(c), j = i + d; if (j < 0 || j >= m.cuts.length) return; m.cuts.splice(i, 1); m.cuts.splice(j, 0, c); });
    T.t = c.start; seek();
  }
  function retime(k, start) {
    const m = T.model, s = Math.max(0, Math.min(start, m.total - .05)), c = cutAt(s) || m.cuts[m.cuts.length - 1];
    k.cut = c.id; k.at = r3(c.in + s - c.start);
  }
  function remove() {
    const s = T.sel; if (!s) return;
    if (s.kind === "ov") return toast("This is placed by the automatic edit. It is placed again after your changes.");
    if (s.kind === "mus") return change(m => { m.music.on = false; });
    if (s.kind === "cut" && T.model.cuts.length < 2) return toast("Keep at least one piece of your video.");
    change(m => { const list = m[KIND[s.kind]]; const i = list.findIndex(x => x.id === s.id); if (i >= 0) list.splice(i, 1); });
    T.sel = null; draw();
  }

  /* ---- pointer: select, scrub, trim, move ---- */
  function wire() {
    const el = T.el, body = el.querySelector("#tleBody");
    el.querySelector("#tleClose").onclick = close;
    el.querySelector("#tleUndo").onclick = () => restore(T.undo, T.redo);
    el.querySelector("#tlePlay").onclick = toggle;
    el.querySelector("#tleMake").onclick = make;
    body.addEventListener("pointerdown", e => {
      const lane = body.querySelector('[data-lane="cut"]'), lr = lane.getBoundingClientRect(), total = T.model.total;
      const timeAt = x => total * Math.min(1, Math.max(0, (x - lr.left) / lr.width));
      const clip = e.target.closest(".clip[data-k]");
      if (!clip) { if (!e.target.closest(".lane")) return; T.t = timeAt(e.clientX); seek(); const mv = ev => { T.t = timeAt(ev.clientX); seek(); };
        body.setPointerCapture(e.pointerId); body.onpointermove = mv; body.onpointerup = () => { body.onpointermove = body.onpointerup = null; }; return; }
      const k = clip.dataset.k, x = find(k, clip.dataset.id), edge = e.target.dataset.h || "";
      T.sel = {kind: k, id: clip.dataset.id};
      if (k === "ov" || k === "mus" || !x) { if (x && k === "ov") { T.t = x.t0; seek(); } draw(); return; }
      const x0 = e.clientX, scale = total / lr.width, before = JSON.stringify(slim(T.model)), orig = JSON.parse(JSON.stringify(x));
      let moved = false, dropAt = -1;
      body.setPointerCapture(e.pointerId); T.dragging = true;
      body.onpointermove = ev => {
        const dt = (ev.clientX - x0) * scale;
        if (!moved && Math.abs(ev.clientX - x0) < 4) return;
        moved = true;
        if (k === "cut" && edge === "l") x.in = r3(Math.max(x.min, Math.min(orig.in + dt, x.out - .25)));
        else if (k === "cut" && edge === "r") x.out = r3(Math.max(x.in + .25, Math.min(orig.out + dt, x.max)));
        else if (k === "cut") {   // reorder: show where it will land
          const t = timeAt(ev.clientX), others = T.model.cuts.filter(c => c !== x);
          dropAt = others.filter(c => c.start + c.len / 2 < t).length;
          let mark = lane.querySelector(".tle-drop"); if (!mark) { mark = document.createElement("div"); mark.className = "tle-drop"; lane.append(mark); }
          const at = dropAt < others.length ? others[dropAt].start - (others[dropAt].start > x.start ? x.len : 0) : T.model.total;
          mark.style.left = (100 * Math.max(0, at) / total) + "%"; return;
        } else if (k === "cap" && edge === "r") x.dur = r3(Math.max(.2, Math.min(15, orig.dur + dt)));
        else if (k === "cap" && edge === "l") { const end = orig.at + orig.dur; x.at = r3(Math.min(end - .2, orig.at + dt)); x.dur = r3(end - x.at); }
        else if (k === "cap") { retime(x, orig.start + dt); }
        else { const len = orig.t1 - orig.t0, t0 = Math.max(0, Math.min(orig.t0 + dt, T.model.total - len)); x.t0 = r3(t0); x.t1 = r3(t0 + len); }
        layout(T.model); draw();
      };
      body.onpointerup = () => {
        body.onpointermove = body.onpointerup = null; T.dragging = false;
        if (!moved) { T.t = k === "cut" ? x.start : k === "cap" ? (x.start ?? T.t) : x.t0; seek(); draw(); return; }
        if (k === "cut" && !edge) {
          if (dropAt < 0) return draw();
          T.undo.push(before); T.redo = [];
          const m = T.model; m.cuts.splice(m.cuts.indexOf(x), 1); m.cuts.splice(dropAt, 0, x);
        } else { T.undo.push(before); T.redo = []; }
        layout(T.model); draw(); save();
        if (k === "cut") { T.t = x.start; seek(); }
      };
    });
  }

  function keys(e) {
    if (!T || !document.body.contains(T.el) || T.el.closest("[hidden]")) return;
    if (document.querySelector(".scrim")) return;
    const typing = /INPUT|TEXTAREA|SELECT/.test(e.target.tagName);
    const ctrl = e.ctrlKey || e.metaKey, k = e.key.toLowerCase();
    if (ctrl && k === "z" && !typing) { e.preventDefault(); e.stopPropagation(); return e.shiftKey ? restore(T.redo, T.undo) : restore(T.undo, T.redo); }
    if (ctrl && k === "y" && !typing) { e.preventDefault(); e.stopPropagation(); return restore(T.redo, T.undo); }
    if (typing || ctrl || e.altKey) return;
    if (e.key === " ") { e.preventDefault(); e.stopPropagation(); toggle(); }
    else if (e.key === "Delete" || e.key === "Backspace") { if (T.sel) { e.preventDefault(); e.stopPropagation(); remove(); } }
    else if (e.key === "Escape") { if (T.sel) { T.sel = null; draw(); e.stopPropagation(); } }
  }

  /* ---- preview: the pieces of your recording played in timeline order ---- */
  function preview(path) {
    if (!T) return;
    if (!path) {   // the quick preview copy is being made in the background
      T.waitTimer = setTimeout(async () => { if (!T) return; try { const r = await api("/api/timeline?root=" + encodeURIComponent(T.root)); preview(r.preview); } catch { preview(""); } }, 2000);
      return;
    }
    const v = T.el.querySelector("#tleVid");
    v.src = media(path); T.el.querySelector("#tleWait").hidden = true;
    v.addEventListener("loadedmetadata", seek, {once: true});
  }
  function seek() {
    if (!T) return;
    const v = T.el.querySelector("#tleVid"), m = T.model;
    T.t = Math.max(0, Math.min(T.t, m.total));
    const c = cutAt(T.t) || m.cuts[m.cuts.length - 1];
    if (c && v.readyState >= 1) { v.currentTime = c.in + Math.min(c.len, T.t - c.start); v.style.objectPosition = `${(c.focus ?? .5) * 100}% 50%`; T.cut = c.id; }
    head();
  }
  function toggle() {
    if (!T) return;
    const v = T.el.querySelector("#tleVid"), btn = T.el.querySelector("#tlePlay");
    if (T.playing) { T.playing = false; v.pause(); btn.textContent = "Play"; cancelAnimationFrame(T.raf); return; }
    if (!v.src) return toast("The preview copy is still being made. Try again in a moment.");
    if (T.t >= T.model.total - .05) T.t = 0;
    seek(); T.playing = true; btn.textContent = "Pause"; v.play().catch(() => {});
    const tick = () => {
      if (!T || !T.playing) return;
      const m = T.model, c = m.cuts.find(x => x.id === T.cut) || cutAt(T.t);
      if (c && !v.seeking) {
        if (v.currentTime >= c.out - .02 || v.ended) {   // end of this piece: jump to the next one
          const i = m.cuts.indexOf(c);
          if (i < 0 || i + 1 >= m.cuts.length) { T.t = m.total; toggle(); head(); return; }
          const n = m.cuts[i + 1]; T.cut = n.id; T.t = n.start; v.currentTime = n.in; v.style.objectPosition = `${(n.focus ?? .5) * 100}% 50%`;
        } else T.t = c.start + Math.max(0, v.currentTime - c.in);
      }
      head(); T.raf = requestAnimationFrame(tick);
    };
    T.raf = requestAnimationFrame(tick);
  }

  /* ---- make the film again ---- */
  async function make() {
    const btn = T.el.querySelector("#tleMake"); btn.disabled = true;
    if (T.playing) toggle();
    const ok = await flush();
    if (!ok || !T) { if (T) btn.disabled = false; return; }
    try {
      const root = T.root;
      const r = await api("/api/timeline/render", {method: "POST", body: JSON.stringify({root})});
      if (r.error) throw new Error(r.error);
      close(); S.job = r.job; S.since = 0;
      toast("Making the film again from your timeline. Pieces you did not change are reused.");
      openProject(root);
    } catch (e) { toast(e.message); if (T) btn.disabled = false; }
  }
})();
