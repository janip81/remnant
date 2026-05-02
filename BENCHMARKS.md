# BENCHMARKS.md — remnant

---

## Baseline — 2026-04-29 (pre-remnant, file-based memory)

### MEMORY.md — old system
| Metric | Value |
|--------|-------|
| Lines | 410 |
| Bytes | 28,042 (~27 KB) |
| Estimated tokens | ~7,000 |
| Truncated in context | **Yes** — system warning: "Only part of it was loaded" |

**Problems:** Full index loaded unconditionally every session. Topics from months ago consumed tokens regardless of relevance. Growing unbounded.

---

## Live benchmarks — 2026-05-02

**Environment:** prod-k8s, Ollama at `192.168.81.20:11434` (qwen2.5:7b + nomic-embed-text), CNPG PostgreSQL 16 + pgvector, gateway through Cilium `internal-shared`.

### Database state at time of benchmark
| Metric | Value |
|--------|-------|
| Active memories | 802 |
| Archived (cold storage) | 28 |
| `mem0` table size | 10 MB |
| `mem0_archive` table size | 208 kB |

---

### `/health` endpoint — 10 runs
| min | avg | max | p95 |
|-----|-----|-----|-----|
| 50 ms | 71 ms | 102 ms | 102 ms |

Includes TLS termination at gateway + pod network hop.

---

### `search_memory` — embedding + pgvector ANN search

| Query | Latency |
|-------|---------|
| `prod-k8s etcd` | 369 ms |
| `remnant tagging` | 219 ms |
| `ArgoCD sync` | 366 ms |
| `CNPG backup` | 231 ms |
| `BGP cilium` | 257 ms |
| **avg** | **288 ms** |

Breakdown: ~200 ms Ollama embed (nomic-embed-text) + ~5 ms pgvector ANN search + network.

---

### `get_all_memories` — full list (802 memories)
| min | avg | max |
|-----|-----|-----|
| 979 ms | 1,087 ms | 1,225 ms |

Fetches all rows from pgvector, serialises to JSON. Scales with memory count — not used in hot path.

---

### `add_memory` — infer=True (LLM extraction + embed + store)
| Run | Latency |
|-----|---------|
| 1 | 18,573 ms |
| 2 | 18,505 ms |
| 3 | 14,682 ms |
| **avg** | **~17,300 ms** |

**Breakdown:** ~15–17 s Ollama LLM extraction (qwen2.5:7b) + ~200 ms embedding + ~5 ms DB write.

**Note:** Content with no extractable facts (e.g. test strings) is silently discarded — LLM extraction returns empty, nothing is written. Hooks that save session summaries always have extractable content.

---

### Nightly CronJob phases — average across 5 runs (802 memories)
| Phase | avg duration | what it does |
|-------|-------------|--------------|
| Hash dedup | 0.1 s | Exact duplicate removal by content hash |
| Semantic dedup | 77.8 s | pgvector cosine similarity clustering + Ollama merge |
| LLM tagging | 195.7 s | Ollama tag assignment for all unreviewed memories |

After first full pass, LLM tagging only processes new memories (via `llm_reviewed` flag) — subsequent runs are proportionally faster.

---

## Context savings vs baseline

| Metric | Before (file-based) | After (remnant) |
|--------|---------------------|-----------------|
| Tokens at session start | ~7,000 (unconditional) | 0 |
| Tokens per memory lookup | — | ~200–500 (top-k only) |
| Relevance | All topics, always | Query-matched only |
| Truncation | Yes (past 200 lines) | Never |
| Growth | Unbounded (file) | Bounded (dedup nightly) |

---

## Log

| Date | Event | Notes |
|------|-------|-------|
| 2026-04-29 | Baseline captured | MEMORY.md 410 lines / 27 KB, truncated in context |
| 2026-04-29 | remnant deployed | mem0 MCP live, file-based memory retired |
| 2026-05-02 | Live benchmarks captured | 802 memories, prod cluster, results above |
