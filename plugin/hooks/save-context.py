#!/usr/bin/env python3
"""
PreCompact / Stop hook — reminds Claude to save anything worth remembering
before the session ends or context is compressed.

Outputs a system note instructing Claude to call add_memory for any facts,
decisions, or corrections that should persist to the next session.

Exit 0 always — memory failures are non-fatal.
"""

import os
import sys


def main():
    url = os.environ.get("CLAUDE_MEMORY_URL", "")
    token = os.environ.get("CLAUDE_MEMORY_TOKEN", "")

    if not url or not token:
        sys.exit(0)

    print(
        "Before this session ends, review the conversation and call add_memory() "
        "for any facts worth keeping: corrections you were given, conventions you "
        "learned, decisions made, or cluster/project-specific details that would "
        "save time in a future session. Be specific — one fact per call."
    )

    sys.exit(0)


if __name__ == "__main__":
    main()
