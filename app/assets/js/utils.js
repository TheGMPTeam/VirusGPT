/* utils.js — DOM/text helpers, tabs, health, themes, matrix rain. */

/* ---------- tabs ---------- */
function initTabs(){
  document.querySelectorAll('.tab').forEach(t=>{
    t.onclick=()=>{
      document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
      document.querySelectorAll('.tabpane').forEach(x=>x.classList.remove('active'));
      t.classList.add('active');
      $('#pane-'+t.dataset.tab).classList.add('active');
      if(t.dataset.tab==='memory') loadMemoryGraph();
      if(t.dataset.tab==='personas'){ loadVoices().then(renderPersonaCards); }
      if(t.dataset.tab==='chat'){ renderPersonas(); renderSessions(); }
      if(t.dataset.tab==='missions'){ loadMissionsList(); }
    };
  });
}

/* ---------- mission-control HUD ---------- */
let __VG_BOOT = Date.now();
function bootUptime(){
  const s = Math.floor((Date.now()-__VG_BOOT)/1000);
  const h = Math.floor(s/3600), m = Math.floor((s%3600)/60);
  return (h? h+'h ':'') + m + 'm';
}
function refreshHud(){
  const hud = document.getElementById('mission-hud'); if(!hud) return;
  const isMission = document.documentElement.getAttribute('data-theme')==='mission';
  hud.style.display = isMission ? '' : 'none';
  if(!isMission) return;
  // Read optional globals defensively — they may be in the TDZ on first paint.
  let agents=0, sessions=0, personasN=0;
  try{ const r=window.rooms; agents = r ? Object.keys(r).length : 0; }catch(e){}
  try{ const s=window.SESSIONS; sessions = s ? s.length : 0; }catch(e){}
  try{ const p=window.personas; personasN = p ? p.length : 0; }catch(e){}
  const d = HEALTH||{};
  const backendPct = (d.ollama&&d.tts) ? 100 : (d.ollama||d.tts) ? 55 : 0;
  const set=(id,v)=>{const e=document.getElementById(id); if(e) e.textContent=v;};
  set('hud-agents', (agents+sessions) ? (agents+'/'+sessions) : (personasN||'—'));
  set('hud-uptime', bootUptime());
  set('hud-latency', d.latency!=null ? d.latency+'ms' : '—');
  set('hud-models', (d.models&&d.models.length)||'—');
  set('hud-voice', d.default_voice||'—');
  set('hud-io', ((d.tts?'TTS✓':'TTS✗')+' / '+(d.whisper?'STT✓':'STT✗')));
  set('hud-backend-pct', backendPct+'%');
  const bar=document.getElementById('hud-backend-bar'); if(bar) bar.style.width=backendPct+'%';
}
/* ---------- health ---------- */
async function refreshHealth(){
  const t0 = performance.now();
  try{
    const r = await fetch(API.base + '/api/health'); const d = await r.json();
    d.latency = Math.round(performance.now()-t0);
    HEALTH = d;
    const st = $('#status'); st.classList.toggle('ok', d.ollama && d.tts); st.classList.toggle('warn', !(d.ollama && d.tts));
    $('#status-text').textContent = `ollama:${d.ollama} tts:${d.tts} whisper:${d.whisper}`;
    $('#model-info').textContent = 'models: ' + (d.models?d.models.length:0);
    $('#voice-info').textContent = 'voices: ' + (d.voices?d.voices.length:0);
    const models = d.models||[];
    // Preferred sane default when the stored model is missing/unavailable, and a
    // blocklist of models that are present but known to hang / not respond.
    const PREFERRED = ['qwen2.5:3b','qwen2.5:1.5b','llama3.2:3b','eslider/bonsai-1.7b:latest'];
    const BLOCKED = ['MichelRosselli/ternary-bonsai:1.7b-f16','digitsflow/bonsai-8b:latest'];
    const pickPreferred = () => PREFERRED.find(m=>models.includes(m) && !BLOCKED.includes(m)) || models.find(m=>!BLOCKED.includes(m)) || '';
    // If the stored/selected model is missing or known-bad, fall back to a live one.
    if(currentModel && (!models.includes(currentModel) || BLOCKED.includes(currentModel))){
      currentModel = pickPreferred();
      lsSet('vg_model', currentModel);
    }
    if(!currentModel && models.length) currentModel = pickPreferred();
    const sel = currentModel || models[0] || '';
    $('#st-model').innerHTML = models.map(m=>`<option ${m===sel?'selected':''}>${m}</option>`).join('');
    $('#st-voice').innerHTML = (d.voices||[]).map(v=>`<option ${v===(d.default_voice||'alba')?'selected':''}>${v}</option>`).join('');
    if(!currentModel && (d.default_model||d.models?.[0])) currentModel = d.default_model||d.models[0];
    $('#btn-mic').disabled = !d.whisper;
    $('#btn-mic').title = d.whisper ? 'Voice input' : 'Voice input (whisper offline)';
    saveJSON('vg_health', d);
    refreshHud();
  }catch(e){ $('#status').classList.add('warn'); $('#status').classList.remove('ok'); $('#status-text').textContent='backend unreachable'; }
}

/* ---------- themes ---------- */
function setTheme(t){ const root=document.documentElement.style;
  if(t==='amber'){root.setProperty('--neon','#ffb000');root.setProperty('--neon2','#ff7a00');root.setProperty('--neon3','#ff3b3b');root.setProperty('--gear','#ffcf6b');root.setProperty('--grid','rgba(255,176,0,0.05)');}
  else if(t==='ice'){root.setProperty('--neon','#39f6ff');root.setProperty('--neon2','#8a7bff');root.setProperty('--neon3','#ff5cf0');root.setProperty('--gear','#bdefff');root.setProperty('--grid','rgba(57,246,255,0.05)');}
  else if(t==='nova'){ /* NEW theme option: violet/magenta + gold, redesigned borders/bubbles */
    root.setProperty('--neon','#c77dff');root.setProperty('--neon2','#ff5cf0');root.setProperty('--neon3','#ffd166');root.setProperty('--gear','#e0aaff');root.setProperty('--grid','rgba(199,125,255,0.06)');
    root.setProperty('--bg','#0a0712');root.setProperty('--bg2','#120a1f');root.setProperty('--panel','#150d24');root.setProperty('--panel2','#1b1230');root.setProperty('--border','#3a2a5a');root.setProperty('--txt','#ecd9ff');root.setProperty('--txt-dim','#8f7bb0');
    root.setProperty('--radius','14px');root.setProperty('--bubble-shift','var(--neon2)');root.setProperty('--shadow','0 0 22px rgba(199,125,255,0.22)');root.setProperty('--warn','#ff6b9d');
  }
  else if(t==='mission'){ /* NEW theme: dark command-center — cyan/teal telemetry + amber KPI accents */
    root.setProperty('--neon','#34e7e4');root.setProperty('--neon2','#3aa0ff');root.setProperty('--neon3','#ffb347');root.setProperty('--gear','#7df3ff');root.setProperty('--grid','rgba(52,231,228,0.05)');
    root.setProperty('--bg','#04070b');root.setProperty('--bg2','#070d14');root.setProperty('--panel','#0a121b');root.setProperty('--panel2','#0d1824');root.setProperty('--border','#16313f');root.setProperty('--txt','#d6f7fb');root.setProperty('--txt-dim','#5c8593');root.setProperty('--warn','#ff6b6b');
    root.setProperty('--radius','8px');root.setProperty('--bubble-shift','var(--neon2)');root.setProperty('--shadow','0 0 20px rgba(52,231,228,0.18)');root.setProperty('--hud','#ffb347');root.setProperty('--hud2','#34e7e4');
  }
  else{root.setProperty('--neon','#00ff9c');root.setProperty('--neon2','#23e0ff');root.setProperty('--neon3','#ff2bd6');root.setProperty('--gear','#7df9ff');root.setProperty('--grid','rgba(0,255,140,0.05)');
    /* full reset to :root defaults so leaving Nova/Mission restores the base theme */
    root.setProperty('--bg','#05070a');root.setProperty('--bg2','#0a0f16');root.setProperty('--panel','#0c1118');root.setProperty('--panel2','#0f1620');root.setProperty('--border','#1c2a3a');root.setProperty('--txt','#c9f7e4');root.setProperty('--txt-dim','#5d7a72');root.setProperty('--warn','#ff5a5a');
    root.setProperty('--radius','6px');root.setProperty('--bubble-shift','var(--neon2)');root.setProperty('--shadow','0 0 18px rgba(0,255,156,0.18)');root.setProperty('--hud','#00ff9c');root.setProperty('--hud2','#23e0ff');}
  document.documentElement.setAttribute('data-theme', t);
  lsSet('vg_theme',t);
}

/* ---------- matrix rain ---------- */
function initMatrix(){
  const c=$('#matrix'); const x=c.getContext('2d'); const cols=Math.floor(innerWidth/14);
  c.width=innerWidth; c.height=innerHeight; const drops=Array(cols).fill(1);
  function draw(){ x.fillStyle='rgba(5,7,10,0.08)'; x.fillRect(0,0,c.width,c.height);
    x.fillStyle=getComputedStyle(document.documentElement).getPropertyValue('--neon')||'#00ff9c'; x.font='14px monospace';
    for(let i=0;i<drops.length;i++){const ch=String.fromCharCode(0x30A0+Math.random()*96);x.fillText(ch,i*14,drops[i]*14);if(drops[i]*14>c.height&&Math.random()>0.975)drops[i]=0;drops[i]++;}
  }
  setInterval(draw,60);
}
