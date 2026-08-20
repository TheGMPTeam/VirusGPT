/* chat.js — sending messages, persona routing, slash commands, and the
   streaming chat turn that renders a live bot bubble with TTS. */

// Pick the persona that should answer, given routing mode + the user's text.
async function choosePersona(text){
  const rm=activeRoom(); const lineup=roomPersonas(rm);
  if(!lineup.length) return personas[0]||null;
  // 1) Mention routing (TalkWithMe style): "@Name", "Name, ...", "..., Name?", "what do you think, Name"
  const mention =
      text.match(/@([\w\-]+)/) ||                         // @Cipher
      text.match(/^\s*([A-Z][\w\-]+)\s*,/) ||             // Cipher, tell me...
      text.match(/[,\s]?\b([A-Z][\w\-]+)[,?]?\s*$/) ||    // ...what do you think, Cipher?
      text.match(/what do you think,?\s*([A-Z][\w\-]+)/i);// what do you think, Cipher
  if(mention){
    const cand=(mention[1]||'').trim().toLowerCase();
    const hit=lineup.map(personaByName).find(p=>p && (p.name.toLowerCase()===cand || p.name.toLowerCase().startsWith(cand)));
    if(hit){ selectedPersona=hit.name; return hit; }
  }
  const mode=whoMode();
  if(mode==='selected'){ const sel=selectedPersonaObj()||personaByName(lineup[0]); return sel; }
  if(mode==='random'){ const n=lineup[Math.floor(Math.random()*lineup.length)]; return personaByName(n); }
  // mode==='router' (LLM decides): ask the model to name the best persona.
  // OPTIMIZATION: a single-persona room needs no routing decision.
  if(lineup.length<=1) return personaByName(lineup[0])||personas[0]||null;
  try{
    const roster=lineup.map(personaByName).filter(Boolean).map(p=>`- ${p.name}: ${(p.description||'')}`).join('\n');
    const meta=[{role:'system',content:'You are a chat router. Given the room personas and the user message, reply with ONLY the exact name of the single best persona to answer (no explanation). Personas:\n'+roster},
                {role:'user',content:text}];
    const r=await fetch(API.base+'/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({model:currentModel,messages:meta})});
    if(r.ok){
      // /api/chat returns SSE (data: {content}), not JSON — accumulate the stream.
      const reader=r.body.getReader(); const dec=new TextDecoder(); let buf=''; let pick='';
      while(true){ const {value,done}=await reader.read(); if(done) break; buf+=dec.decode(value,{stream:true});
        let i; while((i=buf.indexOf('\n\n'))>=0){ const chunk=buf.slice(0,i); buf=buf.slice(i+2);
          if(chunk.startsWith('data: ')){ try{ const o=JSON.parse(chunk.slice(6)); if(o.content) pick+=o.content; }catch(_){} } } }
      pick=pick.trim().replace(/["'.]/g,'');
      const hit=lineup.map(personaByName).find(p=>p && (p.name.toLowerCase()===pick.toLowerCase() || pick.toLowerCase().includes(p.name.toLowerCase())));
      if(hit) return hit;
    }
  }catch(e){ /* fall through to default */ }
  return personaByName(lineup[0]) || personas[0] || null;
}

/* Slash commands — fully client-side. Supported:
   /new    start a fresh session   (alias of the sidebar New button)
   /clear  clear the current session's messages
   /team <task>  launch an agent-to-agent team round
   /help   list commands */
async function runSlashCommand(raw){
  const cmd = raw.replace(/^\//,'').trim().toLowerCase();
  if(cmd==='help' || cmd===''){
    pushMessage('system', 'Commands:\n• /new — start a fresh session\n• /clear — clear this session\n• /team <task> — launch an agent-to-agent team round (also: @team, #team, or "team:")\n• /help — list commands\n\nTips:\n• ✨ Improve button — rewrites your typed draft into a cleaner, improved version\n• Type / @ # in the box for command/persona/tag autocomplete\n• Type free text and AI completion suggestions appear above the box (Tab to accept)');
    return;
  }
  if(cmd==='clear'){
    const rm=activeRoom(); rm.messages=[]; saveJSON('vg_rooms',rooms);
    $('#messages').innerHTML='';
    pushMessage('system','🧹 Session cleared.');
    return;
  }
  if(cmd==='new'){ newSession(); return; }
  pushMessage('system','⚠ unknown command: /'+cmd+'  (try /help)');
}

function extractTeamGoal(text){
  const m = String(text||'').trim().match(/^(?:\/team|@team|team:|#team)\b\s*(.*)$/i);
  return m ? (m[1] || '').trim() : '';
}

async function send(text){
  text=(text||'').trim(); if(!text) return;
  $('#cmd-popup').classList.remove('show'); cpItems=[]; cpActive=-1; sugItems=[]; cpMode='none';

  // Team rounds now run through Autonomous Mission (right-side panel).
  if(/^(?:\/team|@team|team:|#team)\b/i.test(text)){
    const goal = extractTeamGoal(text) || text.replace(/^(?:\/team|@team|team:|#team)\b\s*/i,'').trim();
    const room = activeRoom();
    const lineup = roomPersonas(room).map(personaByName).filter(Boolean);
    if(lineup.length < 2){ alert('Add at least 2 personas to the room for a team mission.'); return; }
    pushMessage('user', text);
    $('#mission-goal').value = goal;
    await startMission(goal);
    return;
  }

  // Slash commands: /new, /clear, /help
  if(text.startsWith('/')){
    await runSlashCommand(text);
    return;
  }
  const persona=await choosePersona(text);
  const sys=buildSystem(persona);
  // Each persona keeps its OWN context (isolated agent memory), not the shared
  // room history. Window it to bound tokens sent to the model.
  const WINDOW = 40;
  const ctx=(persona?.context||[]).slice(-WINDOW);
  const msgs=[{role:'system',content:sys}, ...ctx.map(m=>({role:m.role,content:m.content})), {role:'user',content:text}];
  pushMessage('user',text);
  const bot=addBotMsg(persona); currentBot=bot; let acc='',gotError=false; const myToken=++runToken; currentAbort=new AbortController();
  $('#btn-send').disabled=true;
  try{
    const resp=await fetch(API.base+'/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({model:currentModel,messages:msgs}),signal:currentAbort.signal});
    if(!resp.ok) throw new Error('server '+resp.status);
    const reader=resp.body.getReader(); const dec=new TextDecoder(); let buf='';
    let pending=''; let rafPending=false;
    // Watchdog: if the model stalls (no token within RUN_TIMEOUT_MS), abort and
    // surface a clear error instead of hanging on "⏳" forever.
    let stallTimer=setTimeout(()=>{ try{ currentAbort.abort(); }catch(_){} if(currentBot) currentBot.cur.textContent='⚠ model did not respond in time (try a different model in Settings)'; }, RUN_TIMEOUT_MS);
    const flushDOM=()=>{ rafPending=false; if(!currentBot) return; const tail=acc.slice(currentBot.emittedLen); if(tail){ currentBot.cur.style.display=''; currentBot.cur.textContent=tail; } else { currentBot.cur.style.display='none'; } pending=''; $('#messages').scrollTop=$('#messages').scrollHeight; };
    while(true){const {value,done}=await reader.read(); if(done)break; clearTimeout(stallTimer); buf+=dec.decode(value,{stream:true});
      let i; while((i=buf.indexOf('\n\n'))>=0){const chunk=buf.slice(0,i);buf=buf.slice(i+2);
        if(chunk.startsWith('data: ')){const obj=JSON.parse(chunk.slice(6));
          if(obj.content){acc+=obj.content; pending=acc; if(!rafPending){ rafPending=true; requestAnimationFrame(flushDOM); } feedTTS(obj.content, persona);}
          if(obj.done) flushTTS(persona);
          if(obj.error){gotError=true; if(currentBot) currentBot.cur.textContent='⚠ '+obj.error;}}}}
    flushDOM();
    clearTimeout(stallTimer);
    if(acc&&myToken===runToken){
      // Record the turn in the ANSWERING persona's own isolated context.
      persona.context=persona.context||[];
      persona.context.push({role:'user',content:text});
      persona.context.push({role:'assistant',content:acc,persona:persona.name});
      if(persona.context.length>WINDOW*2) persona.context=persona.context.slice(-WINDOW*2);
      savePersonas();
      // ...and into the shared room view for display.
      const rm=activeRoom(); rm.messages=rm.messages||[]; rm.messages.push({role:'assistant',content:acc,persona:persona?.name}); saveJSON('vg_rooms',rooms);
    }
    if(currentBot){ currentBot.acc=acc; currentBot.cur.style.display='none'; currentBot.bubble.style.display=''; currentBot.bubble.textContent=acc; currentBot.plays.appendChild(makeReplayAll(acc, currentBot.persona)); }
  }catch(e){ if(myToken===runToken&&e.name!=='AbortError'){ if(currentBot) currentBot.cur.textContent='⚠ '+(e.message||'error'); } }
  finally{ currentBot=null; $('#btn-send').disabled=false; }
}
