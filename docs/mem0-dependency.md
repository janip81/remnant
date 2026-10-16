# How Remnant uses Mem0, and what an upgrade or replacement would mean

Status: investigation, 2026-10-16. Nothing was upgraded or replaced. The comparison
is based on the published PyPI wheels (`pip download mem0ai==<v> --no-deps`):

| Version | Wheel sha256 |
|---------|--------------|
| 2.0.2 (pinned in `requirements-mcp.txt`) | `6fda32549c213fb63b393f4b1920f54961ea75c51d95b6492096108609accf3b` |
| 2.2.1 (latest at the time of writing) | `fe91bb91ac8926231993a4aa58df00a60c6c74c709e6a338fe399500776eef4d` |

The line numbers below refer to files inside those wheels.

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
`vector`, `payload JSONB`). The payload has more than one shape. mem0 2.0.2 writes the
caller's metadata keys at the top level of `payload`, but older rows and some
pgvector write paths nest them as `payload.metadata` or even
`payload.metadata.metadata`. Remnant's code has to handle all three:

- the SQL readers use `_META` (`src/memory.py`), which picks `payload->'metadata'`
  when it's an object and falls back to the top level;
- `update_tags` (`src/memory.py`) and the dedup job's tag/metadata writers
  (`scripts/dedup_job.py`, the tagging phase) write into the innermost existing
  level.

Direct SQL handles:

- reads, lists and stats;
- keyword search (its own stored `fts` column);
- vector search (cosine via `<=>`);
- lifecycle state changes, supersede links and checkpoints;
- tag updates;
- the dedup job.

Remnant deliberately does not use `Memory.search()`. With 2.0.2 it ranked far worse
than plain cosine on the author's data.

So the coupling to mem0 is narrow: **the write path (embedding + insert), delete,
and the table/payload layout, including the nested metadata shapes.** The layout is
the real contract, because so much SQL depends on it.

## 2.0.2 → 2.2.1: what changed that matters here

- **Table and payload layout: unchanged.** Both versions create `id UUID PRIMARY KEY,
  vector vector(n), payload JSONB` (`vector_stores/pgvector.py`: 2.0.2 line 162,
  2.2.1 line 271). `_create_memory` writes the same top-level keys: `data`, `hash`,
  `created_at`, `updated_at`, `text_lemmatized`, plus the caller's metadata
  (`memory/main.py`: 2.0.2 line 1594, 2.2.1 line 1983). Remnant's SQL should keep
  working, but the contract test below has to prove it, nested shapes included.
- **pgvector reads:** `get`/`list` no longer select the vector column, and search
  scores are now `1 - distance`. Remnant does neither through mem0, so this has no
  effect. The new score is at 2.2.1 `pgvector.py` line 367.
- **Richer filters** (`eq/ne/in/contains/...`) in the pgvector provider. They only
  apply to `Memory.search/get_all`, which Remnant doesn't use.
- **Optional rerankers** (`cohere`, `huggingface`, `sentence_transformer`, `llm`,
  `zero_entropy`) for `Memory.search(rerank=True)`. Of these, only `llm` would work
  with the local Ollama. The others need an external API or a heavy local model
  dependency. Rerankers apply to mem0's search, not to Remnant's hybrid search.
- **`expiration_date`** (OSS) hides expired memories from mem0's search and
  `get_all`. Remnant's checkpoint TTL already works in SQL, so this adds nothing.
- **`timestamp`** (in `add`) and **`reference_date`** (in `search`) are documented as
  platform-only, not supported in OSS (2.2.1 `memory/main.py` lines 787 and 1435).
  Observation time (see `contracts/`) therefore has to stay Remnant metadata.
- **`memory/main.py` changed substantially** (about 1,100 changed lines). It holds
  the `infer=True` pipeline that Remnant wraps with fallbacks. An upgrade has to
  re-test that path, and the workarounds may become unnecessary or wrong.

## Recommendation

1. **Keep `mem0ai==2.0.2` for now.** 2.2.1 shows no demonstrated benefit for
   Remnant's current paths. Its new features sit behind `Memory.search`, which
   Remnant has replaced. The heavily changed `infer=True` pipeline is used, but only
   opt-in, and how it behaves now is unknown (see Unknowns).
2. **Make the coupling explicit before any upgrade.** Collect the four calls
   (construct, add, embed, delete) behind one small section of `memory.py` with a
   docstring naming the table/payload contract. Add a test that writes through mem0
   and asserts the payload keys Remnant's SQL relies on, including reading and
   tag-writing every nested metadata shape. This test is what makes a
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
