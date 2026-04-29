# BENCHMARKS.md — claude-memory

Baseline captured before mem0 MCP was live.
Goal: quantify context savings after switching to semantic memory retrieval.

---

## Baseline — 2026-04-29 (file-based memory, pre-mem0)

### MEMORY.md size
| Metric | Value |
|--------|-------|
| Lines | 410 |
| Bytes | 28,042 (~27KB) |
| Estimated tokens | ~7,000 (at ~4 bytes/token) |
| Truncated in context | **Yes** — system warning: "Only part of it was loaded" |

### How memory loads today
- Entire `MEMORY.md` index is injected into every session's context unconditionally
- All 410 lines loaded regardless of whether the session touches any of those topics
- Past the ~200-line system limit, content is silently truncated (confirmed by system warning)
- Detail files (e.g. `2026-04-23_prod-k8s-upgrade-argocd-fixes.md`) are NOT loaded — only the index

### Problems this causes
- ~7,000 tokens consumed at session start before any work begins
- Topics from months ago (etcd defrag, dev-mgmt setup) loaded even for unrelated sessions
- Growing unbounded — will get worse every session

---

## Target — post-mem0

| Metric | Expected |
|--------|----------|
| Tokens loaded at session start | 0 (no file injection) |
| Tokens per `search_memory` call | ~200–500 (top-k results only) |
| Truncation | Gone — memories stored externally |
| Relevance | Only facts matching the current query |

---

## How to measure after go-live

1. Start a new Claude Code session with mem0 MCP active
2. Run `/cost` immediately after first response — note baseline token count
3. Call `search_memory("etcd")` — note tokens in response
4. Compare: session-start tokens before vs after

Claude Code does not expose per-message token counts directly, but:
- `/cost` shows cumulative session usage — compare fresh sessions
- MCP server can log per-request token counts from the Anthropic API response headers

---

## Log entries

| Date | Event | Notes |
|------|-------|-------|
| 2026-04-29 | Baseline captured | MEMORY.md 410 lines / 27KB, truncated |
| _(post-deploy)_ | mem0 MCP live | Record `/cost` delta at session start |
