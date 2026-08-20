/* team.js — agent-to-agent auto-chat: Planner decomposes (silent, in the right
   panel) and Workers execute in the chat with TTS, then the Planner synthesizes. */

let autoChatActive=false;
let autoChatTimer=null;

async function startAutoChat(){
  // Auto-chat now runs as an AUTONOMOUS TEAM: a Planner decomposes the task and
  // delegates subtasks to the Workers, then synthesizes their results.
  const task = ($('#message-input').value||'').trim() ||
    'Collaboratively solve: how should a small startup securely launch its first AI product? Each agent should contribute its own specialty.';
  $('#message-input').value='';
  $('#message-input').style.height='auto';
  await runAutoTeam(task);
}
function stopAutoChat(){
  autoChatActive=false;
  clearTimeout(autoChatTimer);
  autoChatTimer=null;
  $('#btn-auto-chat').classList.remove('hidden');
  $('#btn-auto-stop').classList.add('hidden');
  $('#message-input').disabled=false;
  $('#btn-send').disabled=false;
}
/* Global stop: halts a running auto-chat team, kills any in-flight streaming
   turn, and stops all TTS playback. Safe to press any time. */
function stopAll(){
  // 1) kill any running team / streamed turn
  autoChatActive=false;
  clearTimeout(autoChatTimer);
  autoChatTimer=null;
  runToken++;                 // invalidates in-flight silentStream/teamTurn
  if(currentBot){ try{ currentBot.cur.textContent='⏹ stopped'; }catch(e){} currentBot=null; }
  // 2) stop all speech
  stopTTS();
  // 3) restore UI controls
  $('#btn-auto-chat').classList.remove('hidden');
  $('#btn-auto-stop').classList.add('hidden');
  $('#message-input').disabled=false;
  $('#btn-send').disabled=false;
  // 4) reflect in the plan panel if visible
  try{ updatePlanPanel('stopped'); }catch(e){}
}

/* Update the dedicated "Team Plan" sidebar panel (the Planner's decomposition
   is shown here live, NOT in the chat, and never spoken aloud). */
function updatePlanPanel(status, text){
  const panel=$('#team-plan-panel'); if(!panel) return;
  panel.classList.remove('hidden');
  $('#team-plan-status').textContent = status ? status : 'idle';
  if(text!=null) $('#team-plan-text').textContent = text;
}

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

/* The Planner's decomposition step: streams silently into the Team Plan panel
   (no chat bubble, no audio). Returns the plan text. */
async function planTurn(planner, workers, task){
  updatePlanPanel('planning…', 'The Planner is decomposing the task…');
  const panel=$('#team-plan-panel'); if(panel) panel.scrollIntoView({block:'nearest'});
  const plan = await silentStream(planner,
    `Team members available: ${workers.map(w=>w.name).join(', ')}.\nTask: ${task}\n\nBreak this into subtasks and assign each to the best team member BY NAME. Output ONLY a numbered plan, one line per subtask, in this exact format:\n@<PersonName>: <what they should do>\nDo NOT do the work yourself — only delegate.`,
    `You are ${planner.name}, the PLANNER and team lead. Delegate to teammates by name using the @Name: format. Never answer the task yourself.`,
    (live)=>updatePlanPanel('planning…', live));
  updatePlanPanel('✓ plan ready', plan);
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

/* The autonomous team: 1) Planner decomposes the task in the side panel (silent,
   no chat bubble), 2) each Worker executes its subtask in the chat (with TTS),
   3) Planner synthesizes a final answer (chat + TTS). */
async function runAutoTeam(task){
  const rm=activeRoom();
  const lineup=roomPersonas(rm).map(personaByName).filter(Boolean);
  if(lineup.length<2){ alert('Add at least 2 personas to the room for a team (a Planner + Workers).'); updatePlanPanel('idle'); return; }
  autoChatActive=true;
  $('#btn-auto-chat').classList.add('hidden');
  $('#btn-auto-stop').classList.remove('hidden');
  $('#message-input').disabled=true;
  $('#btn-send').disabled=true;
  const planner = lineup.find(p=>(p.role||'worker')==='planner') || lineup[0];
  const workers = lineup.filter(p=>p!==planner);
  pushMessage('user', task, 'YOU');
  // 1) Planner decomposes the task into a delegated plan (shown in side panel, silent)
  const plan = await planTurn(planner, workers, task);
  if(!autoChatActive){ updatePlanPanel('stopped'); return; }
  // 2) Each Worker executes its assigned subtask (chat + TTS)
  const subs=parsePlan(plan, workers);
  const results=[];
  for(const s of subs){
    if(!autoChatActive){ updatePlanPanel('stopped'); return; }
    const r=await teamTurn(s.worker, s.task,
      { systemExtra:`You are ${s.worker.name}. The team lead ${planner.name} assigned you this subtask. Do it yourself, as ${s.worker.name}, and report your findings concisely. Do not narrate the other agents.`,
        connector:`${planner.name} → ${s.worker.name}: ${s.task}`, connectorWho:'System' });
    if(r) results.push({name:s.worker.name, text:r});
    await new Promise(r=>setTimeout(r, 400));
  }
  if(!autoChatActive){ updatePlanPanel('stopped'); return; }
  // 3) Planner synthesizes the final answer from the workers' reports (chat + TTS)
  const synth=`Original task:\n${task}\n\nTeam reports:\n`+results.map(x=>`### ${x.name}\n${x.text}`).join('\n\n')+
    `\n\nNow synthesize everything into ONE coherent final answer to the original task, speaking as ${planner.name}.`;
  await teamTurn(planner, synth,
    { systemExtra:`You are ${planner.name}, the team lead. Combine your workers' reports into a single final answer to the task, speaking as ${planner.name}.`,
      connector:`🧠 ${planner.name} (Planner) is synthesizing the final answer…`, connectorWho:'System' });
  updatePlanPanel('✓ done');
  stopAutoChat();
}
async function drainTTSQueue(){
  // Wait until the serial TTS queue is fully drained and no audio is playing.
  // This guarantees each agent's full turn finishes audio before the next agent speaks.
  while(ttsQueue.length || ttsPlaying){ await new Promise(r=>setTimeout(r, 150)); }
}

function initTeam(){
  $('#btn-auto-chat').onclick=startAutoChat;
  $('#btn-auto-stop').onclick=stopAutoChat;
  $('#btn-stop-all').onclick=stopAll;
  document.addEventListener('keydown', (e)=>{ if(e.key==='Escape'){ try{ stopAll(); }catch(_){} } });
}
