# Issues

Known bugs, blockers, and tech debt. Add items here as they are discovered.

| # | Description | Priority | Status | Notes |
|---|-------------|----------|--------|-------|
| 1 | Web UI has no authentication of its own: the UI proxy adds the bearer token server-side, so UI reachability equals full read/write/delete | P1 | **done** | Chart 0.2.9 `ui.oauth2Proxy` (OIDC sidecar, e-mail allowlist, UI on 127.0.0.1 via `UI_HOST`); the UI proxy rejects cross-origin writes (#29). On in the author's deployment since 2026-10-18 (Keycloak) |
| 2 | DNS rebinding protection is disabled in the MCP transport settings | P1 | **done** | `MCP_ALLOWED_HOSTS` (#20); chart 0.2.7 `mcp.dnsRebindingProtection`; on in the author's deployment since 2026-10-17 |
| 3 | mem0 history DB (`~/.mem0`) lives in the container filesystem and is lost when the container is replaced (rollout, rescheduling) | P2 | **done** | Remnant never reads it; now in-memory (`history_db_path=":memory:"`) |
| 4 | CI builds images but runs no tests | P2 | open | Add `make test` (and a DB service for `make test-db`) to the workflow |
| 5 | Legacy hook scripts `search-memory.*` / `save-context.*` are still shipped but unused by the template | debt | **done** | Removed (no installation or template used them) |
| 6 | Dedup CronJob still sets legacy `CLAUDE_MEMORY_*` env names | debt | **done** | Job reads `REMNANT_*` (#18); chart 0.2.7 sets them |
| 7 | Category/tags invisible when a row mixes metadata levels (top-level category + `payload.metadata` object), and tag writers created that mix on flat rows | P1 | **done** | Deployed 2026-10-17 (#17). Per-key deepest-level readers and writers; affects 133 rows on the author's data, no migration needed |

## Priority levels
- **P0** — Blocker, must fix before continuing
- **P1** — Important, fix soon
- **P2** — Nice to fix, not urgent
- **debt** — Tech debt, address in a future cleanup phase
