/* personas.js — sidebar lineup (chat tab) + the full Personas management pane
   (cards, voice clone, save/delete, persistence to localStorage AND server). */

function personaByName(n){ return personas.find(p=>p.name===n) || null; }

/* ---------- sidebar personas (chat tab: name only + remove) ---------- */
function renderPersonas(){
  const rm=activeRoom(); const lineup=roomPersonas(rm);
  const list=$('#persona-list'); list.innerHTML='';
  lineup.forEach(n=>{ const p=personaByName(n); if(!p) return;
    const chip=document.createElement('div');chip.className='persona-chip'+(n===selectedPersona?' sel':'');
    chip.innerHTML=`<div class="persona-avatar" style="background:${p.color}">${p.emoji||'🤖'}</div><div class="persona-meta"><b>${p.name}</b></div><span class="pc-remove" title="Remove from room">✕</span>`;
    chip.title=p.description||p.name;
    chip.onclick=(e)=>{ if(e.target.classList.contains('pc-remove')){ // remove from room
        rm.personas=rm.personas.filter(x=>x!==n); if(selectedPersona===n) selectedPersona=null; saveJSON('vg_rooms',rooms); renderPersonas(); return; }
      selectedPersona=(selectedPersona===n)?null:n; renderPersonas(); };
    list.appendChild(chip);
  });
  // add-persona dropdown: personas not already in the room
  const sel=$('#persona-add-select');
  const inRoom=new Set(lineup);
  sel.innerHTML='<option value="">+ Add persona…</option>'+personas.filter(p=>!inRoom.has(p.name)).map(p=>`<option value="${p.name}">${p.name}</option>`).join('');
}
$('#persona-add-select').onchange=e=>{ const n=e.target.value; if(!n) return; const rm=activeRoom(); roomPersonas(rm); if(!rm.personas.includes(n)) rm.personas.push(n); saveJSON('vg_rooms',rooms); selectedPersona=n; e.target.value=''; renderPersonas(); };

/* ---------- PERSONAS TAB ---------- */
let VOICE_LIST = [];
let VOICE_LIST_TS = 0;
async function loadVoices(force){
  // cache for 60s so re-opening the tab doesn't re-fetch every time
  if(!force && VOICE_LIST.length && (Date.now()-VOICE_LIST_TS)<60000) return;
  try{ const r=await fetch(API.base+'/api/tts/voices'); const d=await r.json(); VOICE_LIST=d.voices||[]; VOICE_LIST_TS=Date.now(); }
  catch(e){ if(!VOICE_LIST.length) VOICE_LIST=['alba','azelma','cosette','eponine','fantine','javert','jean','marius']; }
}
function voiceOptions(selected){
  const opts = (VOICE_LIST.length?VOICE_LIST:['alba','azelma','cosette','eponine','fantine','javert','jean','marius']).map(v=>{
    const id = (typeof v==='string')?v:v.id; const nm=(typeof v==='string')?v:v.name;
    return `<option value="${id}" ${(id===(selected||'alba'))?'selected':''}>${nm}${v.type==='cloned'?' (cloned)':''}</option>`;
  }).join('');
  return opts + `<option value="__clone__">+ Clone new voice…</option>`;
}
function renderPersonaCards(){
  const grid=$('#persona-grid'); grid.innerHTML='';
  personas.forEach((p,idx)=>{
    const card=document.createElement('div'); card.className='pcard';
    const isClone = (p.voice||'').startsWith('local:');
    card.innerHTML=`
      <h3><div class="pav" style="background:${p.color}">${p.emoji||'🤖'}</div> ${p.name}</h3>
      <div class="form-row" style="padding:0"><label>Name</label><input class="pf-name" value="${p.name||''}" maxlength="25"></div>
      <div class="form-row" style="padding:0"><label>Description</label><input class="pf-desc" value="${p.description||''}" maxlength="40"></div>
      <div class="form-row" style="padding:0"><label>System Prompt (AI behavior)</label><textarea class="pf-sys">${p.system_prompt||''}</textarea></div>
      <div class="form-row" style="padding:0"><label>Skills &amp; Style (agent specialization)</label><textarea class="pf-skills" placeholder="e.g. code review, storytelling, Socratic dialogue">${p.skills||''}</textarea></div>
      <div class="row">
        <div><label>Team Role</label><select class="pf-role"><option value="worker" ${(p.role||'worker')==='worker'?'selected':''}>Worker</option><option value="planner" ${(p.role||'worker')==='planner'?'selected':''}>Planner / Lead</option></select></div>
        <div><label>TTS Voice</label><select class="pf-voice">${voiceOptions(p.voice)}</select></div>
        <div><label>Emoji</label><input class="pf-emoji" maxlength="2" value="${p.emoji||'🤖'}"></div>
        <div><label>Color</label><input class="pf-color" type="color" value="${p.color||'#00ff9c'}" style="height:38px"></div>
      </div>
      <div class="clone-box hidden" style="border:1px dashed var(--border);border-radius:8px;padding:10px;display:flex;flex-direction:column;gap:8px">
        <label style="font-size:11px;color:var(--neon2)">Clone a new voice — upload reference audio (WAV)</label>
        <input class="clone-name" placeholder="voice name (e.g. Morgan)" maxlength="20">
        <input type="file" class="clone-audio" accept="audio/*">
        <div style="display:flex;gap:8px">
          <button class="btn-accent clone-do">Upload &amp; Clone</button>
          <button class="btn-secondary clone-prev">🔊 Preview</button>
        </div>
      </div>
      <div class="clone-status ${isClone?'ready':''}">${isClone?'✔ cloned voice ('+p.voice+')':'select a voice or clone a new one'}</div>
      <div class="actions">
        <button class="btn-secondary pf-test">🔊 Test</button>
        <button class="btn-secondary pf-del">Delete</button>
        <button class="btn-accent pf-save">Save</button>
      </div>`;
    card.querySelector('.pf-voice').onchange=()=>{
      const v=card.querySelector('.pf-voice').value;
      card.querySelector('.clone-box').classList.toggle('hidden', v!=='__clone__');
    };
    card.querySelector('.pf-test').onclick=()=>{
      const v=card.querySelector('.pf-voice').value;
      if(v==='__clone__'){ alert('Choose or clone a voice first.'); return; }
      playTestVoice(v, card.querySelector('.clone-status'));
    };
    card.querySelector('.clone-prev').onclick=async()=>{
      const f=card.querySelector('.clone-audio').files[0];
      if(!f){ alert('Choose a reference WAV first.'); return; }
      const st=card.querySelector('.clone-status');
      const fd=new FormData(); fd.append('audio',f);
      st.textContent='♪ generating preview…'; st.classList.remove('ready');
      try{
        const r=await fetch(API.base+'/api/tts/preview',{method:'POST',body:fd});
        if(!r.ok){ const d=await r.json().catch(()=>({})); throw new Error(d.error||('tts '+r.status)); }
        const b=await r.blob(); const a=new Audio(URL.createObjectURL(b));
        const cleanup=()=>URL.revokeObjectURL(a.src);
        a.onended=()=>{ st.textContent='✔ preview played'; st.classList.add('ready'); cleanup(); };
        a.onerror=cleanup;
        a.play().catch(cleanup); st.textContent='♪ playing preview…';
      }catch(e){ st.textContent='✖ '+ (e.message||'unavailable'); console.error(e); }
    };
    card.querySelector('.clone-do').onclick=async()=>{
      const nm=card.querySelector('.clone-name').value.trim(); const f=card.querySelector('.clone-audio').files[0];
      if(!nm||!f){ alert('Enter a name and choose a WAV'); return; }
      const fd=new FormData(); fd.append('name',nm); fd.append('audio',f);
      card.querySelector('.clone-do').textContent='Cloning…';
      try{ const r=await fetch(API.base+'/api/tts/clone',{method:'POST',body:fd}); const d=await r.json();
        if(d.ok){ await loadVoices(); p.voice=d.voice; renderPersonaCards(); renderPersonas(); }
        else alert('Clone failed: '+(d.error||'unknown'));
      }catch(e){ alert('Clone error: '+e.message); }
      card.querySelector('.clone-do').textContent='Upload & Clone';
    };
    card.querySelector('.pf-save').onclick=()=>{
      p.name=card.querySelector('.pf-name').value.trim()||p.name;
      p.description=card.querySelector('.pf-desc').value.trim();
      p.system_prompt=card.querySelector('.pf-sys').value.trim();
      p.skills=card.querySelector('.pf-skills').value.trim();
      p.role=card.querySelector('.pf-role').value||'worker';
      const vsel=card.querySelector('.pf-voice').value; p.voice = vsel==='__clone__' ? p.voice : vsel;
      p.emoji=card.querySelector('.pf-emoji').value||'🤖';
      p.color=card.querySelector('.pf-color').value;
      savePersonas(); renderPersonaCards(); renderPersonas();
    };
    card.querySelector('.pf-del').onclick=()=>{ if(personas.length<=1)return; personas.splice(idx,1); savePersonas(); renderPersonaCards(); renderPersonas(); };
    grid.appendChild(card);
  });
}
function savePersonas(){ saveJSON('vg_personas',personas);
  // persist locally
  fetch(API.base+'/api/personas',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(personas)}).catch(()=>{});
}
$('#btn-new-persona').onclick=()=>{ personas.push({name:'NewPersona'+(personas.length+1),description:'',system_prompt:'',skills:'',context:[],voice:'alba',color:'#00ff9c',emoji:'🤖',audio:null}); savePersonas(); renderPersonaCards(); renderPersonas(); };
