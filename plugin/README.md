# remnant plugin

Claude Code hooks for the remnant memory server — automatic memory injection on every prompt, session saving on stop and compact.

## Requirements

- A running remnant server (see main [README](../README.md))
- Claude Code with MCP support
- `requests` Python package (`pip install requests`)

## Setup

Set these environment variables (add to your shell profile or Claude Code settings):

```bash
export REMNANT_URL="https://remnant-mcp.your-domain.com"
export REMNANT_TOKEN="your-bearer-token"
export REMNANT_PLUGIN_DIR="/path/to/remnant/plugin"
```

## Add MCP server

```bash
claude mcp add --transport http --scope user remnant \
  "${REMNANT_URL}/mcp" \
  --header "Authorization: Bearer ${REMNANT_TOKEN}"
```

## Configure hooks

Add to `~/.claude/settings.json`:

```json
{
  "env": {
    "REMNANT_URL": "https://remnant-mcp.your-domain.com",
    "REMNANT_TOKEN": "your-bearer-token",
    "REMNANT_PLUGIN_DIR": "/path/to/remnant/plugin"
  },
  "hooks": {
    "UserPromptSubmit": [
      {"matcher": "", "hooks": [{"type": "command", "command": "$REMNANT_PLUGIN_DIR/hooks/on_user_prompt.sh"}]}
    ],
    "PreCompact": [
      {"matcher": "", "hooks": [{"type": "command", "command": "$REMNANT_PLUGIN_DIR/hooks/on_pre_compact.sh"}]}
    ],
    "Stop": [
      {"matcher": "", "hooks": [{"type": "command", "command": "$REMNANT_PLUGIN_DIR/hooks/on_stop.sh"}]}
    ]
  }
}
```

## What the hooks do

| Hook | Trigger | Action |
|------|---------|--------|
| `UserPromptSubmit` | Every prompt | Searches memory for relevant context, injects as `<memory>` block before Claude responds |
| `PreCompact` | Before context compression | Saves session state to memory so nothing is lost during compaction |
| `Stop` | Session end | Saves session summary and key decisions to memory |
