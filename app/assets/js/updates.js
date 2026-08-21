/* updates.js — version-bar click -> update popup.
   Shows the running version + channel. Offers two update targets:
     • update on the SAME channel (only if a newer commit exists)
     • switch to the OTHER channel (always allowed, rebuilds to it)
   You can never be offered to "switch to" the channel you are already on. */

function initUpdates(){
  const ver = document.getElementById('version-info');
  if(ver) ver.style.cursor = 'pointer';
  const overlay = document.getElementById('update-overlay');
  const close = document.getElementById('up-btn-close');
  const check = document.getElementById('up-check');
  const applyBox = document.getElementById('up-apply-box'); // holds the target buttons
  const prog = document.getElementById('up-progress');
  const bar = document.getElementById('up-bar-fill');
  const status = document.getElementById('up-status');
  const msg = document.getElementById('up-msg');
  const notes = document.getElementById('up-notes');
  const curEl = document.getElementById('up-current');
  const latEl = document.getElementById('up-latest');
  const hintEl = document.getElementById('up-hint');

  let _pollTimer = null;
  let _tracked = 'beta';
  let _other = 'main';
  let _features = { "in_app_updater": true, "is_beta": true, "channel": "beta" };

  function applyFeatureGating(){
    const note = document.getElementById('up-feature-note');
    if(note) note.classList.add('hidden');
  }

  function open(){
    if(overlay) overlay.classList.remove('hidden');
    msg.textContent = '';
    if(hintEl) hintEl.textContent = '';
    if(applyBox) applyBox.classList.add('hidden');
    prog.classList.add('hidden');
    loadFeatures().then(fetchVersionThenCheck);
    // Auto-run the check so update targets (incl. switch-to-other-channel) show
    // immediately — no extra "Check" click needed.
    doCheck();
  }

  async function loadFeatures(){
    try{
      const r = await fetch('api/features', {cache:'no-store'});
      if(r.ok){
        _features = await r.json();
        _tracked = (_features.channel === 'main') ? 'main' : 'beta';
        _other = (_tracked === 'main') ? 'beta' : 'main';
        // re-paint the Current line now that the real channel is known
        paintCurrent();
      }
    }catch(e){ /* keep defaults */ }
    applyFeatureGating();
  }
  // Paint the popup's "Current" version line (with channel tag). Uses the
  // build-injected global when present.
  function paintCurrent(){
    const g = (window.__VG_VERSION)||{};
    if(g.version){ curEl.textContent = `v${g.version} · ${g.commit}${channelTag()}`; }
  }
  function closePopup(){
    if(overlay) overlay.classList.add('hidden');
    if(_pollTimer){ clearInterval(_pollTimer); _pollTimer = null; }
  }

  function channelTag(){
    return _features && _features.channel ? ` · ${_features.channel}` : '';
  }
  function fillCurrent(){
    const g = (window.__VG_VERSION)||{};
    if(g.version){
      curEl.textContent = `v${g.version} · ${g.commit}${channelTag()}`;
      check.classList.remove('hidden');
      return true;
    }
    return false;
  }

  async function fetchVersionThenCheck(){
    if(fillCurrent()){
      check.classList.remove('hidden');
      return;
    }
    try{
      const r = await fetch('api/version', {cache:'no-store'});
      if(r.ok){
        const v = await r.json();
        curEl.textContent = `v${v.version} · ${v.commit}${channelTag()}`;
      } else {
        curEl.textContent = 'unknown';
      }
    }catch(e){ curEl.textContent = 'unknown'; }
    check.classList.remove('hidden');
  }

  // Build the target buttons. Same-channel update only if a newer commit exists.
  // Switch-to-other-channel is always offered (rebuild to the other channel).
  function renderTargets(same, other){
    if(!applyBox) return;
    applyBox.innerHTML = '';
    if(same && same.behind){
      applyBox.appendChild(mkTargetBtn(
        `Update on ${_tracked}`, _tracked, 'Update to the latest build on this channel.'));
    }
    const otherLabel = (other && other.latest === other.current)
      ? `Switch to ${_other}` : `Switch to ${_other}`;
    applyBox.appendChild(mkTargetBtn(
      otherLabel, _other, `Rebuild the app on the ${_other} channel.`));
    applyBox.classList.remove('hidden');
  }
  function mkTargetBtn(label, target, title){
    const btn = document.createElement('button');
    btn.className = 'btn-accent up-target-btn';
    btn.textContent = label;
    btn.title = title;
    btn.onclick = ()=> doApply(target);
    return btn;
  }

  async function doCheck(){
    msg.textContent = 'Checking…';
    if(applyBox) applyBox.classList.add('hidden');
    notes.innerHTML = '';
    latEl.textContent = '—';
    try{
      // resolve the running version label (vX.Y) for the "Latest" line
      let verLabel = 'v…';
      try{
        const vr = await fetch('api/version', {cache:'no-store'});
        if(vr.ok){ const vj = await vr.json(); verLabel = `v${vj.version}`; }
      }catch(e){ /* keep default */ }
      const rs = await fetch(`api/update/check?target=${encodeURIComponent(_tracked)}`, {cache:'no-store'});
      const same = await rs.json();
      const ro = await fetch(`api/update/check?target=${encodeURIComponent(_other)}`, {cache:'no-store'});
      const other = await ro.json();
      latEl.textContent = same.latest ? `${verLabel} · ${same.latest}` : (same.current || '—');
      if(same.error === 'not_updatable'){
        msg.textContent = 'This build cannot self-update (no source/venv). Pull & rebuild manually.';
        return;
      }
      if(same.behind && same.notes && same.notes.length){
        notes.innerHTML = `<div class="up-notes-h">What's new on ${_tracked}:</div>` +
          same.notes.map(n=>`<div class="up-note">• ${escapeHtml(n)}</div>`).join('');
      }
      renderTargets(same, other);
      if(hintEl) hintEl.textContent = '';
      if(!same.behind){
        if(msg) msg.textContent = 'You are on the latest ' + _tracked + '.';
        if(hintEl) hintEl.textContent = 'Or switch channels using the button above.';
      } else {
        if(msg) msg.textContent = 'An update is available.';
      }
    }catch(e){
      msg.textContent = 'Check failed: ' + (e.message||e);
    }
  }

  async function doApply(target){
    if(applyBox) applyBox.classList.add('hidden');
    if(check) check.classList.add('hidden');
    prog.classList.remove('hidden');
    bar.style.width = '5%';
    status.textContent = `Starting update to ${target}…`;
    msg.textContent = '';
    try{
      await fetch('api/update/apply', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({target})});
    }catch(e){ /* continue polling status */ }
    if(_pollTimer) clearInterval(_pollTimer);
    _pollTimer = setInterval(pollStatus, 1500);
  }

  async function pollStatus(){
    try{
      const r = await fetch('api/update/status', {cache:'no-store'});
      const s = await r.json();
      const p = Math.max(0, Math.min(100, s.progress||0));
      bar.style.width = p + '%';
      status.textContent = s.message || s.stage || '';
      if(s.stage === 'done'){
        clearInterval(_pollTimer); _pollTimer = null;
        status.textContent = 'Updated! Restarting the app…';
      } else if(s.stage === 'error'){
        clearInterval(_pollTimer); _pollTimer = null;
        prog.classList.add('hidden');
        msg.textContent = 'Update failed: ' + (s.error||'unknown error');
        if(check) check.classList.remove('hidden');
      }
    }catch(e){ /* keep polling */ }
  }

  if(ver) ver.onclick = open;
  if(close) close.onclick = closePopup;
  if(check) check.onclick = doCheck;
  if(overlay) overlay.addEventListener('click', e=>{ if(e.target===overlay) closePopup(); });
}

function escapeHtml(s){
  return (s||'').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
