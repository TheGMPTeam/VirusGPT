/* memory.js — the Memory Graph pane.
   A LIVING, force-directed concept graph (understory-style): concepts drift on a
   physics sim, you can drag / pan / zoom, color encodes type, node size encodes
   how connected it is, orphans are ringed red, and clicking a node opens it
   (shows neighbors + a chat-query box). Edges are synthesized from real
   token-overlap between concept names so the topology reflects the actual memory. */

let __mgNodes = [], __mgEdges = [], __mgAdj = {};
let __mgDrag = null, __mgPan = {x:0,y:0}, __mgZoom = 1, __mgHover = null, __mgSel = null;
let __mgRAF = null, __mgT0 = 0;

// Categorize a concept by name so we can color it like understory's type legend.
function mgCategory(name){
  const n=(name||'').toLowerCase();
  if(n.includes('agent')||n.includes('govern')||n.includes('maestro')||n.includes('hermes')) return 'agent';
  if(n.includes('model')||n.includes('llama')||n.includes('quant')) return 'model';
  if(n.includes('test')||n.includes('result')) return 'test';
  if(n.includes('system')||n.includes('arch')||n.includes('component')||n.includes('integration')) return 'system';
  if(n.includes('user')||n.includes('profile')) return 'user';
  return 'concept';
}
const MG_PALETTE = {
  agent:   '#23e0ff',  // neon2
  model:   '#ff5cf0',  // magenta
  test:    '#ffd23f',  // amber
  system:  '#33ff99',  // neon
  user:    '#ff7a59',  // orange
  concept: '#9b8cff',  // violet
};

// Build nodes + edges from the raw concept-name list.
function mgBuild(concepts){
  __mgNodes = concepts.map((name,i)=>({
    id:i, name, cat:mgCategory(name),
    x: Math.cos(i/concepts.length*Math.PI*2)*180 + (Math.random()-0.5)*40,
    y: Math.sin(i/concepts.length*Math.PI*2)*180 + (Math.random()-0.5)*40,
    vx:0, vy:0, deg:0, phase: Math.random()*Math.PI*2,
  }));
  const idx = {}; __mgNodes.forEach(n=>idx[n.name]=n.id);
  __mgAdj = {}; __mgNodes.forEach(n=>__mgAdj[n.id]=[]);
  // Token-overlap edges: two concepts link if they share a meaningful word.
  const stop = new Set(['the','and','of','a','to','for','in','on','with','is','are','it','this','that','via','from','at','by']);
  const toks = (s)=> new Set((s.toLowerCase().match(/[a-z0-9]+/g)||[]).filter(w=>w.length>2 && !stop.has(w)));
  const sets = __mgNodes.map(n=>toks(n.name));
  __mgEdges = [];
  for(let i=0;i<__mgNodes.length;i++){
    for(let j=i+1;j<__mgNodes.length;j++){
      const inter = [...sets[i]].filter(w=>sets[j].has(w));
      if(inter.length){ __mgEdges.push({a:i,b:j,w:inter.length}); __mgAdj[i].push(j); __mgAdj[j].push(i); }
    }
  }
  // Orphans (degree 0) get ringed red; wire them to the most-connected hub so
  // they're still reachable (mirrors understory's "orphans ringed red" cue).
  const deg = __mgNodes.map(n=>__mgAdj[n.id].length);
  const hub = deg.indexOf(Math.max(...deg));
  __mgNodes.forEach(n=>{ n.deg=__mgAdj[n.id].length; n.orphan = n.deg===0; });
  __mgNodes.forEach(n=>{ if(n.orphan && hub>=0){ __mgEdges.push({a:hub,b:n.id,w:1}); __mgAdj[hub].push(n.id); __mgAdj[n.id].push(hub); n.deg=1; } });
}

// Force simulation step (alive but calm).
function mgStep(){
  const W = $('#graph-canvas').width, H = $('#graph-canvas').height;
  const cx=W/2, cy=H/2;
  for(const n of __mgNodes){
    let fx=0, fy=0;
    for(const m of __mgNodes){ if(m===n) continue;
      let dx=n.x-m.x, dy=n.y-m.y; let d2=dx*dx+dy*dy+0.01; let d=Math.sqrt(d2);
      const rep=2200/d2; fx+=dx/d*rep; fy+=dy/d*rep;
    }
    fx += (cx-n.x)*0.0015; fy += (cy-n.y)*0.0015;   // gentle centering
    n.vx=(n.vx+fx)*0.85; n.vy=(n.vy+fy)*0.85;
  }
  for(const e of __mgEdges){
    const a=__mgNodes[e.a], b=__mgNodes[e.b];
    let dx=b.x-a.x, dy=b.y-a.y; let d=Math.sqrt(dx*dx+dy*dy)+0.01;
    const target=70/Math.max(1,e.w); const k=(d-target)*0.02;
    const fx=dx/d*k, fy=dy/d*k;
    a.vx+=fx; a.vy+=fy; b.vx-=fx; b.vy-=fy;
  }
  for(const n of __mgNodes){ n.x+=n.vx; n.y+=n.vy; }
}

function mgDraw(){
  const cv=$('#graph-canvas'); const x=cv.getContext('2d');
  const W=cv.width, H=cv.height;
  x.clearRect(0,0,W,H);
  x.save();
  x.translate(__mgPan.x, __mgPan.y); x.scale(__mgZoom, __mgZoom);
  // edges
  x.lineWidth=1;
  for(const e of __mgEdges){
    const a=__mgNodes[e.a], b=__mgNodes[e.b];
    const hot = __mgSel!=null && (e.a===__mgSel||e.b===__mgSel);
    x.strokeStyle = hot ? 'rgba(51,255,153,0.9)' : 'rgba(35,224,255,0.16)';
    x.beginPath(); x.moveTo(a.x,a.y); x.lineTo(b.x,b.y); x.stroke();
  }
  // nodes
  const t=performance.now()/600;
  for(const n of __mgNodes){
    const r = 7 + Math.min(14, n.deg*2.2);
    const bob = Math.sin(t+n.phase)*1.6;
    // halo for selected / hover
    if(__mgSel===n.id || __mgHover===n.id){
      x.beginPath(); x.arc(n.x,n.y+bob, r+7, 0, Math.PI*2);
      x.fillStyle='rgba(51,255,153,0.12)'; x.fill();
    }
    // orphan ring (red)
    if(n.orphan){ x.beginPath(); x.arc(n.x,n.y+bob, r+3, 0, Math.PI*2); x.strokeStyle='#ff4d4d'; x.lineWidth=2; x.stroke(); }
    x.beginPath(); x.arc(n.x,n.y+bob, r, 0, Math.PI*2);
    x.fillStyle = MG_PALETTE[n.cat] || MG_PALETTE.concept; x.fill();
    x.lineWidth=1.5; x.strokeStyle='rgba(0,0,0,0.35)'; x.stroke();
    // label
    x.fillStyle='rgba(220,255,240,0.92)'; x.font='10px ui-monospace, monospace'; x.textAlign='center';
    const lbl = n.name.length>18 ? n.name.slice(0,16)+'…' : n.name;
    x.fillText(lbl, n.x, n.y+bob+r+11);
  }
  x.restore();
}

function mgLoop(){
  if(__mgNodes.length){ mgStep(); mgDraw(); }
  __mgRAF = requestAnimationFrame(mgLoop);
}

function mgPick(mx,my){
  // screen -> world
  const wx=(mx-__mgPan.x)/__mgZoom, wy=(my-__mgPan.y)/__mgZoom;
  for(const n of __mgNodes){ const r=7+Math.min(14,n.deg*2.2);
    if((n.x-wx)**2+(n.y-wy)**2 <= (r+4)**2) return n; }
  return null;
}

/* ---------- panel side-detail when a node is selected ---------- */
function mgShowDetail(id){
  const n=__mgNodes[id]; if(!n) return;
  const panel=$('#mg-detail'); if(!panel) return;
  const neighbors=__mgAdj[id].map(j=>__mgNodes[j].name);
  panel.innerHTML = `<div class="mg-d-title" style="color:${MG_PALETTE[n.cat]}">${n.name}</div>
    <div class="mg-d-meta">type: ${n.cat} · links: ${n.deg}${n.orphan?' · <span style="color:#ff4d4d">orphan</span>':''}</div>
    <div class="mg-d-sub">connected to</div>
    <div class="mg-d-neigh">${neighbors.length?neighbors.map(x=>`<span>${x}</span>`).join(''):'<i>none</i>'}</div>
    <div class="mg-d-sub">ask the memory about this</div>
    <div style="display:flex;gap:6px"><input id="mg-q" placeholder="e.g. what is ${n.name}?" style="flex:1"/><button id="mg-q-go" class="accent">↵</button></div>
    <div id="mg-q-res" class="mg-d-res"></div>`;
  $('#mg-q-go').onclick=async ()=>{
    const q=$('#mg-q').value.trim(); if(!q) return;
    const res=$('#mg-q-res'); res.textContent='…thinking';
    try{ const r=await fetch(API.base+'/api/memory/query',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:q})});
      const d=await r.json(); res.textContent=(d.results||'').slice(0,600)||'(no answer)';
    }catch(e){ res.textContent='error'; }
  };
}

// ---------- main load ----------
async function loadMemoryGraph(){
  try{
    const r=await fetch(API.base+'/api/memory/graph'); const d=await r.json();
    if(!d.ok){ $('#mg-concepts').textContent='!'; return; }
    $('#mg-concepts').textContent=d.concepts??'–';
    $('#mg-dirs').textContent=d.directories??'–';
    $('#mg-links').textContent=(d.graph&&d.graph.links)!=null?d.graph.links:'–';
    $('#mg-orphans').textContent=(d.graph&&d.graph.orphans)!=null?d.graph.orphans:'–';
    $('#mg-conform').textContent=d.conformant?'yes':'no';
    if(__mgRAF) cancelAnimationFrame(__mgRAF);
    const names = (d.types&&d.types.length)?d.types : ['Concept'];
    mgBuild(names);
    mgRenderLegend();
    mgResize();
    mgWire();
    mgLoop();
  }catch(e){ console.error(e); }
}

function mgRenderLegend(){
  const el=$('#mem-legend'); if(!el) return;
  el.innerHTML = Object.entries(MG_PALETTE).map(([k,v])=>
    `<span class="lg"><i style="background:${v}"></i>${k}</span>`).join('');
}

function mgResize(){
  const cv=$('#graph-canvas'), wrap=$('#graph-wrap');
  if(!cv||!wrap) return;
  cv.width=wrap.clientWidth; cv.height=wrap.clientHeight;
}

/* ---------- interaction wiring ---------- */
function mgWire(){
  const cv=$('#graph-canvas'); if(!cv || cv.__wired) return; cv.__wired=true;
  const pos=e=>{ const r=cv.getBoundingClientRect(); return [e.clientX-r.left, e.clientY-r.top]; };
  cv.addEventListener('mousedown', e=>{
    const [mx,my]=pos(e); const n=mgPick(mx,my);
    if(n){ __mgDrag={node:n, dx:n.x-(mx-__mgPan.x)/__mgZoom, dy:n.y-(my-__mgPan.y)/__mgZoom, moved:false}; }
    else { __mgDrag={pan:true, sx:mx-__mgPan.x, sy:my-__mgPan.y}; }
  });
  window.addEventListener('mousemove', e=>{
    const [mx,my]=pos(e);
    if(!__mgDrag) { __mgHover = mgPick(mx,my)?.id ?? null; cv.style.cursor=__mgHover!=null?'pointer':'grab'; return; }
    if(__mgDrag.node){ __mgDrag.moved=true; __mgDrag.node.x=(mx-__mgPan.x)/__mgZoom - __mgDrag.dx; __mgDrag.node.y=(my-__mgPan.y)/__mgZoom - __mgDrag.dy; __mgDrag.node.vx=0; __mgDrag.node.vy=0; }
    else if(__mgDrag.pan){ __mgPan.x=mx-__mgDrag.sx; __mgPan.y=my-__mgDrag.sy; }
  });
  window.addEventListener('mouseup', e=>{
    if(__mgDrag && __mgDrag.node && !__mgDrag.moved){ __mgSel=__mgDrag.node.id; mgShowDetail(__mgSel); }
    __mgDrag=null;
  });
  cv.addEventListener('wheel', e=>{ e.preventDefault(); const [mx,my]=pos(e);
    const f = e.deltaY<0?1.1:0.9; const nz=Math.max(0.4,Math.min(3,__mgZoom*f));
    // zoom toward cursor
    __mgPan.x = mx - (mx-__mgPan.x)*(nz/__mgZoom); __mgPan.y = my - (my-__mgPan.y)*(nz/__mgZoom);
    __mgZoom=nz;
  }, {passive:false});
}
