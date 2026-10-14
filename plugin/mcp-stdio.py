#!/usr/bin/env python3
"""
stdio ↔ HTTP MCP proxy for Remnant.

Claude Code spawns this as a stdio MCP server.  It starts instantly
(no network on startup) and forwards each JSON-RPC message to the HTTP
MCP endpoint on demand.

Usage:
  claude mcp add remnant python3 /path/to/mcp-stdio.py
  (env vars REMNANT_URL and REMNANT_TOKEN must be set)
  Optional REMNANT_AGENT_ID: default agent_id for add_memory calls that omit it.
"""

import json
import os
import sys
from typing import Optional

import requests

URL = os.environ.get("REMNANT_URL", "").rstrip("/") + "/mcp"
TOKEN = os.environ.get("REMNANT_TOKEN", "")
AGENT_ID = os.environ.get("REMNANT_AGENT_ID", "").strip()

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


def apply_default_agent_id(msg: dict) -> None:
    """Fill in agent_id on add_memory calls that don't set one."""
    if not AGENT_ID or msg.get("method") != "tools/call":
        return
    params = msg.get("params") or {}
    if params.get("name") != "add_memory":
        return
    args = params.setdefault("arguments", {})
    if not str(args.get("agent_id") or "").strip():
        args["agent_id"] = AGENT_ID


def main():
    if not TOKEN:
        sys.stderr.write("REMNANT_TOKEN not set — set via env var\n")
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
        apply_default_agent_id(msg)
        result = http_post(msg)

        if is_request and result is not None:
            sys.stdout.write(json.dumps(result) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
