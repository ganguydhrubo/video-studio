/* Autopilot: drop a video and the edit starts by itself once the copy on this PC is finished.
   The server does the rest (one automatic retry, covers, share text): see video_studio/autopilot.py.
   Typing instructions or changing any option pauses the automatic start, so it never starts while you are still choosing. */
(() => {
  const go = document.querySelector("#go");
  if (!go || typeof S === "undefined" || typeof create !== "function") return;
  const card = go.closest(".card"), cta = go.closest(".cta");
  const KEY = "tb.autoStart", WAIT = 5;
  const A = { timer: 0, left: 0, sig: "", paused: false, started: new Set() };
  const read = () => { try { return localStorage.getItem(KEY) !== "off"; } catch { return true; } };
  const save = on => { try { localStorage.setItem(KEY, on ? "on" : "off"); } catch { /* private window: still works, just not remembered */ } };

  const style = document.createElement("style");
  style.textContent = `.auto-row{display:flex;flex-wrap:wrap;align-items:center;gap:8px 14px;margin-top:10px}
.auto-row label{display:inline-flex;align-items:center;gap:7px;font-size:13px;cursor:pointer}
.auto-row label input{width:16px;height:16px;accent-color:var(--coral,#ee6a4d)}
.auto-note{font-size:13px;color:var(--muted,#5b6b6d)}.auto-note b{color:var(--ink,#13292b)}
.auto-note button{margin-left:6px;padding:3px 10px;font-size:12.5px}`;
  document.head.append(style);
  const row = document.createElement("div");
  row.className = "auto-row";
  row.innerHTML = `<label><input type="checkbox" id="autoStart"> Start by itself when my video is ready</label>
    <span class="auto-note" id="autoNote" role="status" aria-live="polite"></span>`;
  cta.after(row);
  const box = row.querySelector("#autoStart"), note = row.querySelector("#autoNote");
  box.checked = read();

  const SONG = /\.(mp3|wav|m4a|aac|flac|ogg)$/i;
  // something to show is needed (a clip or pictures); a song alone waits for them
  const ready = () => { const r = S.files.filter(f => f.path && !f.err); return r.some(f => !SONG.test(f.name)) ? r : []; };
  const copying = () => S.files.some(f => !f.path && !f.err);
  const onCreate = () => { const v = document.querySelector("#v-create"); return !v || !v.hidden; };
  const sigOf = list => list.map(f => f.path).sort().join("|");
  let lastNote = "";
  function say(html) { if (html !== lastNote) { lastNote = html; note.innerHTML = html; } }
  function stop() { clearInterval(A.timer); A.timer = 0; A.sig = ""; }

  function arm() {
    if (!S.files.length) { A.paused = false; A.started.clear(); }
    row.hidden = S.mode !== "edit";
    if (!box.checked || S.mode !== "edit" || !onCreate()) { stop(); return say(""); }
    const list = ready(), sig = sigOf(list);
    if (!list.length) { stop(); return say(copying() ? "It starts by itself as soon as the copy finishes." : "Drop a video and it starts by itself."); }
    if (copying()) { stop(); return say("It starts by itself as soon as every copy finishes."); }
    if (A.started.has(sig)) { stop(); return say("This video is already being made. Add another video to start a new one."); }
    if (A.paused) { stop(); return say("Paused while you describe it. Press <b>Create my draft</b> (or Ctrl + Enter) when you are ready."); }
    if (A.timer && A.sig === sig) return;
    stop(); A.sig = sig; A.left = WAIT; tick(); A.timer = setInterval(tick, 1000);
  }
  function tick() {
    if (A.left <= 0) {
      const sig = A.sig; stop();
      if (!box.checked || S.mode !== "edit" || !onCreate() || copying() || sigOf(ready()) !== sig || go.disabled) return arm();
      A.started.add(sig);
      say("Starting…");
      Promise.resolve(create()).then(() => {
        if (location.hash.startsWith("#/job/")) toast("Started by itself. Covers and post text are made at the end, nothing else to do.");
        else { A.started.delete(sig); A.paused = true; arm(); }   // the form shows why it could not start
      });
      return;
    }
    // the sentence is written once (one screen-reader announcement); only the number changes each second
    say(`<b>Starting by itself in <span id="autoSec"></span> s.</b> No need to fill anything in. Want to describe it first? Click in the prompt box. <button type="button" class="btn" id="autoWait">Wait</button>`);
    const sec = note.querySelector("#autoSec");
    if (sec) { sec.textContent = A.left; sec.setAttribute("aria-hidden", "true"); }
    const wait = note.querySelector("#autoWait");
    if (wait) wait.onclick = () => { A.paused = true; stop(); arm(); };
    A.left--;
  }

  // Any change to the instructions or options means the person is choosing: pause, never start under them.
  const choosing = e => {
    const t = e.target;
    if (!t || !t.closest || t.closest("#drop, #files, .local-path-option, .auto-row, #go")) return;
    if (e.type === "click" && !t.closest("button, select, input, summary, .chip")) return;
    if (!A.paused && (A.timer || ready().length)) { A.paused = true; stop(); arm(); }
  };
  // clicking into the prompt (or any text box) during the countdown means "let me describe it first"
  card.addEventListener("focusin", e => {
    if (A.timer && e.target.matches && e.target.matches("textarea, input[type=text], input[type=number]")) { A.paused = true; stop(); arm(); }
  }, true);
  card.addEventListener("input", choosing, true);
  card.addEventListener("click", choosing, true);
  box.addEventListener("change", () => { save(box.checked); A.paused = false; if (!box.checked) { stop(); say(""); } arm(); });
  document.querySelectorAll(".mode").forEach(b => b.addEventListener("click", () => setTimeout(arm, 0)));
  addEventListener("hashchange", () => setTimeout(arm, 60));

  window.autoArm = arm;   // called from renderFiles() whenever a copy starts, finishes or a clip is removed
  window.autoStarted = files => { A.started.add([...(files || [])].sort().join("|")); stop(); arm(); };
  arm();
})();
