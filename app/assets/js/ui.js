/* ui.js — settings modal, input bar wiring, mic (Whisper). */

/* ---------- mobile panel tabs (Chat / Room / Tools) ---------- */
// On small screens the sidebars collapse into swappable panes. Tapping a tab
// shows that pane and hides the others. Desktop ignores this (CSS hides the bar).
function initMobileTabs(){
  const bar=$('#mobile-panel-tabs'); if(!bar) return;
  bar.querySelectorAll('.mtab').forEach(tab=>{
    tab.onclick=()=>{
      const target=tab.getAttribute('data-target');
      bar.querySelectorAll('.mtab').forEach(t=>t.classList.toggle('active', t===tab));
      document.querySelectorAll('.chat-panel-pane').forEach(p=>{
        p.classList.toggle('active', p.getAttribute('data-pane')===target);
      });
    };
  });
}

/* ---------- modals / controls ---------- */
function closeModals(){ document.querySelectorAll('.modal-overlay').forEach(o=>o.classList.add('hidden')); }
function initModals(){
  $('#btn-settings').onclick=()=>{ refreshHealth(); $('#st-url').value=API.base||''; $('#st-timeout').value=RUN_TIMEOUT_MS; $('#st-model').value=currentModel; $('#settings-overlay').classList.remove('hidden'); };
  $('#st-refresh-models').onclick=()=>{ refreshHealth(); $('#st-model').value=currentModel; };
  $('#st-btn-close').onclick=closeModals;
  $('#st-save').onclick=()=>{ let u=$('#st-url').value.trim(); if(!u) u=(location.protocol.startsWith('http')?location.origin:'http://localhost:8500'); API.base=u; lsSet('vg_base',u); currentModel=$('#st-model').value; lsSet('vg_model',currentModel); RUN_TIMEOUT_MS=parseInt($('#st-timeout').value)||60000; lsSet('vg_tts',TTS_ON?'on':'off'); closeModals(); refreshHealth(); };
  $('#btn-tts-toggle').onclick=()=>{
    TTS_ON=!TTS_ON;
    lsSet('vg_tts',TTS_ON?'on':'off');
    const b=$('#btn-tts-toggle');
    b.textContent=TTS_ON?'🔊':'🔇';
    b.classList.toggle('tts-on', TTS_ON);
    // Tie auto-playback to this button: muting immediately halts any in-flight audio.
    if(!TTS_ON) stopTTS();
  };
  // Reflect the live "playing" state on the speaker button (pulse while audio sounds).
  window.__vg_tts_pulse=setInterval(()=>{
    const b=$('#btn-tts-toggle'); if(!b) return;
    b.classList.toggle('speaking', !!(TTS_ON && ttsPlaying));
  }, 250);
  $('#theme-select').onchange=e=>setTheme(e.target.value);
}

/* ---------- input ---------- */
function initInput(){
  $('#btn-send').onclick=()=>{ const t=$('#message-input').value; $('#message-input').value=''; $('#message-input').style.height='auto'; send(t); };
  $('#btn-gen-image').onclick=()=>{ generateImageFromInput(); };
  $('#message-input').addEventListener('keydown',e=>{
    if(e.key==='Enter'&&!e.shiftKey){ e.preventDefault(); $('#btn-send').click(); }
    if((e.ctrlKey||e.metaKey)&&e.shiftKey&&e.key.toLowerCase()==='i'){ e.preventDefault(); generateImageFromInput(); }
  });
  $('#message-input').addEventListener('input',e=>{e.target.style.height='auto';e.target.style.height=Math.min(e.target.scrollHeight,140)+'px';});
}

/* ---------- mic (Whisper STT) ---------- */
// Mobile Chrome requires a SECURE CONTEXT (https://) for getUserMedia, otherwise
// the mic is silently blocked. Detect and warn clearly instead of failing opaque.
function _micSecure(){
  if(window.isSecureContext) return true;
  // localhost is treated as secure even over http.
  try{ if(location.hostname==='localhost'||location.hostname==='127.0.0.1') return true; }catch(_){}
  return false;
}
// Pick a MediaRecorder mimeType the browser actually supports. Mobile Chrome
// records mp4 (AAC); desktop Chrome/Firefox prefer webm/opus.
function _pickMime(){
  const cands=['audio/webm;codecs=opus','audio/webm','audio/mp4','audio/aac','audio/m4a'];
  for(const m of cands){ try{ if(window.MediaRecorder && MediaRecorder.isTypeSupported(m)) return m; }catch(_){} }
  return '';
}
let __micRec=null, __micStream=null, __micBtn=null;
function initMic(){
  $('#btn-mic').onclick=async()=>{
    if(__micRec){ // second tap => stop + send
      try{ __micRec.stop(); }catch(_){}
      return;
    }
    if(!HEALTH.whisper){ alert('Whisper is offline on the server.'); return; }
    if(!_micSecure()){
      alert('Microphone needs HTTPS. Open the app at the https:// address (self-signed cert — accept the warning / trust it once on your phone) instead of the http:// LAN IP.');
      return;
    }
    try{
      const stream=await navigator.mediaDevices.getUserMedia({audio:true});
      __micStream=stream;
      const mime=_pickMime();
      const mr=new MediaRecorder(stream, mime?{mimeType:mime}:undefined);
      const chunks=[];
      mr.ondataavailable=e=>{ if(e.data && e.data.size) chunks.push(e.data); };
      mr.onstop=async()=>{
        try{
          if(__micBtn){ __micBtn.textContent='🎙'; __micBtn.style.background=''; }
          const blob=new Blob(chunks, {type:mime||'audio/webm'});
          if(blob.size===0){ return; }
          const fd=new FormData();
          const ext=(mime||'audio/webm').includes('mp4')?'mp4':(mime||'audio/webm').includes('webm')?'webm':'wav';
          fd.append('audio', blob, 'rec.'+ext);
          const r=await fetch(API.base+'/api/stt',{method:'POST',body:fd});
          const d=await r.json();
          if(d.text){ const box=$('#message-input'); box.value=(box.value+' '+d.text).trim(); box.dispatchEvent(new Event('input')); }
        }catch(e){ console.error(e); alert('STT failed: '+e.message); }
        finally{ if(__micStream){ __micStream.getTracks().forEach(t=>t.stop()); __micStream=null; } __micRec=null; }
      };
      mr.start(); __micRec=mr;
      __micBtn=$('#btn-mic'); if(__micBtn){ __micBtn.textContent='⏹'; __micBtn.style.background='var(--neon,#23e0ff)'; }
    }catch(e){
      let msg=e.message||'mic error';
      if(e && (e.name==='NotAllowedError'||e.name==='SecurityError')) msg='Microphone permission denied / blocked. On mobile Chrome this also happens over http:// — use the https:// address.';
      alert('Mic error: '+msg);
      if(__micStream){ __micStream.getTracks().forEach(t=>t.stop()); __micStream=null; }
      __micRec=null;
    }
  };
}
