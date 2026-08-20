# Tools

Agent tool harness for VirusGPT's autonomous engine. Self-contained — no
Hermes dependency.

## Layout

```
tools/
└── hermes_bridge.py   # bridge to Hermes tools (optional; not used by default)
```

The primary tool harness lives in `autonomous/tools.py` (8 tools + schemas):
`web_search`, `web_fetch`, `shell`, `read_file`, `write_file`, `memory_query`,
`calc`, `git_commit`.

## Notes

- `tools/hermes_bridge.py` is an optional bridge for environments where Hermes
  tools are available. The autonomous engine uses its own harness by default.
- See `autonomous/README.md` §"Self-contained tool harness" for the full tool
  list and schemas.
