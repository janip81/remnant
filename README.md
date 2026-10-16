# Remnant

**Built almost entirely by AI agents, for AI agents—with human direction.**
I supplied the idea and direction, then wanted to see how far AI agents could
build it themselves. Claude Code and Codex have done most of the design,
implementation, review, and iteration on the memory system they use. Remnant is
an ongoing experiment in AI-led development, built on Mem0 and other open-source
projects—not a claim that I wrote every component or that AI-generated code is
automatically correct.

Remnant is a self-hosted MCP memory service for coding agents. It stores durable
knowledge in PostgreSQL + pgvector, combines semantic and keyword retrieval, and
keeps temporary session checkpoints separate from long-term facts. Embeddings
and optional LLM processing run through Ollama; the memory service can operate
without an external AI API. Your connected coding agents use their own providers.

Personal project: use it, fork it, adapt it. No warranty, support commitment or SLA.
This README describes the current source; deployed images may differ.

## Built on Mem0 and open source

**Remnant builds on [Mem0](https://github.com/mem0ai/mem0), rather than building
its entire memory engine from scratch.** The `mem0ai` library provides memory
writes, embedding-provider integration, pgvector storage integration, optional
LLM fact extraction, and deletion. Thank you to the Mem0 maintainers and contributors.

Remnant adds its own hybrid retrieval pipeline (direct PostgreSQL keyword/vector
queries and rank fusion, rather than Mem0's `Memory.search()`), lifecycle and
review flows, session checkpoints, agent hooks, Redis processing, management UI,
and deployment tooling. It also relies on PostgreSQL/pgvector, Ollama, the MCP
Python SDK, and other open-source libraries. See [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES)
for acknowledgments and license information.

## What it does

- **Hybrid retrieval:** PostgreSQL full-text/identifier matching and pgvector
  cosine similarity, combined with weighted reciprocal rank fusion. Keyword
  retrieval remains available when the embedding service is unavailable.
- **Durable facts:** stores text as written by default, with best-effort secret
  redaction, tags, source, agent, project and session metadata. Optional extraction
  output is held as a review candidate when identified as an extraction source.
- **Memory lifecycle:** active/candidate/history views, linked superseding instead
  of silent in-place text replacement, and reviewable LLM merge proposals.
- **Session recovery:** one replaceable checkpoint per session, retrieved by exact
  ID and excluded from ordinary memory search. Default expiry is seven days;
  scheduled maintenance removes expired checkpoints.
- **Agent integration:** MCP tools for clients such as Claude Code and Codex.
  Included lifecycle hooks target Claude Code; the separate Codex integration
  currently lives outside this repository and is not installed by this checkout.
- **Operations:** React management UI, optional Redis write queue, maintenance
  job, metrics, Docker Compose and Kubernetes deployment options.

Project metadata is attribution, not enforced project-level access isolation:
`search_memory(project=...)` narrows results (memories of that project plus, by
default, those without one) but does not restrict who can read what.
Retrieved memories are historical evidence to verify, not instructions or proof
of current infrastructure state. Ranking scores are not confidence probabilities.

## Architecture

```text
Coding agents -- MCP --> Remnant --> PostgreSQL + pgvector
                            |-- Ollama: embeddings / optional LLM processing
                            `-- Redis: optional queued writes, in-process worker
Browser --> Remnant UI --> Remnant API
Scheduled maintenance --> tagging, duplicate handling, checkpoint expiry
```

No graph database is required. Automatic contradiction resolution, structured
incident episodes and cross-agent task handoffs are discussion topics, not
completed features. No measured overall token savings or task-success advantage
is claimed; earlier retrieval experiments are not a general benchmark.

## Quick start

Requires Docker Compose and a reachable Ollama server. Pull the embedding model;
add the chat model if you enable LLM extraction, tagging or merge proposals:

```bash
ollama pull nomic-embed-text
ollama pull qwen2.5:7b

git clone https://github.com/janip81/remnant.git
cd remnant
cp .env.example .env
openssl rand -hex 32
```

Put the generated value in `BEARER_TOKEN` in `.env`. The server refuses empty,
placeholder or shorter-than-16-character tokens. For the Compose stack, set:

```dotenv
BEARER_TOKEN=<paste-generated-token>
DATABASE_URL=postgresql://remnant:remnant@postgres:5432/remnant
OLLAMA_BASE_URL=http://<host-reachable-from-containers>:11434
REMNANT_USER_ID=default
MEM0_INFER=false
```

Use an Ollama address reachable **from the containers**; their `localhost` is not
 your host. `host.docker.internal` requires a suitable host mapping on Linux.
The example database credentials are for local development only.

```bash
docker compose up -d --build
curl --fail http://localhost:8080/health
```

| Service | Address |
| --- | --- |
| MCP | `http://localhost:8080/mcp` |
| UI | `http://localhost:8081` |
| PostgreSQL | `localhost:5432` (published by the development Compose file) |

The supplied Compose file publishes ports on the host. Restrict their exposure
for your environment. The UI proxies API credentials server-side; do not expose
it publicly without an authentication/access-control layer.

Stop with `docker compose down`; data remains in the database volume. Adding
`-v` deletes that volume and its data. `make dev` starts the same Compose stack;
it does not provide source hot reload.

## Connect an agent

Use your client's MCP configuration to connect to `/mcp` with an
`Authorization: Bearer <token>` header. Store credentials outside Git.
For Claude Code setup and hook configuration, see [plugin/README.md](plugin/README.md).

The included Claude prompt hook is selective: it injects a memory only when an
identifier-like term from the prompt (hostname, file, version, IP ...) literally
appears in it, within item/character limits and once per session. Most prompts
get no injected context; agents call `search_memory` themselves. Resume and
compaction restore the session's own checkpoint plus a broad related-memory
search; Stop capture is throttled. `REMNANT_INJECT_POLICY` switches these
behaviours for benchmarking (see [plugin/hooks/bench_log.py](plugin/hooks/bench_log.py)).

| MCP tool | Purpose |
| --- | --- |
| `add_memory` | Store knowledge with optional tags, category and provenance |
| `search_memory` | Hybrid search; active facts by default, optional history/candidates, project scope and JSON output |
| `get_memory` | Inspect one memory and lifecycle details |
| `supersede_memory` | Replace a fact while retaining its linked predecessor |
| `propose_merge` | Submit a review candidate; keep originals active until promotion |
| `save_checkpoint`, `get_checkpoint` | Save/restore temporary state by session ID |
| `get_all_memories` | List memories with optional lifecycle views |
| `delete_memory` | Explicit deletion; distinct from superseding |

Use the tool schemas exposed by your running server for current arguments.
Queued writes return an acknowledgement, not proof that persistence has completed.

## Maintenance and deployment

With `REDIS_URL` configured, writes use a Redis Stream and an in-process worker
with acknowledgement, reclaim/retry and a dead-letter stream. Inspect `/metrics`
for queue depth, pending/dead jobs and worker health. Without Redis, writes execute
synchronously. Embedding availability still matters for new writes.

The maintenance job applies tagging rules, handles exact duplicates, proposes
semantic merges for review, and expires checkpoints. Similarity alone does not
establish equivalent facts. Semantic proposals preserve active originals until
review; do not assume every explicit deletion is archived or reversible.

- [deploy/manifest.yaml](deploy/manifest.yaml): plain Kubernetes deployment; review
  credentials, storage and network exposure before applying.
- [Helm chart](https://github.com/janip81/helm-charts/tree/main/charts/remnant):
  deployment configuration and values; requires the corresponding cluster services.
- [docker-compose.yml](docker-compose.yml), [.env.example](.env.example) and
  [src/config.py](src/config.py): local setup and configuration reference.
- [Makefile](Makefile): build and development commands. `make clean` deletes local
  Compose volumes.

## Development and current work

The pytest suite lives in [tests/](tests/):

```bash
pip install -r requirements-mcp.txt -r requirements-dev.txt
make test        # unit tests; PostgreSQL-dependent tests are skipped
make test-db     # everything, against a throwaway pgvector container (needs Docker)
```

[scripts/bench/](scripts/bench/README.md) is an isolated harness for measuring the
hook injection policies. No benchmark results are published yet; local scenarios,
snapshots, streams and answers may contain private data and stay out of Git.

- [AGENTS.md](AGENTS.md): agent working instructions.
- [PLAN.md](PLAN.md) and [ISSUES.md](ISSUES.md): planning; some older status notes
  still need reconciliation against current code and releases.
- [CHANGELOG.md](CHANGELOG.md): change history.
