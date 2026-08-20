# Memory Graph

VirusGPT's own concept-memory store. Concepts are markdown files with YAML
frontmatter, living in `data/memory/<type>/<name>.md`. The store is **OKF-style**
(Open Knowledge Framework): typed entities with typed links, queryable as a
graph, and rendered as a living force-directed UI.

## Layout

```
memory/
├── __init__.py
└── store.py          # the only module — load/save/query/graph operations
data/
└── memory/
    ├── concept/      # <name>.md  (YAML frontmatter + body with [[links]])
    ├── dream/
    └── ...
```

Each concept file looks like:

```markdown
---
type: concept
created: 2026-08-20
source: selfdev
---

Body text with [[other-concept]] wiki-links.
```

## API (store.py)

- `list_concepts()` → all concepts with metadata
- `get_concept(name)` → single concept (frontmatter + body)
- `save_concept(name, type, body, **meta)` — create or update
- `remove_concept(name)` — delete file + clean up links
- `retrieve(query, k=5)` — keyword-ranked context for RAG injection
- `retrieve_context(query, k=5)` — same, returns string for system prompt
- `graph()` → `{nodes, edges}` for the force-directed UI
- `autolink()` — scan bodies, add missing `[[links]]`
- `memory_status()` → stats (concepts, orphans, types)
- `fact_check(name)` — verify a concept against the live web (used by the Dreamer)

## Endpoints (via server.py)

- `GET /api/memory/graph` — full graph (nodes + edges)
- `GET /api/memory/{name}` — single concept
- `POST /api/memory/update` — create/edit
- `POST /api/memory/remove` — delete
- `POST /api/memory/autolink` — run autolinker
- `POST /api/memory/query` — RAG query (used for context injection)

## How it fits in

- Every chat turn injects the top-k concepts matching the user's message as
  additional system-prompt context (retrieval-augmented generation).
- The Dreamer (`autonomous/selfdev.py`) researches, fact-checks, and trims
  concepts on the gateway cron.
- Missions can read/write concepts via `memory_query` tool.

## Notes

- No Hermes/Understory dependency. This is VirusGPT's own store.
- The frontend renders it as a draggable, zoomable canvas (`memory.js`).
- Stale/orphan concepts are trimmed by the Dreamer's `dream_cycle`.
