# Remnant plugin for Claude Code

These are Claude Code hooks plus a stdio MCP proxy for a Remnant server. They inject
memory selectively, restore the session's checkpoint after resume or compaction, and
capture session state on compaction and stop.

## Requirements

- A running Remnant server (see the main [README](../README.md))
- `python3`, `jq` and `curl` on the PATH (the hooks)
- the `requests` package, for `mcp-stdio.py` only (`pip install requests`)

## Setup

Set the environment in `~/.claude/settings.json`. The hooks read only these
variables and have no built-in server default:

```json
{
  "env": {
    "REMNANT_URL": "https://remnant-mcp.your-domain.com",
    "REMNANT_TOKEN": "your-bearer-token",
    "REMNANT_PLUGIN_DIR": "/path/to/remnant/plugin"
  }
}
```

Then copy the `hooks` block from [hooks/settings-template.json](hooks/settings-template.json)
into the same file.

## Add the MCP server

You can connect directly over HTTP:

```bash
claude mcp add --transport http --scope user remnant \
  "${REMNANT_URL}/mcp" --header "Authorization: Bearer ${REMNANT_TOKEN}"
```

Or use the stdio proxy. It starts without network access, and sets a default
`agent_id` from `REMNANT_AGENT_ID`:

```bash
claude mcp add --scope user remnant python3 "$REMNANT_PLUGIN_DIR/mcp-stdio.py"
```

## What the hooks do

| Hook | Script | Action |
|------|--------|--------|
| `SessionStart` | `on_session_start.sh` | On startup, reminds the agent to search memory. On resume or compact, restores this session's own checkpoint (by exact session id) and adds a broad related-memory search. |
| `UserPromptSubmit` | `on_user_prompt.sh` → `on_user_prompt.py` | Injects a memory only if an identifier-like term from the prompt (hostname, file, version, IP ...) literally appears in it. At most 3 memories and 3000 characters, each at most once per session; unreviewed candidates are never injected. Most prompts get nothing. |
| `PreCompact` | `on_pre_compact.sh` → `on_pre_compact.py` | Saves the session checkpoint synchronously before compaction. |
| `Stop` | `on_stop.sh` | Reminds the agent to store learnings. Captures the checkpoint, throttled to once per 15 minutes per session, and skipped right after that session compacted. |

There are two optional hooks. `on_task_completed.sh` (TaskCompleted) is a learning
reminder. `block_memory_write.sh` (PreToolUse, matcher `Write|Edit`) blocks writes to
`MEMORY.md`/`memory/*.md` and tells the agent to call `add_memory` instead. Nothing is
stored unless the agent then makes that call.

`search-memory.*` and `save-context.*` are the earlier versions of the prompt and
capture hooks. The template no longer uses them.

Per-session state (the injected-memory cache and the compaction flag) lives in
`$XDG_CACHE_HOME/remnant/`. `REMNANT_INJECT_POLICY` (`off|broad|selective`) changes
the automatic injection for benchmarks; unset is the behaviour described above. See
[hooks/bench_log.py](hooks/bench_log.py) and [../scripts/bench/](../scripts/bench/README.md).

Codex uses its own hook integration, which isn't part of this directory.
