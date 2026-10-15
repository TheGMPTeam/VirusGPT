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
  // Inline code can divide one spoken sentence into multiple visible prose
  // fragments. Bind every fragment to the complete speech sentence by content,
  // rather than assuming DOM-fragment index equals playback index.
  const targets=splitSentences(text||'');
  const nodes=[...bubble.querySelectorAll('.speakable-sentence')];
  const norm=s=>cleanMd(s||'').replace(/\s+/g,' ').trim().toLowerCase();
  let nodeIndex=0;
  targets.forEach((target,speechIndex)=>{
    const wanted=norm(target); let collected='';
    while(nodeIndex<nodes.length){
      const node=nodes[nodeIndex++];
      collected=norm((collected?collected+' ':'')+(node.dataset.spoken||node.textContent||''));
      node.dataset.speechIndex=String(speechIndex);
      node.dataset.spoken=target;
      if(collected===wanted || !wanted.startsWith(collected)) break;
    }
  });
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
/* Sentence boundary regex shared by splitSentences and the streaming splitter.
   A '.' is NOT terminal when: preceded by a single capital letter (initials,
   drive letters: "C.", "U.S."), followed by a digit (decimals: "4.2"), or the
   word before it is a known abbreviation ("e.g.", "i.e.", "etc.", "Dr.").
   Ellipses ("...") never end a sentence mid-thought. */
const SENT_RE = /[^.!?]+[.!?]+(?=[\s"')\]}<]|$)|[^.!?]+$/g;
function isBoundaryDot(s, i){
  if(s[i-1] === '.') return false;                      // ellipsis / multi-dot
  if(s[i+1] === '.') return false;                      // "..." never ends here
  const prev = s[i-1] || '';
  const next = s[i+1] || '';
  if(/[0-9]/.test(next)) return false;                  // decimal / version
  if(/^[A-Z]$/.test(prev)){                             // initials, drive letters
    // "... C. Then" (next word capitalized) IS a real boundary; "U.S. forces" is not.
    return next === ' ' && /^[A-Z]/.test(s[i+2] || '');
  }
  // trailing token may itself contain dots ("e.g", "i.e") — match those whole.
  const w = (s.slice(0, i).match(/([A-Za-z][A-Za-z.]*)\s*$/) || [])[1] || '';
  if(/^(e\.g|i\.e|e|i|etc|approx|vs|dr|mr|mrs|ms|prof|jr|sr|st|no|nos|vol|fig|inc|ltd|co|dept|gov|sen|rep|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec|ft|mi|kg|km|lb)$/i.test(w)) return false;
  return true;
}
/* Sentence matches over a (possibly code-sentineled) string, as [text, start,
   end]. A match whose dots are all FALSE boundaries (abbreviation, decimal) is
   held open and merged into the next span, so junk 1-2 word TTS chunks like
   "g." or ", 5-minute TTL) for every call)." can never be produced. The final
   span may be an unterminated partial (end === s.length). */
function sentenceSpans(s){
  const out=[]; let m; let start=0;
  SENT_RE.lastIndex=0;
  while((m=SENT_RE.exec(s))){
    let end=m[0].length;
    // evaluate boundaries against the FULL string at absolute indices so
    // context beyond the regex match (next word) is visible.
    while(end>0 && /[.!?]/.test(m[0][end-1]) && !isBoundaryDot(s, m.index+end-1)) end--;
    if(end===0 || !/[.!?]/.test(m[0][end-1])) continue;  // no true terminator yet
    const abs=m.index+end;
    out.push([s.slice(start, abs), start, abs]);
    start=abs; SENT_RE.lastIndex=abs;
  }
  if(start<s.length) out.push([s.slice(start), start, s.length]);
  return out;
}
/* Remove the code sentinel left by splitSentences and tidy punctuation the code
   span used to separate ("using \u0000," -> "using,", "checks \u0000." -> "checks."). */
function cleanSpeechArtifacts(s){
  return (s||'')
    .replace(/\u0000/g,'')
    .replace(/\s+([,.;:!?])/g,'$1')
    .replace(/,\s*,/g,',')
    .replace(/\s{2,}/g,' ')
    .trim();
}
function splitSentences(t){
  // A saved room message can carry a non-string text (corrupt localStorage);
  // boot must never crash on it and a junk value must never be spoken.
  if(typeof t !== 'string') return [];
  const text=t.trim();
  if(!text) return [];
  // Replace fenced/inline code with a ONE-character sentinel BEFORE sentence
  // splitting. Offsets survive, so a code span can never leave a dangling
  // fragment ("...checks using ." -> its own junk chunk) nor be spoken.
  const sent = (typeof splitCodeSpans==='function')
    ? splitCodeSpans(text).map(s=>s[0]==='code' ? '\u0000' : s[1]).join('')
    : text;
  if(!sent.trim()) return [];
  const out=[];
  for(const [chunk] of sentenceSpans(sent)){
    const s=cleanSpeechArtifacts(cleanMd(chunk));
    if(isPlayableSentence(s)) out.push(s);
  }
  if(out.length) return out;
  const cleaned=cleanSpeechArtifacts(cleanMd(sent));
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
function speechHighlightTargets(messageEl, sentences){
  if(!messageEl) return (sentences||[]).map(()=>null);
  const spans=[...messageEl.querySelectorAll('.bubble .speakable-sentence')];
  return (sentences||[]).map((sentence,i)=>
    spans.find(el=>el.dataset.speechIndex===String(i)) ||
    spans.find(el=>el.dataset.spoken===cleanMd(sentence)) || null);
}
function buildSentencePlays(container, text, persona){
  if(!container) return;
  container.innerHTML='';
  const sents=splitSentences(text||'');
  const msg=container.closest('.msg');
  const targets=speechHighlightTargets(msg, sents);
  sents.forEach((s,i)=>container.appendChild(makeSentencePlay(s, persona, targets[i])));
  if(sents.length) container.appendChild(makePlayAll(sents, persona, targets));
}

/* Re-stream a full response: split into sentences, queue them serially. */
function replayMessage(text, persona, messageEl){
  stopTTS();
  const sentences=splitSentences(text||'');
  const targets=speechHighlightTargets(messageEl, sentences);
  sentences.forEach((s,i)=>playSentenceNow(s, persona, targets[i], messageEl));
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
/* Clear image-update mode and restore the normal composer. */
function clearImageRefineTarget(){
  window.__vgRefineTarget=null;
  document.querySelectorAll('.image-compare-card.refine-selected').forEach(el=>el.classList.remove('refine-selected'));
  document.querySelectorAll('.image-compare-card .refine-select-btn').forEach(btn=>{
    btn.classList.remove('selected');
    btn.textContent='Update this image';
  });
  const mode=$('#refine-mode'); if(mode) mode.classList.remove('active');
  const box=$('#message-input');
  if(box){ box.placeholder=box.dataset.defaultPlaceholder||'Transmit a message to VirusGPT...'; }
  const imageBtn=$('#btn-gen-image');
  if(imageBtn){ imageBtn.classList.remove('refine-active'); imageBtn.title='Generate an image from the typed prompt via ComfyUI (Studio)'; }
}

/* Select a specific generated image as the source for the next Image action. */
function selectImageRefineTarget(sourceFile, card, label){
  clearImageRefineTarget();
  window.__vgRefineTarget=sourceFile;
  if(card){
    card.classList.add('refine-selected');
    const selectedBtn=card.querySelector('.refine-select-btn');
    if(selectedBtn){ selectedBtn.classList.add('selected'); selectedBtn.textContent='Selected for update'; }
  }
  const mode=$('#refine-mode');
  const title=$('#refine-mode-title');
  const help=$('#refine-mode-help');
  if(title) title.textContent='Update mode: '+(label||'selected image');
  if(help) help.textContent='Describe the changes below, then click Image. The selected image will be used as the starting point.';
  if(mode) mode.classList.add('active');
  const cancel=$('#refine-mode-cancel'); if(cancel) cancel.onclick=clearImageRefineTarget;
  const imageBtn=$('#btn-gen-image');
  if(imageBtn){ imageBtn.classList.add('refine-active'); imageBtn.title='Update the selected image using the typed instructions'; }
  const box=$('#message-input');
  if(box){
    if(!box.dataset.defaultPlaceholder) box.dataset.defaultPlaceholder=box.placeholder;
    box.placeholder='Describe what to change, then click Image...';
    box.focus();
    box.scrollIntoView({block:'end',behavior:'smooth'});
  }
}

/* Render a message whose body is one or more generated images (e.g. a mission
   task that called the render_image tool). Each image links to the served
   /api/generated/... URL so the user can open it full-size. */
function pushImageMessage(role, urls, caption, personaName, comfyuiFile, labels){
  const wrap=document.createElement('div'); wrap.className='msg '+(role==='user'?'user':'bot');
  const av=document.createElement('div'); av.className='persona-avatar';
  if(role==='user'){av.style.background='var(--neon2)';av.textContent='\uD83E\uDDD1';}
  else{const p=personaByName(personaName)||selectedPersonaObj()||personas[0];av.style.background=p.color;av.textContent=p.emoji||'\uD83E\uDD16';}
  const col=document.createElement('div');col.style.minWidth='0';
  const who=document.createElement('div');who.className='who';who.textContent=role==='user'?'YOU':((personaByName(personaName)||selectedPersonaObj())?.name||'VirusGPT');
  col.appendChild(who);
  if(caption){
    const cap=document.createElement('div');cap.className='bubble';cap.textContent=caption;col.appendChild(cap);
  }
  const list=urls||[];
  const files=Array.isArray(comfyuiFile)?comfyuiFile:list.map(()=>comfyuiFile);
  const grid=document.createElement('div');
  if(list.length>1) grid.className='image-compare-grid';
  list.forEach((u,idx)=>{
    const card=document.createElement('div'); card.className='image-compare-card';
    if(labels && labels[idx]){
      const lab=document.createElement('div'); lab.className='image-compare-label'; lab.textContent=labels[idx]; card.appendChild(lab);
    }
    const img=document.createElement('img');
    img.className='gen-image';
    // Make relative /api/generated/... URLs absolute against the API base so they
    // resolve in the WebView (and over a remote backend) instead of file://.
    img.src = (typeof u==='string' && u.startsWith('/')) ? (API.base.replace(/\/$/,'')+u) : u;
    img.alt=(labels&&labels[idx])||'generated image'; img.loading='lazy';
    img.onerror=()=>{ img.style.display='none'; };
    img.onclick=()=>window.open(img.src,'_blank');
    img.title='Open image';
    card.appendChild(img);
    const sourceFile=files[idx];
    if(sourceFile){
      const bar=document.createElement('div'); bar.style.cssText='display:flex;gap:8px;margin-top:4px';
      const upd=document.createElement('button'); upd.className='btn ghost refine-select-btn'; upd.textContent='Update this image';
      upd.title='Select this image, describe the changes below, then click Image';
      upd.onclick=()=>selectImageRefineTarget(sourceFile,card,(labels&&labels[idx])||'selected image');
      bar.appendChild(upd); card.appendChild(bar);
    }
    grid.appendChild(card);
  });
  col.appendChild(grid);
  wrap.appendChild(av);wrap.appendChild(col);
  $('#messages').appendChild(wrap);$('#messages').scrollTop=$('#messages').scrollHeight;return wrap;
}
