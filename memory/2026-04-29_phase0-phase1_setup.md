---
name: Phase 0+1 setup
description: Project created, skeleton filled, MCP stub server built and smoke-tested
type: project
phase: 1-complete
---

## What was done

- Copied test-app-skeleton → claude-memory
- Filled AGENTS.md (architecture: mem0 + pgvector on CNPG, sentence-transformers CPU embeddings, Claude API LLM)
- Filled MVP.md, PLAN.md (4 phases), BENCHMARKS.md (baseline: 410 lines / ~7k tokens, truncated)
- Built Python MCP server (src/main.py): 4 stub tools, bearer token middleware, Starlette + mcp 1.27.0
- Dockerfile (python:3.12-slim, non-root), docker-compose (pgvector/pgvector:pg16), Makefile
- Smoke test passed: /health 200, /mcp 401 no-auth, /mcp 307 with token
- Git repo initialized locally (2 commits), not yet pushed to GitHub

## Next (Phase 2)

1. Create GitHub repo `claude-memory` under janip81, push
2. Phase 2: add mem0 + sentence-transformers + psycopg2 to requirements
3. Implement src/memory.py (mem0 client with pgvector + all-MiniLM-L6-v2 + Claude API)
4. Wire memory.py into the 4 MCP tools (replace stubs)
5. Local integration test: add → search → verify semantic match

## Key decisions made
- pgvector on CNPG (not Qdrant) — reuse existing backup infra
- all-MiniLM-L6-v2 embedded in pod (not Ollama) — no extra service
- MCP endpoint at /mcp, health at /health (k8s probes)
- Registry: ghcr.io/janip81/claude-memory
