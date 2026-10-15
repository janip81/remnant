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


def test_semantic_dedup_keeps_originals_when_merge_not_confirmed(mem, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import dedup_job
    import psycopg2.extras
    _insert("near duplicate fact A about pg-main-rw")
    _insert("near duplicate fact A about pg-main-rw!")
    monkeypatch.setattr(dedup_job, "ollama_merge", lambda texts: "- merged fact")
    monkeypatch.setattr(dedup_job, "store_memory_direct", lambda c, category="": ("queued", "job-never-lands"))
    monkeypatch.setattr(dedup_job, "MERGE_CONFIRM_SECONDS", 1)
    conn = psycopg2.connect(URL)
    psycopg2.extras.register_uuid(conn_or_curs=conn)
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS job_runs (id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                started_at TIMESTAMPTZ NOT NULL DEFAULT now(), finished_at TIMESTAMPTZ, phase TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running', memories_scanned INT DEFAULT 0, memories_changed INT DEFAULT 0,
                rules_added INT DEFAULT 0, duration_seconds FLOAT, error TEXT)""")
        n_before = _count(conn)
        dedup_job.phase2_semantic_dedup(conn, threshold=0.5, dry_run=False)   # all vectors equal -> one cluster
        assert _count(conn) == n_before                                       # nothing deleted
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM mem0_archive WHERE archive_reason = 'semantic-dedup'")
            assert cur.fetchone()[0] == 0
    finally:
        conn.rollback(); conn.close()


def test_confirm_stored_finds_queued_row_by_job_id(mem):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import dedup_job
    _insert("merged", {"job_id": "job-42"})
    conn = psycopg2.connect(URL)
    try:
        assert dedup_job.confirm_stored(conn, ("queued", "job-42"), timeout=1) is not None
        assert dedup_job.confirm_stored(conn, ("queued", "missing"), timeout=1) is None
        assert dedup_job.confirm_stored(conn, None) is None
    finally:
        conn.close()


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
