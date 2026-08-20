/* tts.js — sentence-streamed TTS with strict serial (no-overlap) playback.
   Also owns the per-sentence drain that creates the ▶ buttons. */

let ttsBuf='';
let ttsDone=0;             // read offset into ttsBuf (sentence-linking cursor)
let ttsQueue=[];           // [{text, persona}] played strictly in order, one at a time
let ttsPlaying=false;
let ttsCurrentAudio=null;  // the single Audio element currently sounding (so we never overlap)
let audioUnlocked=false;
// Hard-stop any in-flight playback and clear the queue. Used before a fresh
// replay / test so sentences from different sources can never overlap.
function stopTTS(){ try{ if(ttsCurrentAudio){ ttsCurrentAudio.pause(); ttsCurrentAudio=null; } }catch(e){} ttsQueue=[]; ttsPlaying=false; ttsBuf=''; ttsDone=0; }
// Unlock audio on the first user gesture so post-async play() is allowed (autoplay policy).
function unlockAudio(){ if(audioUnlocked) return; audioUnlocked=true;
  try{ const u=new Audio('data:audio/wav;base64,UklGRigAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQQAAAD//wEA'); u.muted=true; u.play().catch(()=>{}); }catch(e){} }
['click','keydown','touchstart'].forEach(ev=>document.addEventListener(ev, unlockAudio));
function feedTTS(t, persona){ ttsBuf+=t; drainSentences(false, persona); }
function flushTTS(persona){ drainSentences(true, persona); }
function autoPlaySentence(text, persona){
  // AUTO-PLAY only — gated by the speaker button (TTS_ON). Used while streaming.
  if(!TTS_ON) return;
  const c=cleanMd(text);
  if(!c) return;
  ttsQueue.push({text:c, persona}); pumpTTS();
}
function playSentenceNow(text, persona){
  // MANUAL play — always plays, regardless of the autoplay (speaker) toggle.
  // This is what the per-sentence ▶ buttons and the ⟲ replay-all button use.
  const c=cleanMd(text);
  if(!c) return;
  ttsQueue.push({text:c, persona}); pumpTTS();
}
function drainSentences(final, persona){
  // Extract complete sentences (ending in . ! ? or a blank newline run).
  // A persistent read offset (ttsDone) guarantees we never re-emit already-spoken
  // text and never drop text — this is the "sentence linking" that keeps a
  // streamed reply playing as one continuous sequence.
  const re=/[^.!?\n]*[.!?]+|\n+/g;
  let m; const collected=[];
  while((m=re.exec(ttsBuf))!==null){ collected.push({s:ttsBuf.slice(ttsDone, re.lastIndex), end:re.lastIndex}); }
  const toEmit=[];
  for(const c of collected){
    if(c.end<=ttsDone) continue;
    if(final || /[.!?]$/.test(c.s.trim()) || /^\n+$/.test(c.s)){ toEmit.push(c); } else break;
  }
  for(const c of toEmit){
    ttsDone=c.end;
    const raw=c.s;
    if(isPlayableSentence(raw)){
      const c2=cleanMd(raw);
      // Attach a ▶ button only if a live bubble is present (guarded — never crash on stale currentBot).
      if(currentBot && currentBot.plays && currentBot.plays.isConnected){ currentBot.plays.appendChild(makeSentencePlay(c2, persona)); }
      autoPlaySentence(c2, persona);
    }
  }
  // On final flush, emit any trailing partial as its own sentence.
  if(final){
    const tail=ttsBuf.slice(ttsDone);
    ttsDone=ttsBuf.length;
    if(tail.trim() && isPlayableSentence(tail)){
      const c2=cleanMd(tail);
      if(currentBot && currentBot.plays && currentBot.plays.isConnected){ currentBot.plays.appendChild(makeSentencePlay(c2, persona)); }
      autoPlaySentence(c2, persona);
    }
    ttsBuf=''; ttsDone=0;
  }
}
async function pumpTTS(){
  if(ttsPlaying) return;        // a loop is already draining the queue
  ttsPlaying=true;
  try{
    while(ttsQueue.length){
      const item=ttsQueue.shift();
      try{ await playTTS(item.text, item.persona); }catch(e){ /* skip bad chunk */ }
    }
  }finally{ ttsPlaying=false; }
}
// Play ONE sentence and resolve only when it has FINISHED sounding (onended),
// so the queue in pumpTTS never starts the next sentence until this one is done.
async function playTTS(text, persona){
  if(!text || !text.trim()) return;   // low-level player: NOT gated by the speaker
                                        // toggle. Gating lives in autoPlaySentence
                                        // so manual ▶/⟲ always play.
  unlockAudio();
  let voice=(persona?.voice)||(selectedPersonaObj()?.voice)||HEALTH.default_voice||'alba';
  try{
    const url=API.base+'/api/tts?text='+encodeURIComponent(text)+'&voice='+encodeURIComponent(voice)+'&format=mp3';
    const r=await fetch(url);
    if(!r.ok) throw new Error('tts '+r.status);
    const b=await r.blob();
    const a=new Audio(URL.createObjectURL(b));
    a.style.display='none'; document.body.appendChild(a);   // attach so it reliably renders/plays
    ttsCurrentAudio=a;
    // Wait for the clip to actually finish before resolving -> strict no-overlap sequencing.
    await new Promise((resolve)=>{
      let done=false; const fin=()=>{ if(done) return; done=true; resolve(); };
      a.onended=fin; a.onerror=fin;
      a.play().catch(fin);   // autoplay-blocked -> resolve so we don't hang the queue
    });
    try{ URL.revokeObjectURL(a.src); }catch(e){}
    try{ a.remove(); }catch(e){}
    if(ttsCurrentAudio===a) ttsCurrentAudio=null;
  }catch(e){
    ttsCurrentAudio=null;
    if(voice!==HEALTH.default_voice){ try{ await playTTS(text, {...(persona||{}), voice: HEALTH.default_voice}); return; }catch(e2){} }
    console.error('TTS error',e);
  }
}
// Play a sample line with a specific voice handle (used by the Test buttons).
async function playTestVoice(voice, statusEl){
  const sample="This is the voice of "+ (voice||'default') +". How does it sound?";
  if(statusEl){ statusEl.textContent='♪ generating…'; statusEl.classList.remove('ready'); }
  try{
    const url=API.base+'/api/tts?text='+encodeURIComponent(sample)+'&voice='+encodeURIComponent(voice||'alba')+'&format=mp3';
    const r=await fetch(url);
    if(!r.ok){ const d=await r.json().catch(()=>({})); throw new Error(d.error||('tts '+r.status)); }
    const b=await r.blob(); const a=new Audio(URL.createObjectURL(b));
    const cleanup=()=>URL.revokeObjectURL(a.src);
    a.onended=()=>{ if(statusEl){ statusEl.textContent='✔ played'; statusEl.classList.add('ready'); } cleanup(); };
    a.onerror=cleanup;
    a.play().catch(cleanup);
    if(statusEl){ statusEl.textContent='♪ playing…'; }
  }catch(e){ if(statusEl){ statusEl.textContent='✖ '+ (e.message||'unavailable'); } console.error('test voice failed',e); }
}
