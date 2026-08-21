"""Pure-logic unit tests for the sentence splitter (no browser needed).

Guards the fix for the bug where splitSentences returned the WHOLE reply N times
(one per sentence) so every ▶ button bound to the entire text — clicking any
sentence played the whole reply.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MSG_JS = ROOT / "app" / "assets" / "js" / "messages.js"


def _split_via_node(text: str):
    # Prepend DOM stubs, then the real source, then export splitSentences. As a
    # real module (run via node), require/process are available.
    header = """
    global.document = { querySelector: () => null,
      createElement: () => ({style:{}, classList:{toggle(){}, add(){}}, appendChild(){},
        setAttribute(){}, set onclick(v){}}), getElementById: () => null };
    global.window = {};
    """
    src = MSG_JS.read_text()
    footer = "\nmodule.exports = { splitSentences };\n"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                      dir=str(ROOT / "tests")) as f:
        f.write(header + src + footer)
        path = f.name
    try:
        res = subprocess.run(["node", "-e",
                              f"const m=require({json.dumps(path)}); "
                              f"console.log(JSON.stringify(m.splitSentences(process.argv[1])));",
                              text],
                             capture_output=True, text=True)
    finally:
        os.unlink(path)
    if res.returncode != 0:
        raise RuntimeError(res.stderr)
    return json.loads(res.stdout.strip())


def test_split_sentences_unit():
    cases = {
        "First sentence of the reply. Second sentence here. Third one too.":
            ["First sentence of the reply.", "Second sentence here.", "Third one too."],
        "A. B. C.": ["A.", "B.", "C."],
        "Just one sentence.": ["Just one sentence."],
        "What about questions? And exclamations! Both work.":
            ["What about questions?", "And exclamations!", "Both work."],
        "Short caption: not a sentence": ["Short caption: not a sentence"],
        "No terminator here just words": ["No terminator here just words"],
        "": [],
    }
    for inp, expected in cases.items():
        got = _split_via_node(inp)
        assert got == expected, f"splitSentences({inp!r}) = {got!r}, expected {expected!r}"
