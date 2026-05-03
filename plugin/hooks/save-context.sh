#!/usr/bin/env bash
# PreCompact / Stop hook — reminds Claude to persist facts before context compacts.
# No stdin parsing needed — just print the instruction.
set -euo pipefail

MEMORY_URL="${REMNANT_URL:-}"
MEMORY_TOKEN="${REMNANT_TOKEN:-}"

[[ -z "$MEMORY_URL" || -z "$MEMORY_TOKEN" ]] && exit 0

echo "Before this session ends, review the conversation and call add_memory() for any facts worth keeping: corrections you received, conventions you learned, decisions made, or cluster/project-specific details that would save time in a future session. Be specific — one fact per call."
exit 0
