/* tts.js — sentence-streamed TTS with strict serial (no-overlap) playback.
   Also owns the per-sentence drain that creates the ▶ buttons. */

let ttsBuf='';
let ttsQueue=[];           // [{text, persona}] played strictly in order, one at a time
let ttsPlaying=false;
let ttsCurrentAudio=null;  // the single Audio element currently sounding (so we never overlap)
let audioUnlocked=false;
// Hard-stop any in-flight playback and clear the queue. Used before a fresh
// replay / test so sentences from different sources can never overlap.
function stopTTS(){ try{ if(ttsCurrentAudio){ ttsCurrentAudio.pause(); ttsCurrentAudio=null; } }catch(e){} ttsQueue=[]; ttsPlaying=false; }
// Unlock audio on the first user gesture so post-async play() is allowed (autoplay policy).
function unlockAudio(){ if(audioUnlocked) return; audioUnlocked=true;
  try{ const u=new Audio('data:audio/wav;base64,UklGRigAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQQAAAD//wEA'); u.muted=true; u.play().catch(()=>{}); }catch(e){} }
['click','keydown','touchstart'].forEach(ev=>document.addEventListener(ev, unlockAudio));
function feedTTS(t, persona){ ttsBuf+=t; drainSentences(false, persona); }
function flushTTS(persona){ drainSentences(true, persona); }
function emitSentence(text, persona){
  if(!TTS_ON) return;
  const c=cleanMd(text);
  if(!c) return;
  ttsQueue.push({text:c, persona}); pumpTTS();                 // queue audio only (no per-sentence rows)
}
function drainSentences(final, persona){
  // extract complete sentences (ending in . ! ? or a blank newline run)
  const re=/[^.!?\n]*[.!?]+|\n+/g;
  let m, last=0; const collected=[];
  while((m=re.exec(ttsBuf))!==null){ collected.push({s:ttsBuf.slice(last, re.lastIndex), end:re.lastIndex}); last=re.lastIndex; }
  let emitLen=0; const toEmit=[];
  for(const c of collected){
    if(final || /[.!?]$/.test(c.s.trim()) || /^\n+$/.test(c.s)){ toEmit.push(c.s); emitLen=c.end; } else break;
  }
  if(toEmit.length){
    const emit=toEmit.join('');
    // Only expose buttons/audio for REAL playable sentences — never blank or
    // markdown-only fragments — so each ▶ plays exactly its own sentence.
    toEmit.forEach(s=>{ if(isPlayableSentence(s)){ const c=cleanMd(s); if(currentBot && currentBot.plays) currentBot.plays.appendChild(makeSentencePlay(c, persona)); emitSentence(c, persona); } });
    ttsBuf=ttsBuf.slice(emit.length);
  }
  if(final && ttsBuf.trim()){ splitSentences(ttsBuf).forEach(s=>{ const c=cleanMd(s); if(currentBot && currentBot.plays) currentBot.plays.appendChild(makeSentencePlay(c, persona)); emitSentence(c, persona); }); ttsBuf=''; }
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
  if(!TTS_ON||!text.trim()) return;
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
