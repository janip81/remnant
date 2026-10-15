import importlib.util
import os
import time
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "plugin" / "hooks" / "on_pre_compact.py"


def load(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    spec = importlib.util.spec_from_file_location("on_pre_compact", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_stop_capture_throttled_per_session(tmp_path, monkeypatch):
    hook = load(tmp_path, monkeypatch)
    assert hook.stop_capture_due("s1", "session-end")            # first Stop captures
    hook.mark_captured("s1")
    assert not hook.stop_capture_due("s1", "session-end")        # next responses skip
    assert hook.stop_capture_due("s2", "session-end")            # other session unaffected
    assert hook.stop_capture_due("s1", "pre-compaction")         # PreCompact always captures
    old = time.time() - hook.STOP_CAPTURE_INTERVAL - 5
    os.utime(os.path.join(hook.STATE_DIR, "last-capture-s1"), (old, old))
    assert hook.stop_capture_due("s1", "session-end")            # due again after the interval


def load_prompt_hook(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    spec = importlib.util.spec_from_file_location("on_user_prompt", HOOK.parent / "on_user_prompt.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_identifiers_from_prompt(tmp_path, monkeypatch):
    h = load_prompt_hook(tmp_path, monkeypatch)
    assert h.identifiers("why is pg-main-rw refusing on 192.168.101.11? see cluster-values.yaml") == \
        ["pg-main-rw", "cluster-values.yaml"]
    assert h.identifiers("all seems green to me now") == []
    assert h.identifiers("upgrade to 1.35 tonight") == []           # bare number is not an identifier


def test_mention_needs_token_boundaries(tmp_path, monkeypatch):
    h = load_prompt_hook(tmp_path, monkeypatch)
    assert h.mentions("restart pg-main-rw now.", "pg-main-rw")
    assert not h.mentions("pg-main-rw-lb.postgres.svc refused", "pg-main-rw")   # longer identifier
    assert h.mentions("see Cluster-Values.yaml", "cluster-values.yaml")


def test_select_applies_gate_budget_cache_and_state(tmp_path, monkeypatch):
    h = load_prompt_hook(tmp_path, monkeypatch)
    monkeypatch.setattr(h, "MAX_CHARS", 60)
    hits = [{"id": "a", "memory": "generic prod-k8s rules"},
            {"id": "b", "memory": "pg-main-rw is the CNPG rw service", "state": "candidate"},
            {"id": "c", "memory": "pg-main-rw failover notes"},
            {"id": "d", "memory": "pg-main-rw " + "x" * 80},                  # over budget
            {"id": "e", "memory": "more pg-main-rw"}]
    assert [x["id"] for x in h.select(hits, ["pg-main-rw"], set())] == ["c", "e"]
    assert [x["id"] for x in h.select(hits, ["pg-main-rw"], {"c"})] == ["e"]   # session cache


def test_seen_cache_roundtrip(tmp_path, monkeypatch):
    h = load_prompt_hook(tmp_path, monkeypatch)
    h.save_seen("s-1", {"x", "y"})
    assert h.load_seen("s-1") == {"x", "y"} and h.load_seen("other") == set()
