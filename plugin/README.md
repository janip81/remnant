# claude-memory plugin

Claude Code plugin for the claude-memory server — hooks, skills, and MCP config.

## Requirements

- A running claude-memory server (see main README)
- Claude Code with MCP support
- `requests` Python package (`pip install requests`)

## Quick install

```bash
export CLAUDE_MEMORY_URL="https://mem0-mcp.your-domain.com"
export CLAUDE_MEMORY_TOKEN="your-bearer-token"
```

Add to Claude Code (user scope, available in all projects):

```bash
claude mcp add --transport http --scope user claude-memory \
  "${CLAUDE_MEMORY_URL}/mcp" \
  --header "Authorization: Bearer ${CLAUDE_MEMORY_TOKEN}"
```

## Hooks

Copy `hooks/hooks.json` into your Claude Code settings, or reference it from
`.claude/settings.json` in a project. Set `CLAUDE_MEMORY_PLUGIN_DIR` to the
absolute path of this `plugin/` directory.

```json
{
  "env": {
    "CLAUDE_MEMORY_URL": "https://mem0-mcp.your-domain.com",
    "CLAUDE_MEMORY_TOKEN": "your-bearer-token",
    "CLAUDE_MEMORY_PLUGIN_DIR": "/path/to/claude-memory/plugin"
  }
}
```

### What the hooks do

| Hook | Trigger | Action |
|------|---------|--------|
| `UserPromptSubmit` | Every prompt | Searches memory for relevant context, injects as `<memory>` block before Claude responds |
| `PreCompact` | Before context compression | Reminds Claude to save important facts before they're lost |
| `Stop` | Session end | Reminds Claude to save anything worth keeping |

## Skill

The skill (`skills/claude-memory/SKILL.md`) teaches Claude when to search,
what to save, and how to write good memories. Install it by adding the path
to your Claude Code skills configuration.

## Manual MCP config (without plugin marketplace)

Add to `~/.claude/mcp.json` or `.mcp.json` in your project:

```json
{
  "mcpServers": {
    "claude-memory": {
      "type": "http",
      "url": "https://mem0-mcp.your-domain.com/mcp",
      "headers": {
        "Authorization": "Bearer your-bearer-token"
      }
    }
  }
}
```
