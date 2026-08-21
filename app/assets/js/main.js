/* main.js — boot order. Loaded last; calls into all the init*() functions and
   kicks off health polling + first paint. */

// Show version + commit in the bottom status bar. Prefers the build-injected
// global (set by desktop/build-*.py into the frozen index.html), then falls back
// to fetching /version.json, then a plain "dev" label for unbuilt dev runs.
function showVersion(){
  const el=document.getElementById('version-info');
  if(!el) return;
  const paint=(v, channel)=>{
    if(!v){ el.textContent='dev'; return; }
    el.textContent = `v${v.version} · ${v.commit}` + (channel ? ` · ${channel}` : '');
  };
  if(window.__VG_VERSION && window.__VG_VERSION.version){
    paint(window.__VG_VERSION, (window.__VG_CHANNEL)||null); return;
  }
  fetch('version.json', {cache:'no-store'}).then(r=>r.ok?r.json():null)
    .then(j=>{ if(j && j.version) paint(j); else el.textContent='dev'; })
    .catch(()=>{ el.textContent='dev'; });
}

function boot(){
  try{
    setTheme(lsGet('vg_theme', 'cyber'));
    initTabs();
    initSessions();      // builds the left-side session list + New button
    renderPersonas();
    switchRoom(currentRoom);
    initAutocomplete();
    initImprove();
    initTeam();
    initModals();
    initInput();
    initMobileTabs();
    initMic();
    initMatrix();
    initUpdates();
    $('#btn-tts-toggle').textContent=TTS_ON?'🔊':'🔇';
    $('#btn-tts-toggle').classList.toggle('tts-on', !!TTS_ON);
    // Seed per-session auto-play from the saved speaker state: if the speaker is
    // ON at boot, the first session auto-plays; after /clear or /new it resets to
    // off until the user clicks the speaker again.
    sessionAutoPlay = !!TTS_ON;
    refreshHealth(); setInterval(refreshHealth,15000);
    showVersion();
    window.addEventListener('resize',()=>{ if(document.querySelector('.tab[data-tab=memory]').classList.contains('active')){ clearTimeout(window.__mgResizeT); window.__mgResizeT=setTimeout(loadMemoryGraph,200); } });
    // Dismiss the boot/loading screen now that the UI is initialized.
    const bootEl=document.getElementById('boot-screen');
    const bootStatus=document.getElementById('boot-status');
    if(bootStatus) bootStatus.textContent='Ready';
    if(bootEl){ setTimeout(()=>{ bootEl.classList.add('hide'); setTimeout(()=>bootEl.remove(), 450); }, 250); }
  }catch(err){
    console.error('[VG boot error]', err);
    const m=document.querySelector('#messages');
    if(m) m.innerHTML='<div class="msg bot"><div class="bubble">⚠ boot error: '+(err&&err.message||err)+'</div></div>';
    const bootEl=document.getElementById('boot-screen');
    if(bootEl){ bootEl.classList.add('hide'); setTimeout(()=>bootEl.remove(), 450); }
  }
}
// Render immediately (DOM is ready — scripts are at end of body) and again on load as a safety net.
boot();
window.addEventListener('load', ()=>{ try{ renderPersonas(); renderSessions(); }catch(e){ console.error(e); } });
