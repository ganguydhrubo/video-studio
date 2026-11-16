/* Functional brief assistance. Fable can replace this view and retain the API/event. */
(() => {
  const input = document.querySelector('#brief');
  if (!input) return;
  const panel = document.createElement('section');
  panel.className = 'hint';
  panel.setAttribute('aria-live', 'polite');
  panel.setAttribute('aria-label', 'Editing instructions assistant');
  input.after(panel);
  let revision = 0, timer;
  function line(text) {
    const p = document.createElement('p');
    p.textContent = text;
    panel.append(p);
  }
  async function check(version) {
    const brief = input.value.trim();
    if (version !== revision || !brief) return;
    const provider = document.querySelector('#ai')?.value || '';
    panel.replaceChildren();
    line(provider === 'none' ? 'Checking your instructions on this computer…' : 'Your AI editor is reading your instructions…');
    try {
      const result = await api('/api/brief/interpret', {method: 'POST', body: JSON.stringify({brief, provider})});
      if (version !== revision) return;
      if (result.checking) {
        line(result.target_seconds == null ? (result.preserve_content ? 'Full message kept. No shorter video length requested.' : 'A shorter edit was requested; no exact length specified.') : `Video length requested: ${result.target_seconds} seconds.`);
        timer = setTimeout(() => check(version), 1600);
        return;
      }
      panel.replaceChildren();
      line(result.summary || 'Your editing instructions');
      line(result.target_seconds == null ? (result.preserve_content ? 'Keep the full message; no shorter length requested.' : 'A shorter edit was requested; no exact length specified.') : `Requested video length: ${result.target_seconds} seconds.`);
      if (result.hook_seconds) line(`Opening treatment: ${result.hook_seconds} seconds. This does not shorten the whole video.`);
      if (result.normalized_brief !== brief) {
        line(result.normalized_brief);
        const apply = document.createElement('button');
        apply.type = 'button'; apply.className = 'btn ghost'; apply.textContent = 'Use corrected wording';
        apply.onclick = () => { input.value = result.normalized_brief; input.dispatchEvent(new Event('input', {bubbles:true})); };
        panel.append(apply);
      }
      for (const suggestion of result.suggestions || []) {
        const button = document.createElement('button');
        button.type = 'button'; button.className = 'chip'; button.textContent = suggestion;
        button.onclick = () => { input.value = input.value.trim() + '\n' + suggestion; input.dispatchEvent(new Event('input', {bubbles:true})); };
        panel.append(button);
      }
      for (const question of result.questions || []) line(question);
      for (const warning of result.warnings || []) line(warning);
      line(result.source.startsWith('ai:') ? `Assisted by ${result.source.slice(3)}. Suggestions are optional.` : 'Local interpretation. AI assistance is off or unavailable.');
      document.dispatchEvent(new CustomEvent('takebloom:brief-intent', {detail:result}));
    } catch (error) {
      if (version !== revision) return;
      panel.replaceChildren(); line('Could not reach the instructions assistant. Your text is kept. ' + error.message);
    }
  }
  function changed() {
    clearTimeout(timer); revision++;
    if (!input.value.trim()) { panel.replaceChildren(); return; }
    timer = setTimeout(() => check(revision), 1200);
  }
  input.addEventListener('input', changed);
  document.querySelector('#ai')?.addEventListener('change', changed);
  // Existing suggestion chips set the textarea programmatically.
  document.addEventListener('click', event => {
    if (event.target.closest('#chips')) changed();
  });
})();
