#!/usr/bin/env bash
# Hook: PreCompact
#
# Fires BEFORE context compaction. Saves session state to mem0 via HTTP
# (synchronously, before compaction starts) then instructs Claude to also
# save a structured summary if MCP tools are available.
#
# Output: Text instructions injected into Claude's context.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

INPUT=$(cat)

# Write flag so the Stop hook knows a compaction just happened and skips
# its own backup (which would save post-compaction content instead).
touch /tmp/.claude-precompact-flag

# Save transcript state synchronously BEFORE compaction starts.
# Running without & ensures the save completes before the hook exits
# and compaction proceeds. Hook timeout is 30s — enough for one HTTP call.
echo "$INPUT" | python3 "$SCRIPT_DIR/on_pre_compact.py" --source=pre-compaction 2>/dev/null

cat <<'EOF'
## CRITICAL: Pre-Compaction Session Summary

Context compaction is about to happen. You are about to lose most of your conversation history. If the mem0 MCP tools are available, store a comprehensive session summary NOW using `add_memory`:

```
## Session Summary (Pre-Compaction)

### User's Goal
[What the user originally asked for and their intent]

### What Was Accomplished
[Numbered list of tasks completed, features built, bugs fixed]

### Key Decisions Made
[Architectural choices, design decisions, trade-offs discussed]

### Files Created or Modified
[List of important file paths with what changed in each]

### Current State
[What is in progress RIGHT NOW — the task you were in the middle of]
[Any pending items, blockers, or next steps]

### Important Context
[User preferences observed, coding patterns, anything that would help
the post-compaction agent continue without asking redundant questions]
```

Note: A transcript-based backup has already been saved automatically. The MCP save is additional structured context on top of that.
EOF

exit 0
