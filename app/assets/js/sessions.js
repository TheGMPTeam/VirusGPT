/* sessions.js — chat rooms as a managed list in the LEFT sidebar.
   Switch / create / rename / delete (remove) / save. Replaces the old
   single dropdown. Each room keeps its own messages + persona lineup. */

function activeRoom(){ return rooms.find(r=>r.name===currentRoom) || rooms[0]; }
// Assigned persona lineup for a room (migrate old rooms that lack it).
function roomPersonas(rm){
  if(!rm) return [];
  if(!Array.isArray(rm.personas)) rm.personas = personas.map(p=>p.name); // default: all
  // keep only names that still exist
  rm.personas = rm.personas.filter(n=>personaByName(n));
  if(!rm.personas.length) rm.personas = personas.length?[personas[0].name]:[];
  return rm.personas;
}
function pushMessage(role, content, personaName){ const rm=activeRoom(); rm.messages=rm.messages||[]; rm.messages.push({role,content,persona:personaName}); saveJSON('vg_rooms',rooms); if(role!=='system') addMsgEl(role,content, personaByName(personaName)); else addMsgEl('system',content); }

function renderSessions(){
  const list = $('#session-list');
  if(!list) return;
  list.innerHTML = '';
  rooms.forEach(r=>{
    const row = document.createElement('div');
    row.className = 'session-row' + (r.name===currentRoom?' active':'');
    row.innerHTML = `<span class="sname">${r.name}</span>`;
    const rename = document.createElement('button');
    rename.className='sbtn'; rename.textContent='✎'; rename.title='Rename';
    rename.onclick=(e)=>{ e.stopPropagation(); const nn=prompt('Rename session:', r.name); if(nn && nn.trim() && !rooms.some(x=>x.name===nn.trim())){ r.name=nn.trim(); if(currentRoom===r.name) currentRoom=r.name; saveJSON('vg_rooms',rooms); renderSessions(); if(r.name===currentRoom) switchRoom(r.name); } };
    const del = document.createElement('button');
    del.className='sbtn'; del.textContent='🗑'; del.title='Remove';
    del.onclick=(e)=>{ e.stopPropagation(); if(rooms.length<=1){ alert('Keep at least one session.'); return; } if(!confirm('Remove session "'+r.name+'"?')) return;
      const idx=rooms.indexOf(r); rooms.splice(idx,1);
      if(currentRoom===r.name){ currentRoom=rooms[0].name; switchRoom(currentRoom); }
      saveJSON('vg_rooms',rooms); renderSessions(); };
    row.appendChild(rename); row.appendChild(del);
    row.onclick=()=>switchRoom(r.name);
    list.appendChild(row);
  });
}
function switchRoom(name){ currentRoom=name; $('#messages').innerHTML=''; (activeRoom().messages||[]).forEach(m=>addMsgEl(m.role,m.content, personaByName(m.persona))); renderSessions(); renderPersonas(); }
function newSession(){
  let n=1, name;
  do { name='chat-'+n; n++; } while(rooms.some(r=>r.name===name));
  const rm={name, messages:[], personas: personas.map(p=>p.name)};
  rooms.push(rm); currentRoom=name; saveJSON('vg_rooms',rooms);
  $('#messages').innerHTML=''; renderSessions(); renderPersonas();
  // Fresh session: streaming auto-play stays off until the speaker is enabled.
  sessionAutoPlay=false; stopTTS();
  pushMessage('system','✨ New session: '+name);
}
// Wire the New button (declared in markup) once DOM is ready.
function initSessions(){ $('#btn-new-session').onclick=newSession; renderSessions(); }
