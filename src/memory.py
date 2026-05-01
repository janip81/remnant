import json
import logging
import threading
import time
from typing import Optional
import psycopg2
import psycopg2.extras
from mem0 import Memory
from mem0.configs.base import MemoryConfig, EmbedderConfig, LlmConfig, VectorStoreConfig

from config import settings

logger = logging.getLogger(__name__)

_EMBEDDING_DIMS = 768  # nomic-embed-text via Ollama

# Seed rules — used to initialise tag_rules table on first deploy; runtime rules come from DB
_TAG_RULES_SEED: dict[str, list[str]] = {
    "prod-k8s": ["prod-k8s", "prod.k8s"],
    "prod-mgmt": ["prod-mgmt"],
    "dev-k8s": ["dev-k8s"],
    "remnant": ["remnant", "claude-memory"],
    "n8n": ["n8n"],
    "frigate": ["frigate"],
    "nextcloud": ["nextcloud"],
    "ghost": ["ghost blog", "ghost cms"],
    "matrix": ["matrix", "synapse", "element"],
    "homeassistant": ["home assistant", "homeassistant", "ha-mcp", "hass"],
    "minecraft": ["minecraft"],
    "valheim": ["valheim"],
    "firefly": ["firefly"],
    "zigbee": ["zigbee", "zigbee2mqtt", "z2m"],
    "velero": ["velero"],
    "argocd": ["argocd", "argo cd"],
    "keycloak": ["keycloak"],
    "backup-operator": ["backup-operator", "backupoperator"],
    "cnpg": ["cnpg", "cloudnativepg", "postgresql", "postgres"],
    "cilium": ["cilium"],
    "cert-manager": ["cert-manager", "certmanager"],
    "capi": ["capi", "cluster api", "clusterapi"],
    "upgrade": ["upgrade", "upgrading", "k8s upgrade", "kubernetes upgrade"],
    "backup": ["backup", "velero", "restore"],
    "etcd": ["etcd"],
    "bgp": ["bgp", "bird", "bgppeers"],
    "networking": ["networking", "network policy", "loadbalancer"],
    "monitoring": ["prometheus", "grafana", "alertmanager", "monitoring", "servicemonitor"],
    "helm": ["helm", "helmchart", "helm chart", "helmrelease"],
    "storage": ["storage", "pvc", "persistentvolume", "nfs", "longhorn", "rook"],
    "auth": ["auth", "keycloak", "oauth", "oidc", "sso"],
    "opnsense": ["opnsense", "firewall"],
    "hpe": ["hpe", "nimble", "simplivity"],
    "worklog": ["worklog", "work log"],
    "cycletrack": ["cycletrack", "cycle track"],
    "gameledger": ["gameledger", "game ledger"],
    "castlist": ["castlist", "cast list"],
    "burst-power": ["burst-power", "burst power"],
    "version-checker": ["version-checker", "versionchecker"],
    "ansible": ["ansible"],
    "blog": ["blog", "ghost"],
}

# ---------------------------------------------------------------------------
# Tag rules — DB-backed with in-memory cache
# ---------------------------------------------------------------------------

_cache: dict = {"rules": {}, "time": 0.0}
_cache_lock = threading.Lock()
_RULES_TTL = 300.0  # refresh every 5 minutes


def _db_conn() -> psycopg2.extensions.connection:
    return psycopg2.connect(settings.database_url)


def _ensure_schema() -> None:
    """Create tag_rules and job_runs tables; seed rules if empty. Idempotent."""
    try:
        conn = _db_conn()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS tag_rules (
                            tag     TEXT NOT NULL,
                            keyword TEXT NOT NULL,
                            source  TEXT NOT NULL DEFAULT 'manual',
                            PRIMARY KEY (tag, keyword)
                        )
                    """)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS job_runs (
                            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                            started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
                            finished_at      TIMESTAMPTZ,
                            phase            TEXT NOT NULL,
                            status           TEXT NOT NULL DEFAULT 'running',
                            memories_scanned INT DEFAULT 0,
                            memories_changed INT DEFAULT 0,
                            rules_added      INT DEFAULT 0,
                            duration_seconds FLOAT,
                            error            TEXT
                        )
                    """)
                    cur.execute("SELECT COUNT(*) FROM tag_rules")
                    if cur.fetchone()[0] == 0:
                        for tag, keywords in _TAG_RULES_SEED.items():
                            for kw in keywords:
                                cur.execute(
                                    "INSERT INTO tag_rules (tag, keyword, source) "
                                    "VALUES (%s, %s, 'seed') ON CONFLICT DO NOTHING",
                                    (tag, kw),
                                )
        finally:
            conn.close()
    except Exception as e:
        logger.warning("_ensure_schema failed (non-fatal): %s", e)


def _load_rules_from_db() -> dict[str, list[str]]:
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT tag, keyword FROM tag_rules ORDER BY tag, keyword")
            rows = cur.fetchall()
        rules: dict[str, list[str]] = {}
        for tag, kw in rows:
            rules.setdefault(tag, []).append(kw)
        return rules
    finally:
        conn.close()


def _get_rules() -> dict[str, list[str]]:
    now = time.monotonic()
    with _cache_lock:
        if _cache["rules"] and (now - _cache["time"]) < _RULES_TTL:
            return _cache["rules"]
    try:
        rules = _load_rules_from_db()
    except Exception:
        return _TAG_RULES_SEED  # fallback when DB unreachable
    with _cache_lock:
        _cache["rules"] = rules
        _cache["time"] = now
    return rules


def _invalidate_rules_cache() -> None:
    with _cache_lock:
        _cache["time"] = 0.0


def _auto_tag(text: str) -> list[str]:
    text_lower = text.lower()
    return sorted(
        tag for tag, keywords in _get_rules().items()
        if any(k in text_lower for k in keywords)
    )


def list_tag_rules() -> list[dict]:
    """Return all rules as list of {tag, keyword, source}."""
    conn = _db_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT tag, keyword, source FROM tag_rules ORDER BY tag, keyword")
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def add_tag_rule(tag: str, keyword: str, source: str = "manual") -> bool:
    tag = tag.strip().lower()
    keyword = keyword.strip().lower()
    if not tag or not keyword:
        return False
    conn = _db_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO tag_rules (tag, keyword, source) "
                    "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                    (tag, keyword, source),
                )
        _invalidate_rules_cache()
        return True
    finally:
        conn.close()


def delete_tag_rule(tag: str, keyword: str) -> bool:
    conn = _db_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM tag_rules WHERE tag = %s AND keyword = %s",
                    (tag, keyword),
                )
                deleted = cur.rowcount > 0
        _invalidate_rules_cache()
        return deleted
    finally:
        conn.close()


def list_job_runs(limit: int = 50) -> list[dict]:
    conn = _db_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM job_runs ORDER BY started_at DESC LIMIT %s",
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        conn.close()


def get_job_run(run_id: str) -> Optional[dict]:
    conn = _db_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM job_runs WHERE id = %s", (run_id,))
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception:
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# mem0 client
# ---------------------------------------------------------------------------

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
    auto = _auto_tag(content)
    explicit = [t for t in (tags or []) if isinstance(t, str) and t.strip()]
    merged = sorted(set(auto) | set(explicit))
    if merged:
        metadata["tags"] = merged
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


def update_tags(memory_id: str, tags: list) -> bool:
    """Directly patch the tags field in the DB payload, preserving all other metadata."""
    conn = _db_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT payload FROM mem0 WHERE id = %s", (memory_id,))
                row = cur.fetchone()
                if not row:
                    return False
                payload = row[0]
                meta = payload.get("metadata") or {}
                if isinstance(meta.get("metadata"), dict):
                    meta["metadata"]["tags"] = tags
                else:
                    meta["tags"] = tags
                payload["metadata"] = meta
                cur.execute(
                    "UPDATE mem0 SET payload = %s WHERE id = %s",
                    (json.dumps(payload), memory_id),
                )
        return True
    finally:
        conn.close()


# Run schema setup at import time (non-fatal if DB not yet ready)
_ensure_schema()
