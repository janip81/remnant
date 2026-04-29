# AGENTS.md — claude-memory

## Project Overview

MCP server that gives Claude persistent semantic memory.
Stores facts extracted from conversations in pgvector (CNPG), retrieves them via semantic search.
Deployed in prod-k8s, accessible from any Claude Code client (desktop, starbase) over HTTP.

---

## 1. Purpose

Claude's file-based MEMORY.md grows unbounded and gets truncated in context.
This service replaces it with a queryable vector store — Claude calls `search_memory("topic")`
and gets back only the relevant facts, not everything ever remembered.

Users: single-user (jani), accessed from desktop + starbase via `mem0-mcp.prod.threshold.se`.

---

## 2. Tech Stack

| Layer | Technology |
|-------|------------|
| MCP server | Python, `mcp` SDK (official), HTTP/SSE transport |
| Memory library | mem0ai/mem0 |
| Embeddings | sentence-transformers `all-MiniLM-L6-v2` (CPU, in-pod, ~90MB) |
| LLM (extraction) | Claude API (Anthropic) |
| Vector store | PostgreSQL + pgvector via CNPG |
| Auth | Bearer token (Vault secret) |
| Deployment | Helm chart → ArgoCD gitops → prod-k8s |

### Why this stack
- pgvector on CNPG reuses existing infrastructure + Barman/Velero backups — no new DB to operate
- sentence-transformers runs CPU-only, no external dep, swap to GPU model later with no architecture change
- Claude API for extraction: best quality, already available
- Official MCP SDK ensures compatibility with Claude Code clients

---

## 3. Architecture

```
Claude Code (desktop / starbase)
    │  HTTP MCP  (Bearer token)
    ▼
mem0-mcp service  [prod-k8s, namespace: claude-memory]
    │
    ├── mem0 library
    │     ├── LLM: Claude API  (memory extraction/dedup)
    │     ├── Embeddings: sentence-transformers all-MiniLM-L6-v2 (in-pod)
    │     └── Vector store → pgvector
    │
    └── mem0-pg CNPG cluster  [prod-k8s, namespace: claude-memory]
          └── WAL archiving → Garage S3 (cnpg-prod-k8s bucket)
```

### MCP tools exposed
| Tool | Description |
|------|-------------|
| `add_memory` | Store a fact or observation |
| `search_memory` | Semantic search — returns top-k relevant memories |
| `get_all_memories` | List all stored memories |
| `delete_memory` | Remove a memory by ID |

---

## 4. Folder Structure

```
claude-memory/
├── src/
│   ├── main.py          # MCP server, tool definitions, auth middleware
│   ├── memory.py        # mem0 client wrapper
│   └── config.py        # settings loaded from env
├── Dockerfile
├── requirements.txt
├── helm/
│   ├── Chart.yaml
│   ├── values.yaml
│   └── templates/
│       ├── deployment.yaml
│       ├── service.yaml
│       ├── httproute.yaml   (Gateway API, internal-shared)
│       ├── cnpg-cluster.yaml
│       └── externalsecret.yaml
├── scripts/
│   └── init-db.sh       # pgvector extension bootstrap (run once)
├── docker-compose.yml   # local dev: postgres+pgvector sidecar
├── .env.example
├── Makefile
├── AGENTS.md
├── CLAUDE.md
├── MVP.md
├── PLAN.md
├── CHANGELOG.md
├── ISSUES.md
├── README.md
└── memory/
```

---

## 5. Key Decisions

| Decision | Choice | Reason |
|----------|--------|--------|
| Vector store | pgvector on CNPG | Reuse existing postgres + backup infra |
| Embeddings | sentence-transformers (in-pod) | CPU-only, no external dep, swap to GPU later |
| LLM | Claude API | Best extraction quality, already available |
| MCP transport | HTTP/SSE | Matches ha-mcp pattern already working |
| Auth | Bearer token | Simple, Vault-managed, standard for internal MCP servers |
| Namespace | `claude-memory` | Isolated from other apps |
| Gateway | internal-shared | Not internet-facing; desktop reaches it via LAN |

---

## 6. Local Development

```bash
# Start postgres+pgvector locally
make dev

# Build image
make build

# Push image
make push

# Run tests
make test
```

---

## 7. How Claude Should Work Each Session

1. Read `PLAN.md` — find the current phase and next unchecked task
2. Check `ISSUES.md` — be aware of known blockers
3. State briefly: what phase, what will be changed
4. Implement in small focused steps
5. Update `PLAN.md` checkboxes as tasks complete
6. Write a memory file to `memory/` before ending the session

### Memory file convention
Filename: `YYYY-MM-DD_topic_phase.md`
Content: key decisions, what was implemented, what's next, any blockers or non-obvious context.
