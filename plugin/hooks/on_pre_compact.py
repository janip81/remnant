#!/usr/bin/env python3
"""Capture session state via the mem0 MCP HTTP endpoint.

Safety net for PreCompact and Stop hooks — reads the transcript JSONL,
extracts structured session state, and stores it in mem0 directly.

Used by:
  - PreCompact hook: Tags with "pre-compaction" (context about to be lost)
  - Stop hook:       Tags with "session-end" (session ending)

Input:  JSON on stdin with transcript_path, session_id, cwd
Output: stderr logs only (exit 0 always — must not block)
"""

from __future__ import annotations

import json
import logging
import os
import sys
import urllib.request
import urllib.error

log = logging.getLogger("mem0-capture")
log.setLevel(logging.DEBUG)
_handler = logging.StreamHandler(sys.stderr)
_handler.setFormatter(logging.Formatter("[mem0-capture] %(message)s"))
log.addHandler(_handler)

MAX_TAIL_LINES = 200
MAX_USER_MESSAGES = 8
MAX_BASH_COMMANDS = 8
MAX_ASSISTANT_TEXT = 1500
MAX_TOTAL_CHARS = 4000  # hard cap to stay within Ollama context window


def tail_lines(filepath: str, n: int) -> list[str]:
    try:
        with open(filepath, "rb") as f:
            f.seek(0, 2)
            file_size = f.tell()
            if file_size == 0:
                return []
            chunk_size = min(file_size, n * 4096)
            f.seek(max(0, file_size - chunk_size))
            data = f.read().decode("utf-8", errors="replace")
            return data.splitlines()[-n:]
    except OSError:
        return []


def parse_transcript(lines: list[str]) -> dict:
    user_messages: list[str] = []
    files_modified: set[str] = set()
    bash_commands: list[str] = []
    last_assistant_text = ""

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        entry_type = entry.get("type")
        if entry_type not in ("user", "assistant"):
            continue
        if entry.get("isSidechain"):
            continue

        message = entry.get("message", {})
        content_blocks = message.get("content", [])

        if entry_type == "user":
            parts = []
            if isinstance(content_blocks, str):
                parts.append(content_blocks)
            elif isinstance(content_blocks, list):
                for block in content_blocks:
                    if isinstance(block, str):
                        parts.append(block)
                    elif isinstance(block, dict) and block.get("type") == "text":
                        parts.append(block.get("text", ""))
            text = "\n".join(parts).strip()
            if text and len(text) > 10 and not text.startswith("<"):
                user_messages.append(text)

        elif entry_type == "assistant":
            for block in content_blocks:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    text = block.get("text", "").strip()
                    if text:
                        last_assistant_text = text
                if block.get("type") == "tool_use":
                    tool_name = block.get("name", "")
                    tool_input = block.get("input", {})
                    if tool_name in ("Write", "Edit"):
                        fp = tool_input.get("file_path", "")
                        if fp:
                            files_modified.add(fp)
                    elif tool_name == "Bash":
                        cmd = tool_input.get("command", "")
                        if cmd:
                            bash_commands.append(cmd)

    return {
        "user_messages": user_messages[-MAX_USER_MESSAGES:],
        "files_modified": sorted(files_modified),
        "bash_commands": bash_commands[-MAX_BASH_COMMANDS:],
        "last_assistant_text": last_assistant_text[:MAX_ASSISTANT_TEXT],
    }


def build_content(state: dict, source: str) -> str:
    parts = [f"## Session State ({source})\n"]

    if state["user_messages"]:
        parts.append("### What the user was working on")
        for msg in state["user_messages"]:
            truncated = msg[:500] + "..." if len(msg) > 500 else msg
            parts.append(f"- {truncated}")
        parts.append("")

    if state["files_modified"]:
        parts.append("### Files modified this session")
        for fp in state["files_modified"]:
            parts.append(f"- `{fp}`")
        parts.append("")

    if state["bash_commands"]:
        parts.append("### Recent commands")
        for cmd in state["bash_commands"]:
            truncated = cmd[:200] + "..." if len(cmd) > 200 else cmd
            parts.append(f"- `{truncated}`")
        parts.append("")

    if state["last_assistant_text"]:
        parts.append("### Last context")
        parts.append(state["last_assistant_text"])
        parts.append("")

    result = "\n".join(parts)
    if len(result) > MAX_TOTAL_CHARS:
        result = result[:MAX_TOTAL_CHARS] + "\n...(truncated)"
    return result


def store_memory(memory_url: str, token: str, content: str, source: str) -> bool:
    """Store session state via mem0 MCP JSON-RPC endpoint."""
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": "add_memory",
            "arguments": {"content": content, "infer": False, "category": "session"},
        },
        "id": 1,
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{memory_url}/mcp",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            # Parse SSE — look for data: lines containing the result
            for line in raw.splitlines():
                if line.startswith("data:"):
                    try:
                        obj = json.loads(line[5:].strip())
                        result = obj.get("result", {})
                        # isError=true means the tool ran but failed (e.g. wrong param name)
                        if "result" in obj and not result.get("isError"):
                            log.info("Session state stored (source=%s)", source)
                            return True
                        if result.get("isError"):
                            text = (result.get("content") or [{}])[0].get("text", "")
                            log.warning("Tool error (source=%s): %s", source, text[:200])
                            return False
                    except json.JSONDecodeError:
                        pass
            log.warning("Unexpected response: %s", raw[:200])
            return False
    except urllib.error.URLError as e:
        log.warning("MCP call failed: %s", e)
        return False


def main():
    source = "pre-compaction"
    for arg in sys.argv[1:]:
        if arg.startswith("--source="):
            source = arg.split("=", 1)[1]

    memory_url = os.environ.get("REMNANT_URL", "")
    token = os.environ.get("REMNANT_TOKEN", "")
    if not memory_url or not token:
        log.debug("REMNANT_URL or REMNANT_TOKEN not set, skipping capture")
        return

    try:
        hook_input = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, OSError):
        log.debug("No valid JSON on stdin")
        return

    transcript_path = hook_input.get("transcript_path", "")
    if not transcript_path:
        log.debug("No transcript_path provided")
        return

    lines = tail_lines(transcript_path, MAX_TAIL_LINES)
    if not lines:
        log.debug("Transcript empty or unreadable: %s", transcript_path)
        return

    state = parse_transcript(lines)
    if not state["user_messages"] and not state["files_modified"]:
        log.debug("No meaningful session state to capture")
        return

    content = build_content(state, source)

    log.info(
        "Capturing session state: %d user msgs, %d files, %d commands",
        len(state["user_messages"]),
        len(state["files_modified"]),
        len(state["bash_commands"]),
    )

    store_memory(memory_url, token, content, source)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.error("Unexpected error: %s", e)
    sys.exit(0)
