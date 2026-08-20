/* team.js — agent-to-agent autonomous mission: Planner decomposes in the team
   board and Workers execute in the chat with TTS, then the Planner synthesizes. */

/* Team Workflow now points to Autonomous Mission; auto-chat removed. */
function stopAll(){
  // Stop any in-flight streamed turn and all speech.
  runToken++;                 // invalidates in-flight teamTurn
  if(currentBot){ try{ currentBot.cur.textContent='⏹ stopped'; }catch(e){} currentBot=null; }
  stopTTS();
  $('#message-input').disabled=false;
  $('#btn-send').disabled=false;
  try{ updatePlanPanel('stopped'); }catch(e){}
}

/* Update the Kanban team board (right sidebar). status shown in the header;
   columns: backlog / progress / done. Cards are created/moved via kbAdd/kbMove. */
function updatePlanPanel(status){
  const el=$('#team-plan-status'); if(el) el.textContent = status ? status : 'idle';
}
function _kbCounts(){
  ['backlog','progress','done'].forEach(c=>{
    const b=document.getElementById('kb-'+c); if(b) b.parentElement.querySelector('.kanban-count').textContent=b.children.length;
  });
}
function kbCard(who, task, cls){
  const d=document.createElement('div'); d.className='kcard'+(cls?(' '+cls):'');
  d.innerHTML=`<div class="kc-who">${who||'Agent'}</div><div class="kc-task">${task||''}</div>`;
  return d;
}
function kbAdd(col, who, task, cls){
  const b=document.getElementById('kb-'+col); if(!b) return null;
  const c=kbCard(who,task,cls); b.appendChild(c); _kbCounts(); return c;
}
function kbMove(card, col){ const b=document.getElementById('kb-'+col); if(!b||!card) return; b.appendChild(card); _kbCounts(); }
function kbReset(){ ['backlog','progress','done'].forEach(c=>{ const b=document.getElementById('kb-'+c); if(b) b.innerHTML=''; }); _kbCounts(); }

/* Silent stream: call the LLM and accumulate `acc` WITHOUT rendering a chat
   bubble or queuing any TTS. onChunk (optional) gets live text for panels. */
async function silentStream(persona, userText, systemExtra, onChunk){
  let acc=''; const myToken=++runToken;
  let sys=buildSystem(persona);
  if(systemExtra) sys += '\n\n'+systemExtra;
  const rm=activeRoom();
  const hist=(rm.messages||[]).slice(-24).map(m=>({role: m.role==='user'?'user':'assistant', content:m.content}));
  const msgs=[{role:'system',content:sys}, ...hist, {role:'user',content:userText}];
  const resp=await fetch(API.base+'/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({model:currentModel,messages:msgs})});
  if(!resp.ok) throw new Error('server '+resp.status);
  const reader=resp.body.getReader(); const dec=new TextDecoder(); let buf='';
  while(true){const {value,done}=await reader.read(); if(done)break; buf+=dec.decode(value,{stream:true});
    let i; while((i=buf.indexOf('\n\n'))>=0){const chunk=buf.slice(0,i);buf=buf.slice(i+2);
      if(chunk.startsWith('data: ')){const obj=JSON.parse(chunk.slice(6));
        if(obj.content){acc+=obj.content; if(onChunk) onChunk(acc);}
        if(obj.error) throw new Error(obj.error);}}}
  if(acc&&myToken===runToken){
    persona.context=persona.context||[];
    persona.context.push({role:'user',content:userText});
    persona.context.push({role:'assistant',content:acc,persona:persona.name});
    if(persona.context.length>80) persona.context=persona.context.slice(-80);
    savePersonas();
  }
  return acc;
}

/* The Planner's decomposition step: streams silently into the Team Board
   (Backlog column), no chat bubble, no audio. Returns the plan text. */
async function planTurn(planner, workers, task){
  updatePlanPanel('planning…'); kbReset();
  const panel=$('#team-plan-panel'); if(panel) panel.scrollIntoView({block:'nearest'});
  // Show the task itself as the first backlog card.
  kbAdd('backlog','Task', task, 'progress');
  const plan = await silentStream(planner,
    `Team members available: ${workers.map(w=>w.name).join(', ')}.\nTask: ${task}\n\nBreak this into subtasks and assign each to the best team member BY NAME. Output ONLY a numbered plan, one line per subtask, in this exact format:\n@<PersonName>: <what they should do>\nDo NOT do the work yourself — only delegate.`,
    `You are ${planner.name}, the PLANNER and team lead. Delegate to teammates by name using the @Name: format. Never answer the task yourself.`,
    null);
  updatePlanPanel('✓ plan ready');
  // Render each parsed subtask as a backlog card.
  const subs=parsePlan(plan, workers);
  kbReset(); kbAdd('backlog','Task', task, 'progress');
  subs.forEach(s=> kbAdd('backlog', s.worker.name, s.task));
  if(panel) panel.scrollIntoView({block:'nearest'});
  return plan;
}

async function teamTurn(persona, userText, opts){
  opts=opts||{};
  if(opts.connector) pushMessage('user', opts.connector, opts.connectorWho||null);
  const bot=addBotMsg(persona); currentBot=bot;
  let acc=''; const myToken=++runToken;
  try{
    let sys=buildSystem(persona);
    if(opts.systemExtra) sys += '\n\n'+opts.systemExtra;
    const rm=activeRoom();
    const hist=(rm.messages||[]).slice(-24).map(m=>({role: m.role==='user'?'user':'assistant', content:m.content}));
    const msgs=[{role:'system',content:sys}, ...hist, {role:'user',content:userText}];
    const resp=await fetch(API.base+'/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({model:currentModel,messages:msgs})});
    if(!resp.ok) throw new Error('server '+resp.status);
    const reader=resp.body.getReader(); const dec=new TextDecoder(); let buf='';
    let pending=''; let rafPending=false;
    const flushDOM=()=>{ rafPending=false; if(!currentBot) return; const tail=acc.slice(currentBot.emittedLen); if(tail){ currentBot.cur.style.display=''; currentBot.cur.textContent=tail; } else { currentBot.cur.style.display='none'; } pending=''; $('#messages').scrollTop=$('#messages').scrollHeight; };
    while(true){const {value,done}=await reader.read(); if(done)break; buf+=dec.decode(value,{stream:true});
      let i; while((i=buf.indexOf('\n\n'))>=0){const chunk=buf.slice(0,i);buf=buf.slice(i+2);
        if(chunk.startsWith('data: ')){const obj=JSON.parse(chunk.slice(6));
          if(obj.content){acc+=obj.content; pending=acc; if(!rafPending){ rafPending=true; requestAnimationFrame(flushDOM); } feedTTS(obj.content, persona);}
          if(obj.done) flushTTS(persona);
          if(obj.error){if(currentBot) currentBot.cur.textContent='⚠ '+obj.error;}}}}
    flushDOM();
    if(acc&&myToken===runToken){
      persona.context=persona.context||[];
      persona.context.push({role:'user',content:userText});
      persona.context.push({role:'assistant',content:acc,persona:persona.name});
      if(persona.context.length>80) persona.context=persona.context.slice(-80);
      savePersonas();
      const rm2=activeRoom(); rm2.messages=rm2.messages||[]; rm2.messages.push({role:'assistant',content:acc,persona:persona?.name}); saveJSON('vg_rooms',rooms);
    }
    if(currentBot){ currentBot.acc=acc; currentBot.cur.style.display='none'; currentBot.bubble.style.display=''; currentBot.bubble.textContent=acc; currentBot.plays.appendChild(makeReplayAll(acc, currentBot.persona)); }
  }catch(e){ if(currentBot) currentBot.cur.textContent='⚠ '+(e.message||'error'); }
  finally{ currentBot=null; }
  await drainTTSQueue();
  return acc;
}

/* Parse a Planner's plan into subtasks. Each line "@PersonName: subtask" is one
   delegated task; the named worker is matched case-insensitively. Falls back to
   round-robining the whole plan across workers if no @Name lines are found. */
function parsePlan(text, workers){
  // One subtask per worker — if the Planner lists the same agent twice, merge
  // the tasks so each agent contributes exactly once (cleaner team turns).
  const map=new Map();
  for(const ln of (text||'').split('\n')){
    const m=ln.match(/@([^:]+):\s*(.+)/);
    if(m){
      const name=m[1].trim(); const task=m[2].trim();
      let w=workers.find(x=>x.name.toLowerCase()===name.toLowerCase())
          || workers.find(x=>name.toLowerCase().includes(x.name.toLowerCase()))
          || workers.find(x=>x.name.toLowerCase().includes(name.toLowerCase()));
      if(!w) w=workers[map.size % workers.length];
      if(map.has(w.name)) map.set(w.name, {worker:w, task:map.get(w.name).task+' '+task});
      else map.set(w.name, {worker:w, task});
    }
  }
  if(map.size) return [...map.values()];
  // Fallback: round-robin the whole plan across workers
  const parts=(text||'').split(/\n+/).map(s=>s.trim()).filter(Boolean);
  const src=parts.length?parts:[text];
  return src.map((t,i)=>({worker:workers[i%workers.length], task:t}));
}

async function drainTTSQueue(){
  // Wait until the serial TTS queue is fully drained and no audio is playing.
  // This guarantees each agent's full turn finishes audio before the next agent speaks.
  while(ttsQueue.length || ttsPlaying){ await new Promise(r=>setTimeout(r, 150)); }
}

function initTeam(){
  $('#btn-stop-all').onclick=stopAll;
  // Mission controls now live in the Team Workflow panel.
  $('#btn-mission-start').onclick=startMission;
  $('#btn-mission-stop').onclick=()=>stopMission(true);
  $('#btn-mission-refresh').onclick=loadMissionsList;
  // Render the available agent-tool catalog.
  loadTools();
  document.addEventListener('keydown', (e)=>{ if(e.key==='Escape'){ try{ stopAll(); }catch(_){} } });
}

/* ---------- agent tool catalog + live tool-call log ---------- */
async function loadTools(){
  try{
    const r = await fetch(API.base+'/api/tools');
    const tools = await r.json();
    const list = $('#tools-list');
    if(!list) return;
    $('#tools-count').textContent = `(${tools.length})`;
    list.innerHTML = '';
    tools.forEach(t=>{
      const c=document.createElement('span');
      c.className='tool-chip';
      c.title=t.description;
      c.textContent=t.name;
      list.appendChild(c);
    });
  }catch(e){ /* non-fatal */ }
}
function logToolCall(agent, tool, args, ok){
  const log=$('#tool-call-log'); if(!log) return;
  const arg = args && args.query ? args.query : (args && args.url ? args.url : (args && args.command ? args.command : (args && args.expression ? args.expression : '')));
  // Dedupe: the SSE re-sends recent events each tick, so only log each call once.
  if(!window.__toolLogKeys) window.__toolLogKeys = new Set();
  const key = agent+'|'+tool+'|'+arg+'|'+ok;
  if(__toolLogKeys.has(key)) return;
  __toolLogKeys.add(key);
  const row=document.createElement('div'); row.className='tool-call';
  row.innerHTML=`<span class="tc-name">${tool}</span><span class="tc-arg">${arg||''}</span><span class="${ok?'tc-ok':'tc-err'}">${ok?'✓':'✕'}</span>`;
  // keep latest at top, cap history
  log.prepend(row);
  while(log.children.length>40) log.removeChild(log.lastChild);
}


let __missionStream = null;
let __activeMissionId = null;
let __missionCard = null;
let __missionTaskCards = {};     // task id -> kanban card element
let __emittedTaskResults = {};   // task id -> bool (avoid duplicate chat posts)
let __emittedArtifacts = {};     // artifact id -> bool

/* Render a mission's planned tasks onto the Kanban board. Each task becomes a
   card whose column tracks its status (pending->backlog, running->progress,
   completed/failed/cancelled->done). The board IS the mission's live plan. */
function renderMissionBoard(st, goal){
  const tasks = (st && Array.isArray(st.tasks)) ? st.tasks : [];
  // Ensure a card exists for every task and position it by status.
  tasks.forEach(t=>{
    let card = __missionTaskCards[t.id];
    if(!card){
      card = kbCard(t.agent || 'Agent', t.title || t.id, '');
      __missionTaskCards[t.id] = card;
    } else {
      card.querySelector('.kc-who').textContent = t.agent || 'Agent';
      card.querySelector('.kc-task').textContent = t.title || t.id;
    }
    const col = (t.status==='running'||t.status==='recovering') ? 'progress'
              : (t.status==='completed'||t.status==='failed'||t.status==='cancelled') ? 'done'
              : 'backlog';
    card.classList.remove('thinking','progress','done');
    if(col==='progress') card.classList.add('progress');
    if(col==='done') card.classList.add('done');
    kbMove(card, col);
  });
}

/* When a task completes, post its result as a clear chat message (one per task).
   Skips if already posted, and skips tasks with no useful result. If the result
   is JSON, prefer a 'summary'/'result'/'output' field over the raw blob. */
function maybeEmitTaskResult(t){
  if(t.status!=='completed') return;
  if(__emittedTaskResults[t.id]) return;
  __emittedTaskResults[t.id] = true;
  let body = (t.result && t.result.trim()) ? t.result.trim() : (t.verification || '');
  if(!body) return;
  // If the stored result is JSON, surface the human-readable part only.
  if(body.trim().startsWith('{') || body.trim().startsWith('[')){
    try{
      const o = JSON.parse(body);
      body = o.summary || o.result || o.output || o.text || o.content || body;
    }catch(e){ /* keep raw */ }
  }
  const who = t.agent && personaByName(t.agent) ? t.agent : (t.agent || 'Worker');
  pushMessage('assistant', body, who);
}

/* When an artifact is recorded, post a chat message with a link to open it. */
function maybeEmitArtifact(a, st){
  if(__emittedArtifacts[a.id]) return;
  __emittedArtifacts[a.id] = true;
  const label = a.kind || 'artifact';
  const who = a.agent || st.planner || 'Mission';
  let link = a.path || '';
  // If it's a server path we can serve, turn it into a clickable link.
  const assetUrl = link ? ('/api/autonomous/artifact?path='+encodeURIComponent(link)) : '';
  const text = assetUrl
    ? `📎 ${label}: ${a.path.split('/').pop()}\n${assetUrl}`
    : `📎 ${label}`;
  pushMessage('assistant', text, who);
}
async function startMission(goalOverride){
  const goal = (goalOverride || $('#mission-goal').value || '').trim();
  if(!goal){ alert('Enter a mission goal first.'); return; }
  const room = activeRoom();
  const lineup = roomPersonas(room).map(personaByName).filter(Boolean);
  if(lineup.length<2){ alert('Add at least 2 personas to the room for a team mission.'); return; }
  $('#mission-state').innerHTML = 'Starting mission…';
  kbReset();
  __missionTaskCards = {}; __emittedTaskResults = {}; __emittedArtifacts = {};
  __missionCard = kbAdd('progress','Mission (Planner)', goal, 'progress thinking');
  try{
    const res = await fetch(API.base+'/api/autonomous/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({goal, room_personas: lineup})});
    const data = await res.json();
    if(!res.ok || !data.ok){ throw new Error(data.error||('HTTP '+res.status)); }
    __activeMissionId = data.mission_id;
    $('#mission-state').innerHTML = `Mission <b>${data.mission_id}</b> · <b id="mission-status">${data.status||'running'}</b> · planner <b>${data.planner||''}</b>`;
    if(__missionStream){ try{__missionStream.close();}catch(e){} }
    __missionStream = new EventSource(API.base + data.stream_url);
    __missionStream.onmessage = (ev)=>{
      try{
        const st = JSON.parse(ev.data);
        const statusEl = $('#mission-status');
        if(statusEl) statusEl.textContent = st.status || statusEl.textContent;
        // Render the mission's planned tasks onto the Kanban board.
        renderMissionBoard(st, goal);
        // Surface any tool calls (from the live event stream) in the tool-call log.
        if(Array.isArray(st.events)){
          st.events.forEach(ev=>{
            if(ev.event==='tool.call' && ev.data && ev.data.tool){
              const ok = !ev.data.result || !ev.data.result.error;
              logToolCall(ev.agent, ev.data.tool, ev.data.args, ok);
            }
          });
        }
        if(__missionCard){
          if(st.status==='completed'||st.status==='failed'||st.status==='cancelled'){
            kbMove(__missionCard,'done'); __missionCard.classList.remove('thinking','progress'); __missionCard.classList.add('done');
          } else { kbMove(__missionCard,'progress'); }
        }
        // Push a chat message for each freshly-completed task (clear output + artifacts).
        if(Array.isArray(st.tasks)) st.tasks.forEach(t=>maybeEmitTaskResult(t));
        if(Array.isArray(st.artifacts)) st.artifacts.forEach(a=>maybeEmitArtifact(a, st));
        if(st.status==='completed' || st.status==='failed' || st.status==='cancelled'){
          stopMission(false);
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

