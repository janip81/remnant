#!/usr/bin/env python3
"""Benchmark instrumentation for the hooks (scripts/bench/).

When REMNANT_BENCH_LOG is set, every hook invocation appends one JSON line to it:
run id, hook event, injection policy, skip/gate reason, timings and injected
counts. Never prompt or memory text; a prompt is identified only by a short
local hash so the launcher can match events to scripted turns. Without
REMNANT_BENCH_LOG this is a no-op.

REMNANT_INJECT_POLICY selects what the hooks inject automatically:
  unset      production behaviour (identifier gate on prompts, broad search on
             resume/compact)
  off        no automatic memory search anywhere (manual search_memory only)
  broad      pre-gate behaviour: project-prefixed top-3 on every prompt, broad
             search on resume/compact
  selective  identifier gate on prompts; resume/compact restore the checkpoint only
The session's own checkpoint is restored identically under every policy.

Shell usage: bench_log.py <event> key=value ...   (numbers are stored as numbers)
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time

POLICIES = ("off", "broad", "selective")


def policy() -> str:
    p = os.environ.get("REMNANT_INJECT_POLICY", "").strip().lower()
    return p if p in POLICIES else ""


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.strip().encode()).hexdigest()[:12]


def log(event: str, **fields) -> None:
    path = os.environ.get("REMNANT_BENCH_LOG", "")
    if not path:
        return
    rec = {"run_id": os.environ.get("REMNANT_BENCH_RUN", ""), "event": event,
           "policy": policy() or "default", "t_wall": round(time.time(), 3), **fields}
    try:
        with open(path, "a") as f:          # one short line per write: appends don't interleave
            f.write(json.dumps(rec, sort_keys=True) + "\n")
    except OSError:
        pass


def _value(v: str):
    try:
        return int(v)
    except ValueError:
        try:
            return float(v)
        except ValueError:
            return v


if __name__ == "__main__":
    if len(sys.argv) > 1:
        log(sys.argv[1], **{k: _value(v) for k, v in (a.split("=", 1) for a in sys.argv[2:] if "=" in a)})
    sys.exit(0)
