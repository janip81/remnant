#!/usr/bin/env bash
# Hook: Stop
#
# Fires when Claude finishes responding. Saves a throttled session backup.
# (No reminder text: a Stop hook's stdout with exit 0 never reaches the model, #39.)
# Skips the transcript backup if a compaction just
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
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // ""' 2>/dev/null || echo "")
FLAG="${XDG_CACHE_HOME:-$HOME/.cache}/remnant/precompact-${SESSION_ID//[^A-Za-z0-9_-]/}"
SKIP_BACKUP=false
if [ -f "$FLAG" ]; then
  FLAG_AGE=$(( $(date +%s) - $(stat -c %Y "$FLAG" 2>/dev/null || echo 0) ))
  if [ "$FLAG_AGE" -lt 120 ]; then
    SKIP_BACKUP=true
  else
    rm -f "$FLAG"
  fi
fi


if [ "$SKIP_BACKUP" = "false" ]; then
  echo "$INPUT" | python3 "$SCRIPT_DIR/on_pre_compact.py" --source=session-end 2>/dev/null &
fi

exit 0
