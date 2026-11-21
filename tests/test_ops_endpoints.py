"""Unauthenticated operational endpoints (#42): /health stays open; /metrics is open
unless METRICS_TOKEN is set, and METRICS_DETAIL=basic drops per-agent/per-tag series."""
import asyncio
import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

BEARER = "b" * 32
SCRAPE = "m" * 32
STATS = {"total": 3, "first_added": None, "last_added": None, "review": {},
         "by_agent": {"agent-x": 2}, "by_category": {"project": 3}, "by_tag": {"tag-y": 1}}


@pytest.fixture
def main(monkeypatch):
    monkeypatch.setenv("BEARER_TOKEN", BEARER)
    monkeypatch.delenv("REDIS_URL", raising=False)
    import config
    importlib.reload(config)
    mod = importlib.reload(importlib.import_module("main")) if "main" in sys.modules else importlib.import_module("main")
    monkeypatch.setattr(mod.mem_store, "stats", lambda: dict(STATS))
    return mod


def call(main, path, token=None):
    headers = [(b"authorization", f"Bearer {token}".encode())] if token else []
    scope = {"type": "http", "path": path, "method": "GET", "headers": headers, "query_string": b""}
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg):
        sent.append(msg)

    asyncio.run(main.app(scope, receive, send))
    return sent[0]["status"], b"".join(m.get("body", b"") for m in sent[1:]).decode()


def test_health_and_metrics_open_by_default(main):
    assert call(main, "/health")[0] == 200
    status, body = call(main, "/metrics")
    assert status == 200 and 'agent_id="agent-x"' in body and 'tag="tag-y"' in body


def test_metrics_token_required_when_set(main, monkeypatch):
    monkeypatch.setattr(main.settings, "metrics_token", SCRAPE)
    assert call(main, "/metrics")[0] == 401
    assert call(main, "/metrics", BEARER)[0] == 401          # the MCP token is not a scrape token
    assert call(main, "/metrics", SCRAPE)[0] == 200
    assert call(main, "/health")[0] == 200                   # probes stay unauthenticated
    assert call(main, "/api/stats", SCRAPE)[0] == 401        # a scrape token opens nothing else


def test_metrics_basic_detail_drops_agent_and_tag_series(main, monkeypatch):
    monkeypatch.setattr(main.settings, "metrics_detail", "basic")
    status, body = call(main, "/metrics")
    assert status == 200 and 'category="project"' in body
    assert "agent_id=" not in body and "tag=" not in body and "by_agent" not in body


@pytest.mark.parametrize("kwargs", [{"metrics_token": "short"}, {"metrics_token": BEARER},
                                    {"metrics_detail": "verbose"}])
def test_unsafe_metrics_settings_refused(kwargs):
    from config import Settings
    with pytest.raises(SystemExit):
        Settings(_env_file=None, bearer_token=BEARER, **kwargs).require_safe_token()
