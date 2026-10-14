"""Redis Streams queue for add_memory.

add_memory returns immediately; a worker inside the MCP process writes the
memory (Ollama extraction can take ~20s). Delivery is at-least-once:

- enqueue: content is redacted *before* it reaches Redis, then XADD.
- worker: XREADGROUP; a job is XACKed only after memory.add succeeded.
- crash / pod restart: unacked jobs stay pending and are reclaimed with
  XAUTOCLAIM once idle for CLAIM_IDLE_MS — well above the slowest Ollama
  call, so a job that is still running is never picked up twice.
- retry idempotency: the job_id is stored in the memory's metadata; a retried
  job whose memory already exists is acked without writing again.
- after MAX_ATTEMPTS failures a job moves to the dead-letter stream.

Content-level duplicates (the same fact sent twice as separate calls) are a
new job each time and are left to the nightly dedup job.
"""
import asyncio
import json
import logging
import os
import socket
import uuid
from typing import Optional

from redact import redact

logger = logging.getLogger(__name__)

STREAM = "remnant:stream:add_memory"
DEAD = "remnant:stream:add_memory:dead"
GROUP = "remnant-workers"
ATTEMPTS = "remnant:stream:add_memory:attempts"   # hash: message id -> attempts
LEGACY_LIST = "remnant:queue:add_memory"          # pre-Streams BLPOP list
STREAM_MAXLEN = 10_000                            # approximate trim of acked history

CLAIM_IDLE_MS = int(os.environ.get("QUEUE_CLAIM_IDLE_SECONDS", "600")) * 1000
MAX_ATTEMPTS = int(os.environ.get("QUEUE_MAX_ATTEMPTS", "5"))
BLOCK_MS = 5000

_client = None
_worker_task: Optional[asyncio.Task] = None
_consumer = os.environ.get("HOSTNAME") or socket.gethostname()


async def _get_client():
    global _client
    if _client is None:
        import redis.asyncio as aioredis
        from config import settings
        _client = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _client


async def enqueue(content: str, agent_id: str, infer: bool, category: str, tags: list) -> str:
    job_id = str(uuid.uuid4())
    job = {"job_id": job_id, "content": redact(content), "agent_id": agent_id,
           "infer": infer, "category": category, "tags": tags}
    client = await _get_client()
    await client.xadd(STREAM, {"job": json.dumps(job)}, maxlen=STREAM_MAXLEN, approximate=True)
    ensure_worker()
    return job_id


def ensure_worker() -> None:
    """Start (or restart) the worker task; call from the running event loop."""
    global _worker_task
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(_run_worker())


async def _ensure_group(client) -> None:
    from redis.exceptions import ResponseError
    try:
        # id="0": a new group also picks up entries added before it existed
        await client.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    except ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


async def _migrate_legacy(client) -> int:
    """Move jobs left in the old BLPOP list into the stream (one-time, idempotent)."""
    moved = 0
    while True:
        raw = await client.lpop(LEGACY_LIST)
        if raw is None:
            return moved
        try:
            job = json.loads(raw)
            job["content"] = redact(job.get("content", ""))
            await client.xadd(STREAM, {"job": json.dumps(job)}, maxlen=STREAM_MAXLEN, approximate=True)
            moved += 1
        except Exception:
            logger.exception("Dropping unreadable legacy queue item")


async def _process(client, msg_id: str, fields: dict) -> None:
    import memory as mem_store
    attempts = await client.hincrby(ATTEMPTS, msg_id, 1)
    try:
        job = json.loads(fields["job"])
    except Exception:
        await _dead(client, msg_id, fields, "unparseable job")
        return
    job_id = job.get("job_id", "")
    try:
        if job_id and await asyncio.to_thread(mem_store.job_written, job_id):
            logger.info("Queue job %s already written; acking retry", job_id)
        else:
            await asyncio.to_thread(
                mem_store.add,
                job["content"],
                agent_id=job.get("agent_id", ""),
                infer=job.get("infer", True),
                category=job.get("category", ""),
                tags=job.get("tags", []),
                job_id=job_id,
            )
            logger.info("Processed queue job %s (attempt %d)", job_id, attempts)
    except Exception as e:
        logger.exception("Queue job %s failed (attempt %d/%d)", job_id, attempts, MAX_ATTEMPTS)
        if attempts >= MAX_ATTEMPTS:
            await _dead(client, msg_id, fields, f"{type(e).__name__}: {e}"[:500])
        return  # left pending; reclaimed after CLAIM_IDLE_MS
    await client.xack(STREAM, GROUP, msg_id)
    await client.hdel(ATTEMPTS, msg_id)


async def _dead(client, msg_id: str, fields: dict, error: str) -> None:
    await client.xadd(DEAD, {**fields, "error": error, "source_id": msg_id}, maxlen=STREAM_MAXLEN, approximate=True)
    await client.xack(STREAM, GROUP, msg_id)
    await client.hdel(ATTEMPTS, msg_id)
    logger.error("Queue message %s moved to %s: %s", msg_id, DEAD, error)


async def _claim_stale(client) -> list:
    """Reclaim jobs pending longer than CLAIM_IDLE_MS (crashed or restarted consumer)."""
    result = await client.xautoclaim(STREAM, GROUP, _consumer, min_idle_time=CLAIM_IDLE_MS,
                                     start_id="0-0", count=10)
    return result[1] if result else []


async def _run_worker() -> None:
    client = await _get_client()
    await _ensure_group(client)
    moved = await _migrate_legacy(client)
    logger.info("Queue worker started (stream=%s group=%s consumer=%s, migrated %d legacy jobs)",
                STREAM, GROUP, _consumer, moved)
    while True:
        try:
            for msg_id, fields in await _claim_stale(client):
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
    """pending = delivered but not acked, lag = not yet delivered, dead = DLQ size."""
    client = await _get_client()
    pending = lag = 0
    try:
        for g in await client.xinfo_groups(STREAM):
            if g.get("name") == GROUP:
                pending = int(g.get("pending") or 0)
                lag = int(g.get("lag") or 0)
    except Exception:
        pass  # stream/group not created yet
    dead = await client.xlen(DEAD)
    return {"pending": pending, "lag": lag, "dead": dead, "depth": pending + lag}
