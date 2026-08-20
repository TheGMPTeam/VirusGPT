/* memory.js — the Memory Graph pane (OKF stats + a simple radial canvas). */

async function loadMemoryGraph(){
  try{
    const r=await fetch(API.base+'/api/memory/graph'); const d=await r.json();
    if(!d.ok){ $('#mg-concepts').textContent='!'; return; }
    $('#mg-concepts').textContent=d.concepts??'–';
    $('#mg-dirs').textContent=d.directories??'–';
    $('#mg-links').textContent=(d.graph&&d.graph.links)!=null?(d.graph.links):'–';
    $('#mg-orphans').textContent=(d.graph&&d.graph.orphans)!=null?(d.graph.orphans):'–';
    $('#mg-conform').textContent=d.conformant?'yes':'no';
    drawGraph(d);
  }catch(e){ console.error(e); }
}
function drawGraph(d){
  const cv=$('#graph-canvas'); const wrap=$('#graph-wrap');
  cv.width=wrap.clientWidth; cv.height=wrap.clientHeight;
  const x=cv.getContext('2d');
  x.clearRect(0,0,cv.width,cv.height);
  const types=d.types||[]; const n=Math.max(types.length,6);
  const cx=cv.width/2, cy=cv.height/2, R=Math.min(cx,cy)-70;
  const nodes=types.map((t,i)=>{const a=(i/n)*Math.PI*2;return{label:t, x:cx+Math.cos(a)*R, y:cy+Math.sin(a)*R};});
  // edges: connect each type to center + ring links (visualizes the 75 links concept)
  x.strokeStyle=getComputedStyle(document.documentElement).getPropertyValue('--neon2'); x.globalAlpha=0.25; x.lineWidth=1;
  nodes.forEach((nd,i)=>{ x.beginPath(); x.moveTo(cx,cy); x.lineTo(nd.x,nd.y); x.stroke();
    const nx=nodes[(i+1)%n]; x.beginPath(); x.moveTo(nd.x,nd.y); x.lineTo(nx.x,nx.y); x.stroke(); });
  x.globalAlpha=1;
  nodes.forEach(nd=>{ x.beginPath(); x.arc(nd.x,nd.y,22,0,Math.PI*2);
    x.fillStyle=getComputedStyle(document.documentElement).getPropertyValue('--panel2'); x.fill();
    x.strokeStyle=getComputedStyle(document.documentElement).getPropertyValue('--neon'); x.lineWidth=2; x.stroke();
    x.fillStyle=getComputedStyle(document.documentElement).getPropertyValue('--txt'); x.font='10px monospace'; x.textAlign='center';
    const short=nd.label.length>14?nd.label.slice(0,12)+'…':nd.label;
    x.fillText(short,nd.x,nd.y+3); });
  // center node
  x.beginPath(); x.arc(cx,cy,30,0,Math.PI*2); x.fillStyle=getComputedStyle(document.documentElement).getPropertyValue('--neon'); x.fill();
  x.fillStyle='#04130d'; x.font='bold 11px monospace'; x.textAlign='center'; x.fillText('OKF',cx,cy+4);
}
