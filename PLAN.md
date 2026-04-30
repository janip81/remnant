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

- [x] `scripts/import_memories.py`: parse all `*.md` files in `/opt/git/wiki/logs/`, extract body text (skip frontmatter + MEMORY.md index), call `add_memory` for each entry
- [x] `--dry-run` flag: print what would be imported without writing
- [x] `--file` flag: import single file for testing
- [x] Run import against prod — 92 files, 368 chunks imported (2026-04-29)

---

## Phase 7 — Web UI
> Goal: browse, search, and manage memories in a browser; separate container in same Helm chart
> Stack: **NEEDS REWORK** — UI must be rewritten to match worklog tech stack: React + Vite + TypeScript, Material UI, same theme as worklog. Current FastAPI + HTMX implementation is a placeholder and should be replaced.
> Images: ✓ Split done (2026-04-30) — MCP: `ghcr.io/janip81/claude-memory` (Dockerfile.mcp), UI: `ghcr.io/janip81/claude-memory-ui` (Dockerfile.ui). Chart 0.1.7.

Features (inspired by OpenMemory UI):
- [x] Memory list with live search (HTMX, 300ms debounce)
- [x] Add / delete actions per memory
- [x] Stats bar: total memories count (out-of-band HTMX update on add)
- [x] Dark theme
- [ ] Source app tag (which Claude session added it)
- [ ] Edit / archive actions
- [ ] Auth: Keycloak OIDC (later phase; currently internal-only via gateway)

Implementation:
- [x] `src/ui/` FastAPI + Jinja2/HTMX — separate pod, same image, different CMD
- [x] Separate Deployment + Service + HTTPRoute in Helm chart 0.1.3
- [x] UI at `mem0.prod.threshold.se` (internal-shared), MCP at `mem0-mcp.prod.threshold.se`
- [x] gitops updated — targetRevision: 0.1.3, ui.host configured

---

## Phase 8 — Blog Series
> Goal: document the full build as a 10–15 part series on blog.threshold.se
> Angle: "Building a fully local persistent memory for Claude Code"
> Point-of-view/author: Claude (User gave claude free hands to research and build its own memory. User has been only deciding what claude finds) (ask if unsure)
> INstructions: I want the blog for this projects to be written as a joint project between me(user) and claude who has done the research, coding, troubleshooting etc (ask if unsure)

> Ask user about point of view and instruction of unsure update this part when clear picture
> wiki/logs can have some longer md files about what we have done that can be good for the posts but only look relevant md files for this project

- [x] Part 1: Architecture decisions — why local, MCP protocol, mem0 vs scratch, Ollama embeddings, pgvector on CNPG (committed: `016-claude-memory-part1-architecture.md`)
- [x] Part 2: MCP server — FastMCP, stateless HTTP, bearer token auth, the 307 routing bug (committed: `017-claude-memory-part2-mcp-server.md`)
> NOTE: Every part must have the "About this series" blockquote box immediately after the H1 title (see Part 1 for exact wording).
- [x] Part 3: mem0 integration — infer=False, pgvector, Ollama LLM + embedder, memory schema
- [x] Part 4: Helm chart — CNPG cluster, Barman S3, Gateway API HTTPRoute, naming pitfalls (committed: `019-claude-memory-part4-helm-chart.md`)
- [x] Part 5: gitops deployment — ArgoCD, AVP secrets, DNS, end-to-end test (committed: `020-claude-memory-part5-gitops-deployment.md`) (used old memory system to figure out how to deploy and ask user what vault keys to create?)
- [x] Part 6: Claude Code plugin — hooks, skills, SKILL.md design philosophy (committed: `021-claude-memory-part6-plugin.md`) (here user gave a lot of input that all hooks needed to work?)
- [x] Part 7: Import script — migrating MEMORY.md files, chunking strategy (committed: `022-claude-memory-part7-import-script.md`)
- [x] Part 8: Web UI — FastAPI + HTMX, memory management, install wizard (committed: `023-claude-memory-part8-web-ui.md`)
- [ ] Part 9: Lessons learned — what worked, what didn't, mem0 quirks
- [ ] Part 10: Benchmarking, findings, fixes, deleting bad memories.
- [ ] Part 11: What's next — per-project namespacing, multi-user, Keycloak SSO
- [ ] Part 12: infer: "true" via cronjob to save gpu and possibility to run  on smaller gpu locally
- [ ] Part 12: New web-ui in react etc....

---

## Phase 9 — Rename (name TBD)
> Goal: stop using "mem0" as our service name — mem0 is a product (mem0.ai); our thing is something else
> Blocked on: Jani picks a name

When the name is decided, update everything:

**Infrastructure:**
- [ ] Vault secret path stays `kubernetes/data/prod-k8s/claude-memory` (no change needed there)
- [ ] CNPG cluster: rename PostgreSQL database from `mem0` → `<name>` (requires pg_dump + restore or ALTER DATABASE — plan carefully, CNPG cluster will need recreation or manual rename)
- [ ] Helm values: `cnpg.database: mem0` → `<name>`
- [ ] gitops: UI HTTPRoute host `mem0.prod.threshold.se` → `<name>.prod.threshold.se`
- [ ] gitops: MCP HTTPRoute host `mem0-mcp.prod.threshold.se` → `<name>-mcp.prod.threshold.se`
- [ ] Update `CLAUDE_MEMORY_URL` in `~/.claude/settings.json` (env var for hooks)
- [ ] Re-run `claude mcp add` with new URL

**Blog posts** — update URL references in:
- [ ] Part 1 (`016-claude-memory-part1-architecture.md`)
- [ ] Part 2 (`017-claude-memory-part2-mcp-server.md`)
- [ ] Part 5 (`020-claude-memory-part5-gitops-deployment.md`)
- [ ] Part 6 (`021-claude-memory-part6-plugin.md`)
- [ ] Part 8 (`023-claude-memory-part8-web-ui.md`)
- [ ] Re-publish all updated posts to Ghost with `--update`

**Note:** References to `mem0ai` (the Python library) stay as-is — that IS its name.

---

## Phase 10 — Gap Analysis vs mem0.ai
> Goal: understand how far we are from what mem0 sells as a product, and decide what's worth closing
> This is research + planning, not implementation
> Do this by reading mem0.ai docs, pricing page, and changelog — then map against what we have

**What we have:**
- Self-hosted, fully local (intentional — our differentiator)
- MCP server with `add_memory`, `search_memory`, `get_all_memories`, `delete_memory`
- Semantic search via pgvector + nomic-embed-text embeddings (Ollama)
- `infer=False` — Claude decides what to save, no LLM extraction layer at write time
- Auto-inject on every prompt (UserPromptSubmit hook)
- Save reminder at session end (Stop + PreCompact hooks)
- SKILL.md teaching Claude when/what/how to save
- Import script for migrating existing docs
- Basic Web UI (HTMX, search + add + delete)
- CNPG-backed with WAL archiving and S3 backups

**Research tasks:**
- [ ] Read mem0.ai product page, docs, and pricing — what are their paid tiers?
- [ ] What memory categories do they support that we don't? (e.g. episodic, semantic, procedural, user-level vs session-level)
- [ ] Do they have memory relationships / a memory graph?
- [ ] Do they have source app tagging (which agent/session added a memory)?
- [ ] What does their infer pipeline actually produce vs our manual approach?
- [ ] Multi-user / org support — do they support teams?
- [ ] SDK vs API — how do they expect apps to integrate?
- [ ] What's their retention / expiry model?

**Gap assessment output:**
- [ ] Write up: features in mem0 we deliberately skipped (infer, cloud) and why
- [ ] Write up: features in mem0 we want and don't have yet
- [ ] Decide: which gaps are worth closing for our use case
- [ ] Feed findings into Part 11 blog post ("What's next")

---

## Phase 6 (old) — Local LLM
> Adopted in Phase 2 (Ollama). ADR-0001 superseded. No separate phase needed.

---

## Completed Phases

[Move phases here when all tasks are checked off]
