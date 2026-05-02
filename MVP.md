# MVP.md — remnant

## What was the MVP?

A working MCP server running in prod-k8s that Claude Code can connect to from any machine.
Stores and retrieves memories semantically using pgvector on CNPG, with Ollama for embeddings.
Bearer token auth, exposed at `remnant-mcp.prod.threshold.se` via internal-shared gateway.

**Status: MVP complete. Project is now well beyond MVP scope.**

---

## MVP Checklist — all done

- [x] Python MCP server with 4 tools: `add_memory`, `search_memory`, `get_all_memories`, `delete_memory`
- [x] pgvector backend via mem0ai library
- [x] Ollama embeddings (`nomic-embed-text`) — replaced planned sentence-transformers
- [x] Ollama LLM (`qwen2.5:7b`) for tagging/dedup — replaced planned Claude API
- [x] Bearer token auth middleware
- [x] `Dockerfile.mcp` and `Dockerfile.ui` (split images)
- [x] Helm chart (0.2.3) with CNPG cluster, Deployment, Service, HTTPRoute, ServiceMonitor, CronJob
- [x] Vault/AVP secret: bearer token injected via ArgoCD AVP plugin
- [x] GitOps wiring (ArgoCD app on prod-k8s, namespace `remnant`)
- [x] `remnant-mcp.prod.threshold.se` reachable from desktop and cluster
- [x] `add_memory` stores, `search_memory` retrieves, pod restarts do not lose data
- [x] CNPG WAL archiving active to S3

---

## Beyond MVP — shipped in 0.5.0-beta

- [x] React UI for browsing, editing, and managing memories (`remnant.prod.threshold.se`)
- [x] Nightly dedup CronJob: hash dedup → semantic dedup → LLM merge
- [x] LLM tagging: keyword at write time + nightly Ollama review pass (hybrid mode)
- [x] DB-backed tag rules (editable from UI without code changes)
- [x] Job history viewer (UI tab)
- [x] Grafana dashboard (memory count, search latency, category/tag distribution)
- [x] Claude Code hooks: `UserPromptSubmit` memory injection, `PreCompact`/`Stop` saves
- [x] GitHub Actions CI (`:latest`, `:dev`, versioned tags)
- [x] `dev` branch strategy with PR-to-main flow
- [x] Benchmarks documented (`BENCHMARKS.md`)

---

## Still open / future

- [ ] Make repo public + switch CI from `CR_PAT` to `GITHUB_TOKEN`
- [ ] Helm chart sync workflow (remnant repo → helm-charts repo)
- [ ] GPU embeddings (when burst nodes are live)
- [ ] Multi-user / per-project memory scoping
- [ ] Blog series (Parts 2–10)
