/* messages.js — markdown cleanup, sentence splitting, message bubble rendering,
   bot streaming container, and per-sentence playback UI. */

/* Strip markdown noise so the spoken sentence (and its tooltip) reads cleanly
   — no "star star", "#", list bullets, or trailing ":" labels. */
function cleanMd(s){
  return (s||'')
    .replace(/`{1,3}[^`]*`{1,3}/g,'')        // code spans/blocks
    .replace(/[*_]{1,3}/g,'')                // bold/italic markers
    .replace(/^#{1,6}\s*/gm,'')              // heading hashes
    .replace(/^\s*>\s?/gm,'')                // blockquote markers
    .replace(/^\s*[-*+]\s+/gm,'')            // list bullets
    .replace(/\s{2,}/g,' ')                  // collapse whitespace
    .trim();
}
/* A real, playable sentence contains spoken words (letters), is not just a
   markdown header/label, and isn't a short trailing ":" caption. This keeps
   every ▶ button bound to ONE clean, spoken sentence. */
function isPlayableSentence(s){
  if(!s || !s.trim()) return false;
  const c=cleanMd(s);
  if(!c) return false;
  if(!/[A-Za-z]/.test(c)) return false;                 // need real words
  if(/^#+\s*$/.test(s.trim())) return false;            // heading-only line
  if(/^[-*+]\s+#/.test(s.trim())) return false;         // bullet heading
  // short caption ending in ":" (e.g. "Why it matters:") is a label, not a sentence
  if(/:\s*$/.test(c) && c.split(/\s+/).length<=6) return false;
  return true;
}
/* Split text into sentence-ish chunks for per-sentence play buttons.
   Every returned chunk is a NON-EMPTY, PLAYABLE, markdown-clean sentence so a
   play button can never bind to a blank/junk value — and each button plays
   ONLY its own sentence. */
function splitSentences(t){
  const re=/[^.!?\n]*[.!?]+|\n+/g; const out=[]; let m,last=0;
  while((m=re.exec(t))!==null){
    const raw=t.slice(last,m.lastIndex); last=m.lastIndex;
    const s=raw.trim();
    if(isPlayableSentence(s)) out.push(cleanMd(s));
  }
  if(last<t.length){ const s=t.slice(last).trim(); if(isPlayableSentence(s)) out.push(cleanMd(s)); }
  const cleaned=[t.trim()].map(cleanMd).filter(isPlayableSentence);
  return out.length?out:cleaned;
}
/* Per-sentence play button: icon-only ▶ on the same inline row; each plays
   ONLY its (markdown-cleaned) sentence through the serial queue. */
function makeSentencePlay(text, persona){
  const c=cleanMd(text);
  const b=document.createElement('button'); b.className='play';
  b.textContent='▶'; b.title=c;
  b.onclick=()=>playSentenceNow(c, persona);
  return b;
}
/* "Replay all" button: icon-only ⟲; re-streams the whole reply sentence-by-sentence. */
function makeReplayAll(text, persona){
  const b=document.createElement('button'); b.className='play'; b.textContent='⟲';
  b.title='Replay all'; b.style.marginTop='6px';
  b.onclick=()=>replayMessage(text, persona);
  return b;
}
/* "Play all" button: ⏯ at the END of a reply. Click-only — never auto-plays.
   Re-streams every sentence in order through the serial queue (stop first so a
   prior replay/auto-play can't overlap). */
function makePlayAll(sentences, persona){
  const b=document.createElement('button'); b.className='play'; b.textContent='⏯';
  b.title='Play all (click to play)'; b.style.marginLeft='4px';
  b.onclick=()=>{ stopTTS(); sentences.forEach(s=>playSentenceNow(s, persona)); };
  return b;
}
/* Build a message's sentence-play row: ONE ▶ button per clean sentence, plus a
   click-only "Play all" at the end. Used for both the live finalize and for
   history messages so every assistant bubble is consistent. */
function buildSentencePlays(container, text, persona){
  if(!container) return;
  container.innerHTML='';
  const sents=splitSentences(text||'');
  sents.forEach(s=>container.appendChild(makeSentencePlay(s, persona)));
  if(sents.length) container.appendChild(makePlayAll(sents, persona));
}

/* Re-stream a full response: split into sentences, queue them serially. */
function replayMessage(text, persona){ stopTTS(); splitSentences(text||'').forEach(s=>playSentenceNow(s, persona)); }

let currentBot=null;
function addBotMsg(persona){
  const wrap=document.createElement('div'); wrap.className='msg bot';
  const av=document.createElement('div'); av.className='persona-avatar';
  const p=persona||selectedPersonaObj()||personas[0];
  av.style.background=p.color; av.textContent=p.emoji||'🤖';
  const col=document.createElement('div'); col.style.minWidth='0';
  const who=document.createElement('div'); who.className='who'; who.textContent=p.name||'VirusGPT';
  const bubble=document.createElement('div'); bubble.className='bubble'; bubble.style.display='none';
  const plays=document.createElement('div'); plays.className='sentence-plays';
  const cur=document.createElement('div'); cur.className='bubble current'; cur.textContent='⏳';
  col.appendChild(who); col.appendChild(cur); col.appendChild(bubble); col.appendChild(plays);
  wrap.appendChild(av); wrap.appendChild(col);
  $('#messages').appendChild(wrap); $('#messages').scrollTop=$('#messages').scrollHeight;
  return {el:wrap, bubble, cur, plays, persona:p, emittedLen:0, acc:''};
}
function addMsgEl(role, text, persona){
  const wrap=document.createElement('div'); wrap.className='msg '+(role==='user'?'user':'bot');
  const av=document.createElement('div'); av.className='persona-avatar';
  if(role==='user'){av.style.background='var(--neon2)';av.textContent='🧑';}
  else{const p=persona||selectedPersonaObj()||personas[0];av.style.background=p.color;av.textContent=p.emoji||'🤖';}
  const col=document.createElement('div');col.style.minWidth='0';
  const who=document.createElement('div');who.className='who';who.textContent=role==='user'?'YOU':((persona||selectedPersonaObj())?.name||'VirusGPT');
  col.appendChild(who);
  if(role==='user'){
    const bub=document.createElement('div');bub.className='bubble';bub.textContent=text;
    col.appendChild(bub);
  }else{
    // assistant: ONE bubble with a per-sentence play button for each sentence
    const bubble=document.createElement('div');bubble.className='bubble';bubble.textContent=text||'';
    const plays=document.createElement('div');plays.className='sentence-plays';
    buildSentencePlays(plays, text, persona);
    col.appendChild(bubble); col.appendChild(plays);
  }
  wrap.appendChild(av);wrap.appendChild(col);
  $('#messages').appendChild(wrap);$('#messages').scrollTop=$('#messages').scrollHeight;return wrap;
}
/* Render a message whose body is one or more generated images (e.g. a mission
   task that called the render_image tool). Each image links to the served
   /api/generated/... URL so the user can open it full-size. */
function pushImageMessage(role, urls, caption, personaName){
  const wrap=document.createElement('div'); wrap.className='msg '+(role==='user'?'user':'bot');
  const av=document.createElement('div'); av.className='persona-avatar';
  if(role==='user'){av.style.background='var(--neon2)';av.textContent='🧑';}
  else{const p=personaByName(personaName)||selectedPersonaObj()||personas[0];av.style.background=p.color;av.textContent=p.emoji||'🤖';}
  const col=document.createElement('div');col.style.minWidth='0';
  const who=document.createElement('div');who.className='who';who.textContent=role==='user'?'YOU':((personaByName(personaName)||selectedPersonaObj())?.name||'VirusGPT');
  col.appendChild(who);
  if(caption){
    const cap=document.createElement('div');cap.className='bubble';cap.textContent=caption;col.appendChild(cap);
  }
  (urls||[]).forEach(u=>{
    const img=document.createElement('img');
    img.className='gen-image'; img.src=u; img.alt='generated image'; img.loading='lazy';
    img.onerror=()=>{ img.style.display='none'; };
    img.onclick=()=>window.open(u,'_blank');
    img.title='Open image';
    col.appendChild(img);
  });
  wrap.appendChild(av);wrap.appendChild(col);
  $('#messages').appendChild(wrap);$('#messages').scrollTop=$('#messages').scrollHeight;return wrap;
}
