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
        cur.execute("DROP TABLE IF EXISTS mem0")
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
