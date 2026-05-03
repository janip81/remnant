#!/usr/bin/env bash
# Hook: UserPromptSubmit
#
# Fires on every user message. Searches mem0 for relevant memories
# and injects them into Claude's context before processing.
#
# Input:  JSON on stdin with prompt, session_id, cwd, transcript_path
# Output: Matching memories as context text (exit 0)
#
# Skips search for very short prompts (< 20 chars) and when
# REMNANT_URL / REMNANT_TOKEN are not set.

# Intentionally omit -e so the script always exits 0 even if
# curl or jq fail — must never block the user's prompt.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

INPUT=$(cat)
PROMPT=$(echo "$INPUT" | jq -r '.prompt // ""' 2>/dev/null || echo "")

MEMORY_URL="${REMNANT_URL:-}"
MEMORY_TOKEN="${REMNANT_TOKEN:-}"

# /clear wipes context immediately — capture session state before it's gone.
# UserPromptSubmit fires before the command executes, so this runs in time.
if echo "$PROMPT" | grep -qE '^\s*/clear\s*$'; then
  echo "$INPUT" | python3 "$SCRIPT_DIR/on_pre_compact.py" --source=clear 2>/dev/null &
  exit 0
fi

if [ ${#PROMPT} -lt 20 ]; then
  exit 0
fi

if [ -z "$MEMORY_URL" ] || [ -z "$MEMORY_TOKEN" ]; then
  exit 0
fi

CWD=$(echo "$INPUT" | jq -r '.cwd // ""' 2>/dev/null || echo "")
PROJECT=$(basename "$CWD")

# Prepend project name so semantic search surfaces project-specific memories
# even for short/vague prompts like "ok how to fix it?"
QUERY_BASE=$(echo "$PROMPT" | head -c 900)
if [ -n "$PROJECT" ] && [ "$PROJECT" != "." ]; then
  QUERY="${PROJECT} ${QUERY_BASE}"
else
  QUERY="$QUERY_BASE"
fi

PAYLOAD=$(jq -cn --arg q "$QUERY" \
  '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"search_memory","arguments":{"query":$q,"limit":3}},"id":1}')

RESPONSE=$(curl -sf --max-time 8 \
  -X POST "${MEMORY_URL}/mcp" \
  -H "Authorization: Bearer ${MEMORY_TOKEN}" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d "$PAYLOAD" 2>/dev/null || echo "")

if [ -z "$RESPONSE" ]; then
  exit 0
fi

TEXT=$(echo "$RESPONSE" | grep '^data:' | sed 's/^data: //' | \
  jq -r '.result.content[0].text // ""' 2>/dev/null || echo "")

if [ -z "$TEXT" ] || [ "$TEXT" = "No memories found." ]; then
  exit 0
fi

printf '<memory>\n%s\n</memory>\n' "$TEXT"
exit 0
