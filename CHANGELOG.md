# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

---

## [0.5.0-beta] - 2026-05-02

### Added
- GitHub Actions CI workflow: builds MCP and UI images on push to `main` (`latest`) and `dev` (`dev`), version tags on release
- `dev` branch strategy: work in `dev`, PR to `main`, cluster runs `:dev` images
- Grafana dashboard JSON committed to `grafana/remnant.json`
- `helm/` directory synced to chart 0.2.3 (tag rules, job runs, settings ConfigMap, ServiceMonitor, dedup CronJob)

### Changed
- Renamed from `claude-memory` to `remnant` throughout — images, env vars (`REMNANT_*`), namespaces, database, Helm release name
- `CLAUDE_MEMORY_URL/TOKEN/PLUGIN_DIR` → `REMNANT_URL/TOKEN/PLUGIN_DIR`
- `MEM0_USER_ID` → `REMNANT_USER_ID`
- Removed obsolete combined `Dockerfile` (split into `Dockerfile.mcp` and `Dockerfile.ui`)
- `scripts/imported.json` excluded from git

### Fixed
- Nightly dedup Phase 0 (LLM tagging) now reviews all memories once via `llm_reviewed` flag — previously skipped memories that already had keyword tags, so keyword-tagged memories never got LLM review

### Documented
- `BENCHMARKS.md`: live benchmark results (search ~288ms avg, add_memory ~17s, health ~71ms, nightly job phases)
- `README.md`: full rewrite covering architecture, tagging modes, nightly job phases, Makefile targets, Kubernetes deployment

---

## [Unreleased - pre-rename]

### Added
- Initial project skeleton (Phase 0)
- AGENTS.md: architecture, tech stack, MCP tools, deployment plan
- MVP.md: scope, done criteria, future phases
- PLAN.md: 4 phases (MCP server → mem0 → Helm → gitops)
- README.md: connection instructions
