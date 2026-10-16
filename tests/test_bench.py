"""Offline tests for scripts/bench/bench.py: no model calls, no containers."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FAKE = Path(__file__).parent / "fixtures" / "fake_claude.py"
spec = importlib.util.spec_from_file_location("bench", ROOT / "scripts" / "bench" / "bench.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)
sys.path.insert(0, str(ROOT / "plugin" / "hooks"))
import bench_log  # noqa: E402


def segment(tmp_path, monkeypatch, mode="ok", prompts=("first question", "second"), timeout=10):
    monkeypatch.setenv("FAKE_MODE", mode)
    import os
    return bench.run_segment([sys.executable, str(FAKE)], tmp_path, dict(os.environ), list(prompts),
                             tmp_path / "stream.jsonl", timeout)


def test_segment_times_each_turn_from_its_own_message(tmp_path, monkeypatch):
    sid, recs = segment(tmp_path, monkeypatch)
    assert sid == "sess-1" and len(recs) == 2
    for r in recs:
        assert r["first_text_ms"] is not None and r["first_text_ms"] <= r["result_ms"]
        assert r["first_text_ms"] >= 15            # the blank delta before it does not count
        assert r["tool_uses"] == ["mcp__remnant__search_memory"] and not r["timeout"] and not r["crashed"]
    assert recs[1]["result"]["result"] == "Answer to: second"


def test_hang_times_out_even_without_output(tmp_path, monkeypatch):
    import time
    t0 = time.monotonic()
    _, recs = segment(tmp_path, monkeypatch, "hang", timeout=1)
    assert recs[-1]["timeout"] and time.monotonic() - t0 < 10


def test_crash_is_recorded_as_a_failed_turn(tmp_path, monkeypatch):
    _, recs = segment(tmp_path, monkeypatch, "crash")
    assert len(recs) == 2 and recs[1]["crashed"] and recs[1]["result"] is None and recs[1]["exit"] == 3


def turns_from(recs):
    return [{"usage": (r["result"] or {}).get("usage"), "cost_usd": (r["result"] or {}).get("total_cost_usd")}
            for r in recs]


def test_usage_totals_and_unknown_stays_unknown(tmp_path, monkeypatch):
    _, recs = segment(tmp_path, monkeypatch)
    t = bench.run_totals(turns_from(recs))
    assert t["total_input_tokens"] == 2 * 1110 and t["output_tokens"] == 10      # thinking not added again
    assert t["cost_usd"] == pytest.approx(0.02)
    _, recs = segment(tmp_path, monkeypatch, "nocost")
    t = bench.run_totals(turns_from(recs))
    assert t["cost_usd"] is None and t["cache_read_input_tokens"] is None and t["total_input_tokens"] is None
    assert t["input_tokens"] == 20                                               # known fields still summed
    assert bench.run_totals([])["cost_usd"] is None


def write_events(path, run_id, events):
    path.write_text("".join(json.dumps({"run_id": run_id, **e}) + "\n" for e in events))


def test_event_check_needs_every_prompt_and_session_start(tmp_path):
    log = tmp_path / "hooks.jsonl"
    segs = [["what about pg-main-rw?", "and then?"], ["after resume"]]
    h = bench_log.prompt_hash
    full = [{"event": "SessionStart", "source": "startup"}, {"event": "SessionStart", "source": "resume"}] + \
           [{"event": "UserPromptSubmit", "prompt_hash": h(p)} for s in segs for p in s]
    write_events(log, "r1", full)
    assert bench.check_events(log, "r1", segs) == []
    assert bench.check_events(log, "other-run", segs)                  # events of other runs don't count
    write_events(log, "r1", full[:1] + full[2:])
    assert bench.check_events(log, "r1", segs) == ["SessionStart resume: expected 1, got 0"]
    write_events(log, "r1", full[:-1])
    assert bench.check_events(log, "r1", segs) == ["turn 2: no UserPromptSubmit event"]
    assert bench.check_events(log, "r1", [["x is fine here", "/compact"]])[-1] == "SessionStart compact: expected 1, got 0"


def scen_file(tmp_path, accept):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"repo": "/r", "repo_commit": "HEAD", "scenarios": [
        {"id": f"s{i}", "category": "identifier", "turns": ["a", "b"], "accept": accept} for i in range(3)]}))
    return str(p)


def test_scenarios_need_objective_checks(tmp_path):
    assert bench.load_scenarios(scen_file(tmp_path, {"1": ["x"]}))
    for bad in ({}, {"5": ["x"]}, {"0": []}):
        with pytest.raises(SystemExit):
            bench.load_scenarios(scen_file(tmp_path, bad))


def test_schedule_is_reproducible_and_paired(tmp_path):
    class A:
        scenarios, repeats, seed = scen_file(tmp_path, {"1": ["x"]}), 2, 42
    plans = []
    for _ in range(2):
        A.out = str(tmp_path / f"plan{len(plans)}.json")
        bench.cmd_schedule(A)
        plans.append(json.loads(Path(A.out).read_text())["runs"])
    assert plans[0] == plans[1] and len(plans[0]) == 3 * 2 * 3
    for i in range(0, len(plans[0]), 3):                               # each block = all three arms together
        block = plans[0][i:i + 3]
        assert len({r["block"] for r in block}) == 1 and sorted(r["arm"] for r in block) == ["B", "B0", "C"]
    A.seed, A.out = 43, str(tmp_path / "plan-other.json")
    bench.cmd_schedule(A)
    assert json.loads(Path(A.out).read_text())["runs"] != plans[0]


def test_export_drops_text(tmp_path):
    row = {"run_id": "r", "arm": "C", "cost_usd": 0.1, "hook_problems": ["turn 0: no UserPromptSubmit event"],
           "turns": [{"i": 0, "prompt_hash": "abc", "tool_uses": ["Read", "mcp__remnant__search_memory"],
                      "usage": {"input_tokens": 1}, "accepted": True, "stderr_tail": "secret answer text"}]}
    out = json.dumps(bench.export_rows([row]))
    assert "secret" not in out and "UserPromptSubmit" not in out and "abc" not in out
    e = bench.export_rows([row])[0]
    assert e["hook_problem_count"] == 1 and e["turns"][0]["memory_searches"] == 1 and e["turns"][0]["tool_calls"] == 2


def test_results_must_not_land_in_the_repo_unignored(tmp_path):
    with pytest.raises(SystemExit):
        bench.assert_private_output(ROOT / "results.jsonl")
    bench.assert_private_output(tmp_path / "results.jsonl")
    bench.assert_private_output(ROOT / "docs" / "discussions" / "results.jsonl")    # ignored path is fine


def test_isolation_check_catches_prod_values(tmp_path, monkeypatch):
    monkeypatch.setattr(bench, "prod_secrets", lambda: ["prod-token-value"])
    env, _ = bench.run_config(tmp_path, "r1", "C", "bench-token")
    bench.assert_isolated(tmp_path, env)
    for leak in ("https://remnant-mcp.prod.threshold.se", "prod-token-value"):
        with pytest.raises(SystemExit):
            bench.assert_isolated(tmp_path, {**env, "EXTRA": leak})


def test_cap_fails_closed(tmp_path, monkeypatch):
    sfile = scen_file(tmp_path, {"1": ["x"]})
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"scenarios": sfile, "runs": [
        {"scenario": f"s{i % 3}", "repeat": i // 3, "arm": "C", "block": "b"} for i in range(6)]}))
    costs = iter([0.5, 2.0, None, 0.1])
    ran = []

    def fake_run(spec, *a):
        c = next(costs)
        ran.append(c)
        row = {"scenario": spec["scenario"], "repeat": spec["repeat"], "arm": spec["arm"], "cost_usd": c,
               "run_id": "r", "harness_valid": True, "cli_ok": True, "accepted": True, "hook_problems": []}
        with open(a[-1], "a") as f:
            f.write(json.dumps(row) + "\n")
        return row

    class Srv:
        def __init__(self, *a): pass
        def stop(self): pass
    monkeypatch.setattr(bench, "run_one", fake_run)
    monkeypatch.setattr(bench, "Server", Srv)

    class A:
        claude, confirm_model_calls, plan_ = sys.executable, False, None
        workdir, python, model, turn_timeout = str(tmp_path / "w"), sys.executable, "m", 5
    A.plan, A.results = str(plan), str(tmp_path / "res.jsonl")
    # worst case 1.0 declared: 0.5 runs (1.0 fits in 3), then worst=1 -> 0.5+1 fits -> 2.0 runs,
    # worst=2 -> 2.5+2 > 3 stops before the next run
    A.cap_usd, A.max_run_usd = 3.0, 1.0
    bench.cmd_execute(A)
    assert ran == [0.5, 2.0]
    A.cap_usd = 100.0                                   # more budget: next run has unknown cost -> stop
    bench.cmd_execute(A)
    assert ran == [0.5, 2.0, None]
    with pytest.raises(SystemExit):                     # and a resume refuses while a cost is unknown
        bench.cmd_execute(A)


def test_real_cli_needs_explicit_confirmation(tmp_path, monkeypatch):
    class A:
        claude, confirm_model_calls = "claude", False
    monkeypatch.setattr(bench.shutil, "which", lambda name: "/usr/bin/claude")
    with pytest.raises(SystemExit, match="confirm-model-calls"):
        bench.cmd_execute(A)
