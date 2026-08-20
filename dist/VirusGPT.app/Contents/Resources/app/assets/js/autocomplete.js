/* autocomplete.js — the / @ # command/persona/tag popup AND the free-text
   AI suggestion chips that appear above the input bar. */

/* Two modes share one popup:
   • STATIC mode — a `/`, `@` or `#` token is active: list commands/personas/tags.
   • AI mode — no trigger token but free text typed: show AI completion chips. */
let cpActive = -1, cpItems = [];          // static token items
let sugItems = [], cpMode = 'none';        // 'none' | 'static' | 'ai'
let sugTimer = null;

function cpToken(text){
  // find all trigger runs; take the last one
  const re = /(^|\s)([@/#])(\w*)$/;
  const m = text.match(re);
  if(!m) return null;
  return { kind: m[2], q: m[3].toLowerCase() };
}
function staticItems(tok){
  let items = [];
  if(tok.kind === '/'){
    items = [
      {key:'/team', desc:'Launch an agent-to-agent team round (also @team, team:, #team)', kind:'slash'},
      {key:'/new', desc:'Start a fresh session', kind:'slash'},
      {key:'/clear', desc:'Clear the current session messages', kind:'slash'},
      {key:'/heartbeat', desc:'Send a 30s watchdog ping', kind:'slash'},
      {key:'/help', desc:'List all commands', kind:'slash'},
    ];
  } else if(tok.kind === '@'){
    const rm = activeRoom();
    const lineup = roomPersonas(rm).map(personaByName).filter(Boolean);
    items = lineup.map(p=>({key:'@'+p.name, desc:(p.description||'Persona')+(p.role==='planner'?' (Planner)':''), kind:'at', emoji:p.emoji}));
    items.unshift({key:'@team', desc:'Run a team round on the typed message', kind:'at', emoji:'🤖'});
  } else if(tok.kind === '#'){
    items = [
      {key:'#team', desc:'Keyword alias for a team round', kind:'hash'},
      {key:'#security', desc:'Tag: security / defensive topic', kind:'hash'},
      {key:'#brainstorm', desc:'Tag: open ideation', kind:'hash'},
      {key:'#research', desc:'Tag: gather & summarize', kind:'hash'},
      {key:'#summarize', desc:'Tag: condense the topic', kind:'hash'},
    ];
  }
  if(tok.q){ items = items.filter(it => it.key.toLowerCase().includes(tok.q) || (it.desc||'').toLowerCase().includes(tok.q)); }
  return items;
}
function cpRender(){
  const box = $('#cmd-popup');
  if(cpMode === 'static'){
    if(!cpItems.length){ box.innerHTML = '<div class="cp-empty">no matches</div>'; box.classList.add('show'); return; }
    box.innerHTML = cpItems.map((it,i)=>
      `<div class="cp-item kind-${it.kind} ${i===cpActive?'active':''}" data-i="${i}">
         <span class="cp-key">${it.emoji?it.emoji+' ':''}${it.key}</span>
         <span class="cp-desc">${it.desc}</span>
       </div>`).join('');
    box.querySelectorAll('.cp-item').forEach(el=>{
      el.onmousedown = (e)=>{ e.preventDefault(); cpApply(parseInt(el.dataset.i)); };
    });
    box.classList.add('show');
  } else if(cpMode === 'ai'){
    box.innerHTML = '<div class="cp-ai-label">✦ AI suggestions</div>'
      + (sugItems.length
          ? '<div class="cp-sug">'+sugItems.map((s,i)=>
              `<button data-i="${i}">${escapeHtml(s)}<span class="cp-sug-sub">tab / click to accept</span></button>`).join('')
            +'</div>'
          : '<div class="cp-loading"><span class="cp-spin"></span>thinking…</div>');
    box.querySelectorAll('.cp-sug button').forEach(el=>{
      el.onmousedown = (e)=>{ e.preventDefault(); applySuggestion(el.dataset.i); };
    });
    box.classList.add('show');
  } else {
    box.classList.remove('show');
  }
}
function cpBuild(){
  const val = $('#message-input').value;
  const tok = cpToken(val);
  if(tok){
    cpMode = 'static'; cpItems = staticItems(tok); cpActive = cpItems.length ? 0 : -1;
    sugItems = []; clearTimeout(sugTimer);
    cpRender();
  } else {
    // No trigger token: show AI completions once there's real free text.
    cpItems = []; cpMode = 'none'; clearTimeout(sugTimer);
    const trimmed = val.trim();
    if(trimmed.length >= 3 && !val.endsWith(' ') && trimmed.split(/\s+/).length >= 2){
      cpMode = 'ai'; sugItems = [];
      cpRender(); // show loading spinner immediately
      sugTimer = setTimeout(()=>debouncedSuggest(trimmed), 450);
    } else {
      $('#cmd-popup').classList.remove('show');
    }
  }
}
async function debouncedSuggest(text){
  if(cpMode !== 'ai' || $('#message-input').value.trim() !== text) return; // context changed
  try{
    const r = await fetch(API.base+'/api/suggest',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({text, model: currentModel})});
    const d = await r.json();
    if(cpMode !== 'ai' || $('#message-input').value.trim() !== text) return;
    sugItems = Array.isArray(d.suggestions) ? d.suggestions.slice(0,3) : [];
    cpRender();
  }catch(e){ /* keep spinner / silent */ }
}
function applySuggestion(i){
  const s = sugItems[i]; if(!s) return;
  const ta = $('#message-input');
  const base = ta.value.trimEnd();
  // Suggestions are CONTINUATIONS: append them to the text already typed.
  ta.value = (base + ' ' + s).replace(/\s+/g,' ').trim();
  $('#cmd-popup').classList.remove('show'); cpMode='none'; sugItems=[];
  ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length);
  ta.dispatchEvent(new Event('input', {bubbles:true}));
}
/* Replace the active trigger token with the chosen key (keeping any trailing
   space). Cursor is placed right after the inserted token. */
function cpApply(i){
  const it = cpItems[i]; if(!it) return;
  const box = $('#cmd-popup'); const ta = $('#message-input');
  const val = ta.value; const m = val.match(/(^|\s)([@/#])(\w*)$/);
  if(!m){ box.classList.remove('show'); return; }
  const before = val.slice(0, m.index + m[1].length); // include leading space if any
  const after = val.slice(m.index + m[0].length);
  ta.value = before + it.key + ' ' + after;
  box.classList.remove('show'); cpItems=[]; cpActive=-1; cpMode='none';
  // move caret to end of inserted token
  const pos = before.length + it.key.length + 1;
  ta.focus(); ta.setSelectionRange(pos, pos);
  ta.dispatchEvent(new Event('input', {bubbles:true}));
}
function refreshCpActive(){
  document.querySelectorAll('#cmd-popup .cp-item').forEach((el,i)=>el.classList.toggle('active', i===cpActive));
  const act = document.querySelector('#cmd-popup .cp-item.active'); if(act) act.scrollIntoView({block:'nearest'});
}
function escapeHtml(s){ return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

function initAutocomplete(){
  $('#message-input').addEventListener('input', cpBuild);
  $('#message-input').addEventListener('keydown', e=>{
    const box = $('#cmd-popup');
    if(!box.classList.contains('show')) return;
    if(cpMode === 'static'){
      if(e.key==='ArrowDown'){ e.preventDefault(); cpActive=(cpActive+1)%cpItems.length; refreshCpActive(); }
      else if(e.key==='ArrowUp'){ e.preventDefault(); cpActive=(cpActive-1+cpItems.length)%cpItems.length; refreshCpActive(); }
      else if(e.key==='Tab' || (e.key==='Enter' && !e.shiftKey)){ e.preventDefault(); cpApply(cpActive>=0?cpActive:0); }
      else if(e.key==='Escape'){ box.classList.remove('show'); cpItems=[]; cpActive=-1; cpMode='none'; }
    } else if(cpMode === 'ai'){
      if(e.key==='Tab'){ e.preventDefault(); applySuggestion(0); }
      else if(e.key==='ArrowDown'){ e.preventDefault(); /* suggestions are clickable; ignore */ }
      else if(e.key==='Escape'){ box.classList.remove('show'); sugItems=[]; cpMode='none'; }
    }
  });
  // hide popup when input loses focus (small delay so clicks register)
  $('#message-input').addEventListener('blur', ()=>setTimeout(()=>{ $('#cmd-popup').classList.remove('show'); }, 150));
  document.addEventListener('click', e=>{ if(!e.target.closest('#cmd-popup') && e.target!==$('#message-input')) $('#cmd-popup').classList.remove('show'); });
}

/* ---------- ✨ Improve text (AI rewrite of the typed draft) ---------- */
function initImprove(){
  $('#btn-improve').onclick = async ()=>{
    const ta = $('#message-input');
    const text = ta.value.trim();
    if(!text){ ta.focus(); return; }
    const btn = $('#btn-improve');
    btn.disabled = true; const old = btn.textContent; btn.textContent = '…';
    // show the popup in AI mode with a busy state
    $('#cmd-popup').innerHTML = '<div class="cp-ai-label">✦ Improve</div><div class="cp-loading"><span class="cp-spin"></span>rewriting your text…</div>';
    $('#cmd-popup').classList.add('show');
    try{
      const r = await fetch(API.base+'/api/improve',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({text, model: currentModel})});
      const d = await r.json();
      if(d.error){ alert('Improve failed: '+d.error); }
      else if(d.improved){
        ta.value = d.improved;
        ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length);
        ta.dispatchEvent(new Event('input', {bubbles:true}));
        setTimeout(()=>$('#cmd-popup').classList.remove('show'), 700);
      }
    }catch(e){ alert('Improve error: '+e.message); }
    finally{ btn.disabled = false; btn.textContent = old; }
  };
}
