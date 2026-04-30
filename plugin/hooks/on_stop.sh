#!/usr/bin/env bash
# Hook: Stop
#
# Fires when Claude finishes responding. Reminds Claude to store any
# unsaved learnings. Skips the transcript backup if a compaction just
# happened (PreCompact already saved pre-compaction state — saving again
# here would capture post-compaction content instead, which is worse).
#
# IMPORTANT: Check stop_hook_active to avoid infinite loops.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

INPUT=$(cat)
STOP_HOOK_ACTIVE=$(echo "$INPUT" | jq -r '.stop_hook_active // false' 2>/dev/null || echo "false")

if [ "$STOP_HOOK_ACTIVE" = "true" ]; then
  exit 0
fi

# Check if PreCompact ran recently (within 120s). If so, skip the Python
# backup — it would save post-compaction content, which is less valuable
# than the pre-compaction snapshot already stored by on_pre_compact.sh.
FLAG=/tmp/.claude-precompact-flag
SKIP_BACKUP=false
if [ -f "$FLAG" ]; then
  FLAG_AGE=$(( $(date +%s) - $(stat -c %Y "$FLAG" 2>/dev/null || echo 0) ))
  if [ "$FLAG_AGE" -lt 120 ]; then
    SKIP_BACKUP=true
  else
    rm -f "$FLAG"
  fi
fi

cat <<'EOF'
Before finishing, check if there are important learnings from this interaction that should be persisted using the mem0 `add_memory` tool:

1. Were any significant decisions made?
2. Were any new patterns or strategies discovered?
3. Did any approach fail?
4. Did you learn anything about the user's preferences?
5. Were there environment/setup discoveries?

Only store genuinely useful learnings — skip if nothing notable happened.
EOF

if [ "$SKIP_BACKUP" = "false" ]; then
  echo "$INPUT" | python3 "$SCRIPT_DIR/on_pre_compact.py" --source=session-end 2>/dev/null &
fi

exit 0
