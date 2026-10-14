import asyncio
import json
import sys
import types
from pathlib import Path

import pytest

fakeredis = pytest.importorskip("fakeredis")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import queue_worker as qw  # noqa: E402

FAKE = "a1" * 32


class FakeMemory(types.ModuleType):
    """Stands in for src/memory.py: records writes, can fail on demand."""

    def __init__(self):
        super().__init__("memory")
        self.calls, self.written, self.fail_times = [], set(), 0

    def add(self, content, **kw):
        self.calls.append((content, kw))
        if self.fail_times:
            self.fail_times -= 1
            raise ConnectionError("ollama down")
        self.written.add(kw["job_id"])

    def job_written(self, job_id):
        return job_id in self.written


@pytest.fixture
def env(monkeypatch):
    mem = FakeMemory()
    monkeypatch.setitem(sys.modules, "memory", mem)
    client = fakeredis.FakeAsyncRedis(decode_responses=True)
    monkeypatch.setattr(qw, "_client", client)
    monkeypatch.setattr(qw, "ensure_worker", lambda: None)
    monkeypatch.setattr(qw, "CLAIM_IDLE_MS", 0)  # reclaim immediately in tests
    asyncio.run(qw._ensure_group(client))
    return client, mem


def run(coro):
    return asyncio.run(coro)


async def _deliver(client):
    resp = await client.xreadgroup(qw.GROUP, qw._consumer, {qw.STREAM: ">"}, count=10, block=10)
    return [m for _s, msgs in resp or [] for m in msgs]


def test_enqueue_redacts_before_redis(env):
    client, _ = env
    run(qw.enqueue(f"REMNANT_TOKEN={FAKE} note", "codex", False, "session", []))
    (_, fields), = run(client.xrange(qw.STREAM))
    assert FAKE not in fields["job"] and "[REDACTED]" in fields["job"]


def test_success_acks_and_passes_job_id(env):
    client, mem = env
    job_id = run(qw.enqueue("fact", "codex", False, "", []))
    for msg_id, fields in run(_deliver(client)):
        run(qw._process(client, msg_id, fields))
    assert mem.calls[0][1]["job_id"] == job_id
    assert run(qw.queue_metrics())["pending"] == 0


def test_failure_stays_pending_then_retry_succeeds(env):
    client, mem = env
    mem.fail_times = 1
    run(qw.enqueue("fact", "codex", False, "", []))
    (msg_id, fields), = run(_deliver(client))
    run(qw._process(client, msg_id, fields))
    assert run(qw.queue_metrics())["pending"] == 1      # not acked after failure
    for mid, f in run(qw._claim_stale(client)):           # reclaimed after idle
        run(qw._process(client, mid, f))
    assert len(mem.calls) == 2 and run(qw.queue_metrics())["pending"] == 0


def test_crash_after_write_does_not_duplicate(env):
    client, mem = env
    job_id = run(qw.enqueue("fact", "codex", False, "", []))
    mem.written.add(job_id)                                # memory written, then crash before XACK
    (msg_id, fields), = run(_deliver(client))
    run(qw._process(client, msg_id, fields))
    assert mem.calls == [] and run(qw.queue_metrics())["pending"] == 0


def test_dead_letter_after_max_attempts(env, monkeypatch):
    client, mem = env
    monkeypatch.setattr(qw, "MAX_ATTEMPTS", 2)
    mem.fail_times = 99
    run(qw.enqueue("fact", "codex", False, "", []))
    (msg_id, fields), = run(_deliver(client))
    run(qw._process(client, msg_id, fields))
    for mid, f in run(qw._claim_stale(client)):
        run(qw._process(client, mid, f))
    m = run(qw.queue_metrics())
    assert m["dead"] == 1 and m["pending"] == 0
    (_, dead), = run(client.xrange(qw.DEAD))
    assert "ConnectionError" in dead["error"]


def test_legacy_list_jobs_are_migrated_and_redacted(env):
    client, mem = env
    run(client.rpush(qw.LEGACY_LIST, json.dumps({"job_id": "old-1", "content": f"password: {FAKE}", "agent_id": "x"})))
    assert run(qw._migrate_legacy(client)) == 1
    assert run(client.llen(qw.LEGACY_LIST)) == 0
    (msg_id, fields), = run(_deliver(client))
    assert FAKE not in fields["job"]
    run(qw._process(client, msg_id, fields))
    assert mem.calls[0][1]["job_id"] == "old-1"
