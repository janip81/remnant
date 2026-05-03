# AGENTS.md — remnant

## Overview

Remnant is a self-hosted MCP memory server for Claude Code.
Stores facts in pgvector (CNPG), retrieves them via semantic search.
Namespace: `remnant` on prod-k8s.

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| MCP server | Python, `mcp` SDK, HTTP/SSE (`FastMCP`) |
| Embeddings | Ollama `nomic-embed-text` (192.168.81.20:11434) |
| LLM (extraction) | Ollama `qwen2.5:7b` |
| Vector store | PostgreSQL + pgvector via CNPG (`remnant-pg`) |
| Async queue | Redis (`redis-replication-master.redis.svc:6379`) — optional, enabled when `REDIS_URL` set |
| Auth | Bearer token (Vault → ExternalSecret) |
| Deployment | Helm chart 0.2.3 → ArgoCD → prod-k8s |

---

## Architecture

```
Claude Code (desktop/starbase)
    │  HTTP MCP (Bearer token)
    ▼
remnant deployment  [prod-k8s, ns: remnant]
    ├── add_memory → Redis queue → queue_worker.py → pgvector
    ├── search_memory / get_all_memories / delete_memory → pgvector (sync)
    ├── Ollama (nomic-embed-text embeddings, qwen2.5:7b extraction)
    └── remnant-pg CNPG cluster (WAL → Garage S3)

remnant-ui deployment  [prod-k8s, ns: remnant]
    └── React SPA — browse/search/edit/tag memories

remnant-dedup CronJob  [03:30 daily]
    └── dedup_job.py — LLM dedup + keyword/LLM tagging → job_runs table

plugin/mcp-stdio.py  [starbase local]
    └── stdio proxy → remnant-mcp.prod.threshold.se (registered as user-scope MCP)
```

### Endpoints
| URL | Purpose |
|-----|---------|
| `https://remnant-mcp.prod.threshold.se` | MCP HTTP endpoint |
| `https://remnant.prod.threshold.se` | Web UI |

### MCP tools
| Tool | Notes |
|------|-------|
| `add_memory` | Queued via Redis when `REDIS_URL` set; params: content, agent_id, infer, category, tags |
| `search_memory` | Semantic search, returns top-k with similarity scores |
| `get_all_memories` | Full list |
| `delete_memory` | By ID |

---

## Folder Structure

```
remnant/
├── src/
│   ├── main.py           # MCP server, FastMCP tools, auth, Redis queue dispatch
│   ├── memory.py         # mem0 wrapper, VALID_CATEGORIES, tag_rules, schema
│   ├── config.py         # settings (pydantic-settings, env-driven)
│   ├── queue_worker.py   # Redis consumer — runs inside MCP pod
│   └── scripts/
│       └── tag_memories.py   # bulk backfill script
├── Dockerfile.mcp
├── Dockerfile.ui
├── helm/
│   ├── Chart.yaml        # version: 0.2.3
│   └── templates/
│       ├── deployment.yaml          # MCP pod
│       ├── deployment-ui.yaml       # UI pod
│       ├── service.yaml / service-ui.yaml
│       ├── httproute.yaml           # Gateway API, internal-shared
│       ├── cnpg-cluster.yaml
│       ├── cronjob-dedup.yaml       # 03:30 daily
│       ├── configmap-settings.yaml  # TAGGING_MODE, TAGGING_LLM_MODEL
│       ├── servicemonitor.yaml
│       └── externalsecret.yaml
├── plugin/
│   ├── mcp-stdio.py      # local stdio proxy
│   ├── hooks/            # Claude Code hooks (UserPromptSubmit, Stop, PreCompact…)
│   └── skills/
├── Makefile              # build/push targets: make build push (both images)
└── PLAN.md / ISSUES.md / CHANGELOG.md
```

---

## Build & Deploy

```bash
cd /opt/git/app-development/remnant

make build push          # build + push both MCP and UI images
make build-mcp push-mcp  # MCP only
make build-ui push-ui    # UI only

kubectl rollout restart deployment/remnant deployment/remnant-ui -n remnant
kubectl rollout status deployment/remnant -n remnant --timeout=120s
```

Helm chart lives in `/opt/git/helm-charts/charts/remnant/`.
Gitops target: `/opt/git/k8s-gitops/prod/clusters/prod-k8s/cluster-apps/remnant/`.

---

## Session Workflow

1. `search_memory` for relevant context (already done by hook at prompt time).
2. Read `PLAN.md` — current phase, next unchecked task.
3. Check `ISSUES.md` — known blockers.
4. Implement. Save memory via `add_memory` at session end.
