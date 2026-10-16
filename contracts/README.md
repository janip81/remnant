# Draft contracts: temporal facts, task handoffs, incident episodes

**Status: draft for review.** These are schemas, validators and synthetic fixtures
only. There is no public API, no production migration and no orchestration service,
and the server image doesn't ship anything from this directory. Everything maps onto
the existing PostgreSQL/JSONB payload and the candidate/supersede lifecycle.

Validation lives in [`contracts.py`](contracts.py), the examples in
[`fixtures/`](fixtures/), and the tests in `tests/test_contracts.py`.

## 1. Temporal and evidence fields for a fact (`remnant.fact/1`)

These are optional metadata on an ordinary memory. A value that isn't known is left
out, never guessed.

| Field | Meaning |
|-------|---------|
| `observed_at` | When the fact was observed. This is not `created_at`, which is when Remnant stored it. |
| `valid_from`, `valid_until` | An optional interval in which the fact holds. Leaving either out means unknown, not "forever". |
| `scope` | `{project, environment}`. Both are optional; when missing, the fact is unscoped. |
| `subject`, `property`, `value` | Optional keys that make two facts comparable, e.g. `pg-main` / `primary` / `pg-main-2`. |
| `evidence` | A list of `{kind, ref, observed_at?}`, where `kind` ∈ `tool_output, file, commit, url, user_statement, agent_inference`. |
| `verification` | `unverified` (the default), `tool_verified` (needs `tool_output`, `file` or `commit` evidence) or `user_confirmed` (needs `user_statement` evidence). |

A self-reported confidence number isn't accepted, and an agent's `agent_inference`
never counts as verification.

**Conflicts propose; they never invalidate.** Two active facts conflict when they
have the same `subject`, `property` and scope, different values, and validity
intervals that overlap or are unknown. A conflict creates a merge/supersede
**candidate** for review through the existing flow; no active fact is changed
automatically. The newer `observed_at` wins only as a suggestion. When observation
time is missing, nothing is suggested.

## 2. Task handoff (`remnant.handoff/1`)

This is a small typed record for one agent continuing another's task. It would be
stored as one memory with `kind=handoff`, looked up by `task_id`, and expire like a
checkpoint unless it's promoted.

Required fields are `schema`, `task_id`, `from_agent`, `created_at`, `goal` and
`state` (`open | blocked | done`). Optional fields:

- `to_agent`;
- `repo {path, branch, commit}`;
- `done[]`, `next[]`, `constraints[]`, `open_questions[]`;
- `evidence[]`;
- `hypotheses[{text, status}]`, where `status` ∈ `untested | supported | refuted`.

`supported` needs evidence other than `agent_inference`. `refuted` keeps the failed idea
visible, so the next agent doesn't retry it blindly.

## 3. Incident episode (`remnant.incident/1`)

This is a timeline that keeps observations, hypotheses, attempts and outcomes apart.
Every timeline entry has `at`, `kind` and `text`, where `kind` ∈
`observation | hypothesis | attempt | outcome`:

- an `attempt` has `result` ∈ `failed | partial | worked`, and `worked` needs evidence
  other than `agent_inference`;
- an `outcome` may stay unverified, but it is then rendered as `[unverified]`;
  claiming `tool_verified`/`user_confirmed` needs matching evidence, as for root causes;
- `resolved_at` needs an `outcome` entry.

Malformed records (null or non-object entries, naive timestamps, a non-string `ref`)
are reported as problems; the validators never raise on them.

## 4. Rendering

`render()` produces a short, bounded text block, the same character-budget idea as
prompt injection. It marks unverified facts and refuted hypotheses explicitly, rather
than presenting them as truth.

## 5. Evaluation: freeform vs structured continuation

This compares the same evidence as a freeform note and as a `remnant.handoff/1`
record, for Claude → Codex and Codex → Claude.

- Both arms carry the same facts and evidence references. The case lists
  `parity_terms`, and a test checks that each term appears in both arms, so only the
  format differs.
- Each synthetic case (`fixtures/eval-*.json`) holds the task state, plus hidden
  acceptance checks that the receiving agent never sees. Examples: "does not retry
  the refuted hypothesis", "continues from the stated next step", "keeps the
  constraint".
- Arms: freeform note vs structured record. Everything else is held constant: model,
  repo state and budget.
- Measures: the acceptance pass rate, repeated failed attempts, tokens and time to
  the first correct action.
- This needs model runs, so it isn't executed. It would reuse `scripts/bench/` once
  Codex runtime parity exists.

## Open questions for review

- Should `scope.environment` be free text or a short controlled list (`prod`,
  `mgmt`, `dev`, ...)?
- Should handoffs expire after 7 days like checkpoints, or stay until `done`?
- Does `subject`/`property` need normalisation (case, hostnames), or is exact match
  enough for proposals?
