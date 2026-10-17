# Retrieval benchmark harness

This measures whether automatic memory injection saves tokens and time without
losing answer quality. It compares three hook policies on scripted, read-only
Claude Code sessions:

| arm | `REMNANT_INJECT_POLICY` | prompts | resume / compact |
|-----|-------------------------|---------|------------------|
| B0  | `off`       | no automatic search | checkpoint only |
| B   | `broad`     | project-prefixed top 3 (the pre-gate behaviour) | checkpoint + broad search |
| C   | `selective` | identifier gate | checkpoint only |

Unset is the production behaviour: the identifier gate on prompts, and a broad
search on resume/compact.

## Isolation

- Every run talks to a local Remnant on `127.0.0.1:18096`. Its database is recreated
  from a snapshot template, Redis is flushed and the hook cache is fresh.
- The server accepts only a bench-only token, which the production server rejects
  (`preflight` checks this).
- User settings aren't loaded (`--setting-sources project`). Hooks and env come from
  a generated `--settings`, and MCP from `--strict-mcp-config`.
- Runs are read-only: `--tools Read,Grep,Glob` plus Remnant `search_memory`/`get_memory`,
  under `--permission-mode dontAsk`.
- Each run gets a fresh `git archive` copy of the target repo. The copy's own
  `.claude/settings*.json` and `.mcp.json` are removed, and recorded in
  `sanitized_project_files`, so the target repo can't add hooks, env or MCP servers.
- Remaining limitation: the real `HOME` is kept, because CLI login needs it. User
  settings are excluded, but other per-user CLI state still applies, identically in
  every arm.
- Each run has its own server log, so MCP request counts don't accumulate across runs.
- The hooks keep their state per session (`$XDG_CACHE_HOME/remnant/`), so benchmark
  sessions and live sessions don't share flags.

## Use

```bash
B="python scripts/bench/bench.py"         # interpreter with requirements-mcp.txt installed
$B snapshot --out ~/.cache/remnant-bench/snap.dump   # reads the prod replica
$B up --dump ~/.cache/remnant-bench/snap.dump
$B preflight                               # no model calls
$B schedule --scenarios scen.json --repeats 2 --seed 42 --out plan.json
$B execute --plan plan.json --results ~/.cache/remnant-bench/results.jsonl \
   --cap-usd 10 --max-run-usd 1.5 --model <id> --confirm-model-calls
$B export --results ~/.cache/remnant-bench/results.jsonl --out metrics.json   # numbers only
$B down
```

`execute --claude <fake>` exercises the harness without model calls
(`tests/fixtures/fake_claude.py` shows the event shapes; `tests/test_bench.py` runs offline).

## Cost cap

The cap fails closed, but it's a guard rather than a hard limit:

- A run starts only if `spent + worst` is still within the cap. `worst` is the largest
  of `--max-run-usd` and every run cost seen so far.
- A single run can still cost more than `worst`, so the cap can be overshot by that
  one run's excess.
- A run with unknown cost (missing `total_cost_usd`) stops the execution, and a later
  `execute` refuses to resume until that run has been inspected.
- Per-turn costs are summed. If the CLI reports a cumulative session cost instead,
  this over-counts, which is the safe direction. The smoke test must settle which it is.

## Token semantics

The Claude result `usage` fields `input_tokens`, `cache_creation_input_tokens` and
`cache_read_input_tokens` are disjoint, and their sum is `total_input_tokens`.
Thinking tokens are part of `output_tokens`, so `output_tokens_details` is never
added on top. A field missing from any turn makes the run total `null`, never 0.

## Scenario files

Scenario files hold infrastructure details, and the results hold model answers, so
both stay out of this repo. A scenario looks like this:

```json
{"repo": "/path/to/repo", "repo_commit": "<sha>", "scenarios": [
  {"id": "s01", "category": "identifier",
   "segments": [["first turn", "follow-up"], ["turn after --resume"]],
   "accept": {"0": ["regex that must match turn 0's answer"]}}]}
```

Each segment is one CLI process, with its turns streamed over stdin. The segments
after the first resume the same session. A `/compact` turn expects a compact
SessionStart.

## Three separate outcomes per run

- `harness_valid` means the run measured what it claims:
  - the hook events match the script exactly: the same order and count (a repeated
    prompt needs two events), startup/resume/compact starts, the arm's policy, and
    only this run's sessions;
  - all scripted turns ran.
- `cli_ok` means every turn returned a result, without a CLI error, timeout, crash
  or non-zero exit.
- `accepted` means every turn that has a check passed it. Every scenario must have at
  least one objective check.

Failed and timed-out runs stay in the results and in summaries. They are never
dropped or silently re-run. Hook events (`bench_log.py`) never contain prompt or
memory text.

## Privacy

- `execute`, `preflight` and `snapshot` run with umask 077.
- They refuse a work directory, results file or snapshot inside the repo unless that
  path is git-ignored. They also refuse an existing one that group/others can read.
- `export` writes only labels, flags, timings, counts and token/cost numbers.

## Codex adapter (pending)

Runtime parity for Codex is not implemented. `codex exec --json` prints JSONL events,
but this harness hasn't inspected their shapes yet, which needs a model call. The
contract an adapter has to provide is the same as `run_segment`:

- per turn: monotonic time from sending the message to the first non-empty text and
  to the final result;
- tool call names;
- native usage fields, with their meaning documented;
- cost, or `null`;
- an error/timeout/crash flag;
- the session id, so a later segment can resume.

Codex's hook needs the same `bench_log` events and `REMNANT_INJECT_POLICY` switch.
