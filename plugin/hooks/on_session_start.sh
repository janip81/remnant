#!/usr/bin/env bash
# Hook: SessionStart (matcher: startup|resume|compact)
#
# Bootstraps mem0 context at the start of every session.
# For compact/resume: does the HTTP search directly and injects results —
# does NOT rely on Claude calling search_memory (MCP may be unavailable).
#
# Input:  JSON on stdin with session_id, source, transcript_path, model, cwd
# Output: Text injected into Claude's context (exit 0)

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

INPUT=$(cat)
SOURCE=$(echo "$INPUT" | jq -r '.source // "startup"' 2>/dev/null || echo "startup")
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // ""' 2>/dev/null || echo "")
CWD=$(echo "$INPUT" | jq -r '.cwd // ""' 2>/dev/null || echo "")
PROJECT=$(basename "$CWD")

MEMORY_URL="${REMNANT_URL:-}"
MEMORY_TOKEN="${REMNANT_TOKEN:-}"

# Helper: search mem0 via HTTP directly and return result text
search_direct() {
  local query="$1"
  local limit="${2:-6}"
  if [ -z "$MEMORY_URL" ] || [ -z "$MEMORY_TOKEN" ]; then
    echo ""
    return
  fi
  local payload
  payload=$(jq -cn --arg q "$query" --argjson l "$limit" \
    '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"search_memory","arguments":{"query":$q,"limit":$l}},"id":1}')
  local response
  response=$(curl -sf --max-time 10 \
    -X POST "${MEMORY_URL}/mcp" \
    -H "Authorization: Bearer ${MEMORY_TOKEN}" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    -d "$payload" 2>/dev/null || echo "")
  echo "$response" | grep '^data:' | sed 's/^data: //' | \
    jq -r '.result.content[0].text // ""' 2>/dev/null || echo ""
}

# Helper: this session's own checkpoint, by exact session_id (never by similarity)
checkpoint_direct() {
  if [ -z "$MEMORY_URL" ] || [ -z "$MEMORY_TOKEN" ] || [ -z "$SESSION_ID" ]; then
    echo ""
    return
  fi
  local payload
  payload=$(jq -cn --arg s "$SESSION_ID" \
    '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"get_checkpoint","arguments":{"session_id":$s}},"id":1}')
  curl -sf --max-time 10 -X POST "${MEMORY_URL}/mcp" \
    -H "Authorization: Bearer ${MEMORY_TOKEN}" -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" -d "$payload" 2>/dev/null | \
    grep '^data:' | sed 's/^data: //' | jq -r '.result.content[0].text // ""' 2>/dev/null | \
    grep -v '^No checkpoint for this session' || echo ""
}

if [ "$SOURCE" = "startup" ]; then
  cat <<'EOF'
## Mem0 Session Bootstrap

You have access to persistent memory via the mem0 MCP tools (search_memory, add_memory, get_all_memories).
Before doing anything else:

1. Call `search_memory` with a query related to the current project or user request to load relevant context.
2. Review the returned memories to understand what has been learned in prior sessions.

IMPORTANT: Do NOT skip this step. Always bootstrap context first.
EOF

elif [ "$SOURCE" = "resume" ]; then
  # Do the search directly so Claude gets results even if MCP is slow to connect
  CHECKPOINT=$(checkpoint_direct)
  RESULTS=$(search_direct "project status recent work $PROJECT $CWD" 6)
  # Built outside the heredoc: an apostrophe inside ${VAR:+...} in a heredoc is a
  # bash "bad substitution" error that silently dropped the whole block.
  CP_BLOCK=""
  [ -n "$CHECKPOINT" ] && CP_BLOCK="### This session's last checkpoint
$CHECKPOINT
"
  cat <<EOF
## Mem0 Session Resumed

This is a resumed session.
${CP_BLOCK}
Relevant memories retrieved directly:

${RESULTS:-*(no memories found — MCP may still be connecting)*}

Continue where you left off. If the memories above look stale, call \`search_memory\` with a more specific query.
EOF

elif [ "$SOURCE" = "compact" ]; then
  # The context no longer contains earlier injections: allow them again.
  [ -n "$SESSION_ID" ] && rm -f "${XDG_CACHE_HOME:-$HOME/.cache}/remnant/injected-${SESSION_ID//[^A-Za-z0-9_-]/}.json"
  # Context was just lost — fetch the pre-compaction summary and recent session state directly.
  # Do NOT rely on Claude calling search_memory; MCP may not be connected yet.
  CHECKPOINT=$(checkpoint_direct)
  RESULTS=$(search_direct "$PROJECT $CWD recent work decisions" 6)
  CP_BLOCK=""
  [ -n "$CHECKPOINT" ] && CP_BLOCK="### This session's latest checkpoint (see \"captured\" time — may predate the compaction)
$CHECKPOINT
"
  cat <<EOF
## Mem0 Post-Compaction Recovery

Context was just compacted.
${CP_BLOCK}
Related memories retrieved directly (HTTP fallback — MCP may not be connected yet):

${RESULTS:-*(no memories found — try calling search_memory manually once MCP reconnects)*}

Use the memories above to continue the session. If something is missing, call \`search_memory\` with a specific query once MCP tools are available.
EOF
fi

exit 0
