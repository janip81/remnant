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

## Phase 7 — Web UI ✓
> Goal: browse, search, and manage memories in a browser; separate container in same Helm chart
> Stack: React + Vite + TypeScript + MUI v6, dark mode, brand blue hsl(210,98%,55%), Inter font. HTMX placeholder replaced 2026-04-30.
> Images: Split done — MCP: `ghcr.io/janip81/claude-memory` (Dockerfile.mcp), UI: `ghcr.io/janip81/claude-memory-ui` (Dockerfile.ui). Chart 0.1.9.

Features (inspired by OpenMemory UI):
- [x] Memory list with live search (300ms debounce)
- [x] Add / delete actions per memory
- [x] Stats bar: total/first/last/by_agent/by_category breakdown
- [x] Dark theme
- [x] Source app tag — agent_id chip + category chip on each card
- [x] Category + agent filter dropdowns, sort, pagination, export
- [ ] Edit / archive actions
- [ ] Auth: Keycloak OIDC (later phase; currently internal-only via gateway)

Implementation:
- [x] React + Vite + MUI v6 SPA — full rewrite from HTMX/FastAPI (2026-04-30)
- [x] Dockerfile.ui: multi-stage (node:20 builder → python:3.12-slim proxy)
- [x] Separate Deployment + Service + HTTPRoute in Helm chart
- [x] UI at `mem0.prod.threshold.se` (internal-shared), MCP at `mem0-mcp.prod.threshold.se`
- [x] gitops targetRevision: 0.1.9 — pushed to main

Dedup + cold storage (added chart 0.1.9, 2026-04-30):
- [x] `scripts/dedup_job.py`: Phase 1 hash dedup + Phase 2 semantic (pgvector cosine similarity)
- [x] `mem0_archive` cold storage table — append-only, dedup never deletes from here
- [x] Helm `cronjob-dedup.yaml` — runs at 03:30 daily
- [x] Memory categorization backfill: 750 memories tagged with agent_id + category (project/incident/feedback/session/reference/user)

Pending / known issues:
- [ ] **CNPG bootstrap fix** — `CREATE EXTENSION vector` must be added to `initdb.postInitApplicationSQL` in CNPG cluster; currently requires manual `psql` after cluster creation
- [ ] **ArgoCD sync** — chart 0.1.9 pushed; ArgoCD will auto-sync and create the CronJob
- [ ] **DNS bug** — `k8s-prod.k8s.threshold.se` resolves to `192.168.102.31` (dev-k8s node), should be `192.168.101.11` (prod-k8s VIP); fix in OPNsense Unbound

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

## Phase 9 — Rename to Remnant
> Goal: stop using "mem0" as our service name — mem0 is a product (mem0.ai); our thing is Remnant.
> Name: **Remnant** | Tagline: A shared local memory layer for stateless AI agents.
> Logo source: `/opt/git/app-development/remnant/Remnant-logo-x3` — one PNG with 3 images: top=dark mode, bottom-left=logotype only, bottom-right=light mode. Cut into 3 and place in correct folders. Use as favicon + helm chart icon.
> App-development folder: renamed from `claude-memory` → `remnant` (2026-04-30)

Update everything:

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

## Phase 11 — Intelligent Tagging + Admin UI
> Goal: replace hardcoded tag rules with DB-backed config, add nightly LLM tagging, expose everything via UI
> Blog angle: "From dumb keyword matching to an evolving, human-curated + AI-assisted tagging system"

**Tag rules — DB-backed, no redeploy to change:**
- [x] `tag_rules` table in PostgreSQL (tag, keyword, source: seed|manual|llm-discovered)
- [x] `job_runs` table for nightly job audit log (phase, status, scanned, changed, rules_added, duration, error)
- [x] `_ensure_schema()` in memory.py — idempotent, seeds from hardcoded defaults on first deploy
- [x] In-memory cache in MCP server with 5-min TTL; invalidated on add/delete
- [x] Fallback to seed dict if DB unreachable at startup

**API (MCP server):**
- [x] `GET  /api/tag-rules` — list all rules grouped by tag
- [x] `POST /api/tag-rules` — add `{tag, keyword}`
- [x] `DELETE /api/tag-rules/{tag}/{keyword}` — remove a rule
- [x] `GET  /api/jobs` — last 50 nightly job runs
- [x] `GET  /api/jobs/{id}` — single run detail
- [x] `GET  /api/stats` — now includes `by_tag` breakdown

**Nightly job (dedup_job.py):**
- [x] Phase 0: LLM tagging — finds memories with no tags, asks Ollama to classify from tag list, writes back
- [x] Phase 0 skipped when `TAGGING_MODE=keyword`; active for `llm` or `hybrid`
- [x] All phases log to `job_runs` table (start time, finish time, scanned/changed counts, duration, errors)
- [x] `--tagging-only` flag for running Phase 0 standalone

**Helm chart (0.2.3):**
- [x] `configmap-settings.yaml` — exposes `TAGGING_MODE`, `TAGGING_LLM_MODEL` as env vars
- [x] `tagging:` section in values.yaml (mode: keyword|llm|hybrid)
- [x] Both MCP Deployment and dedup CronJob get settings via `envFrom`
- [ ] Update gitops tagging.mode to `hybrid` when ready for nightly LLM pass

**UI — 4 pages via tab nav in AppBar:**
- [x] **Memories** — existing list/search/edit/delete (unchanged)
- [x] **Stats** — native React stats page: summary bar, category/agent/tag horizontal bar charts (no charting lib), first/last dates, tagged vs untagged counts
- [x] **Tag Rules** — view all tags grouped with their keywords, add keyword to existing or new tag, delete keyword, filter, source badges (seed/manual/llm-discovered)
- [x] **Jobs** — nightly job history table: phase, status, scanned/changed/rules+, duration, error; summary totals at top

**Blog post (Part 12+):**
- [ ] Part 12: "Tags, rules, and a nightly brain" — keyword matching vs LLM tagging, the DB-backed rules system, the job audit log, why we built a Config-editable tagging pipeline instead of hardcoding

---

## Phase 10 — Gap Analysis vs mem0
> Status: complete — see `remnant-vs-mem0.md` in the prod-k8s repo for full comparison

**Summary of deliberate design differences:**
- `infer=False` — Claude decides what to save; no LLM extraction at write time (unlike mem0's default)
- Fully local — no data leaves the homelab; no cloud dependency
- Single-user by design — simpler, no RBAC needed
- pgvector on existing CNPG — no separate vector store to operate
- Built-in MCP server + Claude Code hooks — zero glue code
- Nightly dedup in batch (qwen2.5:7b) rather than per-write LLM calls

**Gaps we decided to close (in later phases):**
- Graph memory / relationship linking → Phase 12
- Multi-user support → Phase 11
- Embeddable library mode → Phase 13

---

---

## Phase 11 — Multi-user support
> Goal: allow multiple users (or agents) to have isolated memory namespaces

- [ ] User identity via configurable `user_id` per MCP connection (header or token-derived)
- [ ] Per-user memory isolation in pgvector (row-level or schema-level)
- [ ] UI: user switcher / user filter
- [ ] Helm: optional list of users with separate bearer tokens
- [ ] Document: how to run Remnant for a team (shared infra, isolated memories)

---

## Phase 12 — Graph memory
> Goal: relationship linking between memories using existing CNPG — no new infra needed
> Approach: `memory_edges` table in existing Postgres, edges built by the nightly dedup job

**Schema (pure SQL — no Apache AGE, no Neo4j):**
```sql
CREATE TABLE memory_edges (
  source_id UUID REFERENCES mem0_memories(id) ON DELETE CASCADE,
  target_id UUID REFERENCES mem0_memories(id) ON DELETE CASCADE,
  relationship TEXT,  -- related_to | contradicts | derived_from | supersedes
  weight FLOAT,
  created_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (source_id, target_id, relationship)
);
```

- [ ] Add `memory_edges` table to `_ensure_schema()` in memory.py
- [ ] Nightly job Phase 3: for each semantic cluster (already grouped by cosine similarity), ask Ollama to classify relationships between pairs — write edges
- [ ] New MCP tool: `get_related(memory_id, limit=5)` — JOIN on memory_edges, return linked memories with relationship type
- [ ] UI: "Related" section on memory card (expandable, shows linked memories + relationship label)
- [ ] Optional Phase 12b: recursive CTE for multi-hop traversal

---

## Phase 13 — Embeddable library
> Goal: make Remnant usable as a general-purpose memory layer, not just for Claude Code

- [ ] Extract core into a Python package (`remnant-core`) with clean `add/search/delete` API
- [ ] Decouple from Claude Code assumptions (no Claude-specific hook references in core)
- [ ] Publish to PyPI
- [ ] SDK example: use Remnant as memory backend in a LangChain / custom agent
- [ ] Document: embedding Remnant in any Python LLM app (OpenAI, Anthropic, local)

---

## Phase 6 (old) — Local LLM
> Adopted in Phase 2 (Ollama). ADR-0001 superseded. No separate phase needed.

---

## Completed Phases

[Move phases here when all tasks are checked off]
