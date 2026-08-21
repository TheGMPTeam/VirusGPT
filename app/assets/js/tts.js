/* tts.js — sentence-streamed TTS with strict serial (no-overlap) playback.
   Also owns the per-sentence drain that creates the ▶ buttons. */

let ttsBuf='';
let ttsDone=0;             // read offset into ttsBuf (sentence-linking cursor)
let ttsQueue=[];           // [{text, persona}] played strictly in order, one at a time
let ttsPlaying=false;
let ttsCurrentAudio=null;  // the single Audio element currently sounding (so we never overlap)
let audioUnlocked=false;
// Per-session audio registry: roomName -> [{el, url}] so object URLs can be
// revoked and <audio> nodes removed when a session is removed / deleted /
// switched (prevents leaked blob URLs + orphaned audio elements).
let sessionAudio = {};
function _trackAudio(room, el, url){
  if(!room) return;
  (sessionAudio[room] = sessionAudio[room] || []).push({el, url});
}
function pruneSessionAudio(room){
  const arr = sessionAudio[room]; if(!arr) return;
  for(const a of arr){
    try{ if(a.el===ttsCurrentAudio) ttsCurrentAudio=null; }catch(e){}
    try{ if(a.el){ a.el.pause(); a.el.remove(); } }catch(e){}
    try{ if(a.url) URL.revokeObjectURL(a.url); }catch(e){}
  }
  delete sessionAudio[room];
}
// Remove every tracked audio clip whose room no longer exists in `rooms`
// (e.g. a session was deleted elsewhere, or localStorage changed). Keeps the
// registry from leaking blob URLs / orphaned <audio> nodes for dead sessions.
function pruneOrphanedAudio(){
  const live = new Set((typeof rooms!=='undefined' && rooms) ? rooms.map(r=>r.name) : []);
  for(const room of Object.keys(sessionAudio)){
    if(!live.has(room)) pruneSessionAudio(room);
  }
}
// Best-effort periodic sweep so orphaned audio never accumulates.
setInterval(pruneOrphanedAudio, 15000);
/* Per-session auto-play. After /clear or /new the session starts muted for
   streaming auto-play; toggling the speaker button (TTS_ON) re-enables it. */
let sessionAutoPlay = false;
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
  // AUTO-PLAY only — gated by the speaker button (TTS_ON) AND the per-session
  // auto-play flag (turned off by /clear and /new so a fresh session is quiet
  // unless the user re-enables the speaker). Used while streaming.
  if(!TTS_ON || !sessionAutoPlay) return;
  const c=cleanMd(text);
  if(!c) return;
  ttsQueue.push({text:c, persona}); pumpTTS();
}
/* Play exactly ONE sentence, isolated from the shared auto-play queue and from
   any in-flight playback. Used by the per-sentence ▶ buttons so clicking the
   3rd button plays ONLY the 3rd sentence — never the whole reply. */
function playSingle(text, persona){
  const c=cleanMd(text);
  if(!c) return;
  // Stop whatever is sounding (incl. an auto-play run) so a manual click always
  // wins, then play this one sentence and nothing else.
  try{ if(ttsCurrentAudio){ ttsCurrentAudio.pause(); ttsCurrentAudio=null; } }catch(e){}
  ttsQueue=[]; ttsPlaying=false;
  const v=(persona?.voice)||(selectedPersonaObj()?.voice)||HEALTH.default_voice||'alba';
  playTTS(c, {voice:v}).catch(()=>{});
}
function playSentenceNow(text, persona){
  // MANUAL play — always plays, regardless of the autoplay (speaker) toggle.
  // This is what the per-sentence ▶ buttons and the ⟲ replay-all button use.
  const c=cleanMd(text);
  if(!c) return;
  ttsQueue.push({text:c, persona}); pumpTTS();
}
function drainSentences(final, persona){
  // Emit only COMPLETE sentences that appear AFTER the emit cursor (ttsDone),
  // then advance the cursor by exactly the emitted length. Because we operate on
  // ttsBuf.slice(ttsDone) every call, already-spoken text is never re-emitted —
  // this is what stops auto-play from re-queuing every prior sentence (the
  // "loops every sentence" bug) when feedTTS is called repeatedly during a
  // streamed reply.
  const tail = ttsBuf.slice(ttsDone);
  const all = tail.match(/[^.!?]+[.!?]+/g) || [];
  const emit = all.filter(s => /[.!?]$/.test(s.trim()));
  let consumed = 0;
  for (const s of emit) {
    consumed += s.length;
    const c2 = cleanMd(s);
    if (currentBot && currentBot.plays && currentBot.plays.isConnected) {
      currentBot.plays.appendChild(makeSentencePlay(c2, persona));
    }
    autoPlaySentence(c2, persona);
  }
  ttsDone += consumed;
  // On final flush, emit any trailing partial as its own sentence.
  if (final) {
    const rest = ttsBuf.slice(ttsDone);
    ttsDone = ttsBuf.length;
    if (rest.trim() && isPlayableSentence(rest)) {
      const c2 = cleanMd(rest);
      if (currentBot && currentBot.plays && currentBot.plays.isConnected) {
        currentBot.plays.appendChild(makeSentencePlay(c2, persona));
      }
      autoPlaySentence(c2, persona);
    }
    ttsBuf = ''; ttsDone = 0;
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
    const blobUrl=URL.createObjectURL(b);
    const a=new Audio(blobUrl);
    a.style.display='none'; document.body.appendChild(a);   // attach so it reliably renders/plays
    ttsCurrentAudio=a;
    _trackAudio(currentRoom, a, blobUrl);   // tie this clip to the active session for pruning
    // Wait for the clip to actually finish before resolving -> strict no-overlap sequencing.
    // CRITICAL: some engines (WKWebView / blob mp3) never fire 'ended', which would hang the
    // serial queue forever (Play-all plays sentence 1 then freezes, and every re-click piles
    // up another stuck loop). So we ALSO resolve on a duration-based timeout as a hard fallback.
    await new Promise((resolve)=>{
      let done=false; const fin=()=>{ if(done) return; done=true; resolve(); };
      a.onended=fin; a.onerror=fin;
      a.play().catch(fin);   // autoplay-blocked -> resolve so we don't hang the queue
      // Fallback timer: once we know the duration, schedule resolution shortly after it ends.
      // If duration is unknown (0/NaN), fall back to a generous fixed ceiling.
      const arm=()=>{
        const d=a.duration;
        const ms=(isFinite(d)&&d>0) ? (d*1000)+400 : 12000;
        setTimeout(fin, ms);
      };
      if(a.readyState>=1 && a.duration) arm();
      else a.addEventListener('loadedmetadata', arm, {once:true});
      // Absolute ceiling so a never-ending clip can never wedge the queue.
      setTimeout(fin, 15000);
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
