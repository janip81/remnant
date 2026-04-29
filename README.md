# claude-memory

MCP server giving Claude persistent semantic memory via mem0 + pgvector.

Runs in prod-k8s. Connect from any Claude Code client (desktop or starbase) over HTTP.

## Connecting

```bash
claude mcp add --transport http --scope user mem0-mcp https://mem0-mcp.prod.threshold.se/
```

Set the bearer token when prompted (from Vault: `kubernetes/data/prod-k8s/claude-memory#bearer-token`).

## Available tools

| Tool | Description |
|------|-------------|
| `add_memory` | Store a fact |
| `search_memory` | Semantic search — returns relevant memories |
| `get_all_memories` | List all stored memories |
| `delete_memory` | Remove a memory by ID |

## Local development

```bash
cp .env.example .env   # fill in values
make dev               # starts postgres+pgvector + the MCP server
```

## Documentation

- [AGENTS.md](AGENTS.md) — architecture and project guide for Claude Code
- [MVP.md](MVP.md) — scope and done criteria
- [PLAN.md](PLAN.md) — implementation phases
- [CHANGELOG.md](CHANGELOG.md) — change history
