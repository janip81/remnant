# Codex Remnant lifecycle hooks

This directory contains the Codex hook, its installer and standard-library tests.
Claude's hooks live alongside it in `../hooks/`; the shared stdio MCP proxy is
`../mcp-stdio.py`. Python 3.11 is required.

## Install or migrate

Run the installer from the released checkout, after reviewing/merging the change
and updating that checkout. The default target is the installer's sibling hook:

```bash
python3.11 /opt/git/app-development/remnant-live/plugin/codex/install-remnant-hooks.py --migrate
```

`--migrate` replaces handlers using the legacy
`/opt/git/prod-k8s/agent-tools/remnant-hooks.py` path. For SessionStart,
UserPromptSubmit, PreCompact and Stop it preserves unrelated hooks, group metadata,
and the first matching handler's other settings; it removes known Remnant duplicates
and verifies exactly one target Remnant handler per event. Missing events are added.
Unknown same-named installations are rejected rather than deleted or duplicated.
Without `--migrate`, legacy or duplicate handlers cause a failure with no config write.

For a different target, pass `--hook-path /absolute/path/remnant-hooks.py`.
`CODEX_HOME` selects the configuration home, defaulting to `~/.codex`.
The installer backs up changed hooks.json, atomically replaces it with mode 0600,
and is idempotent. It never edits config.toml, MCP credentials or trusted hashes.
Restart Codex, open `/hooks`, and review/re-trust the changed definitions. No trust
bypass is used. A migration changes hook definitions; it is not enabled just by
merging these files. Keep the old installation until cutover is verified.

To verify the installer without changing live configuration, use a private
throwaway CODEX_HOME containing copies of synthetic or existing hook definitions.
Never commit that directory, backups or credentials.

## Behavior

- Startup supplies a brief durable-memory reminder. Resume/compact restores the
  exact session checkpoint; no broad memory search runs by default.
- Prompts are redacted before identifier extraction and search. Generic prompts
  return `{}` without a lookup, reminder or failure message. Slash commands and
  short replies are skipped; absolute paths remain supported.
- Selective retrieval asks for 10 JSON candidates and injects only active,
  non-session memories with a whole identifier/path-component match: at most 3
  whole memories and 3000 rendered characters, including metadata/separators.
  Longer names and dotted continuations are excluded. Hyphenated common words can
  still trigger a search; the gate is a heuristic, not a relevance guarantee.
- A separate bounded cache stores hashed IDs per session/context. File locks
  serialize prompt updates; atomic generation resets on compaction invalidate
  even a late writer's stale IDs. Cache problems do not suppress checkpoints.
- Stop saves bounded redacted user/final-answer context as an exact-session
  checkpoint, at most once per 15 minutes. PreCompact synchronously saves even an
  unchanged capture. Tool outputs, analysis and commentary are excluded. Historical
  snapshots are not evidence of current infrastructure status.
- Delegated reviewers skip all hooks. Plan/dontAsk modes and recent explicit
  no-memory-write requests skip snapshot writes. Deliberate add_memory remains
  the way to save verified reusable facts, with agent_id='codex'.

The existing Remnant transport in CODEX_HOME/config.toml supplies credentials.
No embedded credentials are shipped. Responses over 128 KiB and malformed JSON
fail quietly. Each RPC has a 4s timeout; hook definitions use 12s. Network failures
are advisory and hooks never grant tool permissions or start continuation loops.
Local fingerprint/hashed-ID state is private; no raw prompt/memory logs are made.
Per-session cache-file retention cleanup remains a separate follow-up.

## Benchmark switches

`REMNANT_INJECT_POLICY=off|broad|selective` controls automatic retrieval (unset or
invalid defaults to selective). Off keeps startup reminder and exact-session
checkpoint recovery. Broad explicitly enables the previous project-prefixed
search on startup/prompts and does not deduplicate.

`REMNANT_BENCH_LOG` enables metadata-only JSONL events (policy/reason, prompt hash,
lookup/elapsed time, injected count/chars). `REMNANT_BENCH_READ_ONLY=1` suppresses
remote Stop/PreCompact writes; compaction still invalidates local ID state. Keep
benchmark logs private. The standalone benchmark harness belongs in homelab-ops,
not this package.

## Tests

```bash
python3.11 -B -m unittest discover -s plugin/codex -p 'test_*.py'
# Existing server/hook unit suite, including this package:
env -u TEST_DATABASE_URL python -m pytest -q tests plugin/codex
```

Tests use synthetic events/temporary CODEX_HOME, not live credentials or memories.
They cover filtering, boundaries, budgets, concurrency, cache corruption/late
writers, checkpoint lifecycle, installer migration/preservation and idempotence.
Passing synthetic tests does not prove native Codex trust, real compaction or
production checkpoint persistence. Record those separately at cutover.
