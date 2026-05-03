#!/usr/bin/env bash
# UserPromptSubmit hook — searches mem0 for context relevant to the user prompt
# Claude Code passes the hook event as JSON on stdin.
# Output is injected as context before Claude responds.
set -euo pipefail

MEMORY_URL="${REMNANT_URL:-}"
MEMORY_TOKEN="${REMNANT_TOKEN:-}"

[[ -z "$MEMORY_URL" || -z "$MEMORY_TOKEN" ]] && exit 0

EVENT=$(cat)
PROMPT=$(echo "$EVENT" | jq -r '.prompt // ""' 2>/dev/null)
[[ ${#PROMPT} -lt 10 ]] && exit 0

QUERY=$(echo "$PROMPT" | head -c 500)

PAYLOAD=$(jq -cn --arg q "$QUERY" \
  '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"search_memory","arguments":{"query":$q,"limit":5}},"id":1}')

RESPONSE=$(curl -sf --max-time 8 \
  -X POST "${MEMORY_URL}/mcp" \
  -H "Authorization: Bearer ${MEMORY_TOKEN}" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d "$PAYLOAD" 2>/dev/null) || exit 0

TEXT=$(echo "$RESPONSE" | grep '^data:' | sed 's/^data: //' | \
  jq -r '.result.content[0].text // ""' 2>/dev/null)

[[ -z "$TEXT" || "$TEXT" == "No memories found." ]] && exit 0

printf '<memory>\n%s\n</memory>\n' "$TEXT"
exit 0
