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


def _agent_id(agent_id: str) -> str:
    return agent_id if agent_id else settings.mem0_agent_id


VALID_CATEGORIES = {"project", "feedback", "reference", "user", "session", "incident", ""}

def add(content: str, agent_id: str = "", infer: Optional[bool] = None, category: str = "", tags: Optional[list] = None) -> dict:
    m = get_memory()
    metadata: dict = {}
    if category:
        metadata["category"] = category
    if tags:
        metadata["tags"] = [t for t in tags if isinstance(t, str) and t.strip()]
    result = m.add(
        content,
        user_id=settings.mem0_user_id,
        agent_id=_agent_id(agent_id),
        infer=settings.mem0_infer if infer is None else infer,
        metadata=metadata if metadata else None,
    )
    return result


def search(query: str, limit: int = 5, agent_id: str = "") -> list[dict]:
    m = get_memory()
    filters = {"user_id": settings.mem0_user_id}
    result = m.search(query, filters=filters, top_k=limit)
    if isinstance(result, dict) and "results" in result:
        return result["results"]
    return result if isinstance(result, list) else []


def get_all(agent_id: str = "") -> list[dict]:
    m = get_memory()
    filters = {"user_id": settings.mem0_user_id}
    if agent_id:
        filters["agent_id"] = agent_id
    result = m.get_all(filters=filters, top_k=10000)
    if isinstance(result, dict) and "results" in result:
        return result["results"]
    return result if isinstance(result, list) else []


def update(memory_id: str, content: str) -> dict:
    m = get_memory()
    return m.update(memory_id, content)


def delete(memory_id: str) -> dict:
    m = get_memory()
    return m.delete(memory_id)
