"""Redis Streams queue for add_memory.

add_memory returns immediately; a worker inside the MCP process writes the
memory (Ollama extraction can take ~20s). Delivery is at-least-once:

- enqueue: content is redacted *before* it reaches Redis, then XADD. The
  stream is never trimmed by length; entries are XDELed once acknowledged,
  so the stream only holds unfinished work.
- worker: XREADGROUP; a job is XACKed only after memory.add succeeded.
- crash / pod restart: unacked jobs stay pending and are reclaimed with
  XAUTOCLAIM once idle for CLAIM_IDLE_MS, well above the slowest Ollama call.
  (Pending idle time is not a heartbeat: a single call running longer than
  CLAIM_IDLE_MS while a second pod exists could be picked up twice; with one
  replica this only matters during a rolling update.)
- Ollama down (a desktop GPU host that sleeps): the job is left pending and
  retried every CLAIM_IDLE_MS without using up attempts, however long the
  outage lasts. mem0 swallows LLM extraction errors for infer=True and
  returns [], so an empty result while Ollama is unreachable counts as such.
- other failures count as attempts; after MAX_ATTEMPTS the job moves to the
  dead-letter stream (inspect/replay by hand, see README).
- retry idempotency, infer=False: the job_id is stored in the memory's
  metadata and a retry whose row already exists is acked without writing.
  infer=True: mem0's LLM step compares new facts with existing memories and
  NOOPs duplicates, so a retry is simply re-run.

Content-level duplicates (the same fact sent twice as separate calls) are a
new job each time and are left to the nightly dedup job.
"""
import asyncio
import json
import logging
import os
import socket
import time
import uuid
from typing import Optional

from redact import redact

logger = logging.getLogger(__name__)

STREAM = "remnant:stream:add_memory"
DEAD = "remnant:stream:add_memory:dead"
GROUP = "remnant-workers"
ATTEMPTS = "remnant:stream:add_memory:attempts"   # hash: message id -> attempts
LEGACY_LIST = "remnant:queue:add_memory"          # pre-Streams BLPOP list
LEGACY_STAGING = "remnant:queue:add_memory:migrating"

CLAIM_IDLE_MS = int(os.environ.get("QUEUE_CLAIM_IDLE_SECONDS", "600")) * 1000
MAX_ATTEMPTS = int(os.environ.get("QUEUE_MAX_ATTEMPTS", "5"))
BLOCK_MS = 5000
HEARTBEAT_STALE_S = 60

_client = None
_worker_task: Optional[asyncio.Task] = None
_consumer = os.environ.get("HOSTNAME") or socket.gethostname()
_heartbeat = 0.0          # monotonic time of the last worker loop iteration
_busy = False             # a job is being processed (a single add can take minutes)


class DependencyDown(Exception):
    """Ollama unreachable: retry later without spending an attempt."""


async def _get_client():
    global _client
    if _client is None:
        import redis.asyncio as aioredis
        from config import settings
        _client = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _client


async def enqueue(content: str, agent_id: str, infer: bool, category: str, tags: list, **extra) -> str:
    """extra: kind="checkpoint" with session_id/project routes to save_checkpoint."""
    job_id = str(uuid.uuid4())
    job = {"job_id": job_id, "content": redact(content), "agent_id": agent_id,
           "infer": infer, "category": category, "tags": tags, **extra}
    client = await _get_client()
    await client.xadd(STREAM, {"job": json.dumps(job)})
    ensure_worker()
    return job_id


def ensure_worker() -> None:
    """Start (or restart) the worker task; call from the running event loop."""
    global _worker_task
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(_run_worker())


def worker_alive() -> bool:
    return (_worker_task is not None and not _worker_task.done()
            and (_busy or time.monotonic() - _heartbeat < HEARTBEAT_STALE_S))


async def _ensure_group(client) -> None:
    from redis.exceptions import ResponseError
    try:
        # id="0": a new group also picks up entries added before it existed
        await client.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    except ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


async def _migrate_legacy(client) -> int:
    """Move jobs from the old BLPOP list into the stream without a loss window.

    LMOVE to a staging list is atomic, so a job is always in the list, the
    staging list or the stream. A crash between XADD and LREM can leave a
    duplicate; the job_id makes that retry harmless. Staging leftovers from
    an earlier crash are drained first.
    """
    moved = 0
    while True:
        raw = await client.lindex(LEGACY_STAGING, 0)
        if raw is None:
            raw = await client.lmove(LEGACY_LIST, LEGACY_STAGING, "LEFT", "RIGHT")
            if raw is None:
                return moved
        try:
            job = json.loads(raw)
            if not isinstance(job, dict) or not isinstance(job.get("content"), str):
                raise ValueError("not a job object")
        except ValueError as e:
            logger.error("Unreadable legacy queue item moved to %s: %s", DEAD, e)
            await client.xadd(DEAD, {"job": raw, "error": f"bad legacy item: {e}"[:200], "source_id": "legacy"})
        else:
            job["content"] = redact(job.get("content", ""))
            await client.xadd(STREAM, {"job": json.dumps(job)})
            moved += 1
        await client.lrem(LEGACY_STAGING, 1, raw)


async def _process(client, msg_id: str, fields: dict) -> None:
    global _busy, _heartbeat
    _busy = True
    try:
        await _process_one(client, msg_id, fields)
    finally:
        _busy = False
        _heartbeat = time.monotonic()


async def _process_one(client, msg_id: str, fields: dict) -> None:
    import memory as mem_store
    try:
        job = json.loads(fields["job"])
        if not isinstance(job, dict) or not isinstance(job.get("content"), str):
            raise ValueError("not a job object")
    except Exception:
        await _dead(client, msg_id, fields, "unparseable job")
        return
    job_id = job.get("job_id", "")
    infer = job.get("infer", True)
    if not await asyncio.to_thread(mem_store.ollama_up):
        logger.warning("Ollama unreachable; job %s stays pending (retry in %ds)", job_id, CLAIM_IDLE_MS // 1000)
        return
    attempts = await client.hincrby(ATTEMPTS, msg_id, 1)
    try:
        if job.get("kind") == "checkpoint":
            # replace-by-session is idempotent, so a retry simply writes it again
            # ordered by captured_at, so a late retry never replaces a newer capture
            await asyncio.to_thread(mem_store.save_checkpoint, job.get("session_id", ""), job["content"],
                                    job.get("agent_id", ""), job.get("project", ""),
                                    job.get("captured_at", ""))
            logger.info("Saved checkpoint for session %s (attempt %d)", job.get("session_id"), attempts)
        elif not infer and job_id and await asyncio.to_thread(mem_store.job_written, job_id):
            logger.info("Queue job %s already written; acking retry", job_id)
        else:
            result = await asyncio.to_thread(
                mem_store.add,
                job["content"],
                agent_id=job.get("agent_id", ""),
                infer=infer,
                category=job.get("category", ""),
                tags=job.get("tags", []),
                job_id=job_id,
            )
            if infer and not _has_results(result) and not await asyncio.to_thread(mem_store.ollama_up):
                raise DependencyDown("empty infer result while Ollama is unreachable")
            logger.info("Processed queue job %s (attempt %d)", job_id, attempts)
    except Exception as e:
        if isinstance(e, DependencyDown) or not await asyncio.to_thread(mem_store.ollama_up):
            await client.hincrby(ATTEMPTS, msg_id, -1)  # outage, not this job's fault
            logger.warning("Queue job %s hit an Ollama outage; stays pending", job_id)
            return
        logger.exception("Queue job %s failed (attempt %d/%d)", job_id, attempts, MAX_ATTEMPTS)
        if attempts >= MAX_ATTEMPTS:
            await _dead(client, msg_id, fields, f"{type(e).__name__}: {e}"[:500])
        return  # left pending; reclaimed after CLAIM_IDLE_MS
    await _finish(client, msg_id)


def _has_results(result) -> bool:
    if isinstance(result, dict):
        result = result.get("results", [])
    return bool(result)


async def _finish(client, msg_id: str) -> None:
    await client.xack(STREAM, GROUP, msg_id)
    await client.xdel(STREAM, msg_id)   # keep only unfinished work in the stream
    await client.hdel(ATTEMPTS, msg_id)


async def _dead(client, msg_id: str, fields: dict, error: str) -> None:
    await client.xadd(DEAD, {**fields, "error": error, "source_id": msg_id})
    await _finish(client, msg_id)
    logger.error("Queue message %s moved to %s: %s", msg_id, DEAD, error)


async def _claim_stale(client, cursor: str = "0-0") -> tuple[str, list]:
    """Reclaim jobs pending longer than CLAIM_IDLE_MS; returns (next cursor, messages)."""
    result = await client.xautoclaim(STREAM, GROUP, _consumer, min_idle_time=CLAIM_IDLE_MS,
                                     start_id=cursor, count=10)
    if not result:
        return "0-0", []
    return result[0], result[1]


async def _start(client) -> None:
    """Group + legacy migration, retried until Redis is reachable."""
    delay = 2
    while True:
        try:
            await _ensure_group(client)
            moved = await _migrate_legacy(client)
            logger.info("Queue worker started (stream=%s group=%s consumer=%s, migrated %d legacy jobs)",
                        STREAM, GROUP, _consumer, moved)
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Queue worker cannot start yet; retrying in %ds", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)


async def _run_worker() -> None:
    global _heartbeat
    _heartbeat = time.monotonic()
    client = await _get_client()
    await _start(client)
    loop = asyncio.get_running_loop()
    last_migrate = loop.time()
    cursor = "0-0"
    while True:
        _heartbeat = time.monotonic()
        try:
            # During a rolling update the previous version may still push to the
            # old list; keep draining it (two cheap calls per minute once empty).
            if loop.time() - last_migrate > 60:
                last_migrate = loop.time()
                if moved := await _migrate_legacy(client):
                    logger.info("Migrated %d late legacy jobs", moved)
            cursor, claimed = await _claim_stale(client, cursor)
            for msg_id, fields in claimed:
                if fields:  # deleted entries come back with no fields
                    await _process(client, msg_id, fields)
            resp = await client.xreadgroup(GROUP, _consumer, {STREAM: ">"}, count=1, block=BLOCK_MS)
            for _stream, messages in resp or []:
                for msg_id, fields in messages:
                    await _process(client, msg_id, fields)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Queue worker error, retrying in 2s")
            await asyncio.sleep(2)


async def queue_metrics() -> dict:
    """pending = delivered but not acked, lag = not yet delivered, dead = DLQ size.

    error=1 when the stream/group could not be read (backlog unknown, not zero).
    """
    client = await _get_client()
    pending = lag = 0
    error = 0
    try:
        groups = await client.xinfo_groups(STREAM)
        g = next((g for g in groups if g.get("name") == GROUP), None)
        if g is None:
            error = 1
        else:
            pending = int(g.get("pending") or 0)
            if g.get("lag") is None:   # Redis cannot tell; fall back to the stream length
                lag = max(await client.xlen(STREAM) - pending, 0)
            else:
                lag = int(g["lag"])
    except Exception:
        error = 1
    try:
        dead = await client.xlen(DEAD)
    except Exception:
        dead, error = 0, 1
    return {"pending": pending, "lag": lag, "dead": dead, "depth": pending + lag,
            "worker_up": int(worker_alive()), "error": error}
