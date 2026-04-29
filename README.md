# claude-memory

MCP server that gives Claude persistent semantic memory.

Stores facts in PostgreSQL + pgvector. Retrieves them via semantic search using Ollama embeddings (`nomic-embed-text`). No external API calls — fully self-hosted.

## How it works

```
Claude Code  ──HTTP/MCP──►  claude-memory  ──►  pgvector (CNPG)
                                   │
                                   └──►  Ollama (nomic-embed-text embeddings)
```

Facts are stored directly (no LLM extraction step). Semantic search finds relevant memories even when the query wording differs from what was stored.

---

## Prerequisites

- **Ollama** running and reachable, with `nomic-embed-text` pulled:
  ```bash
  ollama pull nomic-embed-text
  ```
- **PostgreSQL + pgvector** (local dev: included in docker-compose; production: CNPG cluster)

---

## Install

### Local development

```bash
git clone https://github.com/janip81/claude-memory
cd claude-memory
cp .env.example .env
# Edit .env: set BEARER_TOKEN, OLLAMA_BASE_URL
make dev          # starts postgres+pgvector + the MCP server
```

### Kubernetes (Helm + ArgoCD)

1. Create the auth secret (or use AVP to render from Vault):
   ```yaml
   apiVersion: v1
   kind: Secret
   metadata:
     name: claude-memory-auth
     namespace: claude-memory
   stringData:
     BEARER_TOKEN: <your-token>
   ```

2. Deploy via Helm:
   ```bash
   helm install claude-memory ./helm \
     --namespace claude-memory \
     --create-namespace \
     --set gatewayApi.host=mem0-mcp.your-domain.com \
     --set ollama.baseUrl=http://your-ollama-host:11434 \
     --set cnpg.backup.endpointURL=https://your-s3-endpoint \
     --set cnpg.backup.destinationPath=s3://your-bucket/claude-memory
   ```

3. Or deploy via ArgoCD Application pointing at this chart + a `cluster-config/` folder for secrets.

---

## Uninstall

```bash
# Local
make clean        # stops containers, removes postgres volume

# Kubernetes
helm uninstall claude-memory -n claude-memory
kubectl delete namespace claude-memory
```

---

## Configure Claude Code

Add the MCP server after deployment:

```bash
# Deployed (HTTP transport)
claude mcp add --transport http --scope user mem0-mcp https://mem0-mcp.your-domain.com/mcp
# Enter bearer token when prompted

# Local dev
claude mcp add --transport http --scope user mem0-mcp-dev http://localhost:8080/mcp
```

Verify it's connected:
```bash
claude mcp list
```

Once connected, Claude Code can call:

| Tool | Description |
|------|-------------|
| `add_memory` | Store a fact or observation |
| `search_memory` | Semantic search — returns top matching facts |
| `get_all_memories` | List all stored memories |
| `delete_memory` | Remove a memory by ID |

---

## Import existing memories

Import from a folder of markdown files (e.g. an existing MEMORY.md-based system):

```bash
python3 scripts/import_memories.py --path /path/to/memory/files/
```

Options:
```
--path PATH       Directory containing .md files to import
--dry-run         Print what would be imported without writing
--bearer TOKEN    Bearer token (default: reads from .env)
--url URL         MCP server URL (default: http://localhost:8080)
```

Each file's body text is stored as a single memory (frontmatter is stripped).

---

## Export memories

```bash
# Via MCP tool (from Claude Code)
# Ask Claude: "get all memories and save them to a file"

# Direct API call
curl -H "Authorization: Bearer <token>" \
     -H "Content-Type: application/json" \
     -X POST http://localhost:8080/mcp \
     -d '{"method":"tools/call","params":{"name":"get_all_memories","arguments":{}}}'
```

---

## Configuration

All config is via environment variables (see `.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `BEARER_TOKEN` | `change-me` | Auth token for MCP endpoint |
| `DATABASE_URL` | `postgresql://mem0:mem0@localhost:5432/mem0` | PostgreSQL connection string |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_MODEL` | `qwen2.5:7b` | Chat model (used if LLM extraction enabled) |
| `OLLAMA_EMBED_MODEL` | `nomic-embed-text` | Embedding model |
| `MEM0_USER_ID` | `jani` | User ID namespace for memories |
| `APP_PORT` | `8080` | Listen port |

---

## Local development

```bash
make dev      # start postgres + app (docker compose)
make build    # build Docker image
make push     # push to ghcr.io
make test     # run tests
make clean    # stop and remove volumes
```

---

## Documentation

- [AGENTS.md](AGENTS.md) — architecture and session guide for Claude Code
- [PLAN.md](PLAN.md) — implementation phases and progress
- [ADR/](ADR/) — architecture decision records
- [CHANGELOG.md](CHANGELOG.md) — change history
