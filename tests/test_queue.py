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
        self.up = True                 # Ollama reachable
        self.infer_result = None       # override what add() returns for infer=True

    def add(self, content, **kw):
        self.calls.append((content, kw))
        if self.fail_times:
            self.fail_times -= 1
            raise ValueError("bad job")
        self.written.add(kw["job_id"])
        if kw.get("infer") and self.infer_result is not None:
            return self.infer_result
        return {"results": [{"id": "x", "event": "ADD"}]}

    def job_written(self, job_id):
        return job_id in self.written

    def ollama_up(self):
        return self.up


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


def _reclaim_and_process(client):
    for mid, f in run(qw._claim_stale(client))[1]:
        run(qw._process(client, mid, f))


def test_enqueue_redacts_before_redis(env):
    client, _ = env
    run(qw.enqueue(f"REMNANT_TOKEN={FAKE} note", "codex", False, "session", []))
    (_, fields), = run(client.xrange(qw.STREAM))
    assert FAKE not in fields["job"] and "[REDACTED]" in fields["job"]


def test_success_acks_deletes_and_passes_job_id(env):
    client, mem = env
    job_id = run(qw.enqueue("fact", "codex", False, "", []))
    for msg_id, fields in run(_deliver(client)):
        run(qw._process(client, msg_id, fields))
    assert mem.calls[0][1]["job_id"] == job_id
    assert run(qw.queue_metrics())["pending"] == 0
    assert run(client.xlen(qw.STREAM)) == 0      # acked entries are deleted, never trimmed


def test_failure_stays_pending_then_retry_succeeds(env):
    client, mem = env
    mem.fail_times = 1
    run(qw.enqueue("fact", "codex", False, "", []))
    (msg_id, fields), = run(_deliver(client))
    run(qw._process(client, msg_id, fields))
    assert run(qw.queue_metrics())["pending"] == 1      # not acked after failure
    _reclaim_and_process(client)                         # reclaimed after idle
    assert len(mem.calls) == 2 and run(qw.queue_metrics())["pending"] == 0


def test_crash_after_write_does_not_duplicate(env):
    client, mem = env
    job_id = run(qw.enqueue("fact", "codex", False, "", []))
    mem.written.add(job_id)                              # written, then crash before XACK
    (msg_id, fields), = run(_deliver(client))
    run(qw._process(client, msg_id, fields))
    assert mem.calls == [] and run(qw.queue_metrics())["pending"] == 0


def test_infer_true_retry_is_rerun_not_skipped(env):
    client, mem = env
    job_id = run(qw.enqueue("fact", "codex", True, "", []))
    mem.written.add(job_id)                              # partial write before a crash
    (msg_id, fields), = run(_deliver(client))
    run(qw._process(client, msg_id, fields))
    assert len(mem.calls) == 1                           # mem0 LLM dedup handles repeats


def test_dead_letter_after_max_attempts(env, monkeypatch):
    client, mem = env
    monkeypatch.setattr(qw, "MAX_ATTEMPTS", 2)
    mem.fail_times = 99
    run(qw.enqueue("fact", "codex", False, "", []))
    (msg_id, fields), = run(_deliver(client))
    run(qw._process(client, msg_id, fields))
    _reclaim_and_process(client)
    m = run(qw.queue_metrics())
    assert m["dead"] == 1 and m["pending"] == 0
    (_, dead), = run(client.xrange(qw.DEAD))
    assert "ValueError" in dead["error"]


def test_ollama_down_does_not_spend_attempts(env, monkeypatch):
    client, mem = env
    monkeypatch.setattr(qw, "MAX_ATTEMPTS", 1)
    mem.up = False
    run(qw.enqueue("fact", "codex", False, "", []))
    (msg_id, fields), = run(_deliver(client))
    for _ in range(5):                                   # hours of outage
        _reclaim_and_process(client)
    m = run(qw.queue_metrics())
    assert mem.calls == [] and m["pending"] == 1 and m["dead"] == 0
    mem.up = True                                        # Ollama back
    _reclaim_and_process(client)
    assert len(mem.calls) == 1 and run(qw.queue_metrics())["depth"] == 0


def test_infer_empty_result_during_outage_is_retried(env, monkeypatch):
    client, mem = env
    monkeypatch.setattr(qw, "MAX_ATTEMPTS", 1)
    mem.infer_result = []                                # mem0 swallowed the LLM error
    real_add = mem.add

    def add_then_go_down(content, **kw):
        mem.up = False                                   # Ollama dies during extraction
        return real_add(content, **kw)
    monkeypatch.setattr(mem, "add", add_then_go_down)
    run(qw.enqueue("fact", "codex", True, "", []))
    (msg_id, fields), = run(_deliver(client))
    run(qw._process(client, msg_id, fields))
    m = run(qw.queue_metrics())
    assert m["pending"] == 1 and m["dead"] == 0          # not acked, not dead
    assert run(client.hget(qw.ATTEMPTS, msg_id)) == "0"  # attempt given back


def test_legacy_list_jobs_are_migrated_and_redacted(env):
    client, mem = env
    run(client.rpush(qw.LEGACY_LIST, json.dumps({"job_id": "old-1", "content": f"password: {FAKE}", "agent_id": "x"})))
    assert run(qw._migrate_legacy(client)) == 1
    assert run(client.llen(qw.LEGACY_LIST)) == 0 and run(client.llen(qw.LEGACY_STAGING)) == 0
    (msg_id, fields), = run(_deliver(client))
    assert FAKE not in fields["job"]
    run(qw._process(client, msg_id, fields))
    assert mem.calls[0][1]["job_id"] == "old-1"


def test_legacy_staging_leftover_is_recovered(env):
    client, _ = env
    job = json.dumps({"job_id": "stuck-1", "content": "x", "agent_id": "a", "infer": False})
    run(client.rpush(qw.LEGACY_STAGING, job))           # crash after LMOVE, before XADD
    assert run(qw._migrate_legacy(client)) == 1
    assert run(client.llen(qw.LEGACY_STAGING)) == 0


def test_unparseable_legacy_item_goes_to_dead_letter(env):
    client, _ = env
    run(client.rpush(qw.LEGACY_LIST, "not json"))
    assert run(qw._migrate_legacy(client)) == 0
    assert run(client.xlen(qw.DEAD)) == 1 and run(client.llen(qw.LEGACY_STAGING)) == 0


def test_metrics_report_error_without_group(env):
    client, _ = env
    run(client.delete(qw.STREAM))
    m = run(qw.queue_metrics())
    assert m["error"] == 1 and m["worker_up"] == 0


@pytest.mark.parametrize("raw", ["null", "[1, 2]", '{"job_id": "x", "content": 5}'])
def test_valid_json_but_bad_job_is_quarantined(env, raw):
    client, _ = env
    run(client.rpush(qw.LEGACY_LIST, raw))
    run(client.rpush(qw.LEGACY_LIST, json.dumps({"job_id": "ok-1", "content": "x", "agent_id": "a", "infer": False})))
    assert run(qw._migrate_legacy(client)) == 1          # bad one dead-lettered, good one moved
    assert run(client.xlen(qw.DEAD)) == 1 and run(client.llen(qw.LEGACY_STAGING)) == 0


def test_worker_reported_alive_while_busy(monkeypatch):
    class Running:
        def done(self):
            return False
    monkeypatch.setattr(qw, "_worker_task", Running())
    monkeypatch.setattr(qw, "_heartbeat", 0.0)           # last loop long ago
    monkeypatch.setattr(qw, "_busy", True)               # but a slow add is running
    assert qw.worker_alive()
    monkeypatch.setattr(qw, "_busy", False)
    assert not qw.worker_alive()


def test_checkpoint_job_routes_to_save_checkpoint(env):
    client, mem = env
    saved = []
    mem.save_checkpoint = lambda sid, content, agent, project, captured, cid: saved.append((sid, content, project, captured))
    run(qw.enqueue("state", "claude-starbase", False, "session", [], kind="checkpoint",
                   session_id="s-1", project="remnant", captured_at="2026-10-15T10:00:00+00:00"))
    (msg_id, fields), = run(_deliver(client))
    run(qw._process(client, msg_id, fields))
    assert saved == [("s-1", "state", "remnant", "2026-10-15T10:00:00+00:00")] and mem.calls == []
    assert run(qw.queue_metrics())["pending"] == 0


def test_queue_carries_provenance_and_version(env):
    client, mem = env
    run(qw.enqueue("fact", "codex", False, "", [], source="extraction", session_id="s9", project="remnant"))
    (msg_id, fields), = run(_deliver(client))
    assert json.loads(fields["job"])["v"] == 1
    run(qw._process(client, msg_id, fields))
    kw = mem.calls[0][1]
    assert (kw["source"], kw["session_id"], kw["project"]) == ("extraction", "s9", "remnant")


def test_old_jobs_without_provenance_still_work(env):
    client, mem = env
    run(client.xadd(qw.STREAM, {"job": json.dumps({"job_id": "old", "content": "x", "agent_id": "a", "infer": False})}))
    (msg_id, fields), = run(_deliver(client))
    run(qw._process(client, msg_id, fields))
    assert mem.calls[0][1]["source"] == "agent"


def test_depth_is_stream_length_not_drifting_group_lag(env, monkeypatch):
    # remnant#35: finished jobs are XDELed and Redis' group "lag" drifts (seen stuck at 85
    # with an empty stream). Depth must come from XLEN, which is exact.
    client, _ = env
    real = client.xinfo_groups

    async def drifted(stream):
        groups = await real(stream)
        for g in groups:
            g["lag"] = 85
        return groups

    monkeypatch.setattr(client, "xinfo_groups", drifted)
    m = run(qw.queue_metrics())
    assert (m["depth"], m["lag"], m["pending"], m["error"]) == (0, 0, 0, 0)

    run(qw.enqueue("one", "a", False, "project", []))
    run(qw.enqueue("two", "a", False, "project", []))
    run(_deliver(client))                                # both delivered, not acked
    m = run(qw.queue_metrics())
    assert (m["depth"], m["lag"], m["pending"]) == (2, 0, 2)


def test_heartbeat_not_refreshed_while_reads_keep_failing(monkeypatch):
    # remnant#35: a loop where every XREADGROUP fails must not keep reporting the worker alive.
    reads = []

    class Failing:
        async def xreadgroup(self, *a, **kw):
            reads.append(1)
            raise ConnectionError("redis read failed")

    async def no_claim(client, cursor):
        return cursor, []

    async def get_client():
        return Failing()

    async def noop(*a, **kw):
        return None

    loops = []

    async def stop_after_three(_delay):
        loops.append(1)
        if len(loops) >= 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(qw, "_get_client", get_client)
    monkeypatch.setattr(qw, "_start", noop)
    monkeypatch.setattr(qw, "_claim_stale", no_claim)
    monkeypatch.setattr(qw.asyncio, "sleep", stop_after_three)
    times = iter([100.0] + [200.0 + i for i in range(100)])
    monkeypatch.setattr(qw, "time", types.SimpleNamespace(monotonic=lambda: next(times)))  # not the event loop's clock
    monkeypatch.setattr(qw, "_heartbeat", 0.0)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(qw._run_worker())
    assert len(reads) == 3                               # three loops, each read failed
    assert qw._heartbeat == 100.0                        # only the start, no failed loop counted


def test_lag_counts_unread_jobs_despite_deleted_pending_ids(env):
    # A pending ID whose entry was deleted (until XAUTOCLAIM drops it) must not hide unread work.
    client, _ = env
    run(qw.enqueue("a", "a", False, "project", []))
    (a_id, _f), = run(_deliver(client))
    run(client.xdel(qw.STREAM, a_id))                    # deleted, never acked: ghost in the pending list
    run(qw.enqueue("b", "a", False, "project", []))      # unread
    m = run(qw.queue_metrics())
    assert (m["depth"], m["lag"]) == (1, 1)


def test_finish_acks_and_deletes_together(env):
    client, _ = env
    run(qw.enqueue("a", "a", False, "project", []))
    (a_id, _f), = run(_deliver(client))
    run(client.hset(qw.ATTEMPTS, a_id, 2))
    run(qw._finish(client, a_id))
    m = run(qw.queue_metrics())
    assert (m["depth"], m["pending"], m["lag"]) == (0, 0, 0)
    assert run(client.hget(qw.ATTEMPTS, a_id)) is None
