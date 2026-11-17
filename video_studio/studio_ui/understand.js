/* "Takebloom will…": what the prompt sets, shown live while the person types (POST /api/brief/understand, local and
   instant), plus a plain list of what cannot be done, so nothing asked for is silently skipped. A format named in
   the prompt moves the format buttons too, unless the person has picked a format by hand.
   Choices learned from earlier edits (video_studio/signals.py) are listed too, with the real count behind each,
   for the choices the prompt and Fine-tune leave open. */
(() => {
  const brief = document.querySelector("#brief");
  if (!brief || typeof api !== "function" || typeof S === "undefined") return;
  const style = document.createElement("style");
  style.textContent = `.will{margin-top:10px;border-radius:12px;padding:10px 12px;background:var(--panel-2,rgba(19,41,43,.05));font-size:13px;line-height:1.45}
.will h4{margin:0 0 4px;font-size:12.5px;letter-spacing:.02em;color:var(--muted,#5b6b6d);font-weight:600}
.will ul{margin:0 0 6px;padding-left:18px}.will li{margin:2px 0}
.will .no{color:var(--warn-ink,#8a4b00)}.will .no li::marker{content:"! "}.will .q{color:var(--muted,#5b6b6d)}`;
  document.head.append(style);
  const box = document.createElement("div");
  box.className = "will"; box.id = "will"; box.setAttribute("aria-live", "polite");
  (document.querySelector("#chips") || brief).after(box);
  const esc = s => String(s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"})[c]);
  const EMPTY_TEXT = "<h4>Your prompt</h4>Write anything you want: style, colours, pace, motion, music, captions, length, format. " +
    "Or leave it empty and Takebloom decides everything by itself.";
  let timer = 0, seq = 0;

  function syncAspect(a) {
    if (!a || S.aspectChosen || S.aspect === a) return;
    const b = document.querySelector(`#aspect [data-v="${a}"]`);
    if (b) { S.aspectAuto = true; b.click(); S.aspectAuto = false; }
  }
  const val = id => { const el = document.querySelector("#" + id); return el ? el.value : ""; };
  const form = () => ({pacing: val("pacing"), captions: val("captions"), music: val("music"), fx: val("fx"), grade: val("grade"),
    transition: val("transition"), template: val("template"), like: val("like")});
  const hasSong = () => (S.files || []).some(f => /\.(mp3|wav|m4a|aac|flac|ogg)$/i.test(f.name || ""));
  async function check() {
    const text = brief.value.trim(), my = ++seq;
    try {
      const r = await api("/api/brief/understand", {method: "POST", body: JSON.stringify({brief: text, options: form(), has_song: hasSong()})});
      if (my !== seq) return;
      const learned = (r.learned || []).map(x => x.line);
      if (!text && !learned.length) { box.innerHTML = EMPTY_TEXT; return; }
      const will = [...(text ? r.understood || [] : []), ...learned], no = r.cannot || [], qs = r.questions || [];
      box.innerHTML = (will.length ? `<h4>Takebloom will</h4><ul>${will.map(x => `<li>${esc(x)}</li>`).join("")}</ul>`
                                   : `<h4>Takebloom will</h4><p style="margin:0 0 6px">Decide the look, pace, captions and music by itself. Your words still guide the story.</p>`)
        + (no.length ? `<h4>Cannot do yet</h4><ul class="no">${no.map(x => `<li>${esc(x)}</li>`).join("")}</ul>` : "")
        + qs.map(q => `<p class="q" style="margin:0">${esc(q)}</p>`).join("")
        + `<p class="q" style="margin:4px 0 0">Anything you set in Fine-tune wins over the prompt.</p>`;
      syncAspect((r.options || {}).aspect);
    } catch { if (my === seq) box.innerHTML = EMPTY_TEXT; }
  }
  brief.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(check, 300); });
  document.querySelectorAll("#chips").forEach(c => c.addEventListener("click", () => { clearTimeout(timer); timer = setTimeout(check, 50); }));
  document.querySelectorAll("#advBox, #lookRow").forEach(c => c.addEventListener("change", () => { clearTimeout(timer); timer = setTimeout(check, 50); }));
  addEventListener("hashchange", () => { if (/^#\/(create)?$/.test(location.hash || "#/")) { clearTimeout(timer); timer = setTimeout(check, 50); } });
  const files = document.querySelector("#files");   // a dropped song is the music: learned music lines then go
  if (files && window.MutationObserver) new MutationObserver(() => { clearTimeout(timer); timer = setTimeout(check, 200); }).observe(files, {childList: true});
  box.innerHTML = EMPTY_TEXT;
  check();
})();
