# remnant

Self-hosted MCP memory server for Claude Code. Stores facts in PostgreSQL + pgvector, retrieves them via semantic search using Ollama embeddings. No external API calls — fully self-hosted.

## Architecture

```
Claude Code  ──HTTP/MCP──►  remnant (MCP server)  ──►  pgvector (CNPG)
                                    │
                                    ├──►  Ollama (nomic-embed-text embeddings)
                                    └──►  Ollama (qwen2.5:7b — tagging/dedup)

Browser  ────────────────►  remnant-ui (memory browser)
```

**Three components:**

| Component | Image | Purpose |
|-----------|-------|---------|
| MCP server | `ghcr.io/janip81/remnant` | MCP endpoint, memory storage/search |
| UI | `ghcr.io/janip81/remnant-ui` | React browser for viewing/editing memories |
| Dedup CronJob | `ghcr.io/janip81/remnant` | Nightly dedup + LLM tagging (03:30 daily) |

---

## How memories work

**At write time:**

1. `add_memory(content)` is called (by Claude Code hook or directly)
2. Content is embedded via Ollama (`nomic-embed-text`) and stored in pgvector
3. Keyword tag rules are applied immediately (fast, synchronous)

**Nightly (03:30 daily CronJob):**

- **Phase 0 — LLM tagging**: Reviews any memory not yet processed by LLM. Asks Ollama to assign tags from the `tag_rules` table. Merges with existing keyword tags. Sets `llm_reviewed=true` so each memory is processed only once.
- **Phase 1 — Hash dedup**: Removes exact duplicate memories (same content hash). Originals archived to `mem0_archive` before deletion.
- **Phase 2 — Semantic dedup**: Finds clusters of memories with cosine similarity > 0.92. Archives originals, asks Ollama to merge the cluster into one comprehensive memory.

**Tagging modes** (set via `TAGGING_MODE` env / Helm values):

| Mode | Behaviour |
|------|-----------|
| `keyword` | Regex rules at write time only. Fast, no GPU needed. |
| `llm` | Ollama tags at write time. Adds latency. |
| `hybrid` | Keyword at write time + LLM nightly pass for anything missed. Recommended. |

---

## Prerequisites

- **Ollama** with models pulled:
  ```bash
  ollama pull nomic-embed-text   # embeddings (required)
  ollama pull qwen2.5:7b         # tagging + dedup merging (required for llm/hybrid mode)
  ```
- **PostgreSQL + pgvector** (local dev: included in docker-compose; production: CNPG cluster)

---

## Quick start — local development

```bash
git clone https://github.com/janip81/remnant
cd remnant
cp .env.example .env
# Edit .env: set BEARER_TOKEN, OLLAMA_BASE_URL
make dev          # starts postgres+pgvector + MCP server
```

MCP server: `http://localhost:8080`

---

## Kubernetes (Helm)

### Auth secret

Create before deploying (or use AVP to render from Vault):

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: remnant-auth
  namespace: remnant
stringData:
  BEARER_TOKEN: <your-token>
```

### Deploy

```bash
helm repo add janip81 https://janip81.github.io/helm-charts/
helm repo update

helm install remnant janip81/remnant \
  --namespace remnant \
  --create-namespace \
  --set gatewayApi.host=remnant-mcp.your-domain.com \
  --set ui.host=remnant.your-domain.com \
  --set ollama.baseUrl=http://your-ollama-host:11434 \
  --set tagging.mode=hybrid \
  --set cnpg.backup.enabled=true \
  --set cnpg.backup.endpointURL=https://your-s3-endpoint \
  --set cnpg.backup.destinationPath=s3://your-bucket/remnant
```

### ArgoCD

Point an Application at the chart with a `cluster-config/` folder for AVP-rendered secrets. See [`remnant-app.yaml`](https://github.com/janip81/k8s-gitops) for a reference deployment.

---

## Configure Claude Code

```bash
# Add MCP server (HTTP transport)
claude mcp add --transport http --scope user remnant \
  "https://remnant-mcp.your-domain.com/mcp" \
  --header "Authorization: Bearer ${BEARER_TOKEN}"

# Verify
claude mcp list
```

Available MCP tools:

| Tool | Description |
|------|-------------|
| `add_memory` | Store a fact. Supports `tags=["prod-k8s"]`, `category="incident"` |
| `search_memory` | Semantic search — returns top matching memories |
| `get_all_memories` | List all stored memories with metadata |
| `delete_memory` | Remove a memory by ID |

---

## Claude Code hooks

The `plugin/` directory contains hooks that wire remnant into every Claude Code session automatically.

```bash
export CLAUDE_MEMORY_URL="https://remnant-mcp.your-domain.com"
export CLAUDE_MEMORY_TOKEN="your-bearer-token"
export CLAUDE_MEMORY_PLUGIN_DIR="/path/to/remnant/plugin"
```

| Hook | Trigger | Action |
|------|---------|--------|
| `UserPromptSubmit` | Every prompt | Searches memory, injects `<memory>` block |
| `PreCompact` | Before context compression | Saves session state to memory |
| `Stop` | Session end | Saves session summary |

See [`plugin/README.md`](plugin/README.md) for full installation instructions.

---

## Configuration

All config via environment variables (see `.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `BEARER_TOKEN` | `change-me` | Auth token for MCP endpoint |
| `DATABASE_URL` | `postgresql://mem0:mem0@localhost:5432/mem0` | PostgreSQL connection |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_MODEL` | `qwen2.5:7b` | Chat model for tagging and dedup merging |
| `OLLAMA_EMBED_MODEL` | `nomic-embed-text` | Embedding model |
| `TAGGING_MODE` | `keyword` | `keyword` / `llm` / `hybrid` |
| `MEM0_USER_ID` | `jani` | User ID namespace |
| `APP_PORT` | `8080` | Listen port |

---

## Makefile

```bash
make dev          # start postgres + MCP server (docker compose)
make build        # build both images (MCP + UI)
make build-mcp    # build MCP server image only
make build-ui     # build UI image only
make push         # push both images to ghcr.io
make push-mcp     # push MCP image only
make push-ui      # push UI image only
make tag-mcp      # tag + push versioned MCP release: make tag-mcp TAG=v0.3.0
make tag-ui       # tag + push versioned UI release: make tag-ui TAG=v0.3.0
make clean        # stop containers, remove volumes
```

---

## Import existing memories

```bash
python3 scripts/import_memories.py --path /path/to/memory/files/
```

Options:

| Flag | Description |
|------|-------------|
| `--path PATH` | Directory of `.md` files to import |
| `--dry-run` | Print what would be imported without writing |
| `--bearer TOKEN` | Bearer token (default: reads from `.env`) |
| `--url URL` | MCP server URL (default: `http://localhost:8080`) |

---

## Nightly job — manual trigger

```bash
# Trigger a manual run
kubectl create job -n remnant --from=cronjob/remnant-dedup manual-$(date +%s)

# Watch logs
kubectl logs -n remnant -l job-name=<job-name> --follow

# Dry run (no writes)
# Set args: ["python", "src/scripts/dedup_job.py", "--dry-run"] in a one-off Job
```

---

## Database maintenance

The nightly job maintains `mem0_archive` as cold storage — all deduplicated/merged originals are archived there before deletion. Nothing is hard-deleted without first being archived.

```sql
-- Row counts
SELECT COUNT(*) FROM mem0;
SELECT COUNT(*) FROM mem0_archive;

-- Job run history
SELECT phase, status, memories_scanned, memories_changed, duration_seconds, started_at
FROM job_runs ORDER BY started_at DESC LIMIT 20;
```

---

## Documentation

- [`plugin/README.md`](plugin/README.md) — Claude Code hook installation
- [`AGENTS.md`](AGENTS.md) — architecture and session guide
