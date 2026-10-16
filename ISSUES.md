# Issues

Known bugs, blockers, and tech debt. Add items here as they are discovered.

| # | Description | Priority | Status | Notes |
|---|-------------|----------|--------|-------|
| 1 | Web UI has no authentication of its own: the UI proxy adds the bearer token server-side, so UI reachability equals full read/write/delete | P1 | open | Put gateway auth in front, or don't route the UI publicly |
| 2 | DNS rebinding protection is disabled in the MCP transport settings | P1 | open | Re-enable with `allowed_hosts` from config |
| 3 | mem0 history DB (`~/.mem0`) lives in the container filesystem and is lost when the container is replaced (rollout, rescheduling) | P2 | open | PVC or disable |
| 4 | CI builds images but runs no tests | P2 | open | Add `make test` (and a DB service for `make test-db`) to the workflow |
| 5 | Legacy hook scripts `search-memory.*` / `save-context.*` are still shipped but unused by the template | debt | open | Remove once no installation uses them |
| 6 | Dedup CronJob still sets legacy `CLAUDE_MEMORY_*` env names | debt | open | Chart/values cleanup |

## Priority levels
- **P0** — Blocker, must fix before continuing
- **P1** — Important, fix soon
- **P2** — Nice to fix, not urgent
- **debt** — Tech debt, address in a future cleanup phase
