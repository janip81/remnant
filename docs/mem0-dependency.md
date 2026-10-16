# How Remnant uses Mem0, and what an upgrade or replacement would mean

Status: investigation, 2026-10-16. Nothing was upgraded or replaced. The comparison
is based on the published wheels of `mem0ai` 2.0.2 (pinned in
`requirements-mcp.txt`) and 2.2.1 (the latest at the time of writing).

## What Remnant actually calls

| mem0 API | Where | Used for |
|----------|-------|----------|
| `Memory(MemoryConfig(...))` | `src/memory.py` `_build_config`, `get_memory` | pgvector store + Ollama embedder/LLM configuration |
| `Memory.add(..., infer=False)` | `add`, `save_checkpoint`, `supersede` | embed + insert one row (the normal write path) |
| `Memory.add(..., infer=True)` | `add` (opt-in) | LLM fact extraction, with fallbacks for 2.0.2's swallowed errors / `[]` / `AttributeError` |
| `Memory.embedding_model.embed` | `search` (+ `_cap_embedding_input` wrapper) | the query embedding for Remnant's own cosine search |
| `Memory.delete` | `delete` | delete one row |
| `Memory.get_all` | `get_all` | **no callers**: dead code |

Everything else goes through direct SQL against mem0's table (`mem0`: `id UUID`,
`vector`, `payload JSONB`, with the metadata keys at the top level of `payload`):

- reads, lists and stats;
- keyword search (its own stored `fts` column);
- vector search (cosine via `<=>`);
- lifecycle state changes, supersede links and checkpoints;
- tag updates;
- the dedup job.

Remnant deliberately does not use `Memory.search()`. With 2.0.2 it ranked far worse
than plain cosine on the author's data.

So the coupling to mem0 is narrow: **the write path (embedding + insert), delete,
and the table/payload layout.** The layout is the real contract, because so much
SQL depends on it.

## 2.0.2 → 2.2.1: what changed that matters here

- **Table and payload layout: unchanged.** Both versions create `id UUID PRIMARY KEY,
  vector vector(n), payload JSONB`. `_create_memory` writes the same top-level keys:
  `data`, `hash`, `created_at`, `updated_at`, `text_lemmatized`, plus the caller's
  metadata. Remnant's SQL should keep working.
- **pgvector reads:** `get`/`list` no longer select the vector column, and search
  scores are now `1 - distance`. Remnant does neither through mem0, so this has no
  effect.
- **Richer filters** (`eq/ne/in/contains/...`) in the pgvector provider. They only
  apply to `Memory.search/get_all`, which Remnant doesn't use.
- **Optional rerankers** (`cohere`, `huggingface`, `sentence_transformer`, `llm`,
  `zero_entropy`) for `Memory.search(rerank=True)`. Of these, only `llm` would work
  with the local Ollama. The others need an external API or a heavy local model
  dependency. Rerankers apply to mem0's search, not to Remnant's hybrid search.
- **`expiration_date`** (OSS) hides expired memories from mem0's search and
  `get_all`. Remnant's checkpoint TTL already works in SQL, so this adds nothing.
- **`timestamp`** is documented as platform-only, not supported in OSS. Observation
  time (see `contracts/`) therefore has to stay Remnant metadata.
- **`memory/main.py` changed substantially** (about 1,100 changed lines). It holds
  the `infer=True` pipeline that Remnant wraps with fallbacks. An upgrade has to
  re-test that path, and the workarounds may become unnecessary or wrong.

## Recommendation

1. **Keep `mem0ai==2.0.2` for now.** Nothing in 2.2.1 improves a path Remnant
   uses. The new features are behind `Memory.search`, which Remnant has replaced.
2. **Make the coupling explicit before any upgrade.** Collect the four calls
   (construct, add, embed, delete) behind one small section of `memory.py` with a
   docstring naming the table/payload contract. Add a test that writes through mem0
   and asserts the payload keys Remnant's SQL relies on. This test is what makes a
   later upgrade safe. Only do this once it simplifies the code; it doesn't need a
   framework.
3. **Remove the dead `get_all()`.**
4. **Upgrade only with evidence.** On a disposable database, run the full suite,
   plus an `infer=True` check against the local Ollama model, and compare the
   payload keys. Upgrade only if a concrete need appears: a security fix, an
   Ollama/embedder compatibility fix, or a dropped workaround.
5. **Don't replace mem0 now.** A native path (an Ollama embedding call plus one
   INSERT) would be small, but it gives up optional LLM extraction and adds code to
   maintain, without a measured benefit. Reconsider it if a mem0 upgrade becomes
   necessary *and* breaks the payload contract, or if `infer=True` stays unused long
   term. Remnant isn't meant to become a general Mem0 replacement.

## Unknowns

- How 2.2.1's `infer=True` behaves with `qwen2.5:7b`: whether the old failure
  modes are fixed or there are new ones. That needs a run against Ollama.
- Changes to the dependency footprint and import time in 2.2.1.
- Whether the history DB behaviour (`~/.mem0`) changed. See ISSUES.md #3.
