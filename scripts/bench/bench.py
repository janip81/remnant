#!/usr/bin/env python3
"""Retrieval benchmark harness: does automatic memory injection save tokens/time?

Compares hook injection policies (plugin/hooks/bench_log.py) on scripted, read-only
Claude Code sessions against a disposable local copy of the memory database:

  B0 = off        no automatic search; MCP search_memory available
  B  = broad      pre-gate behaviour (prefixed top-3 per prompt, broad resume/compact)
  C  = selective  identifier gate; resume/compact restore the checkpoint only

Isolation: the run never sees the production URL or token. A local Remnant
(127.0.0.1:18096) with a bench-only token serves a database recreated from a
snapshot template before every run; Redis is flushed and the hook cache is fresh.
User settings are not loaded (--setting-sources project + generated --settings).
Runs are read-only: --tools Read,Grep,Glob, Remnant search/get only, dontAsk.

Subcommands (no model calls unless stated):
  snapshot  dump mem0 + tag_rules from the prod read replica to a local file
  up        start the bench Postgres/Redis containers and load a snapshot
  down      remove the containers
  preflight reset, start the local server, check search works, prod rejects the
            bench token, and generated config contains nothing from production
  schedule  write a seeded, paired, randomised run plan for a scenario file
  execute   MODEL CALLS: run a plan in order within --cap-usd (see cmd_execute)
  export    numeric/redacted metrics from a results file
Scenario files and results are local-only (they hold infra details and answers).
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import random
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PLUGIN = REPO / "plugin"
ARMS = {"B0": "off", "B": "broad", "C": "selective"}
PG_NAME, PG_PORT = "remnant-bench-pg", 25440
REDIS_NAME, REDIS_PORT = "remnant-bench-redis", 26390
PG_PASS = "bench-local"
SERVER_PORT = 18096
SERVER_URL = f"http://127.0.0.1:{SERVER_PORT}"
PROD_MARKERS = ("threshold.se", "remnant-mcp.", "remnant-pg-")
HOOK_FILES = {"UserPromptSubmit": "on_user_prompt.sh", "SessionStart": "on_session_start.sh",
              "PreCompact": "on_pre_compact.sh", "Stop": "on_stop.sh"}
ALLOWED_MCP = ["mcp__remnant__search_memory", "mcp__remnant__get_memory"]


def sh(*cmd, check=True, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, text=True, capture_output=True, **kw)


def psql(sql: str, db: str = "postgres") -> str:
    return sh("docker", "exec", PG_NAME, "psql", "-U", "postgres", "-d", db, "-v", "ON_ERROR_STOP=1",
              "-At", "-c", sql).stdout


def git_rev(path: Path) -> str:
    r = sh("git", "-C", str(path), "rev-parse", "--short", "HEAD", check=False)
    dirty = sh("git", "-C", str(path), "status", "--porcelain", "--untracked-files=no", check=False).stdout.strip()
    return (r.stdout.strip() or "?") + ("-dirty" if dirty else "")


# ---------------------------------------------------------------- environment

def cmd_snapshot(a):
    out = Path(a.out)
    with open(out, "wb") as f:
        os.chmod(out, 0o600)
        subprocess.run(["kubectl", "-n", "remnant", "exec", a.pod, "-c", "postgres", "--", "pg_dump", "-U", "postgres",
                        "-d", "remnant", "-t", "mem0", "-t", "tag_rules", "-Fc"], stdout=f, check=True)
    print(f"snapshot {out} ({out.stat().st_size // 1024} KiB) from {a.pod}")


def cmd_up(a):
    for name in (PG_NAME, REDIS_NAME):
        sh("docker", "rm", "-fv", name, check=False)
    sh("docker", "run", "-d", "--name", PG_NAME, "-e", "POSTGRES_PASSWORD=" + PG_PASS,
       "-p", f"127.0.0.1:{PG_PORT}:5432", "pgvector/pgvector:pg16")
    sh("docker", "run", "-d", "--name", REDIS_NAME, "-p", f"127.0.0.1:{REDIS_PORT}:6379", "redis:7-alpine")
    for _ in range(60):
        if sh("docker", "exec", PG_NAME, "pg_isready", "-U", "postgres", "-q", check=False).returncode == 0:
            break
        time.sleep(1)
    time.sleep(2)
    psql("CREATE ROLE remnant LOGIN PASSWORD '" + PG_PASS + "'")
    psql("CREATE DATABASE snapshot OWNER remnant")
    psql("CREATE EXTENSION IF NOT EXISTS vector", "snapshot")
    with open(a.dump, "rb") as f:
        r = subprocess.run(["docker", "exec", "-i", PG_NAME, "pg_restore", "-U", "postgres", "-d", "snapshot",
                            "--no-owner", "--role=remnant"], stdin=f, capture_output=True, text=True)
    if r.returncode not in (0, 1):            # 1 = warnings (e.g. missing prod roles)
        sys.exit(r.stderr)
    psql("ALTER DATABASE snapshot WITH is_template = true")
    n = psql("SELECT count(*) FROM mem0", "snapshot").strip()
    print(f"bench containers up; snapshot has {n} rows")


def cmd_down(a):
    for name in (PG_NAME, REDIS_NAME):
        sh("docker", "rm", "-fv", name, check=False)
    print("bench containers removed")


class Server:
    """Local Remnant for one run, from a clean copy of the snapshot."""

    def __init__(self, workdir: Path, token: str, python: str):
        self.workdir, self.token, self.python, self.proc = workdir, token, python, None

    def reset(self):
        self.stop()                             # worker runs in-process: stopping drains it
        psql("DROP DATABASE IF EXISTS run WITH (FORCE)")
        psql("CREATE DATABASE run TEMPLATE snapshot OWNER remnant")
        sh("docker", "exec", REDIS_NAME, "redis-cli", "FLUSHALL")
        want, got = (psql("SELECT count(*) FROM mem0", db).strip() for db in ("snapshot", "run"))
        if want != got or sh("docker", "exec", REDIS_NAME, "redis-cli", "DBSIZE").stdout.strip() != "0":
            sys.exit(f"reset failed: run db {got} rows vs snapshot {want}, or redis not empty")

    def env(self) -> dict:
        return {"PATH": os.environ["PATH"], "HOME": str(self.workdir), "MEM0_TELEMETRY": "false",
                "BEARER_TOKEN": self.token, "APP_HOST": "127.0.0.1", "APP_PORT": str(SERVER_PORT),
                "DATABASE_URL": f"postgresql://remnant:{PG_PASS}@127.0.0.1:{PG_PORT}/run",
                "REDIS_URL": f"redis://127.0.0.1:{REDIS_PORT}/0",
                "OLLAMA_BASE_URL": os.environ.get("BENCH_OLLAMA_URL", "http://192.168.81.20:11434"),
                "OLLAMA_MODEL": "qwen2.5:7b", "OLLAMA_EMBED_MODEL": "nomic-embed-text",
                "REMNANT_USER_ID": os.environ.get("BENCH_USER_ID", "jani"), "REMNANT_AGENT_ID": "bench",
                "TAGGING_MODE": "rules"}

    def start(self):
        self.workdir.mkdir(parents=True, exist_ok=True)
        assert not (self.workdir / ".env").exists()          # config.py would load it
        log = open(self.workdir / "server.log", "a")
        self.proc = subprocess.Popen([self.python, str(REPO / "src" / "main.py")], cwd=self.workdir, env=self.env(),
                                     stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        for _ in range(90):
            try:
                urllib.request.urlopen(SERVER_URL + "/health", timeout=2)
                return
            except (urllib.error.URLError, OSError):
                if self.proc.poll() is not None:
                    sys.exit(f"local server exited; see {self.workdir / 'server.log'}")
                time.sleep(1)
        sys.exit("local server did not become healthy")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGTERM)
            try:
                self.proc.wait(20)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
        self.proc = None


def mcp_call(url: str, token: str, name: str, args: dict, timeout=30):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": name, "arguments": args}}).encode()
    req = urllib.request.Request(url.rstrip("/") + "/mcp", body, method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for line in r.read().decode().splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:])
    return None


# ---------------------------------------------------------------- one run

def prod_secrets() -> list[str]:
    """Values that must never appear in a run's config or environment."""
    try:
        env = json.loads((Path.home() / ".claude" / "settings.json").read_text()).get("env", {})
    except (OSError, ValueError):
        env = {}
    return [v for k, v in env.items() if k in ("REMNANT_TOKEN", "REMNANT_URL") and v]


def run_config(run_dir: Path, run_id: str, arm: str, token: str) -> tuple[dict, list[str]]:
    cache, log = run_dir / "cache", run_dir / "hooks.jsonl"
    cache.mkdir(parents=True, exist_ok=True)
    env = {"REMNANT_URL": SERVER_URL, "REMNANT_TOKEN": token, "REMNANT_PLUGIN_DIR": str(PLUGIN),
           "REMNANT_INJECT_POLICY": ARMS[arm], "REMNANT_BENCH_LOG": str(log), "REMNANT_BENCH_RUN": run_id,
           "XDG_CACHE_HOME": str(cache), "REMNANT_AGENT_ID": "bench"}
    settings = {"env": env, "hooks": {ev: [{"hooks": [{"type": "command", "command": str(PLUGIN / "hooks" / f),
                                                       "timeout": 20}]}] for ev, f in HOOK_FILES.items()}}
    mcp = {"mcpServers": {"remnant": {"command": "python3", "args": [str(PLUGIN / "mcp-stdio.py")],
                                      "env": {"REMNANT_URL": SERVER_URL, "REMNANT_TOKEN": token,
                                              "REMNANT_AGENT_ID": "bench"}}}}
    (run_dir / "settings.json").write_text(json.dumps(settings, indent=1))
    (run_dir / "mcp.json").write_text(json.dumps(mcp, indent=1))
    proc_env = {"PATH": os.environ["PATH"], "HOME": str(Path.home()), "LANG": os.environ.get("LANG", "C.UTF-8"),
                **env}
    flags = ["--setting-sources", "project", "--settings", str(run_dir / "settings.json"),
             "--strict-mcp-config", "--mcp-config", str(run_dir / "mcp.json"),
             "--tools", "Read,Grep,Glob", "--allowedTools", *ALLOWED_MCP, "--permission-mode", "dontAsk"]
    return proc_env, flags


def assert_isolated(run_dir: Path, proc_env: dict):
    blob = json.dumps(proc_env) + "".join(p.read_text() for p in run_dir.glob("*.json"))
    leaks = [m for m in PROD_MARKERS if m in blob] + ["prod secret" for s in prod_secrets() if s in blob]
    if leaks:
        sys.exit(f"isolation check failed: {leaks}")
    assert proc_env["REMNANT_URL"] == SERVER_URL


def run_segment(cmd: list[str], cwd: Path, env: dict, prompts: list[str], out_path: Path,
                timeout: int) -> tuple[str | None, list[dict]]:
    """One claude process (one session start), several user turns over stream-json stdin.

    Each turn is timed from writing its message: first non-empty text delta and its
    result event, on a monotonic clock. The next turn is sent after the result."""
    p = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, bufsize=1)
    recs, session_id = [], None
    raw = open(out_path, "a")

    def send(text):
        rec = {"t0": time.monotonic(), "first_text_ms": None, "result": None, "tool_uses": [], "timeout": False,
               "crashed": False}
        try:
            p.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n")
            p.stdin.flush()
        except OSError:
            rec["crashed"] = True                    # CLI already gone; the read loop ends at EOF
        return rec

    lines: queue.Queue = queue.Queue()
    err: list[str] = []
    threading.Thread(target=lambda: [lines.put(l) for l in p.stdout] + [lines.put(None)], daemon=True).start()
    threading.Thread(target=lambda: err.extend(p.stderr), daemon=True).start()    # never blocks on a full pipe
    cur = send(prompts[0])
    try:
        while True:
            try:                                     # deadline holds even if the CLI goes silent
                line = lines.get(timeout=max(0.0, cur["t0"] + timeout - time.monotonic()))
            except queue.Empty:
                cur["timeout"] = True
                recs.append(cur)
                break
            if line is None:                         # CLI exited before this turn's result
                cur["crashed"] = True
                recs.append(cur)
                break
            now = time.monotonic()
            ms = round((now - cur["t0"]) * 1000, 1)
            raw.write(json.dumps({"turn": len(recs), "ms": ms, "line": line.rstrip("\n")}) + "\n")
            try:
                ev = json.loads(line)
            except ValueError:
                ev = {}
            if ev.get("session_id"):
                session_id = session_id or ev["session_id"]
            if ev.get("type") == "stream_event" and cur["first_text_ms"] is None:
                d = (ev.get("event") or {}).get("delta") or {}
                if d.get("type") == "text_delta" and d.get("text", "").strip():
                    cur["first_text_ms"] = ms
            if ev.get("type") == "assistant":
                cur["tool_uses"] += [c.get("name") for c in (ev.get("message") or {}).get("content") or []
                                     if c.get("type") == "tool_use"]
            if ev.get("type") == "result":
                cur["result"], cur["result_ms"] = ev, ms
                recs.append(cur)
                if len(recs) == len(prompts):
                    break
                cur = send(prompts[len(recs)])
    finally:
        try:
            p.stdin.close()
        except OSError:
            pass
        if recs and recs[-1]["timeout"]:
            p.kill()
        try:
            p.wait(30)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
        raw.close()
    for r in recs:
        r["exit"], r["wall_ms"] = p.returncode, None
    if recs:
        recs[-1]["stderr_tail"] = "".join(err)[-500:]
    return session_id, recs


def check_events(log: Path, run_id: str, segments: list[list[str]]) -> list[str]:
    """Every submitted prompt needs its UserPromptSubmit event; every process start its
    SessionStart (startup, then resume per later segment); every /compact a compact."""
    try:
        events = [json.loads(l) for l in log.read_text().splitlines()]
    except OSError:
        events = []
    events = [e for e in events if e.get("run_id") == run_id]
    sys.path.insert(0, str(PLUGIN / "hooks"))
    import bench_log
    problems = []
    hashes = [e.get("prompt_hash") for e in events if e["event"] == "UserPromptSubmit"]
    for i, prompt in enumerate(t for seg in segments for t in seg):
        if not prompt.startswith("/") and bench_log.prompt_hash(prompt) not in hashes:
            problems.append(f"turn {i}: no UserPromptSubmit event")
    got = [e.get("source") for e in events if e["event"] == "SessionStart"]
    want = {"startup": 1, "resume": len(segments) - 1,
            "compact": sum(t.strip() == "/compact" for seg in segments for t in seg)}
    for src, n in want.items():
        if got.count(src) < n:
            problems.append(f"SessionStart {src}: expected {n}, got {got.count(src)}")
    return problems


def accept(answer: str, patterns: list[str]) -> bool:
    return all(re.search(p, answer or "", re.I | re.S) for p in patterns)


# Claude result usage: input_tokens, cache_creation_input_tokens and
# cache_read_input_tokens are disjoint (the sum is all input); thinking is part of
# output_tokens (output_tokens_details is a subset, never added).
USAGE_FIELDS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")


def known_sum(values):
    """Sum, or None when any value is unknown — missing never counts as zero."""
    values = list(values)
    return None if not values or any(v is None for v in values) else sum(values)


def run_totals(turns: list[dict]) -> dict:
    out = {f: known_sum((t.get("usage") or {}).get(f) for t in turns) for f in USAGE_FIELDS}
    ins = [out[f] for f in USAGE_FIELDS[:3]]
    out["total_input_tokens"] = None if None in ins else sum(ins)
    # Summing per-turn total_cost_usd is conservative: if the CLI reports a cumulative
    # session cost this over-counts (safe for the cap); the smoke test settles which.
    out["cost_usd"] = known_sum(t.get("cost_usd") for t in turns)
    out["cost_usd_last"] = turns[-1].get("cost_usd") if turns else None
    return out


def hook_stats(log: Path, run_id: str) -> dict:
    try:
        ev = [json.loads(l) for l in log.read_text().splitlines()]
    except OSError:
        ev = []
    ev = [e for e in ev if e.get("run_id") == run_id]
    return {"hook_events": len(ev),
            "auto_lookups": sum(1 for e in ev if e.get("lookup_ms") is not None),
            "auto_injected_chars": sum(e.get("injected_chars") or 0 for e in ev),
            "auto_checkpoint_chars": sum(e.get("checkpoint_chars") or 0 for e in ev)}


def run_one(spec: dict, scen: dict, sfile: dict, a, server: Server, token: str, results: Path):
    run_id = f"{spec['scenario']}-r{spec['repeat']}-{spec['arm']}-{secrets.token_hex(3)}"
    run_dir = Path(a.workdir) / "runs" / run_id
    repo_name = Path(sfile["repo"]).name
    wt = run_dir / repo_name                         # basename = project name the hooks see
    wt.mkdir(parents=True)                           # fails if the run dir already exists
    archive = subprocess.Popen(["git", "-C", sfile["repo"], "archive", sfile["repo_commit"]], stdout=subprocess.PIPE)
    subprocess.run(["tar", "-x", "-C", str(wt)], stdin=archive.stdout, check=True)
    archive.wait()
    server.reset()
    server.start()
    proc_env, flags = run_config(run_dir, run_id, spec["arm"], token)
    assert_isolated(run_dir, proc_env)
    assert not any((run_dir / "cache").iterdir()), "hook cache not fresh"
    segments = scen.get("segments") or [scen["turns"]]
    turns, session_id, n_expected = [], None, sum(map(len, segments))
    sys.path.insert(0, str(PLUGIN / "hooks"))
    import bench_log
    for seg_no, prompts in enumerate(segments):
        cmd = [a.claude, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
               "--include-partial-messages", "--model", a.model, *flags] + (["--resume", session_id] if seg_no else [])
        sid, recs = run_segment(cmd, wt, proc_env, prompts, run_dir / "stream.jsonl", a.turn_timeout)
        session_id = session_id or sid
        for prompt, rec in zip(prompts, recs):
            i = len(turns)
            res = rec.get("result") or {}
            answer = res.get("result") or ""
            checks = scen["accept"].get(str(i), [])
            turns.append({"i": i, "segment": seg_no, "prompt_hash": bench_log.prompt_hash(prompt),
                          "first_text_ms": rec["first_text_ms"], "result_ms": rec.get("result_ms"),
                          "timeout": rec["timeout"], "crashed": rec["crashed"], "exit": rec["exit"],
                          "has_result": bool(res),
                          "is_error": res.get("is_error"), "usage": res.get("usage"),
                          "cost_usd": res.get("total_cost_usd"), "cli_duration_ms": res.get("duration_ms"),
                          "num_turns": res.get("num_turns"), "tool_uses": rec["tool_uses"],
                          "permission_denials": len(res.get("permission_denials") or []),
                          "accepted": accept(answer, checks) if checks else None})
            (run_dir / f"answer-{i}.md").write_text(answer)
        if len(recs) < len(prompts) or not session_id:
            break
    server.stop()
    problems = check_events(run_dir / "hooks.jsonl", run_id, segments)
    hits = sum(1 for l in (server.workdir / "server.log").read_text().splitlines() if "POST /mcp" in l)
    complete = len(turns) == n_expected
    checked = [t["accepted"] for t in turns if t["accepted"] is not None]
    row = {"run_id": run_id, **spec, "model": a.model, "plugin_rev": git_rev(REPO), "repo_commit": sfile["repo_commit"],
           "claude_version": sh(a.claude, "--version").stdout.strip(), "turns": turns,
           # three separate questions: did the harness measure what it claims, did the
           # CLI/model work, did the answers pass their objective checks
           "harness_valid": not problems and complete, "hook_problems": problems, "local_mcp_requests": hits,
           "cli_ok": complete and all(t["has_result"] and not t["timeout"] and not t["is_error"] and t["exit"] == 0
                                      for t in turns),
           "accepted": (all(checked) if complete and len(checked) == len(scen["accept"]) else False),
           **run_totals(turns), **hook_stats(run_dir / "hooks.jsonl", run_id)}
    with open(results, "a") as f:
        f.write(json.dumps(row) + "\n")
    return row


# ---------------------------------------------------------------- commands

def load_scenarios(path: str) -> dict:
    sfile = json.loads(Path(path).read_text())
    for s in sfile["scenarios"]:
        assert s["id"] and s["category"] and (s.get("turns") or s.get("segments")), s
        n = sum(map(len, s.get("segments") or [s["turns"]]))
        acc = s.get("accept") or {}
        if not acc or any(not v or not 0 <= int(k) < n for k, v in acc.items()):
            sys.exit(f"scenario {s['id']}: needs objective accept checks on existing turn indices")
    return sfile


def cmd_schedule(a):
    sfile = load_scenarios(a.scenarios)
    rng = random.Random(a.seed)
    blocks = [(s["id"], r) for s in sfile["scenarios"] for r in range(a.repeats)]
    rng.shuffle(blocks)
    plan = []
    for sid, r in blocks:                      # paired: all arms of one scenario×repeat together
        arms = list(ARMS)
        rng.shuffle(arms)
        plan += [{"scenario": sid, "repeat": r, "arm": arm, "block": f"{sid}-r{r}"} for arm in arms]
    Path(a.out).write_text(json.dumps({"seed": a.seed, "scenarios": a.scenarios, "runs": plan}, indent=1))
    print(f"{len(plan)} runs -> {a.out}")


def read_token(a) -> str:
    p = Path(a.workdir) / "bench-token"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(secrets.token_hex(32))
        os.chmod(p, 0o600)
    return p.read_text().strip()


def cmd_preflight(a):
    token = read_token(a)
    server = Server(Path(a.workdir) / "server", token, a.python)
    server.reset()
    server.start()
    try:
        r = mcp_call(SERVER_URL, token, "search_memory", {"query": "remnant", "limit": 3, "format": "json"})
        hits = json.loads(r["result"]["content"][0]["text"])
        print(f"local search ok: {len(hits)} hits")
        try:
            mcp_call(mcp_call_prod_url(), token, "search_memory", {"query": "x"}, timeout=10)
            sys.exit("prod accepted the bench token — abort")
        except urllib.error.HTTPError as e:
            print(f"prod rejects the bench token: HTTP {e.code}")
        run_dir = Path(a.workdir) / "preflight"
        run_dir.mkdir(parents=True, exist_ok=True)
        proc_env, flags = run_config(run_dir, "preflight", "C", token)
        assert_isolated(run_dir, proc_env)
        print("generated config contains no production URL/token")
        print("claude flags:", " ".join(flags))
    finally:
        server.stop()


def mcp_call_prod_url() -> str:
    urls = [v for v in prod_secrets() if v.startswith("http")]
    if not urls:
        sys.exit("no production URL found to verify token rejection")
    return urls[0]


def assert_private_output(path: Path):
    """Results hold model output: never inside the repo unless git-ignored."""
    path = path.resolve()
    if REPO in path.parents and sh("git", "-C", str(REPO), "check-ignore", "-q", str(path), check=False).returncode:
        sys.exit(f"{path} is inside the repository and not ignored: results must stay private")


def cmd_execute(a):
    real = shutil.which(a.claude) == shutil.which("claude")
    if real and not a.confirm_model_calls:
        sys.exit("this runs the real Claude CLI (model calls, usage cost): pass --confirm-model-calls "
                 "once the user has agreed to this phase, or --claude <fake> for harness tests")
    if not shutil.which(a.claude):
        sys.exit(f"{a.claude}: not found or not executable (note: /tmp is mounted noexec here)")
    os.umask(0o077)                                   # run dirs, streams, answers: owner only
    results = Path(a.results)
    assert_private_output(results)
    plan = json.loads(Path(a.plan).read_text())
    sfile = load_scenarios(plan["scenarios"])
    by_id = {s["id"]: s for s in sfile["scenarios"]}
    token = read_token(a)
    rows = [json.loads(l) for l in results.read_text().splitlines()] if results.exists() else []
    done = {(r["scenario"], r["repeat"], r["arm"]) for r in rows}
    if any(r["cost_usd"] is None for r in rows):
        sys.exit("a previous run has unknown cost: the cap cannot be enforced; inspect before continuing")
    spent = sum(r["cost_usd"] for r in rows)
    worst = max([a.max_run_usd] + [r["cost_usd"] for r in rows])
    server = Server(Path(a.workdir) / "server", token, a.python)
    try:
        for spec in plan["runs"]:
            if (spec["scenario"], spec["repeat"], spec["arm"]) in done:
                continue
            # Start a run only if even the worst run seen so far (or the declared
            # per-run maximum) still fits. A single run can still exceed that bound,
            # so the cap can be overshot by at most one run's excess.
            if spent + worst > a.cap_usd:
                print(f"cap: spent ${spent:.2f}, next run may cost up to ${worst:.2f}, cap ${a.cap_usd:.2f}; stopping")
                break
            row = run_one(spec, by_id[spec["scenario"]], sfile, a, server, token, results)
            print(f"{row['run_id']}: harness_valid={row['harness_valid']} cli_ok={row['cli_ok']} "
                  f"accepted={row['accepted']} cost={row['cost_usd']} problems={row['hook_problems']}")
            if row["cost_usd"] is None:
                print("run cost unknown: stopping, the cap cannot be enforced (fail closed)")
                break
            spent += row["cost_usd"]
            worst = max(worst, row["cost_usd"])
    finally:
        server.stop()
    print(f"spent ${spent:.2f} of ${a.cap_usd:.2f}")


EXPORT_RUN = ("run_id", "scenario", "repeat", "arm", "block", "model", "plugin_rev", "repo_commit", "claude_version",
              "harness_valid", "cli_ok", "accepted", "local_mcp_requests", "hook_events", "auto_lookups",
              "auto_injected_chars", "auto_checkpoint_chars", "total_input_tokens", "cost_usd", "cost_usd_last",
              *USAGE_FIELDS)
EXPORT_TURN = ("i", "segment", "first_text_ms", "result_ms", "timeout", "crashed", "is_error", "cost_usd", "cli_duration_ms",
               "num_turns", "permission_denials", "accepted")


def export_rows(rows: list[dict]) -> list[dict]:
    """Numbers and labels only: no prompts, answers, tool inputs or problem strings."""
    out = []
    for r in rows:
        e = {k: r.get(k) for k in EXPORT_RUN}
        e["hook_problem_count"] = len(r.get("hook_problems") or [])
        e["turns"] = [{**{k: t.get(k) for k in EXPORT_TURN},
                       "tool_calls": len(t.get("tool_uses") or []),
                       "memory_searches": sum(1 for n in t.get("tool_uses") or [] if n and n.endswith("search_memory"))}
                      for t in r.get("turns") or []]
        out.append(e)
    return out


def cmd_export(a):
    rows = [json.loads(l) for l in Path(a.results).read_text().splitlines()]
    Path(a.out).write_text(json.dumps(export_rows(rows), indent=1))
    print(f"{len(rows)} runs -> {a.out} (numeric/redacted)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workdir", default=os.path.expanduser("~/.cache/remnant-bench"))
    ap.add_argument("--python", default=os.path.expanduser("~/.cache/remnant-test-venv/bin/python"),
                    help="interpreter with the server requirements installed")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("snapshot"); p.add_argument("--out", required=True); p.add_argument("--pod", default="remnant-pg-1")
    p = sub.add_parser("up"); p.add_argument("--dump", required=True)
    sub.add_parser("down")
    sub.add_parser("preflight")
    p = sub.add_parser("schedule"); p.add_argument("--scenarios", required=True); p.add_argument("--repeats", type=int, default=2)
    p.add_argument("--seed", type=int, required=True); p.add_argument("--out", required=True)
    p = sub.add_parser("execute"); p.add_argument("--plan", required=True); p.add_argument("--results", required=True)
    p.add_argument("--cap-usd", type=float, required=True); p.add_argument("--model", required=True)
    p.add_argument("--turn-timeout", type=int, default=600)
    p.add_argument("--claude", default="claude", help="CLI to run (a fake for harness tests)")
    p.add_argument("--confirm-model-calls", action="store_true", help="required to run the real CLI")
    p.add_argument("--max-run-usd", type=float, required=True,
                   help="declared worst-case cost of one run, used until a costlier run is observed")
    p = sub.add_parser("export"); p.add_argument("--results", required=True); p.add_argument("--out", required=True)
    a = ap.parse_args()
    {"snapshot": cmd_snapshot, "up": cmd_up, "down": cmd_down, "preflight": cmd_preflight,
     "schedule": cmd_schedule, "execute": cmd_execute, "export": cmd_export}[a.cmd](a)


if __name__ == "__main__":
    main()
