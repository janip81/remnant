import asyncio
import json
import logging
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

_QUEUE_KEY = "remnant:queue:add_memory"
_client = None
_worker_task: Optional[asyncio.Task] = None


async def _get_client():
    global _client
    if _client is None:
        import redis.asyncio as aioredis
        from config import settings
        _client = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _client


async def enqueue(content: str, agent_id: str, infer: bool, category: str, tags: list) -> str:
    job_id = str(uuid.uuid4())
    payload = json.dumps({
        "job_id": job_id,
        "content": content,
        "agent_id": agent_id,
        "infer": infer,
        "category": category,
        "tags": tags,
    })
    client = await _get_client()
    await client.rpush(_QUEUE_KEY, payload)
    await _ensure_worker()
    return job_id


async def _ensure_worker():
    global _worker_task
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(_run_worker())


async def _run_worker():
    import memory as mem_store
    client = await _get_client()
    logger.info("Redis add_memory worker started (key=%s)", _QUEUE_KEY)
    while True:
        try:
            result = await client.blpop(_QUEUE_KEY, timeout=5)
            if result is None:
                continue
            _, raw = result
            job = json.loads(raw)
            mem_store.add(
                job["content"],
                agent_id=job.get("agent_id", ""),
                infer=job.get("infer", True),
                category=job.get("category", ""),
                tags=job.get("tags", []),
            )
            logger.info("Processed queue job %s", job.get("job_id", "?"))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Queue worker error, retrying in 1s")
            await asyncio.sleep(1)
