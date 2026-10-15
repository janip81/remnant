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
