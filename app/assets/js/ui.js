/* ui.js — settings modal, input bar wiring, mic (Whisper). */

/* ---------- modals / controls ---------- */
function closeModals(){ document.querySelectorAll('.modal-overlay').forEach(o=>o.classList.add('hidden')); }
function initModals(){
  $('#btn-settings').onclick=()=>{ refreshHealth(); $('#st-url').value=API.base||''; $('#st-timeout').value=RUN_TIMEOUT_MS; $('#st-model').value=currentModel; $('#settings-overlay').classList.remove('hidden'); };
  $('#st-refresh-models').onclick=()=>{ refreshHealth(); $('#st-model').value=currentModel; };
  $('#st-btn-close').onclick=closeModals;
  $('#st-save').onclick=()=>{ let u=$('#st-url').value.trim(); if(!u) u=(location.protocol.startsWith('http')?location.origin:'http://localhost:8500'); API.base=u; lsSet('vg_base',u); currentModel=$('#st-model').value; lsSet('vg_model',currentModel); RUN_TIMEOUT_MS=parseInt($('#st-timeout').value)||60000; lsSet('vg_tts',TTS_ON?'on':'off'); closeModals(); refreshHealth(); };
  $('#btn-tts-toggle').onclick=()=>{ TTS_ON=!TTS_ON; lsSet('vg_tts',TTS_ON?'on':'off'); $('#btn-tts-toggle').textContent=TTS_ON?'🔊':'🔇'; };
  $('#theme-select').onchange=e=>setTheme(e.target.value);
}

/* ---------- input ---------- */
function initInput(){
  $('#btn-send').onclick=()=>{ const t=$('#message-input').value; $('#message-input').value=''; $('#message-input').style.height='auto'; send(t); };
  $('#message-input').addEventListener('keydown',e=>{ if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();$('#btn-send').click();} });
  $('#message-input').addEventListener('input',e=>{e.target.style.height='auto';e.target.style.height=Math.min(e.target.scrollHeight,140)+'px';});
}

/* ---------- mic (Whisper STT) ---------- */
function initMic(){
  $('#btn-mic').onclick=async()=>{ if(!HEALTH.whisper){alert('Whisper is offline on the server.');return;}
    try{ const stream=await navigator.mediaDevices.getUserMedia({audio:true}); const mr=new MediaRecorder(stream); const chunks=[];
      mr.ondataavailable=e=>chunks.push(e.data); mr.onstop=async()=>{const blob=new Blob(chunks);const fd=new FormData();fd.append('audio',blob,'rec.webm');
        try{const r=await fetch(API.base+'/api/stt',{method:'POST',body:fd});const d=await r.json();if(d.text)send(d.text);}catch(e){console.error(e);}};
      mr.start(); setTimeout(()=>mr.stop(),5000);
    }catch(e){ alert('Mic error: '+e.message); } };
}
