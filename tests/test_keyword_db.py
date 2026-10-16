"""Keyword search against a real Postgres (pgvector). Skipped unless TEST_DATABASE_URL is set.

  docker run -d --rm --name kw -e POSTGRES_PASSWORD=x -p 127.0.0.1:25434:5432 pgvector/pgvector:pg16
  TEST_DATABASE_URL=postgresql://postgres:x@127.0.0.1:25434/postgres pytest tests/test_keyword_db.py
"""
import os
import sys
import time
from pathlib import Path

import pytest

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")
psycopg2 = pytest.importorskip("psycopg2")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

DOCS = ["alpha morbius.prod.threshold.se note", "beta note", "gamma note", "delta note", "epsilon note",
        "pg-main-rw-lb.postgres.svc refused", "project_worklog repo layout", "eta", "theta", "iota"]


@pytest.fixture
def mem(monkeypatch):
    conn = psycopg2.connect(URL)
    with conn, conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute("DROP TABLE IF EXISTS mem0, mem0_archive")
        cur.execute("CREATE TABLE mem0 (id uuid PRIMARY KEY, vector vector(3), payload jsonb)")
        for d in DOCS:
            cur.execute("INSERT INTO mem0 VALUES (gen_random_uuid(), '[1,0,0]', jsonb_build_object('user_id','u','data',%s::text))", (d,))
    conn.close()
    import memory
    monkeypatch.setattr(memory.settings, "database_url", URL)
    monkeypatch.setattr(memory.settings, "mem0_user_id", "u")
    monkeypatch.setattr(memory, "_FTS_DOC", memory._FTS_EXPR)
    conn = psycopg2.connect(URL)
    with conn, conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS idempotency_keys")
    conn.close()
    memory._ensure_schema()                       # creates idempotency_keys (and tag_rules, job_runs)
    return memory


def texts(rows):
    return [r["memory"] for r in rows]


@pytest.mark.parametrize("q", ["alpha zzzunknown", "zzzunknown alpha"])
def test_unmatched_term_does_not_hide_matches(mem, q):
    assert texts(mem.keyword_search(q, 5)) == [DOCS[0]]


def test_common_term_dropped_when_rare_one_exists(mem):
    out = mem.keyword_search("note morbius.prod.threshold.se", 5)
    assert texts(out) == [DOCS[0]]                       # "note" is in 50% of docs


def test_identifier_substring_match(mem):
    assert texts(mem.keyword_search("pg-main-rw", 5)) == [DOCS[5]]
    assert texts(mem.keyword_search("project_worklog", 5)) == [DOCS[6]]


def test_migration_adds_column_and_is_idempotent(mem):
    assert mem.ensure_fts_index() and mem._FTS_DOC == "fts"
    assert mem.ensure_fts_index()                        # second run skips the ALTER
    assert texts(mem.keyword_search("alpha", 5)) == [DOCS[0]]


def test_migration_gives_up_quickly_when_table_is_locked(mem):
    holder = psycopg2.connect(URL)
    cur = holder.cursor()
    cur.execute("BEGIN; LOCK TABLE mem0 IN ACCESS SHARE MODE")   # e.g. a long dedup transaction
    try:
        t = time.time()
        assert mem.ensure_fts_index() is False
        assert time.time() - t < 10 and mem._FTS_DOC == mem._FTS_EXPR
        assert texts(mem.keyword_search("alpha", 5)) == [DOCS[0]]  # still works via expression
    finally:
        holder.rollback(); holder.close()


def test_existing_column_missing_index_is_recreated(mem):
    assert mem.ensure_fts_index()
    conn = psycopg2.connect(URL)
    with conn, conn.cursor() as cur:
        cur.execute("DROP INDEX mem0_fts_idx")
    conn.close()
    assert mem.ensure_fts_index()
    conn = psycopg2.connect(URL)
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_indexes WHERE indexname = 'mem0_fts_idx'")
        assert cur.fetchone()
    conn.close()


# --- Session checkpoints ---------------------------------------------------

SNAP = "## Session State (session-end)\n\nalpha morbius.prod.threshold.se worked on remnant"


class FakeMem0ForDB:
    """mem0 Memory stand-in that writes rows the way mem0 2.0.2 does (top-level metadata keys)."""

    class embedding_model:
        @staticmethod
        def embed(text, kind):
            return [1, 0, 0]

    def add(self, content, user_id, agent_id, infer, metadata):
        import json, uuid
        mid = str(uuid.uuid4())
        payload = {"data": content, "user_id": user_id, "agent_id": agent_id,
                   "created_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()), **metadata}
        conn = psycopg2.connect(URL)
        with conn, conn.cursor() as cur:
            cur.execute("INSERT INTO mem0 VALUES (%s, '[1,0,0]', %s)", (mid, json.dumps(payload)))
        conn.close()
        time.sleep(0.01)
        return {"results": [{"id": mid, "event": "ADD"}]}


def _insert(data, extra=None):
    import json, uuid
    conn = psycopg2.connect(URL)
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO mem0 VALUES (%s, '[1,0,0]', %s)",
                    (str(uuid.uuid4()), json.dumps({"user_id": "u", "data": data, **(extra or {})})))
    conn.close()


def test_snapshots_excluded_from_keyword_search_unless_asked(mem):
    _insert(SNAP)
    _insert("## Comprehensive Merged Memory\n\n### What the user was working on\n- morbius.prod.threshold.se")
    _insert("## Codex session state (session-end)\nmorbius.prod.threshold.se checked")
    _insert("checkpoint text about morbius.prod.threshold.se", {"kind": "checkpoint", "session_id": "s1"})
    assert texts(mem.keyword_search("morbius.prod.threshold.se", 5)) == [DOCS[0]]
    assert len(mem.keyword_search("morbius.prod.threshold.se", 5, include_checkpoints=True)) == 5


def test_snapshots_excluded_from_vector_search(mem, monkeypatch):
    monkeypatch.setattr(mem, "get_memory", lambda: FakeMem0ForDB())
    _insert(SNAP)
    out = texts(mem.search("anything", limit=50))
    assert SNAP not in out and len(out) == len(DOCS)
    assert SNAP in texts(mem.search("anything", limit=50, include_checkpoints=True))


def test_checkpoint_is_replaced_per_session_and_read_back_by_id(mem, monkeypatch):
    monkeypatch.setattr(mem, "get_memory", lambda: FakeMem0ForDB())
    mem.save_checkpoint("sess-1", "first state", project="remnant")
    r = mem.save_checkpoint("sess-1", "second state, REMNANT_TOKEN=" + "f" * 40, project="remnant")
    mem.save_checkpoint("sess-2", "other session")
    assert r["removed"] == 1
    cp = mem.get_checkpoint("sess-1")
    assert cp["memory"].startswith("second state") and "f" * 40 not in cp["memory"]
    assert cp["project"] == "remnant"
    assert mem.get_checkpoint("nope") is None
    conn = psycopg2.connect(URL)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM mem0 WHERE payload->>'kind' = 'checkpoint'")
        assert cur.fetchone()[0] == 2                     # one per session
    conn.close()


def test_dedup_expiry_archives_old_snapshots_and_expired_checkpoints(mem, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import dedup_job
    monkeypatch.setattr(dedup_job, "DATABASE_URL", URL, raising=False)
    _insert(SNAP, {"created_at": "2026-01-01T00:00:00+00:00"})                       # legacy, old
    _insert(SNAP, {"created_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())})  # legacy, fresh
    _insert("cp old", {"kind": "checkpoint", "session_id": "a", "expires_at": "2026-01-01T00:00:00+00:00"})
    _insert("cp live", {"kind": "checkpoint", "session_id": "b", "expires_at": "2999-01-01T00:00:00+00:00"})
    merged = "## Comprehensive Merged Memory\n\n### What the user was working on\n- durable fact"
    _insert(merged, {"created_at": "2026-01-01T00:00:00+00:00"})               # old LLM-merged row
    import psycopg2.extras
    conn = psycopg2.connect(URL)
    psycopg2.extras.register_uuid(conn_or_curs=conn)   # as dedup_job.get_conn() does
    with conn, conn.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS job_runs (id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(), finished_at TIMESTAMPTZ, phase TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'running', memories_scanned INT DEFAULT 0, memories_changed INT DEFAULT 0,
            rules_added INT DEFAULT 0, duration_seconds FLOAT, error TEXT)""")
    try:
        _check_expiry(dedup_job, conn, merged)
    finally:
        conn.rollback(); conn.close()


def _check_expiry(dedup_job, conn, merged):
    assert dedup_job.phase_expire_checkpoints(conn, dry_run=False) == 2
    with conn.cursor() as cur:
        cur.execute("SELECT payload->>'data' FROM mem0 WHERE " + dedup_job.CHECKPOINT_SQL.replace("%%", "%"))
        assert sorted(r[0][:7] for r in cur.fetchall()) == ["## Comp", "## Sess", "cp live"]
        cur.execute("SELECT count(*) FROM mem0_archive WHERE archive_reason = 'checkpoint-expired'")
        assert cur.fetchone()[0] == 2
        cur.execute("SELECT count(*) FROM mem0 WHERE payload->>'data' = %s", (merged,))
        assert cur.fetchone()[0] == 1                     # hidden from search, never expired


def test_stale_retry_does_not_replace_newer_capture(mem, monkeypatch):
    monkeypatch.setattr(mem, "get_memory", lambda: FakeMem0ForDB())
    mem.save_checkpoint("s", "newer", captured_at="2026-10-15T10:05:00+00:00")
    mem.save_checkpoint("s", "older retry", captured_at="2026-10-15T10:00:00+00:00")
    assert mem.get_checkpoint("s")["memory"] == "newer"


def test_concurrent_saves_keep_exactly_the_newest(mem, monkeypatch):
    import threading
    monkeypatch.setattr(mem, "get_memory", lambda: FakeMem0ForDB())
    caps = [f"2026-10-15T10:0{i}:00+00:00" for i in range(6)]
    threads = [threading.Thread(target=mem.save_checkpoint, args=("c", f"state {i}"), kwargs={"captured_at": c})
               for i, c in enumerate(caps)]
    [t.start() for t in threads]; [t.join() for t in threads]
    conn = psycopg2.connect(URL)
    with conn.cursor() as cur:
        cur.execute("SELECT payload->>'data' FROM mem0 WHERE payload->>'session_id' = 'c'")
        assert [r[0] for r in cur.fetchall()] == ["state 5"]
    conn.close()


def test_mixed_timezone_offsets_order_by_real_time(mem, monkeypatch):
    monkeypatch.setattr(mem, "get_memory", lambda: FakeMem0ForDB())
    mem.save_checkpoint("tz", "newer", captured_at="2026-10-15T09:30:00+00:00")      # 09:30 UTC
    mem.save_checkpoint("tz", "older", captured_at="2026-10-15T10:00:00+01:00")      # 09:00 UTC
    assert mem.get_checkpoint("tz")["memory"] == "newer"


def test_same_instant_tie_broken_by_capture_id_not_row_id(mem, monkeypatch):
    monkeypatch.setattr(mem, "get_memory", lambda: FakeMem0ForDB())
    t = "2026-10-15T10:00:00.123456+00:00"
    mem.save_checkpoint("tie", "b-capture", captured_at=t, capture_id="bbbb")
    mem.save_checkpoint("tie", "a-capture", captured_at=t, capture_id="aaaa")
    mem.save_checkpoint("tie", "b-capture retry", captured_at=t, capture_id="bbbb")
    assert mem.get_checkpoint("tie")["memory"].startswith("b-capture")


def _job_runs(conn):
    with conn, conn.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS job_runs (id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(), finished_at TIMESTAMPTZ, phase TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'running', memories_scanned INT DEFAULT 0, memories_changed INT DEFAULT 0,
            rules_added INT DEFAULT 0, duration_seconds FLOAT, error TEXT)""")


def test_semantic_dedup_only_proposes_and_never_deletes(mem, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import dedup_job
    import psycopg2.extras
    calls = []
    monkeypatch.setattr(dedup_job, "ollama_merge", lambda texts: "- merged fact")
    monkeypatch.setattr(dedup_job, "propose_merge_direct",
                        lambda ids, text, key: calls.append((sorted(map(str, ids)), key)) or {"ok": True})
    conn = psycopg2.connect(URL)
    psycopg2.extras.register_uuid(conn_or_curs=conn)
    try:
        _job_runs(conn)
        n_before = _count(conn)
        dedup_job.phase2_semantic_dedup(conn, threshold=0.5, dry_run=False)   # all vectors equal -> one cluster
        assert _count(conn) == n_before and len(calls) == 1                    # proposed, nothing deleted
        _insert("the proposal", {"idempotency_key": calls[0][1], "state": "candidate"})
        dedup_job.phase2_semantic_dedup(conn, threshold=0.5, dry_run=False)
        assert len(calls) == 1                                                 # same cluster not proposed again
    finally:
        conn.rollback(); conn.close()


def test_hash_dedup_supersedes_instead_of_deleting(mem):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import dedup_job
    import psycopg2.extras
    _insert("dup", {"hash": "hx", "created_at": "2026-01-01"})
    _insert("dup", {"hash": "hx", "created_at": "2026-01-02"})
    conn = psycopg2.connect(URL)
    psycopg2.extras.register_uuid(conn_or_curs=conn)
    try:
        _job_runs(conn)
        dedup_job.phase1_hash_dedup(conn, dry_run=False)
        with conn.cursor() as cur:
            cur.execute("SELECT coalesce(payload->>'state','active'), payload->>'superseded_by' IS NOT NULL "
                        "FROM mem0 WHERE payload->>'hash' = 'hx' ORDER BY payload->>'created_at'")
            assert cur.fetchall() == [("active", False), ("superseded", True)]
    finally:
        conn.rollback(); conn.close()


def _count(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM mem0")
        n = cur.fetchone()[0]
    conn.commit()
    return n


# --- Lifecycle slice 1 -------------------------------------------------------

def test_candidates_and_superseded_excluded_by_default(mem, monkeypatch):
    monkeypatch.setattr(mem, "get_memory", lambda: FakeMem0ForDB())
    _insert("cand morbius.prod.threshold.se", {"state": "candidate"})
    _insert("old morbius.prod.threshold.se", {"state": "superseded"})
    assert texts(mem.keyword_search("morbius.prod.threshold.se", 5)) == [DOCS[0]]
    got = texts(mem.keyword_search("morbius.prod.threshold.se", 5, states=("active", "superseded")))
    assert sorted(got) == sorted([DOCS[0], "old morbius.prod.threshold.se"])
    vec = texts(mem.search("x", limit=50))
    assert "cand morbius.prod.threshold.se" not in vec and "old morbius.prod.threshold.se" not in vec
    assert "cand morbius.prod.threshold.se" in texts(mem.search("x", limit=50, states=("active", "candidate")))


def test_review_promote_reject_conflict_notfound(mem):
    import uuid
    cid = str(uuid.uuid4())
    conn = psycopg2.connect(URL)
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO mem0 VALUES (%s, '[1,0,0]', %s)",
                    (cid, json_dumps({"user_id": "u", "data": "llm chatter", "state": "candidate"})))
    conn.close()
    assert mem.review(cid, "promote", "jani", "looks right") == {"ok": True, "id": cid, "state": "active"}
    again = mem.review(cid, "reject")
    assert again["ok"] is False and again["error"] == "conflict" and again["state"] == "active"
    assert mem.review(str(uuid.uuid4()), "promote")["error"] == "not found"
    assert mem.review(cid, "delete")["ok"] is False
    conn = psycopg2.connect(URL)
    with conn.cursor() as cur:
        cur.execute("SELECT payload->>'reviewed_by', payload->>'review_note' FROM mem0 WHERE id = %s", (cid,))
        assert cur.fetchone() == ("jani", "looks right")
    conn.close()


def test_expired_checkpoint_not_restored(mem):
    _insert("cp gone", {"kind": "checkpoint", "session_id": "exp", "expires_at": "2026-01-01T00:00:00+00:00",
                        "captured_at": "2026-01-01T00:00:00+00:00"})
    assert mem.get_checkpoint("exp") is None


def test_dedup_ignores_candidates(mem):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import dedup_job
    import psycopg2.extras
    _insert("same text", {"hash": "h1", "created_at": "2026-01-01"})
    _insert("same text", {"hash": "h1", "created_at": "2026-01-02", "state": "candidate"})
    conn = psycopg2.connect(URL)
    psycopg2.extras.register_uuid(conn_or_curs=conn)
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS job_runs (id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                started_at TIMESTAMPTZ NOT NULL DEFAULT now(), finished_at TIMESTAMPTZ, phase TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running', memories_scanned INT DEFAULT 0, memories_changed INT DEFAULT 0,
                rules_added INT DEFAULT 0, duration_seconds FLOAT, error TEXT)""")
        dedup_job.phase1_hash_dedup(conn, dry_run=False)
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM mem0 WHERE payload->>'hash' = 'h1'")
            assert cur.fetchone()[0] == 2          # the candidate is not deduplicated away
    finally:
        conn.rollback(); conn.close()


def json_dumps(o):
    import json
    return json.dumps(o)


def test_list_rows_default_eligibility_for_mcp(mem):
    _insert("cand row", {"state": "candidate"})
    _insert("cp row", {"kind": "checkpoint", "session_id": "z"})
    _insert("## Session State (session-end)\nold snapshot")
    everything = texts(mem.list_rows())
    assert "cand row" in everything and "cp row" in everything               # UI inventory
    eligible = texts(mem.list_rows("", "", ["active"], False))
    assert "cand row" not in eligible and "cp row" not in eligible
    assert not any(t.startswith("## Session State") for t in eligible)
    assert len(eligible) == len(DOCS)


def test_tag_update_does_not_undo_concurrent_review(mem, monkeypatch):
    import threading, uuid
    cid = str(uuid.uuid4())
    _insert_with_id(cid, {"data": "chatter", "state": "candidate"})
    real_conn = mem._db_conn
    gate = threading.Event(); release = threading.Event()

    class SlowConn:                       # pause update_tags after its SELECT ... FOR UPDATE
        def __init__(self): self.c = real_conn()
        def __getattr__(self, n): return getattr(self.c, n)
        def __enter__(self): return self.c.__enter__()
        def __exit__(self, *a): return self.c.__exit__(*a)
        def cursor(self, *a, **k):
            return SlowCursor(self.c.cursor(*a, **k))

    class SlowCursor:
        def __init__(self, cur): self.cur = cur
        def __getattr__(self, n): return getattr(self.cur, n)
        def __enter__(self): self.cur.__enter__(); return self
        def __exit__(self, *a): return self.cur.__exit__(*a)
        def execute(self, sql, params=None):
            r = self.cur.execute(sql, params)
            if "FOR UPDATE" in sql:
                gate.set(); release.wait(5)
            return r
    monkeypatch.setattr(mem, "_db_conn", lambda: SlowConn())
    t = threading.Thread(target=mem.update_tags, args=(cid, ["x"])); t.start(); gate.wait(5)
    monkeypatch.setattr(mem, "_db_conn", real_conn)
    out = {}
    r = threading.Thread(target=lambda: out.update(mem.review(cid, "promote"))); r.start()
    time.sleep(0.5)
    assert r.is_alive()                   # review waits for the tag update's row lock
    release.set(); t.join(5); r.join(5)
    assert out["ok"] is True
    conn = psycopg2.connect(URL)
    with conn.cursor() as cur:
        cur.execute("SELECT payload->>'state', payload->>'reviewed_at' IS NOT NULL FROM mem0 WHERE id = %s", (cid,))
        assert cur.fetchone() == ("active", True)        # review not undone by the tag write
    conn.close()


def _insert_with_id(mid, payload):
    conn = psycopg2.connect(URL)
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO mem0 VALUES (%s, '[1,0,0]', %s)", (mid, json_dumps({"user_id": "u", **payload})))
    conn.close()


def test_keyword_hits_carry_provenance(mem):
    _insert("prov row zeta-9", {"source": "hook", "project": "remnant", "review_reason": ""})
    (hit,) = mem.keyword_search("zeta-9", 5)
    assert hit["source"] == "hook" and hit["project"] == "remnant"


def test_checkpoint_has_source(mem, monkeypatch):
    monkeypatch.setattr(mem, "get_memory", lambda: FakeMem0ForDB())
    mem.save_checkpoint("src-s", "state")
    conn = psycopg2.connect(URL)
    with conn.cursor() as cur:
        cur.execute("SELECT payload->>'source' FROM mem0 WHERE payload->>'session_id' = 'src-s'")
        assert cur.fetchone()[0] == "hook"
    conn.close()


# --- Lifecycle slice 2: supersede ----------------------------------------------

def _ids(conn_rows):
    return [str(r[0]) for r in conn_rows]


def _row(mid):
    conn = psycopg2.connect(URL)
    with conn.cursor() as cur:
        cur.execute("SELECT payload FROM mem0 WHERE id::text = %s", (mid,))
        r = cur.fetchone()
    conn.close()
    return r[0] if r else None


def _new_active(mem, text):
    import uuid
    mid = str(uuid.uuid4())
    _insert_with_id(mid, {"data": text})
    return mid


def test_supersede_keeps_old_linked_and_hidden(mem, monkeypatch):
    monkeypatch.setattr(mem, "get_memory", lambda: FakeMem0ForDB())
    old = _new_active(mem, "laptop is XPS 9502 with RTX 2070 qx-laptop")
    r = mem.supersede([old], "laptop is XPS 15 9510 with RTX 3050 Ti qx-laptop", "user corrected the model",
                      expected_hashes={old: mem.content_hash("laptop is XPS 9502 with RTX 2070 qx-laptop")})
    assert r["ok"] and r["state"] == "active"
    o, n = _row(old), _row(r["id"])
    assert o["state"] == "superseded" and o["superseded_by"] == r["id"] and n["supersedes_ids"] == [old]
    assert texts(mem.keyword_search("qx-laptop", 5)) == ["laptop is XPS 15 9510 with RTX 3050 Ti qx-laptop"]
    assert len(mem.keyword_search("qx-laptop", 5, states=("active", "superseded"))) == 2
    assert mem.get_memory_detail(old)["superseded_by"] == r["id"]


def test_supersede_conflicts(mem, monkeypatch):
    monkeypatch.setattr(mem, "get_memory", lambda: FakeMem0ForDB())
    old = _new_active(mem, "v1 text")
    stale = mem.supersede([old], "v2", "edit", expected_hashes={old: mem.content_hash("something else")})
    assert stale["ok"] is False and stale["changed"] == [old] and _row(old).get("state") is None
    ok = mem.supersede([old], "v2", "edit")
    again = mem.supersede([old], "v3", "edit")
    assert ok["ok"] and again["error"] == "conflict" and again["superseded_by"][old] == ok["id"]
    assert mem.supersede([old], "v3", "")["error"] == "reason is required"


def test_supersede_idempotent_retry(mem, monkeypatch):
    monkeypatch.setattr(mem, "get_memory", lambda: FakeMem0ForDB())
    old = _new_active(mem, "retry me")
    a = mem.supersede([old], "new", "fix", idempotency_key="k-1")
    b = mem.supersede([old], "new", "fix", idempotency_key="k-1")
    assert a["ok"] and b["replayed"] and b["id"] == a["id"] and b["state"] == "active"


def test_concurrent_supersede_only_one_wins(mem, monkeypatch):
    import threading
    monkeypatch.setattr(mem, "get_memory", lambda: FakeMem0ForDB())
    old = _new_active(mem, "contested")
    out = []
    ts = [threading.Thread(target=lambda i=i: out.append(mem.supersede([old], f"winner {i}", "race"))) for i in range(4)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sum(1 for r in out if r.get("ok")) == 1
    conn = psycopg2.connect(URL)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM mem0 WHERE payload->'supersedes_ids' ? %s AND payload->>'state' = 'active'", (old,))
        assert cur.fetchone()[0] == 1
    conn.close()


def test_merge_proposal_review(mem, monkeypatch):
    monkeypatch.setattr(mem, "get_memory", lambda: FakeMem0ForDB())
    a, b = _new_active(mem, "fact a"), _new_active(mem, "fact b")
    p = mem.supersede([a, b], "fact a and b", "merge", as_candidate=True)
    assert p["state"] == "candidate" and _row(a).get("state") is None          # originals still active
    assert mem.review(p["id"], "promote")["superseded"] == sorted([a, b])
    assert _row(a)["state"] == "superseded" and _row(b)["superseded_by"] == p["id"]
    c, d = _new_active(mem, "fact c"), _new_active(mem, "fact d")
    q = mem.supersede([c, d], "fact c and d", "merge", as_candidate=True)
    mem.review(q["id"], "reject")
    assert _row(c).get("state") is None and _row(d).get("state") is None       # rejection changes nothing


def test_edit_is_supersede_with_conflict_check(mem, monkeypatch):
    monkeypatch.setattr(mem, "get_memory", lambda: FakeMem0ForDB())
    old = _new_active(mem, "ui text")
    assert mem.update(old, "ui text v2", original="not what is stored")["error"] == "conflict"
    r = mem.update(old, "ui text v2", original="ui text")
    assert r["ok"] and _row(old)["state"] == "superseded" and _row(r["id"])["source"] == "ui"


def test_orphaned_pending_rows_are_expired(mem):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import dedup_job
    import psycopg2.extras
    _insert("orphan", {"state": "pending", "created_at": "2026-01-01T00:00:00+00:00"})
    _insert("fresh pending", {"state": "pending", "created_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())})
    conn = psycopg2.connect(URL)
    psycopg2.extras.register_uuid(conn_or_curs=conn)
    try:
        _job_runs(conn)
        assert dedup_job.phase_expire_checkpoints(conn, dry_run=False) == 1
    finally:
        conn.rollback(); conn.close()



def test_idempotency_key_atomic_and_bound_to_request(mem, monkeypatch):
    import threading
    monkeypatch.setattr(mem, "get_memory", lambda: FakeMem0ForDB())
    a, b = _new_active(mem, "dup one"), _new_active(mem, "dup two")
    out = []
    ts = [threading.Thread(target=lambda: out.append(mem.supersede([a, b], "merged", "merge", idempotency_key="merge:x",
                                                                   as_candidate=True))) for _ in range(4)]
    [t.start() for t in ts]; [t.join() for t in ts]
    conn = psycopg2.connect(URL)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM mem0 WHERE payload->>'idempotency_key' = 'merge:x'")
        assert cur.fetchone()[0] == 1                      # concurrent identical proposals -> one row
    conn.close()
    assert sum(1 for r in out if r.get("ok")) >= 1 and all(r.get("ok") or r.get("retry") for r in out)
    replay = mem.supersede([a, b], "merged", "merge", idempotency_key="merge:x", as_candidate=True)
    assert replay["replayed"] and replay["state"] == "candidate"
    reuse = mem.supersede([a, b], "different text", "merge", idempotency_key="merge:x", as_candidate=True)
    assert reuse["ok"] is False and "different request" in reuse["error"]


def test_edit_keeps_tags_and_project(mem, monkeypatch):
    import uuid
    monkeypatch.setattr(mem, "get_memory", lambda: FakeMem0ForDB())
    old = str(uuid.uuid4())
    _insert_with_id(old, {"data": "tagged", "tags": ["cnpg", "prod-k8s"], "project": "remnant", "category": "project"})
    r = mem.update(old, "tagged v2", original="tagged")
    n = _row(r["id"])
    assert n["tags"] == ["cnpg", "prod-k8s"] and n["project"] == "remnant" and n["category"] == "project"


def test_promotion_refused_when_an_original_changed(mem, monkeypatch):
    monkeypatch.setattr(mem, "get_memory", lambda: FakeMem0ForDB())
    a, b = _new_active(mem, "orig a"), _new_active(mem, "orig b")
    p = mem.supersede([a, b], "merged a+b", "merge", as_candidate=True)
    fix = mem.supersede([a], "corrected a", "user correction")
    r = mem.review(p["id"], "promote")
    assert r["ok"] is False and r["originals"] == {a: "superseded"}
    assert _row(a)["superseded_by"] == fix["id"] and _row(p["id"])["state"] == "candidate"


def test_vanished_replacement_is_not_finalised(mem, monkeypatch):
    class Vanishing(FakeMem0ForDB):
        def add(self, *a, **k):
            r = super().add(*a, **k)
            conn = psycopg2.connect(URL)
            with conn, conn.cursor() as cur:
                cur.execute("DELETE FROM mem0 WHERE id = %s", (r["results"][0]["id"],))
            conn.close()
            return r
    monkeypatch.setattr(mem, "get_memory", lambda: Vanishing())
    old = _new_active(mem, "keep me")
    r = mem.supersede([old], "lost", "x")
    assert r["ok"] is False and "vanished" in r["error"] and _row(old).get("state") is None


def test_hash_dedup_preserves_concurrent_supersede_link(mem, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import dedup_job, uuid
    import psycopg2.extras
    keep, drop = str(uuid.uuid4()), str(uuid.uuid4())
    _insert_with_id(keep, {"data": "dupe", "hash": "hz", "created_at": "2026-01-01"})
    _insert_with_id(drop, {"data": "dupe", "hash": "hz", "created_at": "2026-01-02",
                           "state": "active"})
    conn = psycopg2.connect(URL)
    psycopg2.extras.register_uuid(conn_or_curs=conn)
    try:
        _job_runs(conn)
        # a correction supersedes the keeper after the cluster query would have seen it
        c2 = psycopg2.connect(URL)
        with c2, c2.cursor() as cur:
            cur.execute("UPDATE mem0 SET payload = payload || '{\"state\":\"superseded\",\"superseded_by\":\"elsewhere\"}' WHERE id = %s", (keep,))
        c2.close()
        # keeper is no longer active -> cluster skipped, the duplicate keeps its state
        dedup_job.phase1_hash_dedup(conn, dry_run=False)
        assert _row(keep)["superseded_by"] == "elsewhere" and _row(drop).get("state") == "active"
    finally:
        conn.rollback(); conn.close()


def _age_key(key, seconds=600):
    conn = psycopg2.connect(URL)
    with conn, conn.cursor() as cur:
        cur.execute("UPDATE idempotency_keys SET created_at = now() - make_interval(secs => %s) WHERE key = %s",
                    (seconds, key))
    conn.close()


def test_crash_after_reservation_is_recovered(mem, monkeypatch):
    monkeypatch.setattr(mem, "get_memory", lambda: FakeMem0ForDB())
    old = _new_active(mem, "crash before write")
    rh = mem._request_hash([old], "new", False)
    assert mem._reserve_key("k-crash-1", rh) is None                       # reserved, then the process died
    assert mem.supersede([old], "new", "fix", idempotency_key="k-crash-1")["error"] == "in progress"
    _age_key("k-crash-1")
    r = mem.supersede([old], "new", "fix", idempotency_key="k-crash-1")    # abandoned -> taken over
    assert r["ok"] and r["state"] == "active"


def test_crash_after_finalisation_is_recovered(mem, monkeypatch):
    monkeypatch.setattr(mem, "get_memory", lambda: FakeMem0ForDB())
    old = _new_active(mem, "crash after write")
    first = mem.supersede([old], "new", "fix", idempotency_key="k-crash-2")
    conn = psycopg2.connect(URL)                                          # simulate: result never recorded
    with conn, conn.cursor() as cur:
        cur.execute("UPDATE idempotency_keys SET result = NULL WHERE key = 'k-crash-2'")
    conn.close()
    _age_key("k-crash-2")
    again = mem.supersede([old], "new", "fix", idempotency_key="k-crash-2")
    assert again["ok"] and again["id"] == first["id"] and again["recovered"] and again["replayed"]
