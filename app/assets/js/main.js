/* main.js — boot order. Loaded last; calls into all the init*() functions and
   kicks off health polling + first paint. */

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
    $('#btn-tts-toggle').textContent=TTS_ON?'🔊':'🔇';
    $('#btn-tts-toggle').classList.toggle('tts-on', !!TTS_ON);
    // Seed per-session auto-play from the saved speaker state: if the speaker is
    // ON at boot, the first session auto-plays; after /clear or /new it resets to
    // off until the user clicks the speaker again.
    sessionAutoPlay = !!TTS_ON;
    refreshHealth(); setInterval(refreshHealth,15000);
    window.addEventListener('resize',()=>{ if(document.querySelector('.tab[data-tab=memory]').classList.contains('active')){ clearTimeout(window.__mgResizeT); window.__mgResizeT=setTimeout(loadMemoryGraph,200); } });
  }catch(err){
    console.error('[VG boot error]', err);
    const m=document.querySelector('#messages');
    if(m) m.innerHTML='<div class="msg bot"><div class="bubble">⚠ boot error: '+(err&&err.message||err)+'</div></div>';
  }
}
// Render immediately (DOM is ready — scripts are at end of body) and again on load as a safety net.
boot();
window.addEventListener('load', ()=>{ try{ renderPersonas(); renderSessions(); }catch(e){ console.error(e); } });
