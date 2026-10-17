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
    out = []
    for r in recs:
        res = r["result"] or {}
        mu = res.get("modelUsage")
        out.append({"usage": res.get("usage"), "cost_usd": res.get("total_cost_usd"),
                    "model_usage_output": sum(v["outputTokens"] for v in mu.values()) if mu else None})
    return out


def test_usage_totals_and_unknown_stays_unknown(tmp_path, monkeypatch):
    _, recs = segment(tmp_path, monkeypatch)
    t = bench.run_totals(turns_from(recs))
    assert t["total_input_tokens"] == 2 * 1110 and t["output_tokens"] == 10      # thinking not added again
    assert t["cost_usd"] == pytest.approx(0.02) and t["cost_semantics"] == "cumulative"   # last value, not 0.01+0.02
    assert t["cost_usd_raw_sum"] == pytest.approx(0.03)
    _, recs = segment(tmp_path, monkeypatch, "nocost")
    t = bench.run_totals(turns_from(recs))
    assert t["cost_usd"] is None and t["cache_read_input_tokens"] is None and t["total_input_tokens"] is None
    assert t["input_tokens"] == 20                                               # known fields still summed
    assert bench.run_totals([])["cost_usd"] is None


def write_events(path, run_id, events):
    path.write_text("".join(json.dumps({"run_id": run_id, **e}) + "\n" for e in events))


def test_event_check_matches_order_multiplicity_policy_and_session(tmp_path):
    log = tmp_path / "hooks.jsonl"
    segs = [["what about pg-main-rw?", "what about pg-main-rw?"], ["after resume"]]   # repeated prompt
    h = bench_log.prompt_hash
    ev = lambda **e: {"policy": "selective", "session_id": "s1", **e}
    full = [ev(event="SessionStart", source="startup"),
            ev(event="UserPromptSubmit", prompt_hash=h(segs[0][0])),
            ev(event="UserPromptSubmit", prompt_hash=h(segs[0][1])),
            ev(event="SessionStart", source="resume"),
            ev(event="UserPromptSubmit", prompt_hash=h("after resume"))]
    check = lambda events, **kw: (write_events(log, "r1", events),
                                  bench.check_events(log, "r1", segs, "selective", {"s1"}, **kw))[1]
    assert check(full) == []
    assert bench.check_events(log, "other-run", segs)                                  # other runs don't count
    # one event can't cover a prompt that was sent twice
    assert check(full[:2] + full[3:]) == ["missing or out of order: UserPromptSubmit " + h(segs[0][0])]
    assert check([full[0], full[3], full[1], full[2], full[4]])                       # reordered
    assert check(full + [full[-1]]) == ["unexpected extra hook events"]
    assert check([{**full[0], "policy": "broad"}] + full[1:]) == ["event with policy 'broad', expected 'selective'"]
    assert check([{**full[0], "session_id": "live"}] + full[1:]) == ["event from a session outside this run"]


def test_compact_turn_needs_compact_start_and_slash_event_is_optional(tmp_path):
    log = tmp_path / "hooks.jsonl"
    h = bench_log.prompt_hash
    segs = [["tell me about the backups please", "/compact", "and now?"]]
    base = [{"event": "SessionStart", "source": "startup"}, {"event": "UserPromptSubmit", "prompt_hash": h(segs[0][0])}]
    tail = [{"event": "SessionStart", "source": "compact"}, {"event": "UserPromptSubmit", "prompt_hash": h("and now?")}]
    for middle in ([], [{"event": "UserPromptSubmit", "prompt_hash": h("/compact")}]):
        write_events(log, "r1", base + middle + tail)
        assert bench.check_events(log, "r1", segs) == []
    write_events(log, "r1", base + tail[1:])
    assert bench.check_events(log, "r1", segs) == ["missing or out of order: SessionStart compact"]


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
        with open(a[-2], "a") as f:           # (..., results, meta)
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
        reserve_usd, effort, cache_regime = 0.0, "medium", "test"
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


def test_project_settings_are_removed_from_the_run_copy(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text('{"hooks": {"Stop": [{"hooks": [{"command": "sentinel"}]}]}}')
    (tmp_path / ".mcp.json").write_text("{}")
    (tmp_path / ".claude" / "commands").mkdir()
    assert bench.sanitize_project(tmp_path) == [".claude/settings.json", ".mcp.json"]
    assert not (tmp_path / ".claude" / "settings.json").exists() and (tmp_path / ".claude" / "commands").exists()


def test_private_destinations(tmp_path):
    d = bench.private_dir(tmp_path / "w")
    assert d.stat().st_mode & 0o777 == 0o700
    open_dir = tmp_path / "open"
    open_dir.mkdir(mode=0o755)
    open_dir.chmod(0o755)
    with pytest.raises(SystemExit):
        bench.private_dir(open_dir)
    f = tmp_path / "res.jsonl"
    f.write_text("")
    f.chmod(0o644)
    with pytest.raises(SystemExit):
        bench.private_file(f)
    with pytest.raises(SystemExit):
        bench.private_dir(ROOT / "bench-work")                     # in the repo and not ignored


def test_cost_is_not_double_counted_across_resume(tmp_path, monkeypatch):
    """Smoke 2026-10-17: after --resume total_cost_usd/modelUsage include the earlier
    segment. Session cost = last cumulative value."""
    _, seg0 = segment(tmp_path, monkeypatch, prompts=("first",))
    monkeypatch.setenv("FAKE_PREV_COST", "0.01")
    monkeypatch.setenv("FAKE_PREV_OUT", "5")
    _, seg1 = segment(tmp_path, monkeypatch, prompts=("after resume",))
    turns = turns_from(seg0 + seg1)
    assert bench.cost_semantics(turns) == ["either", "cumulative"]
    assert bench.run_cost(turns) == (pytest.approx(0.02), "cumulative")


def test_per_turn_costs_are_summed_and_mixed_fails_closed(tmp_path, monkeypatch):
    _, recs = segment(tmp_path, monkeypatch, "per-turn")
    turns = turns_from(recs)
    assert bench.cost_semantics(turns) == ["either", "per-turn"]
    assert bench.run_cost(turns) == (pytest.approx(0.02), "per-turn")
    mixed = [dict(turns[0]), {**turns[1], "model_usage_output": 999}]
    assert bench.run_cost(mixed) == (None, "mixed/unknown")


def test_reject_patterns_fail_contradicting_answers():
    assert bench.accept("uses Recreate because of the RWO volume", ["Recreate"], ["RollingUpdate"])
    assert not bench.accept("Recreate, or actually RollingUpdate", ["Recreate"], ["RollingUpdate"])
