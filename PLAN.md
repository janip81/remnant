# PLAN.md — claude-memory

## How to use this file

- Each phase is a milestone with a clear goal
- Check off tasks as they complete: `[ ]` → `[x]`
- Add phases as needed — keep them small and focused
- Claude reads this at the start of every session to orient itself

---

## Phase 0 — Setup & Skeleton
> Goal: project scaffolding in place, repo initialized, planning docs complete

- [x] Copy test-app-skeleton
- [x] Fill in AGENTS.md (architecture, tech stack, tools)
- [x] Fill in MVP.md (scope, done criteria)
- [x] Fill in PLAN.md phases
- [ ] Initialize git repo and push to GitHub (`claude-memory`)
- [ ] Fill in README.md
- [ ] Confirm local dev environment works (`make dev` starts postgres+pgvector)

---

## Phase 1 — Python MCP Server
> Goal: working MCP server locally with stub tools (no mem0 yet)

- [x] `requirements.txt`: mcp, starlette, uvicorn, pydantic-settings
- [x] `src/config.py`: load BEARER_TOKEN, ANTHROPIC_API_KEY, DATABASE_URL from env
- [x] `src/main.py`: Starlette app + MCP HTTP server, 4 stub tools, bearer token middleware
- [x] `.env.example` updated with all required vars
- [x] `docker-compose.yml`: add postgres+pgvector service for local dev
- [x] `Makefile`: working dev, build, push, tag, run targets
- [x] `Dockerfile`: python:3.12-slim, non-root user, health check
- [x] Local smoke test: /health 200, /mcp no-auth 401, /mcp with-auth 307 ✓

---

## Phase 2 — mem0 Integration
> Goal: real memory storage and retrieval via mem0 + pgvector
> Stack: Ollama (qwen2.5:7b LLM + nomic-embed-text embedder) at 192.168.81.20:11434 — no Claude API key needed

- [x] Add mem0, psycopg2-binary, ollama to requirements.txt
- [x] `scripts/init-db.sh`: CREATE EXTENSION pgvector
- [x] `src/memory.py`: mem0 client with pgvector + Ollama LLM + Ollama embedder
- [x] Wire memory.py into MCP tools (replace stubs)
- [x] Test locally: add memory → search → verify semantic match works
- [x] Test that memories persist across container restart
- [ ] `scripts/import-memories.py`: import existing MEMORY.md files into mem0

---

## Phase 3 — Helm Chart + Docker Image
> Goal: deployable Helm chart, image pushed to registry

- [x] `helm/Chart.yaml`: name claude-memory, version 0.1.0
- [x] `helm/values.yaml`: image, replicas, resources, gateway, cnpg config
- [x] `helm/templates/deployment.yaml`
- [x] `helm/templates/service.yaml`
- [x] `helm/templates/httproute.yaml` (gateway API, configurable parentRef)
- [x] `helm/templates/cnpg-cluster.yaml` (instances, pgvector, Barman S3)
- [x] Build and push image to registry (ghcr.io/janip81/claude-memory:latest)
- [x] Test `helm template` renders correctly (4 resources: Service, Deployment, HTTPRoute, Cluster)

---

## Phase 4 — gitops Deployment
> Goal: running in prod-k8s, accessible from desktop + starbase

- [ ] Vault secret: `kubernetes/data/prod-k8s/claude-memory`
- [ ] gitops: ArgoCD Application in `prod/clusters/prod-k8s/cluster-apps/`
- [ ] DNS / gateway: `mem0-mcp.prod.threshold.se` via internal-shared
- [ ] Verify pod running, CNPG healthy, WAL archiving active
- [ ] `claude mcp add --transport http mem0-mcp https://mem0-mcp.prod.threshold.se/` from desktop
- [ ] End-to-end test: add → search → verify from Claude Code

---

## Phase 5 — Import + README
> Goal: migrate existing MEMORY.md files into mem0, write user-facing README

- [ ] `scripts/import_memories.py`: parse all `*.md` files in `/opt/git/wiki/logs/`, extract body text, call `add_memory` for each — skip frontmatter, skip MEMORY.md index
- [ ] Dry-run mode (`--dry-run`): print what would be imported without writing
- [ ] Run import against prod mem0 after Phase 4 deployment
- [ ] `README.md`: setup guide (Ollama prereqs, `.env` config, `make dev`, MCP add command), tool reference (`add_memory`, `search_memory`, `get_all_memories`, `delete_memory`)

---

## Phase 6 — Local LLM (Done — Ollama from Phase 2)
> Ollama was adopted in Phase 2 (not deferred). ADR-0001 marked superseded.
> RTX 2070 SUPER desktop at 192.168.81.20: qwen2.5:7b (extraction) + nomic-embed-text (embeddings)

---

## Completed Phases

[Move phases here when all tasks are checked off]
