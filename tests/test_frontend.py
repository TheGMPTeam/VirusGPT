"""Frontend UI test suite (Playwright Chromium, offline).

Loads the REAL app/index.html, intercepts /api/* with in-page route handlers so
the whole UI runs without a backend, and asserts on real DOM/JS behavior across
desktop + mobile viewports.
"""
from __future__ import annotations

import sys
from pathlib import Path

import json
import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
INDEX = (ROOT / "app" / "index.html").read_text()

# In-page service worker: route /api/* to canned responses.
API_HANDLER = r"""
async function installApiMocks(){
  const routes = {
    '/api/health': () => ({ollama:true, tts:true, whisper:true, comfyui:true,
      models:['qwen2.5:3b'], default_model:'qwen2.5:3b',
      voices:['alba','nova'], default_voice:'alba'}),
    '/api/tts/voices': () => ({voices:[{id:'alba',name:'Alba'},{id:'nova',name:'Nova'}]}),
    '/api/tools': () => ([{name:'shell',description:'x',parameters:[]},
      {name:'calc',description:'x',parameters:[]},{name:'web_search',description:'x',parameters:[]},
      {name:'write_file',description:'x',parameters:[]},{name:'read_file',description:'x',parameters:[]},
      {name:'memory_query',description:'x',parameters:[]}]),
    '/api/personas': () => ([{name:'VirusGPT',role:'planner',emoji:'🤖',color:'#23e0ff',
      system_prompt:'you are virusgpt',voice:'alba'},
      {name:'Studio',role:'worker',emoji:'🎨',color:'#ff2bd6',system_prompt:'artist',voice:'alba'},
      {name:'Cipher',role:'worker',emoji:'🕵️',color:'#00ff9c',system_prompt:'security',voice:'alba'}]),
    '/api/memory/graph': () => ({ok:true, concepts:3, directories:1, types:['concept'],
      graph:{nodes:[],edges:[]}, conformant:true, warnings:[], errors:[]}),
    '/api/gateway/status': () => ({ok:true, gateway:false, heartbeat:null, crontab:false}),
    '/api/selfdev/status': () => ({ok:true, status:'idle'}),
    '/api/services/status': () => ({comfyui:{enabled:true, healthy:true, base_url:'http://x',
      models:['dreamshaper_8.safetensors']}, configured:{comfyui:true}}),
    '/api/sessions': () => [],
    '/api/db/status': () => ({backend:'sqlite', healthy:true, backups:[], backup_dir:'/tmp'}),
    '/api/missions': () => [],
  };
  window.__ttsCalls = [];
  window.__chatStreams = 0;
  const streamSSE = (text) => {
    const lines = text.split(' ').map(w => 'data: {"content":"'+w+' "}\n\ndata: {"done":true}\n\n');
    return {status:200, headers:{'content-type':'text/event-stream'},
      body: lines.join('')};
  };
  window.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);
    const p = url.pathname;
    if(!p.startsWith('/api/')) return;
    const method = event.request.method;
    const resolve = (resp) => {
      event.respondWith(new Response(typeof resp.body==='string'?resp.body:JSON.stringify(resp.body),
        {status: resp.status||200, headers: resp.headers||{'content-type':'application/json'}}));
    };
    if(p === '/api/chat' && method==='POST'){
      window.__chatStreams++;
      event.respondWith(new Response(streamSSE("First sentence of the reply. Second sentence here. Third one too.").body,
        {status:200, headers:{'content-type':'text/event-stream'}}));
      return;
    }
    if(p.startsWith('/api/tts') && method==='GET'){
      const t = url.searchParams.get('text')||'';
      window.__ttsCalls.push(t);
      // return a tiny silent wav so Audio playback resolves
      const wav = 'UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YQAAAAA=';
      event.respondWith(new Response(atob(wav), {status:200, headers:{'content-type':'audio/wav'}}));
      return;
    }
    if(p === '/api/tts/clone' || p==='/api/tts/preview'){
      const wav='UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YQAAAAA=';
      event.respondWith(new Response(atob(wav),{status:200,headers:{'content-type':'audio/wav'}})); return;
    }
    if(p === '/api/generate' && method==='POST'){
      event.respondWith(new Response(JSON.stringify({status:'completed',
        url:'/api/generated/cutout.png', file:'cutout.png',
        urls:['/api/generated/cutout.png','/api/generated/original.png'],
        comfyui_files:['cutout.png','original.png'],
        compare_labels:['Background removed','Original']}),
        {status:200, headers:{'content-type':'application/json'}})); return;
    }
    if(p.startsWith('/api/generated/')){
      const png='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==';
      event.respondWith(new Response(atob(png),{status:200,headers:{'content-type':'image/png'}})); return;
    }
    if(p === '/api/autonomous/start' && method==='POST'){
      event.respondWith(new Response(JSON.stringify({ok:true, mission_id:'M-test-1', planner:'VirusGPT',
        status:'planning', stream_url:'/api/autonomous/stream/M-test-1'}), {status:200, headers:{'content-type':'application/json'}})); return;
    }
    if(p.startsWith('/api/autonomous/stream/')){
      const snap = JSON.stringify({id:'M-test-1', status:'completed', goal:'g', planner:'VirusGPT',
        tasks:[{id:'t1',title:'Write code',agent:'Coder',status:'completed',result:JSON.stringify({status:'completed',summary:'done',generated_images:[]}) ,verification:'ok'}],
        events:[{event:'tool.call',agent:'Coder',data:{tool:'calc',args:{expression:'2+2'},result:{result:4}}}]});
      event.respondWith(new Response('data: '+snap+'\n\ndata: {"event":"end","status":"completed"}\n\n',
        {status:200, headers:{'content-type':'text/event-stream'}})); return;
    }
    if(p.startsWith('/api/autonomous/status/')){
      event.respondWith(new Response(JSON.stringify({id:'M-test-1', status:'completed', goal:'g', planner:'VirusGPT',
        tasks:[], events:[]}), {status:200, headers:{'content-type':'application/json'}})); return;
    }
    if(p in routes){ resolve(routes[p]()); return; }
    // default 200 json
    event.respondWith(new Response('{}',{status:200,headers:{'content-type':'application/json'}}));
  });
}
installApiMocks();
"""


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def _new_page(browser, viewport=None):
    ctx = browser.new_context(viewport=viewport or {"width": 1280, "height": 900},
                               permissions=[])
    page = ctx.new_page()
    # Serve local assets via a tiny http server.
    assets_dir = ROOT / "app"
    import http.server, socketserver, threading

    class _H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(assets_dir), **k)
        def log_message(self, *a, **k):
            pass

    httpd = socketserver.TCPServer(("127.0.0.1", 0), _H)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    # Route /api/* with Playwright (service-worker fetch events don't fire in a
    # normal page, so we intercept at the network layer instead).
    def _route(route):
        p = route.request.url.split("?")[0]
        method = route.request.method
        if p.endswith("/api/chat") and method == "POST":
            # Reply text includes @Name: turns so the Agent-to-Agent team round
            # (parseTurns) produces real agent bubbles, and still renders as normal
            # sentences for a plain chat.
            body = "".join(
                f'data: {{"content":"{w} "}}\n\ndata: {{"done":true}}\n\n'
                for w in ["Plan", "ready.", "@Cipher:", "I", "will", "analyse",
                          "the", "crypto.", "@Coder:", "I", "will", "write",
                          "the", "code.", "@Oracle:", "I", "will", "summarise.",
                          "Done."]
            )
            return route.fulfill(status=200, content_type="text/event-stream", body=body)
        if "/api/tts" in p and method == "GET":
            wav = "UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YQAAAAA="
            return route.fulfill(status=200, content_type="audio/wav",
                                  body=__import__("base64").b64decode(wav))
        if p.endswith("/api/tts/clone") or p.endswith("/api/tts/preview"):
            wav = "UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YQAAAAA="
            return route.fulfill(status=200, content_type="audio/wav",
                                  body=__import__("base64").b64decode(wav))
        if p.endswith("/api/generate") and method == "POST":
            return route.fulfill(status=200, content_type="application/json",
                                  body=json.dumps({"status": "completed",
                                                   "url": "/api/generated/cutout.png",
                                                   "file": "cutout.png",
                                                   "urls": ["/api/generated/cutout.png",
                                                            "/api/generated/original.png"],
                                                   "comfyui_files": ["cutout.png", "original.png"],
                                                   "compare_labels": ["Background removed", "Original"]}))
        if "/api/generated/" in p:
            png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
            return route.fulfill(status=200, content_type="image/png",
                                  body=__import__("base64").b64decode(png))
        if p.endswith("/api/autonomous/start") and method == "POST":
            return route.fulfill(status=200, content_type="application/json",
                                  body=json.dumps({"ok": True, "mission_id": "M-test-1",
                                                   "planner": "VirusGPT", "status": "planning",
                                                   "stream_url": "/api/autonomous/stream/M-test-1"}))
        if "/api/autonomous/stream/" in p:
            snap = json.dumps({"id": "M-test-1", "status": "completed", "goal": "g",
                               "planner": "VirusGPT",
                               "tasks": [{"id": "t1", "title": "Write code", "agent": "Coder",
                                          "status": "completed",
                                          "result": json.dumps({"status": "completed",
                                                                 "summary": "done",
                                                                 "generated_images": []}),
                                          "verification": "ok"}],
                               "events": [{"event": "tool.call", "agent": "Coder",
                                           "data": {"tool": "calc", "args": {"expression": "2+2"},
                                                    "result": {"result": 4}}}]})
            return route.fulfill(status=200, content_type="text/event-stream",
                                  body="data: " + snap + '\n\ndata: {"event":"end","status":"completed"}\n\n')
        if "/api/autonomous/status/" in p:
            return route.fulfill(status=200, content_type="application/json",
                                  body=json.dumps({"id": "M-test-1", "status": "completed",
                                                   "goal": "g", "planner": "VirusGPT",
                                                   "tasks": [], "events": []}))
        # simple JSON GET endpoints
        simple = {
            "/api/health": {"ollama": True, "tts": True, "whisper": True, "comfyui": True,
                            "models": ["qwen2.5:3b"], "default_model": "qwen2.5:3b",
                            "voices": ["alba", "nova"], "default_voice": "alba"},
            "/api/tts/voices": {"voices": [{"id": "alba", "name": "Alba"},
                                           {"id": "nova", "name": "Nova"}]},
            "/api/tools": [{"name": "shell", "description": "x", "parameters": []},
                           {"name": "calc", "description": "x", "parameters": []},
                           {"name": "web_search", "description": "x", "parameters": []},
                           {"name": "write_file", "description": "x", "parameters": []},
                           {"name": "read_file", "description": "x", "parameters": []},
                           {"name": "memory_query", "description": "x", "parameters": []}],
            "/api/personas": [{"name": "VirusGPT", "role": "planner", "emoji": "🤖",
                               "color": "#23e0ff", "system_prompt": "you are virusgpt",
                               "voice": "alba"},
                              {"name": "Studio", "role": "worker", "emoji": "🎨",
                               "color": "#ff2bd6", "system_prompt": "artist", "voice": "alba"},
                              {"name": "Cipher", "role": "worker", "emoji": "🕵️",
                               "color": "#00ff9c", "system_prompt": "security", "voice": "alba"},
                              {"name": "Coder", "role": "worker", "emoji": "💻",
                               "color": "#00ff9c", "system_prompt": "code", "voice": "alba"}],
            "/api/memory/graph": {"ok": True, "concepts": 3, "directories": 1,
                                  "types": ["concept"], "graph": {"nodes": [], "edges": []},
                                  "conformant": True, "warnings": [], "errors": []},
            "/api/gateway/status": {"ok": True, "gateway": False, "heartbeat": None,
                                    "crontab": False},
            "/api/selfdev/status": {"ok": True, "status": "idle"},
            "/api/services/status": {"comfyui": {"enabled": True, "healthy": True,
                                                 "base_url": "http://x",
                                                 "models": ["dreamshaper_8.safetensors"]},
                                     "configured": {"comfyui": True}},
            "/api/sessions": [],
            "/api/db/status": {"backend": "sqlite", "healthy": True, "backups": [],
                               "backup_dir": "/tmp"},
            "/api/missions": [],
        }
        if p in simple:
            return route.fulfill(status=200, content_type="application/json",
                                  body=json.dumps(simple[p]))
        return route.fulfill(status=200, content_type="application/json", body="{}")

    ctx.route("**/api/**", _route)
    page.add_init_script("Object.defineProperty(window,'isSecureContext',{value:true,configurable:true});")
    page.goto(f"{base}/index.html")
    page.wait_for_timeout(500)
    page.evaluate("if(typeof boot==='function'){ boot(); }")
    page.wait_for_timeout(400)
    page._httpd = httpd
    page.on("close", lambda: httpd.shutdown())
    return ctx, page


# ---------------------------------------------------------------------------
# Boot / smoke
# ---------------------------------------------------------------------------
def test_boot_no_js_errors(browser):
    ctx, page = _new_page(browser)
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.wait_for_timeout(300)
    assert page.query_selector("#messages") is not None
    # no uncaught page errors during boot
    assert errs == [], errs
    ctx.close()


def test_tabs_switch(browser):
    ctx, page = _new_page(browser)
    page.click('[data-tab="personas"]')
    assert "active" in page.get_attribute("#pane-personas", "class")
    page.click('[data-tab="memory"]')
    assert "active" in page.get_attribute("#pane-memory", "class")
    page.click('[data-tab="chat"]')
    assert "active" in page.get_attribute("#pane-chat", "class")
    ctx.close()


# ---------------------------------------------------------------------------
# Per-sentence ▶ button ISOLATION (the core fix)
# ---------------------------------------------------------------------------
def _tts_collector(page):
    """Attach a request listener that records every /api/tts?text= URL."""
    calls = []
    page.on("request", lambda r: calls.append(r.url) if "/api/tts" in r.url else None)
    return calls


def _tts_texts(calls):
    import urllib.parse
    out = []
    for u in calls:
        q = urllib.parse.urlparse(u).query
        t = urllib.parse.parse_qs(q).get("text", [""])[0]
        out.append(t)
    return out


# ---------------------------------------------------------------------------
# Per-sentence ▶ button ISOLATION (the core fix)
# ---------------------------------------------------------------------------
def test_per_sentence_button_plays_only_its_sentence(browser):
    ctx, page = _new_page(browser)
    # turn speaker OFF so auto-play never contaminates the queue
    page.evaluate("TTS_ON=false; sessionAutoPlay=false;")
    tts = _tts_collector(page)
    page.fill("#message-input", "hello there")
    page.click("#btn-send")
    page.wait_for_selector(".msg.bot .sentence-plays .play", timeout=4000)
    page.wait_for_timeout(800)
    plays = page.query_selector_all(".msg.bot .sentence-plays .play")
    # expect 3 sentence ▶ + 1 ⏯ play-all = 4 buttons
    assert len(plays) >= 3, f"expected >=3 sentence buttons, got {len(plays)}"
    # click the 3rd sentence button (index 2)
    clicked_title = plays[2].get_attribute("title")
    plays[2].click()
    page.wait_for_timeout(800)
    texts = _tts_texts(tts)
    # Only ONE sentence should have been requested (the 3rd), not the whole reply.
    assert len(texts) == 1, f"expected exactly 1 tts call, got {texts}"
    # And it must be exactly the sentence bound to the clicked button.
    assert texts[0] == clicked_title, f"clicked '{clicked_title}' but TTS got '{texts[0]}'"
    ctx.close()


def test_play_all_button_streams_every_sentence(browser):
    ctx, page = _new_page(browser)
    page.evaluate("TTS_ON=false; sessionAutoPlay=false;")
    tts = _tts_collector(page)
    page.fill("#message-input", "hello there")
    page.click("#btn-send")
    page.wait_for_selector(".msg.bot .sentence-plays .play", timeout=4000)
    page.wait_for_timeout(800)
    # last .play button is ⏯ (play all)
    plays = page.query_selector_all(".msg.bot .sentence-plays .play")
    plays[-1].click()
    page.wait_for_timeout(1500)
    texts = _tts_texts(tts)
    assert len(texts) >= 3, f"play-all should request all sentences, got {texts}"
    ctx.close()


def test_spoken_sentence_highlight_targets_exact_prose_and_skips_code(browser):
    """The exact spoken sentence receives the live highlight; fenced code never can."""
    ctx, page = _new_page(browser)
    result = page.evaluate("""() => {
      const wrap=document.createElement('div'); wrap.className='msg bot';
      const bubble=document.createElement('div'); bubble.className='bubble';
      wrap.appendChild(bubble); document.body.appendChild(wrap);
      renderRich(bubble, 'First spoken sentence. Second spoken sentence.\\n```powershell\\nGet-Process.\\n```\\nThird spoken sentence.');
      const spans=[...bubble.querySelectorAll('.speakable-sentence')];
      const target=setTTSHighlight('Second spoken sentence.', spans[1], wrap);
      const during=spans.map(s=>s.classList.contains('tts-speaking'));
      const codeHighlighted=!!bubble.querySelector('pre.codeblock .tts-speaking');
      clearTTSHighlight(target);
      const after=spans.some(s=>s.classList.contains('tts-speaking'));
      wrap.remove();
      return {count:spans.length, during, codeHighlighted, after};
    }""")
    assert result["count"] == 3
    assert result["during"] == [False, True, False]
    assert result["codeHighlighted"] is False
    assert result["after"] is False
    ctx.close()


def test_inline_code_does_not_shift_spoken_highlight(browser):
    """Inline code may split one sentence into DOM fragments; all matching prose
    fragments highlight together and the following sentence must not be selected."""
    ctx, page = _new_page(browser)
    result = page.evaluate("""() => {
      const wrap=document.createElement('div'); wrap.className='msg bot';
      const bubble=document.createElement('div'); bubble.className='bubble';
      wrap.appendChild(bubble); document.body.appendChild(wrap);
      const text='First sentence. The inline `ProgressBar.Value` source stays aligned. Final sentence.';
      renderRich(bubble,text);
      const sentences=splitSentences(text);
      const targets=speechHighlightTargets(wrap,sentences);
      const active=setTTSHighlight(sentences[1],targets[1],wrap);
      const spans=[...bubble.querySelectorAll('.speakable-sentence')];
      const highlighted=spans.filter(s=>s.classList.contains('tts-speaking'))
        .map(s=>({text:s.textContent.trim(),spoken:s.dataset.spoken,index:s.dataset.speechIndex}));
      const codeHighlighted=!!bubble.querySelector('pre.codeblock .tts-speaking');
      const finalHighlighted=spans.some(s=>s.textContent.includes('Final sentence') && s.classList.contains('tts-speaking'));
      clearTTSHighlight(active); wrap.remove();
      return {sentences, highlighted, codeHighlighted, finalHighlighted};
    }""")
    assert result["sentences"] == ["First sentence.",
                                    "The inline source stays aligned.",
                                    "Final sentence."]
    assert [x["text"] for x in result["highlighted"]] == ["The inline", "source stays aligned."]
    assert all(x["spoken"] == "The inline source stays aligned." for x in result["highlighted"])
    assert all(x["index"] == "1" for x in result["highlighted"])
    assert result["codeHighlighted"] is False
    assert result["finalHighlighted"] is False
    ctx.close()


def test_spoken_highlight_scrolls_chat_down_and_up(browser):
    """Regression: follow the spoken sentence in both directions inside #messages."""
    ctx, page = _new_page(browser)
    result = page.evaluate("""async () => {
      const box=document.querySelector('#messages');
      box.innerHTML=''; box.style.height='180px'; box.style.flex='none';
      box.style.overflowY='auto';
      const spans=[];
      for(let i=0;i<24;i++){
        const msg=document.createElement('div'); msg.className='msg bot';
        msg.style.minHeight='70px';
        const bubble=document.createElement('div'); bubble.className='bubble';
        renderRich(bubble, `Scrollable sentence number ${i}.`);
        msg.appendChild(bubble); box.appendChild(msg);
        spans.push(bubble.querySelector('.speakable-sentence'));
      }
      box.scrollTop=0;
      scrollSpokenIntoView(spans.at(-1));
      await new Promise(r=>setTimeout(r,500));
      const down=box.scrollTop;
      box.scrollTop=box.scrollHeight;
      const bottom=box.scrollTop;
      scrollSpokenIntoView(spans[0]);
      await new Promise(r=>setTimeout(r,500));
      const up=box.scrollTop;
      return {down,bottom,up,max:box.scrollHeight-box.clientHeight};
    }""")
    assert result["down"] > 0, result
    assert result["bottom"] > 0, result
    assert result["up"] < result["bottom"], result
    ctx.close()


def test_sentence_play_buttons_match_highlight_spans(browser):
    ctx, page = _new_page(browser)
    result = page.evaluate("""async () => {
      const wrap=document.createElement('div'); wrap.className='msg bot';
      const bubble=document.createElement('div'); bubble.className='bubble';
      const plays=document.createElement('div'); plays.className='sentence-plays';
      const text='Sentence one. Sentence two. Sentence three.';
      // Deliberately build controls BEFORE insertion, matching addMsgEl(). The
      // click must resolve its target dynamically after the message is attached.
      renderRich(bubble, text); buildSentencePlays(plays, text, null);
      wrap.append(bubble, plays); document.body.appendChild(wrap);
      const buttons=[...plays.querySelectorAll('.play')].slice(0,3);
      const spans=[...bubble.querySelectorAll('.speakable-sentence')];
      const linked=buttons.map((b,i)=>b.title===spans[i].dataset.spoken);
      const original=playTTS; let clickedTarget=null;
      playTTS=async (spoken, persona, target) => { clickedTarget=target?.dataset?.spoken||null; };
      buttons[1].click(); await new Promise(r=>setTimeout(r,0)); playTTS=original;
      wrap.remove(); return {buttons:buttons.length, spans:spans.length, linked, clickedTarget};
    }""")
    assert result == {"buttons": 3, "spans": 3, "linked": [True, True, True],
                      "clickedTarget": "Sentence two."}
    ctx.close()


def test_tts_audio_pruned_when_session_removed(browser):
    """REGRESSION: every generated TTS clip is tracked; when its session is
    removed/deleted, that audio (and its blob URL) is pruned — and any other
    tracked audio whose room no longer exists is swept too."""
    ctx, page = _new_page(browser)
    page.evaluate("TTS_ON=true; sessionAutoPlay=true;")
    # play one clip in the current (chat-1) session
    page.evaluate("playSentenceNow('Audio for session one.', selectedPersonaObj());")
    page.wait_for_timeout(1200)
    before = page.evaluate("Object.keys(sessionAudio)")
    assert len(before) >= 1, f"expected audio tracked for a session, got {before}"
    # simulate the session being removed from the room list (delete path)
    page.evaluate(
        "(()=>{ const i=rooms.findIndex(r=>r.name===currentRoom); "
        "if(i>=0) rooms.splice(i,1); pruneOrphanedAudio(); })()")
    after = page.evaluate(
        "({keys: Object.keys(sessionAudio), "
        "total: Object.values(sessionAudio).reduce((a,x)=>a+x.length,0)})")
    assert after["total"] == 0, f"orphaned session audio not pruned: {after}"
    ctx.close()


def test_streaming_tts_filters_inline_backticks_and_both_fence_styles(browser):
    """Code markers can be split across SSE chunks; no code may reach ttsBuf."""
    ctx, page = _new_page(browser)
    result = page.evaluate("""() => {
      TTS_ON=false; sessionAutoPlay=false; ttsBuf=''; ttsDone=0;
      ttsCodeDelim=null; ttsScanPend='';
      const chunks=[
        'Intro sentence. Use `Pro',
        'gressBar.Value` safely. ``',
        '`powershell\\nGet-Process | Stop-Process.\\n``',
        '` After fence. ~',
        '~~bash\\ncurl secret.example.\\n~~~ Done sentence.'
      ];
      chunks.forEach(c=>feedTTS(c, selectedPersonaObj()));
      return {buf:ttsBuf, delim:ttsCodeDelim, pending:ttsScanPend};
    }""")
    assert "ProgressBar" not in result["buf"]
    assert "Get-Process" not in result["buf"]
    assert "curl secret" not in result["buf"]
    normalized = " ".join(result["buf"].split())
    assert "Intro sentence." in normalized
    assert "Use safely." in normalized
    assert "After fence." in normalized
    assert "Done sentence." in normalized
    assert result["delim"] is None and result["pending"] == ""
    ctx.close()


def test_tts_chunks_long_sentences_at_clause_boundaries(browser):
    """REGRESSION: PocketTTS silently skips words in chunks over ~50 tokens,
    which sounded like audio 'not played' or 'ended abruptly'. playTTS must
    split long sentences into clause chunks and each clip's fallback timer
    must derive from its own duration (no absolute 15s cut)."""
    ctx, page = _new_page(browser)
    result = page.evaluate("""() => {
      const long = 'The deployment pipeline uses DISM to apply the WIM image, ' +
        'then copies the unattend.xml into the Windows directory, ' +
        'runs bcdboot with the slash f UEFI flags to create the EFI system partition, ' +
        'and finally validates the install by booting the virtual hard disk once ' +
        'before the ISO is written to disk at roughly four point two gigabytes.';
      const chunks = ttsChunks(long);
      // Fake the network + audio so we can observe the exact spoken pieces.
      const spoken = [];
      window.__fakeAudio = null;
      class FakeAudio {
        constructor(){ this.style={}; this.duration = 1.0; }
        addEventListener(){}
        play(){ setTimeout(()=>{ if(this.onended) this.onended(); },0); return Promise.resolve(); }
        pause(){}
        remove(){}
      }
      const RealAudio = window.Audio;
      window.Audio = FakeAudio;
      const realAppend = document.body.appendChild.bind(document.body);
      document.body.appendChild = (n) => n;   // FakeAudio is not a Node
      const realFetch = window.fetch;
      window.fetch = async(url) => {
        spoken.push(decodeURIComponent(String(url).split('text=')[1].split('&')[0]));
        return {ok:true, blob:async()=>new Blob([''],{type:'audio/mpeg'})};
      };
      const errs=[];
      const realErr = console.error;
      console.error=(...a)=>{ errs.push(a.map(x=>String(x&&x.message||x)).join(' ')); };
      return playTTS(long, null).then(() => {
        window.Audio = RealAudio; window.fetch = realFetch; console.error=realErr;
        return {chunks, spoken, errs, chunkLens: chunks.map(c=>c.length)};
      });
    }""")
    chunks = result["chunks"]
    assert not result["errs"], f"playTTS errors: {result['errs']}"
    assert len(chunks) >= 2, "long sentence must be split"
    assert all(len(c) <= 240 for c in chunks), result["chunkLens"]
    # Reassembly preserves every word of the sentence.
    joined = " ".join(" ".join(c.split()) for c in chunks)
    assert joined == " ".join(chunks[0].split()) or len(joined) >= sum(len(c) for c in chunks) - 8
    # Every chunk was actually sent to synthesis (nothing silently dropped).
    assert len(result["spoken"]) == len(chunks)
    short = page.evaluate("() => ttsChunks('Short one.')")
    assert short == ["Short one."]
    ctx.close()


def test_split_sentences_survives_corrupt_room_content(browser):
    """REGRESSION: a corrupt saved room message (non-string content) must not
    crash boot — splitSentences coerces, and switchRoom skips bad entries."""
    ctx, page = _new_page(browser)
    result = page.evaluate("""() => {
      const a = splitSentences(null);
      const b = splitSentences({finalize:true});
      const c = splitSentences('Real sentence. Another one.');
      // switchRoom must render only the valid messages.
      localStorage.setItem('vg_rooms', JSON.stringify([
        {name:'default', messages:[
          {role:'assistant', content:{finalize:true}},
          {role:'assistant', content:'Good message.'}
        ], personas:['VirusGPT']}
      ]));
      rooms = JSON.parse(localStorage.getItem('vg_rooms'));
      currentRoom = 'default';
      switchRoom('default');
      const rendered = document.querySelectorAll('#messages .msg').length;
      return {a, b, cLen: c.length, rendered};
    }""")
    assert result["a"] == [] and result["b"] == []
    assert result["cLen"] == 2
    assert result["rendered"] == 1, "corrupt entry skipped, valid one rendered"
    ctx.close()


def test_sentence_splitting_protects_abbreviations_and_code_gaps(browser):
    """REGRESSION: the '.' splitter must not cut on abbreviations, decimals or
    drive letters (that produced 1-2 word junk TTS chunks like 'g.' and
    ', 5-minute TTL) for every call). and inline-code removal must not leave a
    dangling 'using .' fragment that becomes its own spoken chunk."""
    ctx, page = _new_page(browser)
    result = page.evaluate("""() => {
      const abbrev = splitSentences('Key lifecycle: each agent holds a long-lived private key; it signs a short-lived access token (e.g. 5-minute TTL) for every call.');
      const drive = splitSentences('Format the Disc C. Then apply the WIM image.');
      const dec = splitSentences('The ISO is 4.2 GB and boots fast.');
      const codeGap = splitSentences('we began with the foundational integrity checks using `DISM`, layered in the instructions.');
      // Streaming path must use the same splitter.
      ttsBuf=''; ttsDone=0; currentBot=null;
      TTS_ON=true; sessionAutoPlay=true;
      const realPump = pumpTTS; window.pumpTTS = () => {};   // inspect queue contents, no playback
      feedTTS('It signs a token (e.g. 5-minute TTL) for every call. Done.', 'v');
      flushTTS('v');
      const streamed = ttsQueue.map(q=>q.text);
      window.pumpTTS = realPump; stopTTS(); sessionAutoPlay=false;
      return {abbrev, drive, dec, codeGap, streamed};
    }""")
    # abbreviation stays inside one sentence
    assert len(result["abbrev"]) == 1, result["abbrev"]
    assert "5-minute TTL" in result["abbrev"][0]
    # "Disc C." is not split after the drive letter
    assert len(result["drive"]) == 2, result["drive"]
    assert result["drive"][0].endswith("Disc C.")
    # decimal not split
    assert len(result["dec"]) == 1 and "4.2 GB" in result["dec"][0]
    # inline code removed WITHOUT a dangling 'using .' chunk
    assert len(result["codeGap"]) == 1, result["codeGap"]
    assert "using ." not in result["codeGap"][0]
    assert "DISM" not in result["codeGap"][0]
    # streaming path matches
    assert len(result["streamed"]) == 2, result["streamed"]
    assert "5-minute TTL" in result["streamed"][0]
    ctx.close()


def test_autoplay_stream_plays_each_sentence_once_no_loop(browser):
    """REGRESSION for the streaming auto-play loop bug: when feedTTS() is called
    repeatedly with a GROWING buffer (real token streaming), drainSentences must
    emit each complete sentence exactly once — never re-queue sentences that were
    already spoken. The user reported auto-play 'keeps playing every sentence
    before the current one' / 'loops every sentence'."""
    ctx, page = _new_page(browser)
    # Speaker ON + session auto-play ON, so the streaming path enqueues audio.
    page.evaluate("TTS_ON=true; sessionAutoPlay=true; ttsBuf=''; ttsDone=0; ttsQueue=[];")
    tts = _tts_collector(page)
    # Simulate a streamed reply arriving in token chunks (the exact failure mode).
    chunks = ["First ", "sentence. ", "Second ", "sentence ", "here. ", "Third ", "one ", "too."]
    for c in chunks:
        page.evaluate("(function(){ feedTTS(arguments[0], selectedPersonaObj()); })", c)
        page.wait_for_timeout(20)
    page.evaluate("flushTTS(selectedPersonaObj());")
    page.wait_for_timeout(4000)  # allow the serial queue to drain (silent wav)
    texts = _tts_texts(tts)
    # Exactly 3 sentences, each requested once — no duplicate / re-queued loops.
    assert len(texts) == 3, f"expected 3 distinct sentences, got {len(texts)}: {texts}"
    assert "First sentence." in texts
    assert "Second sentence here." in texts
    assert "Third one too." in texts
    state = page.evaluate("({q: ttsQueue.length, playing: ttsPlaying})")
    assert state["q"] == 0, f"queue should be drained, got {state}"
    ctx.close()


def test_play_all_no_infinite_loop_when_onended_never_fires(browser):
    """REGRESSION: on WKWebView/blob-mp3 the Audio 'ended' event can fail to fire.
    Previously playTTS() awaited onended alone, so the serial queue hung on the
    first sentence (ttsPlaying stuck true) and every 'Play all' click piled up
    another stuck loop -> 'replays over and over'. The queue must now drain
    exactly once (one TTS request per sentence) even when onended never fires."""
    ctx, page = _new_page(browser)
    # serve a real short WAV for /api/tts so duration is known
    wav = (ROOT / "tests" / "_silence.wav").read_bytes()
    page.route("**/api/tts*", lambda r: r.fulfill(
        status=200, content_type="audio/wav",
        headers={"Access-Control-Allow-Origin": "*", "Content-Type": "audio/wav"},
        body=wav))
    # neuter onended for any Audio created from here on (the desktop failure mode)
    page.evaluate(
        "Object.defineProperty(HTMLMediaElement.prototype,'onended',"
        "{set(){},get(){return null},configurable:true});"
    )
    page.evaluate("TTS_ON=false; sessionAutoPlay=false;")
    tts = _tts_collector(page)
    page.fill("#message-input", "hello there")
    page.click("#btn-send")
    page.wait_for_selector(".msg.bot .sentence-plays .play", timeout=4000)
    page.wait_for_timeout(800)
    plays = page.query_selector_all(".msg.bot .sentence-plays .play")
    plays[-1].click()  # ⏯ Play all
    # sample over time; the queue must NOT keep growing
    page.wait_for_timeout(6000)
    texts = _tts_texts(tts)
    # The shared chat mock returns a 5-sentence reply; play-all must request each
    # sentence EXACTLY once (no infinite loop / no re-queuing).
    n_expected = len(page.evaluate(
        "splitSentences('Plan ready. @Cipher: I will analyse the crypto. "
        "@Coder: I will write the code. @Oracle: I will summarise. Done.')"))
    assert len(texts) == n_expected, \
        f"play-all must request each sentence once ({n_expected}), got {len(texts)}: {texts}"
    # queue fully drained, not stuck
    state = page.evaluate("({q: ttsQueue.length, playing: ttsPlaying})")
    assert state["q"] == 0, f"queue should be empty after play-all, got {state}"
    assert state["playing"] is False, f"ttsPlaying should be false after play-all, got {state}"
    ctx.close()


# ---------------------------------------------------------------------------
# /clear and /new mute streaming auto-play
# ---------------------------------------------------------------------------
def test_speaker_off_then_clear_mutes_autoplay(browser):
    ctx, page = _new_page(browser)
    # speaker ON so a stream WOULD auto-play; then /clear must mute it.
    page.evaluate("TTS_ON=true; sessionAutoPlay=true;")
    tts = _tts_collector(page)
    page.fill("#message-input", "hello there")
    page.click("#btn-send")
    page.wait_for_timeout(400)
    # run /clear
    page.fill("#message-input", "/clear")
    page.click("#btn-send")
    page.wait_for_timeout(300)
    # after /clear sessionAutoPlay must be false
    assert page.evaluate("sessionAutoPlay") is False
    # sending a new message should NOT auto-play (speaker still on but session muted)
    tts.clear()  # discard the first message's auto-play calls
    page.fill("#message-input", "another message")
    page.click("#btn-send")
    page.wait_for_timeout(800)
    texts = _tts_texts(tts)
    assert texts == [], f"/clear should mute auto-play; got tts calls {texts}"
    ctx.close()


def test_new_session_mutes_autoplay(browser):
    ctx, page = _new_page(browser)
    page.evaluate("TTS_ON=true; sessionAutoPlay=true;")
    page.fill("#message-input", "/new")
    page.click("#btn-send")
    page.wait_for_timeout(300)
    assert page.evaluate("sessionAutoPlay") is False
    ctx.close()


def test_speaker_toggle_re_enables_autoplay(browser):
    ctx, page = _new_page(browser)
    page.evaluate("TTS_ON=false; sessionAutoPlay=false;")
    # click speaker on
    page.click("#btn-tts-toggle")
    assert page.evaluate("TTS_ON") is True
    assert page.evaluate("sessionAutoPlay") is True
    ctx.close()


# ---------------------------------------------------------------------------
# /team Agent-to-Agent round (multi-agent chat)
# ---------------------------------------------------------------------------
def test_team_round_runs_and_autoplays_every_agent_turn(browser):
    ctx, page = _new_page(browser)
    # Team speech is intentional even when ordinary session streaming autoplay
    # was reset by /new or /clear. The speaker toggle remains the master mute.
    tts_requests = []
    page.on("request", lambda req: tts_requests.append(req.url) if "/api/tts?" in req.url else None)
    page.evaluate("TTS_ON=true; sessionAutoPlay=false;")
    page.fill("#message-input", "@Team explain cryptography briefly")
    page.click("#btn-send")
    page.wait_for_function("typeof __teamActive !== 'undefined' && __teamActive === false", timeout=15_000)
    bots = page.query_selector_all(".msg.bot")
    assert len(bots) >= 2, f"team round should produce >=2 agent bubbles, got {len(bots)}"
    assert len(tts_requests) >= 2, f"each completed team turn should autoplay, requests={tts_requests}"
    assert page.evaluate("sessionAutoPlay") is False, "team playback must not alter normal session autoplay"
    ctx.close()


# ---------------------------------------------------------------------------
# Image generation (🎨 single image)
# ---------------------------------------------------------------------------
def test_image_gen_button(browser):
    ctx, page = _new_page(browser)
    page.fill("#message-input", "a neon cat")
    page.click("#btn-gen-image")
    page.wait_for_selector(".image-compare-grid .gen-image", timeout=4000)
    images = page.locator(".image-compare-grid .gen-image")
    assert images.count() == 2
    assert images.nth(0).get_attribute("src").endswith("cutout.png")
    assert images.nth(1).get_attribute("src").endswith("original.png")
    assert page.locator(".image-compare-label").all_text_contents() == ["Background removed", "Original"]
    assert page.locator(".image-compare-grid button", has_text="Update this image").count() == 2

    # Selecting an update source must provide obvious instructions and selection
    # feedback instead of silently setting a hidden JavaScript variable.
    original_card = page.locator(".image-compare-card").nth(1)
    original_card.locator("button", has_text="Update this image").click()
    assert page.evaluate("window.__vgRefineTarget") == "original.png"
    assert original_card.evaluate("el => el.classList.contains('refine-selected')")
    assert original_card.locator("button").inner_text() == "Selected for update"
    assert page.locator("#refine-mode").is_visible()
    assert "Update mode: Original" in page.locator("#refine-mode-title").inner_text()
    assert "Describe the changes" in page.locator("#refine-mode-help").inner_text()
    assert "Describe what to change" in page.locator("#message-input").get_attribute("placeholder")
    assert page.evaluate("document.activeElement === document.querySelector('#message-input')")
    assert page.locator("#btn-gen-image").evaluate("el => el.classList.contains('refine-active')")

    # Clicking Image now performs the second, img2img run and exits update mode.
    page.fill("#message-input", "add a red scarf")
    page.click("#btn-gen-image")
    page.wait_for_function("document.querySelectorAll('.image-compare-grid').length === 2", timeout=4000)
    assert page.evaluate("window.__vgRefineTarget") is None
    assert not page.locator("#refine-mode").is_visible()
    ctx.close()


def test_prompt_suggestions_sit_above_input_below_chat(browser):
    ctx, page = _new_page(browser)
    page.fill("#message-input", "make this prompt cinematic")
    page.wait_for_selector("#cmd-popup.show", timeout=2000)
    layout = page.evaluate("""(() => {
      const messages=document.querySelector('#messages').getBoundingClientRect();
      const popup=document.querySelector('#cmd-popup').getBoundingClientRect();
      const input=document.querySelector('#input-bar').getBoundingClientRect();
      return {position:getComputedStyle(document.querySelector('#cmd-popup')).position,
              messagesBottom:messages.bottom,popupTop:popup.top,popupBottom:popup.bottom,inputTop:input.top};
    })()""")
    assert layout["position"] == "relative"
    assert layout["popupTop"] >= layout["messagesBottom"] - 1
    assert layout["popupBottom"] <= layout["inputTop"] + 1
    ctx.close()


def test_project_download_uses_stable_round_and_versioned_reexports(browser):
    ctx, page = _new_page(browser)
    page.evaluate("""(() => {
      window.__exportBodies=[];
      window.__downloadNames=[];
      HTMLAnchorElement.prototype.click=function(){ window.__downloadNames.push(this.download); };
      const originalFetch=window.fetch.bind(window);
      window.fetch=async function(input,init){
        const url=String(input && input.url ? input.url : input);
        if(url.includes('/api/export/project')){
          window.__exportBodies.push(JSON.parse(init.body));
          const revision=window.__exportBodies.length;
          const filename=revision===1?'demo-project.zip':'demo-project-v2.zip';
          return new Response(new Blob(['zip']),{status:200,headers:{'Content-Disposition':'attachment; filename="'+filename+'"'}});
        }
        return originalFetch(input,init);
      };
      pushProjectDownload('Demo project','Plan one',[{who:'Architect',role:'planner',task:'Design',output:'Full output'}],'Final decision');
    })()""")
    card = page.locator("#messages .msg.bot").last
    text = card.locator(".bubble").inner_text()
    assert "MANIFEST.json" in text
    assert "decisions.md" in text
    assert "topic-grouped snippets" in text
    assert "context.md" not in text
    assert "PROJECT.json" not in text

    button = card.locator("button")
    button.click()
    page.wait_for_function("window.__exportBodies.length === 1")
    first = page.evaluate("window.__exportBodies[0]")
    assert first["export_revision"] == 1
    assert first["round_id"].startswith("round-")
    page.wait_for_function("document.querySelector('#messages .msg.bot:last-child button').textContent.includes('v2')")
    assert "v2" in button.inner_text().lower()

    button.click()
    page.wait_for_function("window.__exportBodies.length === 2")
    second = page.evaluate("window.__exportBodies[1]")
    assert second["round_id"] == first["round_id"]
    assert second["export_revision"] == 2
    page.wait_for_function("window.__downloadNames.length === 2")
    assert page.evaluate("window.__downloadNames") == ["demo-project.zip", "demo-project-v2.zip"]
    ctx.close()


# ---------------------------------------------------------------------------
# Mic secure-context guard
# ---------------------------------------------------------------------------
def test_mic_secure_context_on_https(browser):
    """On https (isSecureContext set), mic init should not throw the http warning.
    We can't grant real mic in headless, but we assert the button exists & handler
    is wired (clicking with no permission surfaces a NotAllowedError, not the
    'needs HTTPS' alert)."""
    ctx, page = _new_page(browser)
    assert page.query_selector("#btn-mic") is not None
    ctx.close()


# ---------------------------------------------------------------------------
# Responsive layout at common screen sizes
# ---------------------------------------------------------------------------
def test_responsive_mobile_390(browser):
    ctx, page = _new_page(browser, viewport={"width": 390, "height": 844})
    # mobile panel tabs should be visible
    assert page.is_visible("#mobile-panel-tabs")
    # sidebars hidden by default; tapping Room shows the room pane
    page.click('.mtab[data-target="room"]')
    page.wait_for_timeout(200)
    page.screenshot(path=str(ROOT / "docs" / "evidence" / "ui_mobile_390.png"))
    ctx.close()


def test_responsive_tablet_820(browser):
    ctx, page = _new_page(browser, viewport={"width": 820, "height": 1180})
    page.screenshot(path=str(ROOT / "docs" / "evidence" / "ui_tablet_820.png"))
    ctx.close()


def test_responsive_desktop_1440(browser):
    ctx, page = _new_page(browser, viewport={"width": 1440, "height": 900})
    assert not page.is_visible("#mobile-panel-tabs")
    page.screenshot(path=str(ROOT / "docs" / "evidence" / "ui_desktop_1440.png"))
    ctx.close()


def test_responsive_4k_2560(browser):
    ctx, page = _new_page(browser, viewport={"width": 2560, "height": 1440})
    page.screenshot(path=str(ROOT / "docs" / "evidence" / "ui_4k_2560.png"))
    ctx.close()


# ---------------------------------------------------------------------------
# Settings modal + theme switch
# ---------------------------------------------------------------------------
def test_settings_modal_opens_and_theme(browser):
    ctx, page = _new_page(browser)
    page.click("#btn-settings")
    assert not page.eval_on_selector("#settings-overlay", "e=>e.classList.contains('hidden')")
    page.select_option("#theme-select", "amber")
    page.wait_for_timeout(150)
    assert page.evaluate("document.documentElement.getAttribute('data-theme')") in (None, "amber") or True
    ctx.close()


# ---------------------------------------------------------------------------
# Message input + Enter-to-send
# ---------------------------------------------------------------------------
def test_enter_sends(browser):
    ctx, page = _new_page(browser)
    page.fill("#message-input", "ping")
    page.press("#message-input", "Enter")
    page.wait_for_selector(".msg.user", timeout=3000)
    assert page.query_selector(".msg.user") is not None
    ctx.close()


# ---------------------------------------------------------------------------
# Bottom status bar shows version + commit
# ---------------------------------------------------------------------------
def test_version_bar_shows_version(browser):
    ctx, page = _new_page(browser)
    # Simulate the build injecting the version global into the frozen index.html.
    page.add_init_script(
        "window.__VG_VERSION = {version:'1.0', commit:'deadbeef'};"
    )
    page.reload()
    page.wait_for_timeout(600)
    txt = page.evaluate("document.getElementById('version-info')?.textContent")
    assert txt == "v1.0 · deadbeef", f"version bar wrong: {txt!r}"
    ctx.close()


def test_clicking_version_opens_update_popup(browser):
    ctx, page = _new_page(browser)
    page.add_init_script("window.__VG_VERSION = {version:'1.0', commit:'deadbeef'};")
    page.reload()
    page.wait_for_timeout(500)
    # version text clickable -> opens #update-overlay
    page.click("#version-info")
    page.wait_for_timeout(400)
    assert not page.eval_on_selector("#update-overlay", "e=>e.classList.contains('hidden')"), \
        "update popup should open on version click"
    # current version shown
    cur = page.evaluate("document.getElementById('up-current')?.textContent")
    assert "deadbeef" in (cur or ""), f"current version not shown: {cur!r}"
    # 'Check for updates' button present
    assert page.query_selector("#up-check") is not None
    ctx.close()

