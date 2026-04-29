import json
from typing import Optional
from mem0 import Memory
from mem0.configs.base import MemoryConfig, EmbedderConfig, LlmConfig, VectorStoreConfig

from config import settings

_EMBEDDING_DIMS = 768  # nomic-embed-text via Ollama


def _build_config() -> MemoryConfig:
    return MemoryConfig(
        vector_store=VectorStoreConfig(
            provider="pgvector",
            config={
                "connection_string": settings.database_url,
                "collection_name": "mem0",
                "embedding_model_dims": _EMBEDDING_DIMS,
                "hnsw": True,
            },
        ),
        llm=LlmConfig(
            provider="ollama",
            config={
                "model": settings.ollama_model,
                "ollama_base_url": settings.ollama_base_url,
            },
        ),
        embedder=EmbedderConfig(
            provider="ollama",
            config={
                "model": settings.ollama_embed_model,
                "ollama_base_url": settings.ollama_base_url,
                "embedding_dims": _EMBEDDING_DIMS,
            },
        ),
    )


_mem: Optional[Memory] = None


def get_memory() -> Memory:
    global _mem
    if _mem is None:
        _mem = Memory(config=_build_config())
    return _mem


def add(content: str) -> dict:
    m = get_memory()
    # infer=False: skip LLM extraction, store content directly as-is.
    # Our callers already provide structured facts, not raw conversations.
    result = m.add(content, user_id=settings.mem0_user_id, infer=False)
    return result


def search(query: str, limit: int = 5) -> list[dict]:
    m = get_memory()
    result = m.search(query, filters={"user_id": settings.mem0_user_id}, limit=limit)
    if isinstance(result, dict) and "results" in result:
        return result["results"]
    return result if isinstance(result, list) else []


def get_all() -> list[dict]:
    m = get_memory()
    result = m.get_all(filters={"user_id": settings.mem0_user_id})
    if isinstance(result, dict) and "results" in result:
        return result["results"]
    return result if isinstance(result, list) else []


def delete(memory_id: str) -> dict:
    m = get_memory()
    return m.delete(memory_id)
