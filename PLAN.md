# PLAN.md — claude-memory

## How to use this file

- Each phase is a milestone with a clear goal
- Check off tasks as they complete: `[ ]` → `[x]`
- Add phases as needed — keep them small and focused
- Claude reads this at the start of every session to orient itself

---

## Phase 0 — Setup & Skeleton ✓
> Goal: project scaffolding in place, repo initialized, planning docs complete

- [x] Copy test-app-skeleton
- [x] Fill in AGENTS.md (architecture, tech stack, tools)
- [x] Fill in MVP.md (scope, done criteria)
- [x] Fill in PLAN.md phases
- [ ] Initialize git repo and push to GitHub (`claude-memory`)
- [ ] Fill in README.md
- [ ] Confirm local dev environment works (`make dev` starts postgres+pgvector)

---

## Phase 1 — Python MCP Server ✓
> Goal: working MCP server locally with stub tools (no mem0 yet)

- [x] `requirements.txt`: mcp, starlette, uvicorn, pydantic-settings
- [x] `src/config.py`: load BEARER_TOKEN, ANTHROPIC_API_KEY, DATABASE_URL from env
- [x] `src/main.py`: Starlette app + MCP HTTP server, 4 stub tools, bearer token middleware
- [x] `.env.example` updated with all required vars
- [x] `docker-compose.yml`: add postgres+pgvector service for local dev
- [x] `Makefile`: working dev, build, push, tag, run targets
- [x] `Dockerfile`: python:3.12-slim, non-root user, health check
- [x] Local smoke test: /health 200, /mcp no-auth 401, /mcp with-auth 200 ✓

---

## Phase 2 — mem0 Integration ✓
> Goal: real memory storage and retrieval via mem0 + pgvector
> Stack: Ollama (qwen2.5:7b LLM + nomic-embed-text embedder) at 192.168.81.20:11434 — no Claude API key needed

- [x] Add mem0, psycopg2-binary, ollama to requirements.txt
- [x] `scripts/init-db.sh`: CREATE EXTENSION pgvector
- [x] `src/memory.py`: mem0 client with pgvector + Ollama LLM + Ollama embedder
- [x] Wire memory.py into MCP tools (replace stubs)
- [x] Test locally: add memory → search → verify semantic match works
- [x] Test that memories persist across container restart

---

## Phase 3 — Helm Chart + Docker Image ✓
> Goal: deployable Helm chart, image pushed to registry

- [x] `helm/Chart.yaml` + published to helm-charts repo (chart `claude-memory`)
- [x] `helm/values.yaml`: image, replicas, resources, gateway, cnpg config
- [x] `helm/templates/deployment.yaml`
- [x] `helm/templates/service.yaml`
- [x] `helm/templates/httproute.yaml` (Gateway API, internal-shared parentRef)
- [x] `helm/templates/cnpg-cluster.yaml` (pgvector bootstrap, Barman S3 WAL archiving)
- [x] Build and push image to registry (ghcr.io/janip81/claude-memory:latest)
- [x] `helm/templates/_helpers.tpl`: fullname fix — `contains` check avoids `claude-memory-claude-memory-*` doubling
- [x] Chart 0.1.2 on PR #146 (pending merge → publishes to GitHub Pages)

---

## Phase 4 — gitops Deployment ✓
> Goal: running in prod-k8s, accessible from desktop + starbase

- [x] Vault secret: `kubernetes/data/prod-k8s/claude-memory` (bearer-token, db creds, Barman S3)
- [x] gitops: ArgoCD Application in `prod/clusters/prod-k8s/cluster-apps/claude-memory/`
- [x] AVP-annotated ExternalSecret for all credentials
- [x] DNS / gateway: `mem0-mcp.prod.threshold.se` via internal-shared HTTPRoute
- [x] Pod running, CNPG cluster healthy (pg WAL archiving active)
- [x] MCP routing bug fixed: bypass Starlette Router → call `_starlette_app.router.routes[0].app` directly
- [x] DNS rebinding fix: `TransportSecuritySettings(enable_dns_rebinding_protection=False)`
- [x] `claude mcp add --transport http claude-memory https://mem0-mcp.prod.threshold.se/mcp` ✓
- [x] End-to-end /health + /mcp 200 via gateway confirmed

**Cleanup done (2026-04-29):**
- [x] Push gitops with `targetRevision: 0.1.2`
- [x] Delete old CNPG cluster `claude-memory-claude-memory-pg`; ArgoCD recreated as `claude-memory-pg`
- [x] `imagePullPolicy` reverted to `IfNotPresent` via chart 0.1.2

---

## Phase 5 — Plugin (hooks + skills) ✓
> Goal: Claude Code plugin that auto-injects memory context and teaches Claude when/what to save

- [x] `plugin/.claude-plugin/plugin.json`: manifest with MCP server, hooks, skills, env vars
- [x] `plugin/.mcp.json`: manual MCP config template
- [x] `plugin/hooks/hooks.json`: UserPromptSubmit → search, PreCompact + Stop → save reminder
- [x] `plugin/hooks/search-memory.py`: reads prompt from stdin, calls search_memory via MCP, injects `<memory>` block
- [x] `plugin/hooks/save-context.py`: outputs reminder text to call add_memory before session ends
- [x] `plugin/skills/claude-memory/SKILL.md`: when to search, when/how to save, when to delete, type table
- [x] `plugin/README.md`: install guide (env vars, claude mcp add, hooks setup, skill setup)
- [x] Commit plugin/ directory to app-development repo

---

## Phase 6 — Import Script
> Goal: migrate existing MEMORY.md wiki files into mem0

- [ ] `scripts/import_memories.py`: parse all `*.md` files in `/opt/git/wiki/logs/`, extract body text (skip frontmatter + MEMORY.md index), call `add_memory` for each entry
- [ ] `--dry-run` flag: print what would be imported without writing
- [ ] `--file` flag: import single file for testing
- [ ] Run import against prod after Phase 4 cleanup complete

---

## Phase 7 — Web UI
> Goal: browse, search, and manage memories in a browser; separate container in same Helm chart
> Stack: FastAPI + HTMX (lightweight, Python, no Node build step)

Features (inspired by OpenMemory UI):
- [ ] Memory list with search + category filter
- [ ] Add / edit / delete / archive actions per memory
- [ ] Source app tag (which Claude session added it)
- [ ] Stats bar: total memories, searches today
- [ ] Install wizard tab: shows exact `claude mcp add` command for this deployment
- [ ] Dark mode
- [ ] Auth: internal-only (internal-shared gateway), Keycloak OIDC as later phase

Implementation:
- [ ] `src/ui/` FastAPI router + Jinja2/HTMX templates
- [ ] Separate container in Helm chart (or sidecar — decide at implementation time)
- [ ] HTTPRoute for `/` (UI) vs `/mcp` (MCP server) — same hostname or separate

---

## Phase 8 — Blog Series
> Goal: document the full build as a 10–12 part series on blog.threshold.se
> Angle: "Building a fully local persistent memory for Claude Code"

- [x] Part 1: Architecture decisions — why local, MCP protocol, mem0 vs scratch, Ollama embeddings, pgvector on CNPG (committed: `016-claude-memory-part1-architecture.md`)
- [ ] Part 2: MCP server — FastMCP, stateless HTTP, bearer token auth, the 307 routing bug
- [ ] Part 3: mem0 integration — infer=False, pgvector, Ollama LLM + embedder, memory schema
- [ ] Part 4: Helm chart — CNPG cluster, Barman S3, Gateway API HTTPRoute, naming pitfalls
- [ ] Part 5: gitops deployment — ArgoCD, AVP secrets, DNS, end-to-end test
- [ ] Part 6: Claude Code plugin — hooks, skills, SKILL.md design philosophy
- [ ] Part 7: Import script — migrating MEMORY.md files, chunking strategy
- [ ] Part 8: Web UI — FastAPI + HTMX, memory management, install wizard
- [ ] Part 9: Lessons learned — what worked, what didn't, mem0 quirks
- [ ] Part 10: What's next — per-project namespacing, multi-user, Keycloak SSO

---

## Phase 6 (old) — Local LLM
> Adopted in Phase 2 (Ollama). ADR-0001 superseded. No separate phase needed.

---

## Completed Phases

[Move phases here when all tasks are checked off]
