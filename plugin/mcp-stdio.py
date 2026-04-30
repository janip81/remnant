#!/usr/bin/env python3
"""
stdio ↔ HTTP MCP proxy for claude-memory / Remnant.

Claude Code spawns this as a stdio MCP server.  It starts instantly
(no network on startup) and forwards each JSON-RPC message to the HTTP
MCP endpoint on demand.

Usage:
  claude mcp add claude-memory python3 /path/to/mcp-stdio.py
  (env vars CLAUDE_MEMORY_URL and CLAUDE_MEMORY_TOKEN must be set)
"""

import json
import os
import sys
from typing import Optional

import requests

URL = os.environ.get("CLAUDE_MEMORY_URL", "").rstrip("/") + "/mcp"
TOKEN = os.environ.get("CLAUDE_MEMORY_TOKEN", "")

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


def http_post(payload: dict) -> Optional[dict]:
    try:
        resp = requests.post(URL, json=payload, headers=HEADERS, timeout=30, stream=True)
        resp.raise_for_status()
        for raw in resp.iter_lines():
            if not raw:
                continue
            line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            if line.startswith("data:"):
                text = line[5:].strip()
                if text:
                    return json.loads(text)
        return None
    except requests.exceptions.HTTPError as e:
        return {
            "jsonrpc": "2.0",
            "id": payload.get("id"),
            "error": {"code": -32603, "message": f"HTTP {e.response.status_code}: {e.response.text[:200]}"},
        }
    except Exception as e:
        return {
            "jsonrpc": "2.0",
            "id": payload.get("id"),
            "error": {"code": -32603, "message": str(e)},
        }


def main():
    if not TOKEN:
        sys.stderr.write("CLAUDE_MEMORY_TOKEN not set\n")
        sys.exit(1)

    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            msg = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        is_request = "id" in msg
        result = http_post(msg)

        if is_request and result is not None:
            sys.stdout.write(json.dumps(result) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
