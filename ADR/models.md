# Model Selection — Notes & Future Discussion

Current models in production (prod-k8s, 2026-05-02):

| Role | Model | Host | Latency |
|------|-------|------|---------|
| Embeddings | `nomic-embed-text` | Ollama @ 192.168.81.20 | ~200ms |
| LLM tagging + dedup merge | `qwen2.5:7b` | Ollama @ 192.168.81.20 | ~15–17s |

---

## Embedding model

### Current: nomic-embed-text
- 768 dimensions
- ~200ms per call over LAN
- Good quality for semantic search

### Candidates for the future

| Model | Dims | Est. latency | Notes |
|-------|------|-------------|-------|
| `all-minilm` | 384 | ~50ms | 4x faster, lower quality, migration required |
| `mxbai-embed-large` | 1024 | ~120ms | Better quality than nomic, more memory |
| GPU-accelerated nomic | 768 | ~10ms | Same quality, needs GPU in-cluster |

**Migration cost**: changing embedding model requires re-embedding all memories and recreating the pgvector index at the new dimension size. Not a config flip.

**Decision**: stay on `nomic-embed-text` until GPU is available in-cluster (ESXi passthrough planned). At that point re-evaluate based on actual latency measurements.

---

## LLM model (tagging + dedup)

### Current: qwen2.5:7b
- ~15–17s per `add_memory` call (infer=True)
- ~195s to LLM-tag all 800 memories in nightly job
- Acceptable for nightly batch; borderline for write-time use

### Candidates for the future

| Model | Notes |
|-------|-------|
| `qwen2.5:14b` | Better extraction quality, 2x slower |
| `llama3.1:8b` | Similar size, different strengths |
| `mistral:7b` | Fast, good instruction following |
| GPT-4o (Anthropic API) | Best quality, external dependency, cost |

**Decision**: stay on `qwen2.5:7b` for now. GPU in-cluster will reduce latency enough to revisit write-time LLM extraction (currently `infer=True` but slow).

---

## Benchmark targets

When GPU is available, run `mem0ai/memory-benchmarks` (LoCoMo, LongMemEval) against remnant endpoint to get retrieval precision/recall scores. Compare:
- Current: `nomic-embed-text` + `qwen2.5:7b`
- Candidate: faster embed model + same or better LLM

See `BENCHMARKS.md` for current system latency benchmarks.

---

## GPU roadmap

- ESXi passthrough to a k8s node planned (no ETA)
- Target: Ollama running in-cluster on the GPU node
- Expected impact: embed ~200ms → ~10ms, LLM ~17s → ~2–3s
- At that point: re-embed all memories, consider upgrading to a larger LLM
