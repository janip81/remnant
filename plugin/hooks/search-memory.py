#!/usr/bin/env python3
"""
UserPromptSubmit hook — searches memory for context relevant to the user's prompt
and injects it as a system note before Claude responds.

Claude Code passes the hook event as JSON on stdin:
  {"prompt": "...", "session_id": "..."}

Output on stdout is injected into the conversation context.
Exit 0 always — memory failures are non-fatal.
"""

import json
import os
import sys

import requests


def main():
    url = os.environ.get("REMNANT_URL", "").rstrip("/")
    token = os.environ.get("REMNANT_TOKEN", "")

    if not url or not token:
        sys.exit(0)

    try:
        event = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    prompt = event.get("prompt", "")
    if not prompt or len(prompt.strip()) < 10:
        sys.exit(0)

    query = prompt[:500]

    try:
        resp = requests.post(
            f"{url}/mcp",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "search_memory",
                    "arguments": {"query": query, "limit": 5},
                },
                "id": 1,
            },
            timeout=8,
            stream=True,
        )
        resp.raise_for_status()

        result_text = ""
        for line in resp.iter_lines():
            if not line:
                continue
            decoded = line.decode("utf-8") if isinstance(line, bytes) else line
            if decoded.startswith("data: "):
                try:
                    data = json.loads(decoded[6:])
                    content = (
                        data.get("result", {})
                        .get("content", [{}])[0]
                        .get("text", "")
                    )
                    if content and content != "No memories found.":
                        result_text = content
                except Exception:
                    pass

        if result_text:
            print(f"<memory>\n{result_text}\n</memory>")

    except Exception:
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
