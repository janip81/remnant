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

REMNANT_INJECT_POLICY (bench_log.py) switches this off, or back to the pre-gate
"broad" behaviour, for benchmarks; unset means the identifier gate.

Input: hook JSON on stdin. Output: <memory> block or nothing. Always exits 0.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench_log  # noqa: E402

MAX_ITEMS = int(os.environ.get("REMNANT_INJECT_MAX_ITEMS", "3"))
MAX_CHARS = int(os.environ.get("REMNANT_INJECT_MAX_CHARS", "3000"))
CANDIDATES = 10
TIMEOUT = float(os.environ.get("REMNANT_INJECT_TIMEOUT", "5"))
STATE_DIR = os.path.join(os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")), "remnant")

# \w is Unicode-aware, so Swedish names (åäö-config) are identifiers too; a
# leading '/' is kept so absolute paths match themselves.
_IDENT = re.compile(r"/?\w[\w.\-/:]*\w")
_ID_CHARS = re.compile(r"[\d\-._/:]")
_IPV4 = re.compile(r"\d{1,3}(\.\d{1,3}){3}")
# Token boundaries: no identifier character before, and after it neither an
# identifier character nor a '.' that continues one (node-a vs node-a.example.com).
# A trailing '.' that ends a sentence is fine.
_BEFORE = r"(?<![\w\-/.])"
_AFTER = r"(?![\w\-/]|\.\w)"


def identifiers(prompt: str) -> list[str]:
    """Identifier-like tokens of the prompt, lower-cased, in order, unique."""
    out = []
    for tok in _IDENT.findall(prompt):
        t = tok.lower().rstrip(".:/") if tok != "/" else tok
        if len(t) < 4 or not _ID_CHARS.search(t):
            continue
        if re.fullmatch(r"[\d.:/\-]+", t) and not _IPV4.fullmatch(t):
            continue                 # bare numbers and versions (1.35) are too generic
        if t not in out:
            out.append(t)
    return out


def mentions(text: str, ident: str) -> bool:
    """ident occurs in text as a whole token (not inside a longer identifier)."""
    return re.search(rf"{_BEFORE}{re.escape(ident)}{_AFTER}", text, re.I) is not None


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
        fd, tmp = tempfile.mkstemp(dir=STATE_DIR, prefix=".injected-")   # unique per writer
        with os.fdopen(fd, "w") as f:
            json.dump(sorted(seen), f)
        os.replace(tmp, cache_path(session_id))      # atomic
    except OSError:
        pass


class session_lock:
    """Serialise read-select-write of one session's cache across concurrent prompts."""

    def __init__(self, session_id: str):
        self.path = cache_path(session_id) + ".lock" if session_id else ""
        self.fd = None

    def __enter__(self):
        if self.path:
            try:
                os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
                self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
                deadline = time.monotonic() + 2
                while True:
                    try:
                        fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() > deadline:
                            raise TimeoutError("cache lock busy")
                        time.sleep(0.05)
            except TimeoutError:
                os.close(self.fd)
                self.fd = None
                raise
            except OSError:          # cache dir unusable: run without the cache lock
                self.fd = None
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)       # releases the lock


def search(url: str, token: str, query: str, limit: int = CANDIDATES) -> list[dict]:
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "search_memory",
                          "arguments": {"query": query, "limit": limit, "format": "json"}}}
    req = urllib.request.Request(f"{url.rstrip('/')}/mcp", data=json.dumps(payload).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                                          "Accept": "application/json, text/event-stream"})
    deadline = time.monotonic() + TIMEOUT           # total budget, not per socket read
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        while time.monotonic() < deadline:
            raw = resp.readline()
            if not raw:
                break
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("data:"):
                result = json.loads(line[5:]).get("result", {})
                text = (result.get("content") or [{}])[0].get("text", "")
                return json.loads(text) if text.startswith("[") else []
    return []


def broad_query(prompt: str, cwd: str) -> str:
    """Pre-gate behaviour (before the identifier gate): project name + prompt, top 3."""
    project = os.path.basename(cwd.rstrip("/")) if cwd else ""
    return f"{project} {prompt[:900]}" if project and project != "." else prompt[:900]


def render(chosen: list[dict], idents: list[str]) -> str:
    def label(h):
        hit = [i for i in idents if mentions(h["memory"], i)]
        return f"{h['id'][:8]} | matches {', '.join(hit)}" if hit else h["id"][:8]
    body = "\n\n".join(f"[{label(h)}]\n{h['memory']}" for h in chosen)
    return f"<memory>\n{body}\n</memory>"


def main() -> None:
    t0 = time.monotonic()
    policy = bench_log.policy()
    stats: dict = {"lookup_ms": None, "injected_count": 0, "injected_chars": 0}

    def done(reason: str, out: str = "") -> None:
        if out:
            print(out)
        bench_log.log("UserPromptSubmit", reason=reason, prompt_hash=bench_log.prompt_hash(prompt),
                      session_id=session_id, elapsed_ms=round((time.monotonic() - t0) * 1000, 1), **stats)

    prompt, session_id = "", ""
    url, token = os.environ.get("REMNANT_URL", ""), os.environ.get("REMNANT_TOKEN", "")
    try:
        event = json.loads(sys.stdin.read())
    except ValueError:
        return done("bad-input")
    prompt = (event.get("prompt") or "").strip()
    session_id = str(event.get("session_id") or "")
    if policy == "off":
        return done("policy-off")
    if not url or not token:
        return done("no-config")
    if len(prompt) < 20 or prompt.startswith("/"):
        return done("short-or-command")
    if policy == "broad":
        t1 = time.monotonic()
        try:
            hits = search(url, token, broad_query(prompt, str(event.get("cwd") or "")), limit=3)
        except Exception:
            return done("search-failed")
        finally:
            stats["lookup_ms"] = round((time.monotonic() - t1) * 1000, 1)
        if not hits:
            return done("no-hits")
        out = render(hits, [])
        stats.update(injected_count=len(hits), injected_chars=len(out))
        return done("injected", out)
    idents = identifiers(prompt[:900])
    if not idents:
        return done("no-identifier")                 # nothing specific to look up
    t1 = time.monotonic()
    try:
        hits = search(url, token, prompt[:900])
    except Exception:
        return done("search-failed")                 # never block or slow the prompt
    finally:
        stats["lookup_ms"] = round((time.monotonic() - t1) * 1000, 1)
    try:
        with session_lock(session_id):
            seen = load_seen(session_id)
            chosen = select(hits, idents, seen)
            if chosen:
                save_seen(session_id, seen | {str(h["id"]) for h in chosen})
    except TimeoutError:
        return done("cache-busy")                    # another prompt holds the cache; skip rather than wait
    if not chosen:
        return done("no-match")
    out = render(chosen, idents)
    stats.update(injected_count=len(chosen), injected_chars=len(out))
    return done("injected", out)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
