/* updates.js — version-bar click -> update popup.
   Shows the running version + channel, the available update targets (stay on
   the same channel for a newer build, or switch to the other channel), release
   notes, and drives a background update (git pull + rebuild + replace). */

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
  const branchBox = document.getElementById('up-branches');

  let _pollTimer = null;
  let _tracked = 'beta';
  let _features = { "in_app_updater": true, "is_beta": true, "channel": "beta" };
  let _other = 'main';

  function applyFeatureGating(){
    const note = document.getElementById('up-feature-note');
    if(note) note.classList.add('hidden');
  }

  function renderBranches(list, tracked){
    _tracked = tracked;
    _other = (tracked === 'main') ? 'beta' : 'main';
    if(!branchBox) return;
    branchBox.innerHTML = '';
    (list||[]).forEach(b=>{
      const btn = document.createElement('button');
      btn.className = 'branch-chip' + (b===tracked ? ' active' : '');
      btn.textContent = b;
      btn.onclick = ()=> selectBranch(b);
      branchBox.appendChild(btn);
    });
  }

  async function loadBranches(){
    try{
      const r = await fetch('api/update/branches', {cache:'no-store'});
      if(r.ok){
        const d = await r.json();
        renderBranches(d.available, d.tracked);
        return;
      }
    }catch(e){ /* ignore */ }
    renderBranches(['beta','main'], _tracked);
  }

  async function selectBranch(b){
    try{
      const r = await fetch('api/update/branch', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({branch:b})});
      if(r.ok){
        const d = await r.json();
        renderBranches(d.available, d.tracked);
      } else {
        renderBranches([b], b);
      }
    }catch(e){ renderBranches([b], b); }
    loadFeatures();
    doCheck();
  }

  function open(){
    if(overlay) overlay.classList.remove('hidden');
    msg.textContent = '';
    if(applyBox) applyBox.classList.add('hidden');
    prog.classList.add('hidden');
    loadBranches();
    loadFeatures();
    fetchVersionThenCheck();
  }

  async function loadFeatures(){
    try{
      const r = await fetch('api/features', {cache:'no-store'});
      if(r.ok){ _features = await r.json(); }
    }catch(e){ /* keep defaults */ }
    applyFeatureGating();
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

  // Build the two target buttons: same-channel update (if newer exists) and
  // switch-to-other-channel (always allowed, even at the same commit).
  function renderTargets(sameCheck, otherCheck){
    if(!applyBox) return;
    applyBox.innerHTML = '';
    // Same channel: only offer if there is a newer commit.
    if(sameCheck && sameCheck.behind){
      const b = mkTargetBtn(`Update on ${_tracked}`, _tracked, 'Update available for this channel.');
      applyBox.appendChild(b);
    }
    // Other channel: always offer a switch (rebuild to the other channel).
    const otherLabel = (otherCheck && otherCheck.latest === otherCheck.current)
      ? `Switch to ${_other}` : `Switch to ${_other} (${otherCheck ? otherCheck.latest : '…'})`;
    const ob = mkTargetBtn(otherLabel, _other, 'Rebuild to the other channel.');
    applyBox.appendChild(ob);
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
      // same channel
      const rs = await fetch(`api/update/check?target=${encodeURIComponent(_tracked)}`, {cache:'no-store'});
      const same = await rs.json();
      // other channel
      const ro = await fetch(`api/update/check?target=${encodeURIComponent(_other)}`, {cache:'no-store'});
      const other = await ro.json();
      latEl.textContent = same.latest ? `v… · ${same.latest}` : (same.current || '—');
      if(same.error === 'not_updatable'){
        msg.textContent = 'This build cannot self-update (no source/venv). Pull & rebuild manually.';
        return;
      }
      if(same.behind && same.notes && same.notes.length){
        notes.innerHTML = `<div class="up-notes-h">What's new on ${_tracked}:</div>` +
          same.notes.map(n=>`<div class="up-note">• ${escapeHtml(n)}</div>`).join('');
      }
      renderTargets(same, other);
      if(!same.behind){
        msg.textContent = 'You are on the latest ' + _tracked + '. You can still switch channels below.';
      } else {
        msg.textContent = 'An update is available.';
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
