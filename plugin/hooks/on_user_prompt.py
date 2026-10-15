#!/usr/bin/env python3
"""UserPromptSubmit: inject memories only on an exact identifier match.

Calibration on 45 real prompts (2026-10-15, agent-tools/calibration-2026-10-15.json):
injecting the top 3 hits for every prompt was useful 13% of the time; no cosine
threshold got above ~44%. Hits where an identifier from the prompt (a hostname,
chart, file, version, error code ...) literally appears in the memory were useful
5 of 6 times. So this hook injects only those, and nothing otherwise — agents call
search_memory themselves for everything else.

Rules: identifier = token with a digit or one of - . _ / : (len >= 4, not just a
number); matched on token boundaries in the original prompt, not the project name;
at most MAX_ITEMS memories and MAX_CHARS characters; 'candidate' rows (unreviewed
LLM output) are never injected; a memory is injected at most once per session
(cache in $XDG_CACHE_HOME/remnant, cleared on compaction by on_session_start.sh).

Input: hook JSON on stdin. Output: <memory> block or nothing. Always exits 0.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request

MAX_ITEMS = int(os.environ.get("REMNANT_INJECT_MAX_ITEMS", "3"))
MAX_CHARS = int(os.environ.get("REMNANT_INJECT_MAX_CHARS", "3000"))
CANDIDATES = 10
TIMEOUT = float(os.environ.get("REMNANT_INJECT_TIMEOUT", "5"))
STATE_DIR = os.path.join(os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")), "remnant")

_IDENT = re.compile(r"[A-Za-z0-9][\w.\-/:]*[A-Za-z0-9]")
_ID_CHARS = re.compile(r"[\d\-._/:]")
_BORDER = r"[\w\-/]"   # characters that continue an identifier; '.' may end a sentence


def identifiers(prompt: str) -> list[str]:
    """Identifier-like tokens of the prompt, lower-cased, in order, unique."""
    out = []
    for tok in _IDENT.findall(prompt):
        t = tok.lower().rstrip(".:/")
        if len(t) < 4 or not _ID_CHARS.search(t) or re.fullmatch(r"[\d.:/\-]+", t):
            continue
        if t not in out:
            out.append(t)
    return out


def mentions(text: str, ident: str) -> bool:
    """ident occurs in text as a whole token (not inside a longer identifier)."""
    return re.search(rf"(?<!{_BORDER}){re.escape(ident)}(?!{_BORDER})", text, re.I) is not None


def select(hits: list[dict], idents: list[str], seen: set[str]) -> list[dict]:
    chosen, used = [], 0
    for h in hits:
        text, mid = h.get("memory") or "", str(h.get("id") or "")
        if not mid or mid in seen or (h.get("state") or "active") == "candidate":
            continue
        if not any(mentions(text, i) for i in idents):
            continue
        if used + len(text) > MAX_CHARS:
            continue                      # skip whole memories rather than cutting them
        chosen.append(h)
        used += len(text)
        if len(chosen) >= MAX_ITEMS:
            break
    return chosen


def cache_path(session_id: str) -> str:
    return os.path.join(STATE_DIR, f"injected-{re.sub(r'[^A-Za-z0-9_-]', '', session_id)}.json")


def load_seen(session_id: str) -> set[str]:
    if not session_id:
        return set()
    try:
        with open(cache_path(session_id)) as f:
            return set(json.load(f))
    except (OSError, ValueError):
        return set()


def save_seen(session_id: str, seen: set[str]) -> None:
    if not session_id:
        return
    try:
        os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
        tmp = cache_path(session_id) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(sorted(seen), f)
        os.replace(tmp, cache_path(session_id))      # atomic
    except OSError:
        pass


def search(url: str, token: str, query: str) -> list[dict]:
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "search_memory",
                          "arguments": {"query": query, "limit": CANDIDATES, "format": "json"}}}
    req = urllib.request.Request(f"{url.rstrip('/')}/mcp", data=json.dumps(payload).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                                          "Accept": "application/json, text/event-stream"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        for line in resp.read().decode("utf-8", "replace").splitlines():
            if line.startswith("data:"):
                result = json.loads(line[5:]).get("result", {})
                text = (result.get("content") or [{}])[0].get("text", "")
                return json.loads(text) if text.startswith("[") else []
    return []


def main() -> None:
    url, token = os.environ.get("REMNANT_URL", ""), os.environ.get("REMNANT_TOKEN", "")
    try:
        event = json.loads(sys.stdin.read())
    except ValueError:
        return
    prompt = (event.get("prompt") or "").strip()
    if not url or not token or len(prompt) < 20 or prompt.startswith("/"):
        return
    idents = identifiers(prompt[:900])
    if not idents:
        return                                       # nothing specific to look up
    session_id = str(event.get("session_id") or "")
    try:
        hits = search(url, token, prompt[:900])
    except Exception:
        return                                       # never block or slow the prompt
    seen = load_seen(session_id)
    chosen = select(hits, idents, seen)
    if not chosen:
        return
    save_seen(session_id, seen | {str(h["id"]) for h in chosen})
    body = "\n\n".join(f"[{h['id'][:8]} | matches {', '.join(i for i in idents if mentions(h['memory'], i))}]\n{h['memory']}"
                       for h in chosen)
    print(f"<memory>\n{body}\n</memory>")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
