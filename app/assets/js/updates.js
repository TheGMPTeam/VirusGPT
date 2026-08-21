/* updates.js — version-bar click -> update popup.
   Shows the currently-running version + latest available, release notes, and
   drives a background update (git pull + rebuild + replace) via the API. */

function initUpdates(){
  const ver = document.getElementById('version-info');
  if(ver) ver.style.cursor = 'pointer';
  const overlay = document.getElementById('update-overlay');
  const close = document.getElementById('up-btn-close');
  const check = document.getElementById('up-check');
  const apply = document.getElementById('up-apply');
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
  let _features = { "in_app_updater": true, "is_beta": true };

  function applyFeatureGating(){
    // Updating is always allowed (from main or beta). Experimental UI flags can
    // be toggled here later; for now the channel chips + update controls stay on.
    const note = document.getElementById('up-feature-note');
    if(note) note.classList.add('hidden');
  }

  function renderBranches(list, tracked){
    _tracked = tracked;
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
    // re-check against the newly selected branch
    loadFeatures();
    doCheck();
  }

  function open(){
    if(overlay) overlay.classList.remove('hidden');
    msg.textContent = '';
    apply.classList.add('hidden');
    prog.classList.add('hidden');
    loadBranches();
    loadFeatures();
    // Immediately show the running version from the injected global / version.json.
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

  function fillCurrent(){
    const g = (window.__VG_VERSION)||{};
    if(g.version){
      curEl.textContent = `v${g.version} · ${g.commit}`;
      check.classList.remove('hidden');
      return true;
    }
    return false;
  }

  async function fetchVersionThenCheck(){
    // The running version is authoritative from the injected global (set at
    // build time). Show it immediately; only enrich/override from the API if
    // the global is missing (dev runs without a build stamp).
    if(fillCurrent()){
      check.classList.remove('hidden');
      return;
    }
    try{
      const r = await fetch('api/version', {cache:'no-store'});
      if(r.ok){
        const v = await r.json();
        curEl.textContent = `v${v.version} · ${v.commit}`;
      } else {
        curEl.textContent = 'unknown';
      }
    }catch(e){ curEl.textContent = 'unknown'; }
    check.classList.remove('hidden');
  }

  async function doCheck(){
    msg.textContent = 'Checking…';
    apply.classList.add('hidden');
    notes.innerHTML = '';
    latEl.textContent = '—';
    try{
      const r = await fetch('api/update/check', {cache:'no-store'});
      const d = await r.json();
      latEl.textContent = d.latest ? `v… · ${d.latest}` : (d.current || '—');
      if(d.error === 'not_updatable'){
        msg.textContent = 'This build cannot self-update (no source/venv). Pull & rebuild manually.';
        return;
      }
      if(d.behind && d.notes && d.notes.length){
        notes.innerHTML = '<div class="up-notes-h">What\'s new:</div>' +
          d.notes.map(n=>`<div class="up-note">• ${escapeHtml(n)}</div>`).join('');
        apply.classList.remove('hidden');
        msg.textContent = 'An update is available.';
      } else if(d.latest === d.current){
        msg.textContent = 'You are on the latest version.';
      } else {
        msg.textContent = d.error ? `Check failed: ${d.error}` : 'No update available.';
      }
    }catch(e){
      msg.textContent = 'Check failed: ' + (e.message||e);
    }
  }

  async function doApply(){
    apply.classList.add('hidden');
    check.classList.add('hidden');
    prog.classList.remove('hidden');
    bar.style.width = '5%';
    status.textContent = 'Starting update…';
    msg.textContent = '';
    try{
      await fetch('api/update/apply', {method:'POST'});
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
        check.classList.remove('hidden');
      }
    }catch(e){ /* keep polling */ }
  }

  if(ver) ver.onclick = open;
  if(close) close.onclick = closePopup;
  if(check) check.onclick = doCheck;
  if(apply) apply.onclick = doApply;
  if(overlay) overlay.addEventListener('click', e=>{ if(e.target===overlay) closePopup(); });
}

function escapeHtml(s){
  return (s||'').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
