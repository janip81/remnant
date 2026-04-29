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

- [ ] Add mem0, sentence-transformers, psycopg2-binary to requirements.txt
- [ ] `scripts/init-db.sh`: CREATE EXTENSION pgvector; CREATE DATABASE mem0
- [ ] `src/memory.py`: mem0 client configured with pgvector + sentence-transformers + Claude API
- [ ] Wire memory.py into MCP tools (replace stubs)
- [ ] Test locally: add memory → search → verify semantic match works
- [ ] Test that memories persist across container restart

---

## Phase 3 — Helm Chart + Docker Image
> Goal: deployable Helm chart, image pushed to registry

- [ ] `helm/Chart.yaml`: name claude-memory, version 0.1.0
- [ ] `helm/values.yaml`: image, replicas, resources, ingress, cnpg config
- [ ] `helm/templates/deployment.yaml`
- [ ] `helm/templates/service.yaml`
- [ ] `helm/templates/httproute.yaml` (internal-shared, mem0-mcp.prod.threshold.se)
- [ ] `helm/templates/cnpg-cluster.yaml` (1 instance MVP, pgvector, Barman S3)
- [ ] `helm/templates/externalsecret.yaml` (bearer token + anthropic key from Vault)
- [ ] Build and push image to registry
- [ ] Test `helm template` renders correctly

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

## Completed Phases

[Move phases here when all tasks are checked off]
