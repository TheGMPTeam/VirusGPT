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
    pushMessage('system','Commands:\n• /new — start a fresh session\n• /clear — clear this session\n• /team <task> — launch an agent-to-agent team round (also: @team, #team, or "team:")\n• /studio <prompt> — launch an image-team mission that renders a branding/asset kit via ComfyUI (alias /mission)\n• /help — list commands\n\nTips:\n• ✨ Improve button — rewrites your typed draft into a cleaner, improved version\n• 🎨 Image button — generate one image from the typed prompt via ComfyUI (Studio)\n• Type / @ # in the box for command/persona/tag autocomplete\n• Type free text and AI completion suggestions appear above the box (Tab to accept)');
    return;
  }
  if(cmd==='clear'){
    const rm=activeRoom(); rm.messages=[]; saveJSON('vg_rooms',rooms);
    $('#messages').innerHTML='';
    // A fresh session is quiet for streaming auto-play until the speaker is on.
    sessionAutoPlay=false; stopTTS();
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

/* Generate an image from the current input-box text via ComfyUI (/api/generate)
   and render it inline in the chat. Falls back to a clear error if ComfyUI is
   offline. If promptOverride is given (natural-language image request), use it
   instead of the box. The Studio persona is the artist. */
async function generateImageFromInput(promptOverride){
  const box=$('#message-input');
  const prompt=(promptOverride && promptOverride.trim()) || (box.value||'').trim();
  if(!prompt){ alert('Type an image description first, then click 🎨 Image.'); return; }
  const btn=$('#btn-gen-image'); if(btn){ btn.disabled=true; btn.textContent='🎨…'; }
  if(!promptOverride) pushMessage('user', '🎨 '+prompt, 'YOU');
  // A minimal bot placeholder so the user sees activity.
  const bot=addBotMsg(personaByName('Studio')||selectedPersonaObj()||personas[0]);
  currentBot=bot; let myToken=++runToken;
  bot.cur.style.display=''; bot.cur.textContent='⏳ rendering in ComfyUI…';
  try{
    const resp=await fetch(API.base+'/api/generate',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({prompt, model:'dreamshaper_8.safetensors', steps:22, cfg_scale:7.5, width:512, height:512})});
    const data=await resp.json();
    if(!resp.ok || data.status!=='completed'){
      const err=(data && (data.error||data.detail)) || ('HTTP '+resp.status);
      bot.cur.textContent='⚠ '+err;
      return;
    }
    // Render the image(s) inline.
    bot.cur.style.display='none';
    if(currentBot){ currentBot.cur.style.display='none'; currentBot.bubble.style.display=''; }
    pushImageMessage('assistant', [data.url], data.prompt||prompt, 'Studio');
    box.value=''; box.style.height='auto';
  }catch(e){
    if(bot) bot.cur.textContent='⚠ '+(e.message||'image generation failed');
  }finally{
    currentBot=null;
    if(btn){ btn.disabled=false; btn.textContent='🎨 Image'; }
  }
}

/* Launch an autonomous "image team" mission (planner + Studio artist + Cipher
   reviewer) that renders a branding/asset kit via ComfyUI. Works from normal chat
   via the /studio or /mission slash command. Renders each completed image inline
   as the mission streams. */
async function startImageMission(userGoal){
  // If the user gave a vague goal, expand it into an explicit per-asset checklist
  // so the (small) planner creates ONE task per asset instead of collapsing them.
  const ASSETS = ['primary logo','app icon','hero banner','social/profile banner','loading screen','UI accent: divider','UI accent: button glow','UI accent: chat bubble'];
  const goal = (userGoal && userGoal.trim())
    ? userGoal.trim()
    : 'Generate a full futuristic branding kit for the VirusGPT app in our cyber-neon style: dark background, neon green #00ff9c primary, cyan #23e0ff secondary, magenta #ff2bd6 accent, tech/matrix vibe.';
  const checklist = ASSETS.map((a,i)=>`${i+1}) Render the ${a} for VirusGPT (call render_image, cyber-neon style).`).join('\n');
  const fullGoal = `${goal}\n\nCreate exactly these assets, one task each:\n${checklist}\nFor every asset you MUST call the render_image tool. Do not skip any.`;

  const btn=$('#btn-send'); if(btn) btn.disabled=true;
  const bot=addBotMsg(personaByName('Studio')||selectedPersonaObj()||personas[0]);
  currentBot=bot; bot.cur.style.display=''; bot.cur.textContent='⏳ launching image team mission…';
  try{
    const room=activeRoom();
    const lineup=roomPersonas(room).map(personaByName).filter(Boolean);
    const hasStudio = lineup.some(p=>p.name==='Studio');
    const team = lineup.length>=2 ? lineup : [
      personaByName('VirusGPT'), personaByName('Studio'), personaByName('Cipher')
    ].filter(Boolean);
    if(!hasStudio && !team.some(p=>p.name==='Studio')) team.push(personaByName('Studio'));
    const start=await fetch(API.base+'/api/autonomous/start',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({goal: fullGoal, room_personas: team.map(p=>({name:p.name, role:(p.name==='Studio'?'worker':(p.role||'planner')), system_prompt:p.system_prompt, skills:p.skills||'', voice:p.voice||'', tools:p.tools||['render_image','web_search','memory_query','calc']}))})});
    const sd=await start.json();
    if(!start.ok || !sd.ok){ bot.cur.textContent='⚠ '+(sd.error||'mission start failed'); return; }
    const mid=sd.mission_id;
    bot.cur.textContent=`🚀 Mission ${mid} running — rendering assets…`;
    // Stream the mission SSE and render images as tasks complete.
    const es=new EventSource(API.base+'/api/autonomous/stream/'+mid);
    const seen={};
    es.onmessage=ev=>{
      try{
        const st=JSON.parse(ev.data);
        if(st.event==='end'){ es.close(); if(bot){bot.cur.style.display='none';} return; }
        (st.tasks||[]).forEach(t=>{
          if(t.status==='completed' && !seen[t.id]){
            seen[t.id]=true;
            let r=t.result; try{ if(typeof r==='string') r=JSON.parse(r); }catch(_){}
            if(r && Array.isArray(r.generated_images) && r.generated_images.length){
              pushImageMessage('assistant', r.generated_images, (t.title||'Studio asset'), 'Studio');
            }
          }
        });
      }catch(_){}
    };
    es.onerror=()=>{ try{es.close();}catch(_){} if(bot) bot.cur.style.display='none'; };
  }catch(e){
    if(bot) bot.cur.textContent='⚠ '+(e.message||'mission failed');
  }finally{
    currentBot=null;
    if(btn) btn.disabled=false;
  }
}

async function send(text){
  text=(text||'').trim(); if(!text) return;
  $('#cmd-popup').classList.remove('show'); cpItems=[]; cpActive=-1; sugItems=[]; cpMode='none';

  // Team rounds now run as a visible chat team-round (each agent takes a turn).
  if(/^(?:\/team|@team|team:|#team)\b/i.test(text)){
    const goal = extractTeamGoal(text) || text.replace(/^(?:\/team|@team|team:|#team)\b\s*/i,'').trim();
    const room = activeRoom();
    const lineup = roomPersonas(room).map(personaByName).filter(Boolean);
    if(lineup.length < 2){ alert('Add at least 2 personas to the room for a team round.'); return; }
    pushMessage('user', text);
    await runTeamRound(goal);
    return;
  }

  // /studio <prompt>  (alias /mission) — launch an autonomous image-team mission
  // that renders a full branding/asset kit via ComfyUI (Studio persona).
  if(/^\/(?:studio|mission)\b/i.test(text)){
    const goal = text.replace(/^\/(?:studio|mission)\b\s*/i,'').trim() ||
      'Generate a full futuristic branding kit for the VirusGPT app (logo, app icon, hero banner, social banner, loading screen, and UI accents) in our cyber-neon style: dark background, neon green #00ff9c primary, cyan #23e0ff secondary, magenta #ff2bd6 accent, tech/matrix vibe.';
    pushMessage('user', text);
    await startImageMission(goal);
    return;
  }

  // Natural-language image trigger: a normal chat message that asks to generate
  // images should just work — route to Studio (single image or a full image team).
  const multi = /\b(generate images?|create (?:a |an )?(?:team|branding|image|logo|asset)|banners?|logos?|branding kit|everything else|image (?:team|kit)|set of images|render (?:a |an )?(?:image|logo|banner))\b/i;
  const single = /\b(make|generate|create|draw|render|paint)(?: me)? (?:a |an |some )?(?:image|picture|photo|artwork|render|logo|banner|icon|wallpaper|avatar)\b/i;
  if(multi.test(text)){
    pushMessage('user', text);
    await startImageMission(text);
    return;
  }
  if(single.test(text) && !multi.test(text)){
    // single image request -> use the 🎨 /api/generate path
    pushMessage('user', text);
    await generateImageFromInput(text);
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
    if(currentBot){ currentBot.acc=acc; currentBot.cur.style.display='none'; currentBot.bubble.style.display=''; currentBot.bubble.textContent=acc; buildSentencePlays(currentBot.plays, acc, currentBot.persona); }
  }catch(e){ if(myToken===runToken&&e.name!=='AbortError'){ if(currentBot) currentBot.cur.textContent='⚠ '+(e.message||'error'); } }
  finally{ currentBot=null; $('#btn-send').disabled=false; }
}
