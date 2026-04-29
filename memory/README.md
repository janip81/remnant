# Memory Files

Cross-session memory files for Claude Code. Claude writes one of these at the
end of every work session so the next session has full context without needing
to re-read all the code.

## Naming convention

`YYYY-MM-DD_topic_phase.md`

Examples:
- `2026-03-25_auth-setup_phase1.md`
- `2026-03-26_api-endpoints_phase2.md`

## What to save

- Key decisions made this session
- What was implemented and what comes next
- Blockers or known issues discovered
- Non-obvious context that future-Claude will need
- Phase completion notes

## What NOT to save

- Code — it lives in git
- Task tracking — that is PLAN.md
- Bug tracking — that is ISSUES.md
- Anything obvious from reading the code
