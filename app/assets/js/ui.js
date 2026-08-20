/* ui.js — settings modal, input bar wiring, mic (Whisper), and the autonomous
   mission panel controls. */

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

/* ---------- autonomous mission panel ---------- */
let __missionStream = null;
let __activeMissionId = null;
async function startMission(){
  const goal = ($('#mission-goal').value||'').trim();
  if(!goal){ alert('Enter a mission goal first.'); return; }
  const room = activeRoom();
  const lineup = roomPersonas(room).map(personaByName).filter(Boolean);
  if(lineup.length<2){ alert('Add at least 2 personas to the room for a team mission.'); return; }
  $('#mission-state').innerHTML = 'Starting mission…';
  try{
    const res = await fetch(API.base+'/api/autonomous/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({goal, room_personas: lineup})});
    const data = await res.json();
    if(!res.ok || !data.ok){ throw new Error(data.error||('HTTP '+res.status)); }
    __activeMissionId = data.mission_id;
    $('#mission-state').innerHTML = `Mission <b>${data.mission_id}</b> · status <b id="mission-status">${data.status||'running'}</b> · planner <b>${data.planner||''}</b>`;
    $('#mission-events').innerHTML = '';
    if(__missionStream){ try{__missionStream.close();}catch(e){} }
    __missionStream = new EventSource(API.base + data.stream_url);
    __missionStream.onmessage = (ev)=>{
      try{
        const st = JSON.parse(ev.data);
        const statusEl = $('#mission-status');
        if(statusEl) statusEl.textContent = st.status || statusEl.textContent;
        appendMissionEvent(st.event || 'state', st.planner || 'system', JSON.stringify(st).slice(0,180));
        if(st.status==='completed' || st.status==='failed' || st.status==='cancelled'){
          stopMission(false);
          if(st.final_result) appendMissionEvent('final', data.planner||'system', (typeof st.final_result==='string'?st.final_result:JSON.stringify(st.final_result)).slice(0,400));
        }
      }catch(e){}
    };
    $('#btn-mission-start').classList.add('hidden');
    $('#btn-mission-stop').classList.remove('hidden');
  }catch(err){ $('#mission-state').textContent = '⚠ '+(err.message||'error'); }
}
function stopMission(notify=true){
  if(__missionStream){ try{__missionStream.close();}catch(e){} __missionStream=null; }
  if(__activeMissionId){
    fetch(API.base+'/api/autonomous/stop/'+encodeURIComponent(__activeMissionId),{method:'POST'}).catch(()=>{});
  }
  __activeMissionId = null;
  $('#btn-mission-start').classList.remove('hidden');
  $('#btn-mission-stop').classList.add('hidden');
  if(notify) appendMissionEvent('system','system','Mission stopped.');
}
function appendMissionEvent(evt, agent, text){
  const el = $('#mission-events');
  if(!el) return;
  const row = document.createElement('div');
  row.style.cssText = 'padding:3px 0;border-bottom:1px dashed var(--border)';
  row.textContent = `[${new Date().toLocaleTimeString()}] ${agent||'?'}: ${evt} — ${text}`;
  el.prepend(row);
  while(el.children.length>200) el.removeChild(el.lastChild);
}
async function loadMissionsList(){
  try{
    const r = await fetch(API.base+'/api/missions');
    const list = await r.json();
    const el = $('#mission-state');
    if(!el) return;
    if(!Array.isArray(list) || !list.length){ el.textContent = 'No missions yet.'; return; }
    el.innerHTML = list.slice(0,20).map(m=>`<div><b>${m.id}</b> · ${m.status} · ${(m.goal||'').slice(0,60)} · ${m.updated_at||''}</div>`).join('');
  }catch(e){ const el=$('#mission-state'); if(el) el.textContent='⚠ missions unavailable'; }
}
function initMissions(){
  $('#btn-mission-start').onclick=startMission;
  $('#btn-mission-stop').onclick=()=>stopMission(true);
  $('#btn-mission-refresh').onclick=loadMissionsList;
}
