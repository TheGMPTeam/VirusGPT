/* main.js — boot order. Loaded last; calls into all the init*() functions and
   kicks off health polling + first paint. */

// Show version + commit in the bottom status bar. Prefers the build-injected
// global (set by desktop/build-*.py into the frozen index.html), then falls back
// to fetching /version.json, then a plain "dev" label for unbuilt dev runs.
function showVersion(){
  const el=document.getElementById('version-info');
  if(!el) return;
  // Track the live channel separately from the rendered text so we never try to
  // re-derive it by slicing the string (that previously re-parsed the COMMIT as
  // the channel and produced a garbled "vX · <commit> · <commit>" label when
  // version.json was also consulted).
  let _channel = (window.__VG_CHANNEL) || null;
  const paint=(v)=>{
    if(!v){ el.textContent='dev'; return; }
    el.textContent = `v${v.version} · ${v.commit}` + (_channel ? ` · ${_channel}` : '');
  };
  const g = window.__VG_VERSION || null;
  if(g && g.version){ paint(g); }
  const resolveVersion=()=>{
    if(g && g.version){ paint(g); return; }
    fetch('version.json', {cache:'no-store'}).then(r=>r.ok?r.json():null)
      .then(j=>{ if(j && j.version) paint(j); else if(!el.textContent) el.textContent='dev'; })
      .catch(()=>{ if(!el.textContent) el.textContent='dev'; });
  };
  // Re-resolve the LIVE channel from /api/features so the bar matches the
  // running channel (not just the baked-in build-time value).
  fetch('api/features', {cache:'no-store'}).then(r=>r.ok?r.json():null).then(f=>{
    if(f && f.channel){ _channel = f.channel; }
    resolveVersion();
  }).catch(()=>{ resolveVersion(); });
}

function boot(){
  try{
    setTheme(lsGet('vg_theme', 'cyber'));
    if(window.initIcons) initIcons();   // fill .vg-ico placeholders from icons.js
    initTabs();
    initSessions();      // builds the left-side session list + New button
    renderPersonas();
    switchRoom(currentRoom);
    initAutocomplete();
    initImprove();
    initTeam();
    initModals();
    initDisplaySettings();
    initVoiceSettings();
    initInput();
    initMobileTabs();
    initMic();
    initMatrix();
    initUpdates();
    initServicesPanel();
    $('#btn-tts-toggle').innerHTML=VG_ICON(TTS_ON?'speaker_on':'speaker_off');
    $('#btn-tts-toggle').classList.toggle('tts-on', !!TTS_ON);
    // Seed per-session auto-play from the saved speaker state: if the speaker is
    // ON at boot, the first session auto-plays; after /clear or /new it resets to
    // off until the user clicks the speaker again.
    sessionAutoPlay = !!TTS_ON;
    refreshHealth(); setInterval(refreshHealth,15000);
    showVersion();
    window.addEventListener('resize',()=>{ if(document.querySelector('.tab[data-tab=memory]').classList.contains('active')){ clearTimeout(window.__mgResizeT); window.__mgResizeT=setTimeout(loadMemoryGraph,200); } });
    // Dismiss the boot/loading screen once the full page has painted (window.load),
    // not on a fixed timer — keeps the colored loader as the first thing shown and
    // prevents a late flash after the UI is already visible.
    const bootEl=document.getElementById('boot-screen');
    const bootStatus=document.getElementById('boot-status');
    if(bootStatus) bootStatus.textContent='Ready';
    const hideBoot=()=>{ if(bootEl){ bootEl.classList.add('hide'); setTimeout(()=>bootEl.remove(), 450); } };
    if(document.readyState==='complete'){ setTimeout(hideBoot, 200); }
    else { window.addEventListener('load', ()=> setTimeout(hideBoot, 200)); }
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
