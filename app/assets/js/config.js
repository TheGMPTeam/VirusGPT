/* config.js — global constants, API base, shared state, localStorage helpers.
   Loaded first; everything else depends on these globals. */
const $ = s => document.querySelector(s);
const VG_CFG = window.__VG_CONFIG || {};

function lsGet(k, d){ try{ const v=localStorage.getItem(k); return v===null?d:v; }catch(e){ return d; } }
function lsSet(k, v){ try{ localStorage.setItem(k, v); }catch(e){ /* storage unavailable — ignore */ } }

// Default the API base to the origin that served this page (so it always points
// at the local server). Only override with a saved/explicit value when present.
const API = { base: (location.protocol.startsWith('http')
    ? location.origin
    : (lsGet('vg_base', '') || VG_CFG.backend || 'http://localhost:8500')) };

let TTS_ON = (lsGet('vg_tts', 'on') !== 'off');
let HEALTH = {ollama:false,tts:false,whisper:false,models:[],voices:[],default_voice:'alba'};
let personas = loadJSON('vg_personas', [
  {name:'VirusGPT', description:'Default offline agent', system_prompt:'You are VirusGPT, a helpful offline AI assistant.', skills:'General reasoning, summarization, and friendly help.', voice:'alba', color:'#00ff9c', emoji:'🦠', audio:null},
  {name:'Cipher', description:'Security & code expert', system_prompt:'You are Cipher, a concise security and coding expert.', skills:'Code review, exploit analysis, malware dissection, offensive/defensive security.', voice:'azelma', color:'#23e0ff', emoji:'🛡', audio:null},
  {name:'Oracle', description:'Philosophical narrator', system_prompt:'You are Oracle, a calm philosophical narrator.', skills:'Storytelling, Socratic questioning, big-picture synthesis.', voice:'cosette', color:'#ff2bd6', emoji:'🔮', audio:null},
]).map(p=>({context:[], skills:'', ...p}));  // ensure each persona has its own context + skills
let rooms = loadJSON('vg_rooms', [{name:'default', messages:[]}]);
let currentRoom = 'default';
let selectedPersona = null;
let currentAbort = null, runToken = 0, RUN_TIMEOUT_MS = 60000;
let currentModel = lsGet('vg_model', '');

function loadJSON(k, def){ try{ const v=localStorage.getItem(k); return v?JSON.parse(v):def; }catch(e){ return def; } }
function saveJSON(k, v){ try{ localStorage.setItem(k, JSON.stringify(v)); }catch(e){ /* storage unavailable (e.g. file://) — ignore */ } }
function selectedPersonaObj(){ return personas.find(p=>p.name===selectedPersona) || null; }
function whoMode(){ return document.querySelector('input[name=who]:checked').value; }
// Build the SYSTEM prompt for a persona: its own prompt + its own specialized
// skills/style. Each agent is isolated — context & skills never bleed across.
function buildSystem(persona){
  let s = persona?.system_prompt || personas[0]?.system_prompt || 'You are a helpful assistant.';
  if(persona?.skills && persona.skills.trim()) s += '\n\nSpecialized skills & style:\n' + persona.skills.trim();
  // Hard constraint so an agent never narrates a whole group scene or writes
  // lines for the other personas — it speaks ONLY as itself, in first person.
  const nm = persona?.name || 'the assistant';
  s += `\n\nYou are roleplaying as ${nm}. Stay fully in that character and reply in the first person as ${nm} ONLY. Do NOT narrate, quote, or write dialogue for any other persona, and do NOT format your reply as a multi-character script or scene. Write a single, natural response as yourself.`;
  return s;
}
