# Skill: remnant

You have access to a persistent memory store via four tools: `add_memory`,
`search_memory`, `get_all_memories`, and `delete_memory`. The store is fully
local — self-hosted by the user, no data leaves their infrastructure.

## When to search

Call `search_memory` at the start of any session where project or user context
would help — before reading code, before making architecture decisions, before
running commands in a familiar environment. Also search when the user references
something you don't have context for ("the usual gateway", "the prod cluster",
"that bug we fixed").

You don't need to announce that you're searching. Just do it.

## When to save

Call `add_memory` when you learn something that would save time in a future
session:

- A correction ("don't use git add -A here — stage specific files only")
- A convention ("cert-manager uses ClusterIssuer named letsencrypt-prod")
- A project-specific fact ("CNPG barman secret is cnpg-barman-s3-creds")
- A decision and its reason ("selfHeal:false on ArgoCD apps is temporary, not design")
- A tool or workflow the user prefers

Do not save things derivable from reading the current code or git history.
Do not save ephemeral state (what's currently broken, in-progress work).
Do not save things already in CLAUDE.md or README.

## How to write a memory

Be specific. Include the WHY when it matters.

Good:
```
add_memory("Never use git add -A in this repo — gitignored paths contain sensitive files that must not be committed")
```

Too vague:
```
add_memory("Be careful with git")
```

One fact per call. Don't bundle multiple things.

## When to delete

Call `delete_memory` when you find a stored memory that is wrong, outdated, or
no longer applies. If the user corrects a memory ("that's no longer true"), delete
the old one and add the corrected version.

## Memory types worth saving

| Type | Example |
|------|---------|
| Feedback / correction | "Don't mock the database in tests — we got burned when mocked tests passed but prod migration failed" |
| Convention | "Use Gateway API HTTPRoute, not Ingress — internal gateway for private services, external for public" |
| Project-specific fact | "Readiness probe on CNPG pods uses /readyz — not /healthz" |
| Decision + reason | "n8n liveness probe: initialDelaySeconds:120 — pg connection takes 90-120s to resolve" |
| User preference | "User prefers bundled PRs over many small ones for refactors in this repo" |

## What not to save

- File contents or code snippets — read the file instead
- Git history — use git log
- Anything already in CLAUDE.md
- "User asked me to do X today" — that's ephemeral, not a fact worth keeping
