# MVP.md — claude-memory

## What is the MVP?

A working MCP server running in prod-k8s that Claude Code can connect to from both the desktop and starbase.
It stores and retrieves memories semantically using mem0 + pgvector on CNPG.
Bearer token auth, exposed at `mem0-mcp.prod.threshold.se` via internal-shared gateway.

---

## In Scope

- [x] Project skeleton and planning docs
- [ ] Python MCP server with 4 tools: add_memory, search_memory, get_all_memories, delete_memory
- [ ] mem0 integration with pgvector backend
- [ ] sentence-transformers embeddings (CPU, all-MiniLM-L6-v2, in-pod)
- [ ] Claude API as LLM provider for memory extraction
- [ ] Bearer token auth middleware
- [ ] Dockerfile (python:3.12-slim base)
- [ ] Helm chart with CNPG cluster, Deployment, Service, HTTPRoute, ExternalSecret
- [ ] Vault secret: `kubernetes/data/prod-k8s/claude-memory` (bearer token + anthropic key)
- [ ] gitops wiring (ArgoCD app on prod-k8s)

---

## Out of Scope (for MVP)

- GPU-accelerated embeddings (add later by swapping embedding provider)
- Multi-user support (single user_id hardcoded for now)
- Auto-extraction hook (Claude manually calls add_memory for now)
- Web UI for browsing memories
- Metrics / Grafana dashboard

---

## Done Criteria

The MVP is complete when:

- [ ] `claude mcp add --transport http mem0-mcp https://mem0-mcp.prod.threshold.se/` works from desktop
- [ ] `add_memory("test fact")` stores a memory and returns an ID
- [ ] `search_memory("test")` returns the stored memory
- [ ] Pod restarts do not lose memories (pgvector persisted via CNPG PVC)
- [ ] CNPG cluster has WAL archiving active to Garage S3
- [ ] README explains how to connect

---

## Future Phases

- GPU embedding backend (when stargate-prod/burst are live)
- Auto-extraction: hook into Claude Code conversation end to extract facts automatically
- Per-project memory scoping (tag memories by project)
- Grafana dashboard: memory count, search latency, embedding time
- Periodic memory consolidation (merge redundant facts)
