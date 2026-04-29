# ADR-0001 — Local LLM for Memory Extraction via Ollama

## Status
Accepted — adopted in Phase 2 (2026-04-29). Both LLM and embedder use Ollama.
qwen2.5:7b for extraction, nomic-embed-text for embeddings (768 dims), desktop at 192.168.81.20:11434.

## Context

The current design uses the Claude API (claude-haiku-4-5) for memory extraction — the step
where mem0 reads conversation content and decides what facts to store or deduplicate.

The user runs an RTX 2070 SUPER (8 GB VRAM) on their desktop. That GPU can comfortably run
7B-parameter quantized models locally via Ollama, which would eliminate the Claude API
dependency for extraction (cost, internet round-trip, API key rotation).

mem0 already supports `provider: "ollama"` for both LLM and embedder, so this is a
configuration swap, not a rewrite.

## Decision

**Not yet made.** Current plan keeps Claude API for extraction (Phase 2) and defers this
to a future phase. Reasons to defer:

- Get the system working end-to-end first; optimize later.
- Quality of extraction matters — haiku is already fast and cheap; local 7B may be worse.
- Requires Ollama to be running and reachable from the prod-k8s pod (internal network).

## Options considered

| Option | VRAM | Notes |
|--------|------|-------|
| `qwen2.5:7b` | ~4.7 GB | Good instruction following, solid extraction quality |
| `llama3.2:3b` | ~2 GB | Very fast, lighter context window |
| `phi-4-mini:3.8b` | ~2.5 GB | Strong reasoning for size |
| `mistral:7b` | ~4.1 GB | Reliable general-purpose |

For embeddings: swap `huggingface/all-MiniLM-L6-v2` → `ollama/nomic-embed-text`
(768 dims, GPU-accelerated, would require updating `embedding_model_dims` in pgvector config).

## Implementation sketch

```python
# config change in memory.py — no other code changes needed
llm=LlmConfig(
    provider="ollama",
    config={
        "model": "qwen2.5:7b",
        "ollama_base_url": "http://desktop.threshold.se:11434",
    },
),
embedder=EmbedderConfig(
    provider="ollama",
    config={
        "model": "nomic-embed-text",
        "ollama_base_url": "http://desktop.threshold.se:11434",
        "embedding_dims": 768,
    },
),
```

Note: switching embedder model requires re-indexing all existing vectors (dimensions change).
If only switching LLM, no re-indexing needed.

## Consequences

**If adopted:**
- No Claude API calls during extraction — zero cost, works offline
- Latency depends on desktop availability (not always on?)
- Extraction quality may degrade vs haiku — needs A/B testing
- Requires Ollama service exposed on LAN, reachable from prod-k8s pod
- GPU contention if desktop is in use (gaming, etc.)

**If kept as-is (Claude API):**
- Haiku is ~$0.25/1M input tokens — negligible for single-user memory
- Always available, no dependency on desktop uptime
- Best extraction quality
