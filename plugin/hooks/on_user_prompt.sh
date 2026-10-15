#!/usr/bin/env bash
# Hook: UserPromptSubmit
#
# Fires on every user message. Injects memories only when the prompt names an
# identifier that literally appears in them (on_user_prompt.py).
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

# Injection decision (identifier gate, budget, per-session cache) lives in
# on_user_prompt.py — see its docstring for the calibration behind it.
echo "$INPUT" | python3 "$SCRIPT_DIR/on_user_prompt.py" 2>/dev/null
exit 0
