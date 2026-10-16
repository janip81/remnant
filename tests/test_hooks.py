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
        ["pg-main-rw", "192.168.101.11", "cluster-values.yaml"]
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


def test_review_boundary_cases(tmp_path, monkeypatch):
    h = load_prompt_hook(tmp_path, monkeypatch)
    assert not h.mentions("node-a.example.com is down", "node-a")          # dotted continuation
    assert not h.mentions("see other.main.py", "main.py")                  # dotted prefix
    assert h.mentions("node-a. Then restart", "node-a")                    # sentence full stop
    assert h.mentions("edit /etc/hosts first", "/etc/hosts")               # absolute path matches itself
    assert h.identifiers("check /etc/hosts and https://x.example.se/a?b=1") == \
        ["/etc/hosts", "https://x.example.se/a"]
    assert h.identifiers("ping 192.168.81.20 now") == ["192.168.81.20"]    # IPs kept
    assert h.identifiers("bump to v1.2.6 not 1.35") == ["v1.2.6"]          # bare version dropped
    assert h.identifiers("åäö-config är trasig") == ["åäö-config"]         # Unicode identifiers


def test_cache_lock_serialises_and_times_out(tmp_path, monkeypatch):
    import fcntl
    h = load_prompt_hook(tmp_path, monkeypatch)
    with h.session_lock("s-lock"):
        holder = os.open(h.cache_path("s-lock") + ".lock", os.O_RDWR)
        try:
            fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
            raise AssertionError("second lock should not be granted")
        except BlockingIOError:
            pass
        finally:
            os.close(holder)
    import threading
    started = threading.Event()

    def hold():
        with h.session_lock("s-busy"):
            started.set(); time.sleep(3)
    t = threading.Thread(target=hold); t.start(); started.wait()
    t0 = time.time()
    try:
        with h.session_lock("s-busy"):
            raise AssertionError("should time out")
    except TimeoutError:
        assert time.time() - t0 < 3
    t.join()


def test_search_has_total_deadline(tmp_path, monkeypatch):
    h = load_prompt_hook(tmp_path, monkeypatch)
    monkeypatch.setattr(h, "TIMEOUT", 0.5)

    class Trickle:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def readline(self):
            time.sleep(0.2); return b": keep-alive\n"
    monkeypatch.setattr(h.urllib.request, "urlopen", lambda *a, **k: Trickle())
    t0 = time.time()
    assert h.search("http://x", "t", "q") == []
    assert time.time() - t0 < 1.5


def test_session_start_resume_and_compact_render(tmp_path):
    """Regression: an apostrophe inside ${VAR:+...} in the heredoc made bash abort the
    resume/compact output entirely (no checkpoint, no memories)."""
    import json, subprocess, threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Fake(BaseHTTPRequestHandler):
        def do_POST(self):
            name = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["params"]["name"]
            text = "checkpoint: was fixing pg" if name == "get_checkpoint" else "1. memory hit"
            self.send_response(200)
            self.end_headers()
            self.wfile.write(f"data: {json.dumps({'result': {'content': [{'text': text}]}})}\n\n".encode())

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "XDG_CACHE_HOME": str(tmp_path),
           "REMNANT_URL": f"http://127.0.0.1:{srv.server_port}", "REMNANT_TOKEN": "t"}
    try:
        for source, title in (("resume", "Session Resumed"), ("compact", "Post-Compaction Recovery")):
            r = subprocess.run(["bash", str(HOOK.parent / "on_session_start.sh")], env=env, text=True,
                               capture_output=True, input=json.dumps({"source": source, "session_id": "s1", "cwd": "/x/p"}))
            assert "bad substitution" not in r.stderr
            assert title in r.stdout and "was fixing pg" in r.stdout and "memory hit" in r.stdout
    finally:
        srv.shutdown()
