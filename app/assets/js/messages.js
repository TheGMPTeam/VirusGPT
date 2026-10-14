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
/* Render a message body into a bubble: prose as text, fenced/inline code as
   real <pre> blocks (monospace, scrollable, never spoken). Original text is
   preserved exactly; only the presentation changes. */
function renderRich(bubble, text){
  bubble.innerHTML='';
  const spans = (typeof splitCodeSpans==='function') ? splitCodeSpans(text)
              : [['text', text||'']];
  let sentenceIndex=0;
  for(const [kind, chunk] of spans){
    if(kind==='code'){
      let body=chunk;
      const m=body.match(/^(```|~~~)([a-zA-Z0-9+_-]*)\n?([\s\S]*?)\1$/);
      let code=body, lang='';
      if(m){ code=m[3]; lang=m[2]; }
      else code=body.replace(/^`|`$/g,'');
      const pre=document.createElement('pre'); pre.className='codeblock';
      if(lang){ const tag=document.createElement('span'); tag.className='code-lang'; tag.textContent=lang; pre.appendChild(tag); }
      const c=document.createElement('code'); c.textContent=code.replace(/\n+$/,'');
      pre.appendChild(c); bubble.appendChild(pre);
    }else if(chunk){
      // Wrap readable prose sentence-by-sentence without changing its text. TTS
      // adds .tts-speaking to the matching span for the duration of playback.
      const parts=chunk.match(/[^.!?]+[.!?]+|[^.!?]+$/g) || [chunk];
      for(const raw of parts){
        const spoken=cleanMd(raw.trim());
        if(isPlayableSentence(spoken)){
          const s=document.createElement('span');
          s.className='speakable-sentence';
          s.dataset.sentenceIndex=String(sentenceIndex++);
          s.dataset.spoken=spoken;
          s.textContent=raw;
          bubble.appendChild(s);
        }else{
          bubble.appendChild(document.createTextNode(raw));
        }
      }
    }
  }
}
/* Split text into [(kind, chunk)] with kind 'text'|'code' (fences + inline). */
function splitCodeSpans(text){
  const spans=[]; let pos=0;
  const re=/(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]+`)/g; let m;
  while((m=re.exec(text||''))){
    if(m.index>pos) spans.push(['text', text.slice(pos,m.index)]);
    spans.push(['code', m[0]]); pos=m.index+m[0].length;
  }
  if(pos<(text||'').length) spans.push(['text', text.slice(pos)]);
  return spans;
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
  const text=(t||'').trim();
  if(!text) return [];
  // Drop fenced/inline code BEFORE sentence splitting so a ▶ button can never
  // bind to code (code is for reading, not speaking).
  const prose = (typeof splitCodeSpans==='function')
    ? splitCodeSpans(text).filter(s=>s[0]==='text').map(s=>s[1]).join('\n')
    : text;
  if(!prose.trim()) return [];
  // Split into sentence-ish pieces on . ! ? (keep the terminator). The previous
  // regex accidentally matched the WHOLE reply as one span (so every ▶ button
  // bound to the entire text and clicking any sentence played everything) — this
  // one matches each terminal-bounded sentence strictly.
  const parts = prose.match(/[^.!?]+[.!?]+|[^.!?]+$/g) || [prose];
  const out=[];
  for(const raw of parts){
    const s=cleanMd(raw.trim());
    if(isPlayableSentence(s)) out.push(s);
  }
  if(out.length) return out;
  const cleaned=cleanMd(text);
  return isPlayableSentence(cleaned)?[cleaned]:[];
}
/* Per-sentence play button: icon-only ▶ on the same inline row; each plays
   ONLY its own sentence, in isolation (it must not trigger the whole reply). */
function makeSentencePlay(text, persona, highlightEl){
  const c=cleanMd(text);
  const b=document.createElement('button'); b.className='play';
  b.innerHTML=VG_ICON('play'); b.title=c;
  b.onclick=()=>{
    const msg=b.closest('.msg');
    const target=(highlightEl && highlightEl.isConnected)
      ? highlightEl : resolveTTSHighlight(c, null, msg);
    playSingle(c, persona, target, msg);
  };
  return b;
}
/* "Replay all" button: icon-only ⟲; re-streams the whole reply sentence-by-sentence. */
function makeReplayAll(text, persona){
  const b=document.createElement('button'); b.className='play'; b.textContent='⟲';
  b.title='Replay all'; b.style.marginTop='6px';
  b.onclick=()=>replayMessage(text, persona, b.closest('.msg'));
  return b;
}
/* "Play all" button: ⏯ at the END of a reply. Click-only — never auto-plays.
   Re-streams every sentence in order through the serial queue (stop first so a
   prior replay/auto-play can't overlap). */
function makePlayAll(sentences, persona, highlightEls){
  const b=document.createElement('button'); b.className='play'; b.textContent='⏯';
  b.title='Play all (click to play)'; b.style.marginLeft='4px';
  b.onclick=()=>{
    stopTTS();
    sentences.forEach((s,i)=>playSentenceNow(s, persona, highlightEls?.[i], b.closest('.msg')));
  };
  return b;
}
/* Build a message's sentence-play row: ONE ▶ button per clean sentence, plus a
   click-only "Play all" at the end. Used for both the live finalize and for
   history messages so every assistant bubble is consistent. */
function buildSentencePlays(container, text, persona){
  if(!container) return;
  container.innerHTML='';
  const sents=splitSentences(text||'');
  const msg=container.closest('.msg');
  const spans=msg ? [...msg.querySelectorAll('.bubble .speakable-sentence')] : [];
  sents.forEach((s,i)=>container.appendChild(makeSentencePlay(s, persona, spans[i]||null)));
  if(sents.length) container.appendChild(makePlayAll(sents, persona, spans));
}

/* Re-stream a full response: split into sentences, queue them serially. */
function replayMessage(text, persona, messageEl){
  stopTTS();
  const spans=messageEl ? [...messageEl.querySelectorAll('.bubble .speakable-sentence')] : [];
  splitSentences(text||'').forEach((s,i)=>playSentenceNow(s, persona, spans[i]||null, messageEl));
}

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
    const bubble=document.createElement('div');bubble.className='bubble';renderRich(bubble,text||'');
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
function pushImageMessage(role, urls, caption, personaName, comfyuiFile){
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
    img.className='gen-image';
    // Make relative /api/generated/... URLs absolute against the API base so they
    // resolve in the WebView (and over a remote backend) instead of file://.
    img.src = (typeof u==='string' && u.startsWith('/')) ? (API.base.replace(/\/$/,'')+u) : u;
    img.alt='generated image'; img.loading='lazy';
    img.onerror=()=>{ img.style.display='none'; };
    img.onclick=()=>window.open(img.src,'_blank');
    img.title='Open image';
    col.appendChild(img);
    if(comfyuiFile){
      const bar=document.createElement('div'); bar.style.cssText='display:flex;gap:8px;margin-top:4px';
      const upd=document.createElement('button'); upd.className='btn ghost'; upd.textContent='Update this image';
      upd.title='img2img refine: keeps the composition, re-renders the subject with your new prompt';
      upd.onclick=()=>{
        window.__vgRefineTarget=comfyuiFile;
        const box=$('#message-input');
        if(box){ box.placeholder='Describe the update (e.g. add a red scarf)… then click Image'; box.focus(); }
      };
      bar.appendChild(upd); col.appendChild(bar);
    }
  });
  wrap.appendChild(av);wrap.appendChild(col);
  $('#messages').appendChild(wrap);$('#messages').scrollTop=$('#messages').scrollHeight;return wrap;
}
