import sys
from pathlib import Path

import pytest

pytest.importorskip("mem0")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import memory  # noqa: E402


def test_query_terms_keep_identifiers_and_drop_stopwords():
    terms = memory._query_terms("What do we know about capi-cluster 0.2.6 on remnant-mcp.prod.threshold.se?")
    assert terms == ["capi-cluster", "0.2.6", "remnant-mcp.prod.threshold.se"]


def test_query_terms_swedish_stopwords_and_dedup():
    assert memory._query_terms("vad är det för fel på frigate frigate") == ["fel", "frigate"]


def _item(i, score=None):
    return {"id": i, "memory": f"m{i}", "score": score, "metadata": {}}


def _patch(monkeypatch, vector, keyword):
    monkeypatch.setattr(memory, "search", lambda q, limit, **kw: vector)
    monkeypatch.setattr(memory, "keyword_search", lambda q, limit, *a, **kw: keyword)


def test_identifier_query_lets_keyword_hit_win(monkeypatch):
    _patch(monkeypatch, vector=[_item("a", 0.6), _item("b", 0.5)], keyword=[_item("b"), _item("c")])
    out = memory.hybrid_search("pg-main-rw", limit=3)
    assert [r["id"] for r in out][0] == "b"          # found by both sides
    assert out[0]["match"] == "vector+keyword" and out[0]["vector_score"] == 0.5
    assert 0 < out[-1]["score"] < out[0]["score"] <= 1.0


def test_plain_query_prefers_vector_order(monkeypatch):
    _patch(monkeypatch, vector=[_item("a", 0.6), _item("b", 0.5)], keyword=[_item("c"), _item("d")])
    out = memory.hybrid_search("why did the pod restart", limit=4)
    assert [r["id"] for r in out][:2] == ["a", "b"]   # keyword-only hits rank below


def test_vector_outage_falls_back_to_keyword(monkeypatch):
    def down(q, limit, **kw):
        raise ConnectionError("ollama offline")
    monkeypatch.setattr(memory, "search", down)
    monkeypatch.setattr(memory, "keyword_search", lambda q, limit, *a, **kw: [_item("k")])
    out = memory.hybrid_search("morbius.prod.threshold.se")
    assert [r["id"] for r in out] == ["k"] and out[0]["match"] == "keyword"
