import json
import os
import math
import re
import logging
import threading
import time
from typing import Optional
import psycopg2
import psycopg2.extras
import psycopg2.errors
from mem0 import Memory
from mem0.configs.base import MemoryConfig, EmbedderConfig, LlmConfig, VectorStoreConfig

from config import settings
from redact import redact

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
                    # supersede/propose_merge idempotency: the key is reserved atomically and
                    # bound to a hash of the request, so a retry replays and a reuse fails
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS idempotency_keys (
                            key          TEXT PRIMARY KEY,
                            request_hash TEXT NOT NULL,
                            memory_id    TEXT,
                            result       JSONB,
                            created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
                        )
                    """)
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
# Direct read paths — plain SQL on the mem0 table, no vectors, no Ollama.
# mem0's get_all() selects every embedding (~25 KB per row as Python floats),
# which OOM-killed the pod at 512Mi; it also needs Ollama just to initialise.
# Category/tags follow the same precedence as main._extract_meta: a nested
# payload.metadata object (older rows) wins over top-level keys (newer rows).
# ---------------------------------------------------------------------------

_MEM_TABLE = "mem0"

# Session checkpoints (hook snapshots) are for recovering a session by its id,
# not for semantic search: they mention everything, rank high for any prompt
# and echo the current conversation back. Legacy snapshots have no 'kind'
# but a fixed heading — or, after the nightly LLM merge rewrote the heading
# ('## Comprehensive Merged Memory'), the snapshot's section header.
# '%%' because these fragments go into parameterised SQL.
# coalesce: payload->>'kind' is NULL for normal memories, and NOT (NULL OR ...)
# would be NULL, i.e. exclude every normal row.
# Codex writes its own snapshots ('## Codex session state') as checkpoints via
# save_checkpoint since 2026-10-15; its legacy add_memory snapshots are excluded too.
_CHECKPOINT = ("(coalesce(payload->>'kind', '') = 'checkpoint' "
               "OR coalesce(payload->>'data', '') LIKE '## Session State%%' "
               "OR coalesce(payload->>'data', '') LIKE '## Codex session state%%' "
               "OR coalesce(payload->>'data', '') LIKE '%%### What the user was working on%%')")
_SEARCHABLE = f"NOT {_CHECKPOINT}"

# Lifecycle. A missing state means active (all rows written before 2026-10-15).
#   active      normal memory
#   candidate   unreviewed LLM output — never auto-injected; only via explicit filters
#   superseded  replaced by a newer memory (kept for history)
#   rejected    candidate rejected in review
# Checkpoints are orthogonal (kind=checkpoint) and handled by _SEARCHABLE.
VALID_STATES = ("active", "candidate", "superseded", "rejected", "pending")
# pending: a replacement written by supersede() but not yet finalised — never
# searchable; a crash leaves it orphaned and the nightly job archives it.
VALID_SOURCES = ("agent", "hook", "extraction", "merge", "import", "ui")


def _state_sql(states) -> str:
    """SQL condition for the allowed states (values come from VALID_STATES only)."""
    allowed = [st for st in states if st in VALID_STATES] or ["active"]
    return "coalesce(payload->>'state', 'active') IN (" + ", ".join(f"'{st}'" for st in allowed) + ")"


def _population(include_checkpoints: bool = False, states=("active",)) -> str:
    """The one eligibility rule shared by vector search, keyword search (incl. idf
    statistics) and the hooks; dedup uses the same definition in dedup_job.py."""
    parts = [_state_sql(states)]
    if not include_checkpoints:
        parts.append(_SEARCHABLE)
    return " AND ".join(parts)
CHECKPOINT_TTL_DAYS = int(os.environ.get("CHECKPOINT_TTL_DAYS", "7"))
_META = ("(CASE WHEN jsonb_typeof(payload->'metadata') = 'object' "
         "THEN payload->'metadata' ELSE payload END)")


def _row_to_result(row: dict) -> dict:
    """Shape a SQL row like a mem0 get_all() item, so callers need no changes."""
    meta = {"category": row["category"] or "", "tags": row["tags"] or []}
    return {
        "id": str(row["id"]),
        "memory": row["memory"] or "",
        "agent_id": row["agent_id"] or "",
        "created_at": row["created_at"] or "",
        "updated_at": row["updated_at"] or "",
        "metadata": meta,
        # lifecycle: 'candidate' rows (unreviewed LLM output) are never auto-injected
        "state": row.get("state") or "active",
        "source": row.get("source") or "",
        "review_reason": row.get("review_reason") or "",
        "project": row.get("project") or "",
        "kind": row.get("kind") or "",
        "superseded_by": row.get("superseded_by") or "",
    }


def list_rows(agent_id: str = "", state: str = "", states=None, include_checkpoints: bool = True) -> list[dict]:
    """All memories for the configured user, without vectors.

    states=None and include_checkpoints=True list everything (UI/export inventory);
    MCP get_all_memories passes the default eligibility instead."""
    sql = (f"SELECT id, payload->>'data' AS memory, payload->>'agent_id' AS agent_id, "
           f"payload->>'created_at' AS created_at, payload->>'updated_at' AS updated_at, "
           f"{_META}->>'category' AS category, "
           f"CASE WHEN jsonb_typeof({_META}->'tags') = 'array' THEN {_META}->'tags' END AS tags, "
           f"payload->>'state' AS state, payload->>'source' AS source, "
           f"payload->>'review_reason' AS review_reason, payload->>'project' AS project, "
                f"payload->>'superseded_by' AS superseded_by, "
           f"payload->>'kind' AS kind "
           f"FROM {_MEM_TABLE} WHERE payload->>'user_id' = %s")
    params: list = [settings.mem0_user_id]
    if agent_id:
        sql += " AND payload->>'agent_id' = %s"
        params.append(agent_id)
    if state in VALID_STATES:
        sql += " AND " + _state_sql([state])
    elif states is not None:
        sql += " AND " + _state_sql(states)
    if not include_checkpoints:
        sql += " AND " + _SEARCHABLE          # '%%' is right: this query binds parameters
    conn = _db_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [_row_to_result(r) for r in cur.fetchall()]
    except psycopg2.errors.UndefinedTable:
        return []  # fresh install, nothing written yet
    finally:
        conn.close()


def stats() -> dict:
    """Aggregate counts in SQL — same shape as the old in-Python /api/stats."""
    where = "WHERE payload->>'user_id' = %s"
    p = (settings.mem0_user_id,)
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*), min(nullif(payload->>'created_at', '')), max(nullif(payload->>'created_at', '')) "
                        f"FROM {_MEM_TABLE} {where}", p)
            total, first, last = cur.fetchone()
            cur.execute(f"SELECT coalesce(nullif(payload->>'agent_id', ''), 'unknown'), count(*) "
                        f"FROM {_MEM_TABLE} {where} GROUP BY 1", p)
            by_agent = dict(cur.fetchall())
            cur.execute(f"SELECT coalesce(nullif({_META}->>'category', ''), 'uncategorized'), count(*) "
                        f"FROM {_MEM_TABLE} {where} GROUP BY 1", p)
            by_category = dict(cur.fetchall())
            cur.execute(f"SELECT t, count(*) FROM {_MEM_TABLE}, "
                        f"jsonb_array_elements_text(CASE WHEN jsonb_typeof({_META}->'tags') = 'array' "
                        f"THEN {_META}->'tags' ELSE '[]'::jsonb END) AS t {where} GROUP BY 1", p)
            by_tag = dict(cur.fetchall())
    except psycopg2.errors.UndefinedTable:
        return {"total": 0, "first_added": None, "last_added": None,
                "by_agent": {}, "by_category": {}, "by_tag": {}}
    finally:
        conn.close()
    return {"total": total, "first_added": first, "last_added": last,
            "by_agent": by_agent, "by_category": by_category, "by_tag": by_tag}


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
_mem_lock = threading.Lock()


# nomic-embed-text runs with Ollama's default 2048-token context; longer input
# fails with "the input length exceeds the context length" and the write is
# lost (a 6105-char memory was dead-lettered on 2026-10-15). The full text is
# still stored — only the embedding input is capped, so a long memory is found
# by its first part. ~3 chars/token for code-heavy text keeps 4000 chars safe.
MAX_EMBED_CHARS = int(os.environ.get("MAX_EMBED_CHARS", "4000"))


def _cap_embedding_input(embedder) -> None:
    original = embedder.embed

    def embed(text, memory_action=None, *args, **kwargs):
        if isinstance(text, str) and len(text) > MAX_EMBED_CHARS:
            text = text[:MAX_EMBED_CHARS]
        return original(text, memory_action, *args, **kwargs)

    embedder.embed = embed
    if hasattr(embedder, "embed_batch"):          # used by mem0's infer=True pipeline
        original_batch = embedder.embed_batch

        def embed_batch(texts, memory_action=None, *args, **kwargs):
            texts = [t[:MAX_EMBED_CHARS] if isinstance(t, str) else t for t in texts]
            return original_batch(texts, memory_action, *args, **kwargs)

        embedder.embed_batch = embed_batch


def get_memory() -> Memory:
    # Calls arrive from worker threads (asyncio.to_thread); build the client once.
    global _mem
    if _mem is None:
        with _mem_lock:
            if _mem is None:
                m = Memory(config=_build_config())
                _cap_embedding_input(m.embedding_model)
                _mem = m
    return _mem


def _agent_id(agent_id: str) -> str:
    return agent_id if agent_id else settings.mem0_agent_id


# mem0 2.0.x catches these inside the infer=True pipeline, logs them and returns
# [] or a partial result. A genuine "nothing new to extract" logs none of them.
_SWALLOWED = ("LLM extraction failed", "Error parsing extraction response",
              "Failed to embed memory text", "Failed to insert memory")


class _ThreadErrorCapture(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.thread = threading.get_ident()
        self.errors: list[str] = []

    def emit(self, record):
        if record.thread == self.thread:
            msg = record.getMessage()
            if msg.startswith(_SWALLOWED):
                self.errors.append(msg)


VALID_CATEGORIES = {"project", "feedback", "reference", "user", "session", "incident", ""}


def add(content: str, agent_id: str = "", infer: Optional[bool] = None, category: str = "",
        tags: Optional[list] = None, job_id: str = "", source: str = "agent",
        session_id: str = "", project: str = "", state: str = "") -> dict:
    m = get_memory()
    content = redact(content)
    # provenance: where a memory came from (agent tool call, hook, extraction, merge, import, ui)
    metadata: dict = {"source": source if source in VALID_SOURCES else "agent"}
    if session_id:
        metadata["session_id"] = session_id
    if project:
        metadata["project"] = project
    if source == "extraction" and not state:
        state = "candidate"          # LLM-extracted facts wait for review before they count
    if state in VALID_STATES and state != "active":
        metadata["state"] = state
    if category:
        metadata["category"] = category
    if job_id:
        metadata["job_id"] = job_id  # lets a retried queue job detect it already wrote
    auto = _auto_tag(content)
    explicit = [t for t in (tags or []) if isinstance(t, str) and t.strip()]
    merged = sorted(set(auto) | set(explicit))
    if merged:
        metadata["tags"] = merged
    use_infer = settings.mem0_infer if infer is None else infer
    kwargs = dict(user_id=settings.mem0_user_id, agent_id=_agent_id(agent_id))
    if not use_infer:
        return m.add(content, infer=False, metadata=metadata or None, **kwargs)

    # infer=True: mem0 2.0.x returns [] when the LLM's JSON is not in the shape it
    # expects (qwen2.5:7b often answers {"message": ...}), swallows extraction
    # errors, and crashes on {"memory": ["plain string"]}. Any of these used to
    # drop the write silently. Store the raw text instead; a genuine duplicate
    # this creates is merged by the nightly dedup job.
    capture = _ThreadErrorCapture()
    mem0_log = logging.getLogger("mem0.memory.main")
    mem0_log.addHandler(capture)
    reason = ""
    try:
        result = m.add(content, infer=True, metadata=metadata or None, **kwargs)
    except Exception as e:  # mem0 bug on unexpected LLM output
        result, reason = None, f"{type(e).__name__}: {e}"
    finally:
        mem0_log.removeHandler(capture)
    if capture.errors:
        reason = "; ".join(capture.errors)
    elif not reason and not _has_results(result):
        reason = "inference extracted nothing"
    if not reason:
        return result
    logger.warning("infer=True produced no memory (%s); storing raw text", reason[:200])
    fallback_meta = {**metadata, "infer_fallback": reason[:200]}
    return m.add(content, infer=False, metadata=fallback_meta, **kwargs)


def _has_results(result) -> bool:
    if isinstance(result, dict):
        result = result.get("results", [])
    return bool(result)


def ollama_up(timeout: float = 3.0) -> bool:
    """Cheap reachability check for the Ollama host (it may be a desktop that sleeps)."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"{settings.ollama_base_url.rstrip('/')}/api/tags", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def job_written(job_id: str) -> bool:
    """True when a memory carrying this queue job_id already exists."""
    conn = _db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT 1 FROM {_MEM_TABLE} WHERE payload->>'job_id' = %s LIMIT 1", (job_id,))
            return cur.fetchone() is not None
    except psycopg2.errors.UndefinedTable:
        return False  # fresh install: mem0 creates its table on the first write
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Hybrid search: Postgres full-text (exact identifiers) + mem0 vectors (meaning),
# merged with reciprocal rank fusion. The 'simple' text config does no stemming,
# so prod-k8s, 0.2.6, remnant-mcp.prod.threshold.se or c46e6ae8 stay one term.
# ---------------------------------------------------------------------------

_FTS_EXPR = "to_tsvector('simple', coalesce(payload->>'data', ''))"
_FTS_DOC = "fts"   # stored generated column; falls back to _FTS_EXPR if it cannot be added
_RRF_K = 60
_IDENTIFIER = re.compile(r"[\d\-._/:]")
KEYWORD_WEIGHT_PLAIN = float(os.environ.get("SEARCH_KEYWORD_WEIGHT_PLAIN", "0.3"))
_CANDIDATES = 25
_TERM = re.compile(r"[\w][\w.\-/:]*[\w]|[\w]", re.UNICODE)
_STOPWORDS = frozenset("""
a about an and any anything are as at be by can did do does for from has have how i in
info is it its know me my of on or our remember so tell that the this to was we were what
when where which who why will with you your
och att det en ett är för på som med av till den har inte om var jag vi de kan hur vad
""".split())


def _query_terms(query: str, max_terms: int = 12) -> list[str]:
    terms = []
    for t in _TERM.findall(query.lower()):
        if len(t) < 2 or t in _STOPWORDS or t in terms:
            continue
        terms.append(t)
    return terms[:max_terms]


def ensure_fts_index() -> bool:
    """Add a stored tsvector column + GIN index for keyword search (idempotent).

    mem0 always names its columns (id, vector, payload), so an extra generated
    column is invisible to it. Returns False when the table does not exist yet
    (fresh install) or the database is read-only; keyword search then uses the
    expression directly.
    """
    global _FTS_DOC
    try:
        conn = _db_conn()
    except Exception as e:
        logger.info("fts column check skipped, database unreachable (%s)", type(e).__name__)
        return False
    try:
        if _has_fts_column():
            # already migrated: skip the ALTER, which would take an ACCESS EXCLUSIVE lock again
            with conn:
                with conn.cursor() as cur:
                    cur.execute("SET LOCAL lock_timeout = '3s'")
                    cur.execute("SET LOCAL statement_timeout = '60s'")
                    cur.execute(f"CREATE INDEX IF NOT EXISTS {_MEM_TABLE}_fts_idx ON {_MEM_TABLE} USING gin (fts)")
            _FTS_DOC = "fts"
            return True
        with conn:
            with conn.cursor() as cur:
                # bounded: never block startup or writers behind a long transaction
                cur.execute("SET LOCAL lock_timeout = '3s'")
                cur.execute("SET LOCAL statement_timeout = '60s'")
                cur.execute(f"ALTER TABLE {_MEM_TABLE} ADD COLUMN IF NOT EXISTS fts tsvector "
                            f"GENERATED ALWAYS AS ({_FTS_EXPR}) STORED")
                cur.execute(f"CREATE INDEX IF NOT EXISTS {_MEM_TABLE}_fts_idx ON {_MEM_TABLE} USING gin (fts)")
        _FTS_DOC = "fts"
        return True
    except Exception as e:
        logger.info("fts column not added (%s); keyword search uses the expression", type(e).__name__)
        _FTS_DOC = _has_fts_column() and "fts" or _FTS_EXPR
        return False
    finally:
        conn.close()


def _has_fts_column() -> bool:
    try:
        conn = _db_conn()
    except Exception:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = 'fts'",
                        (_MEM_TABLE,))
            return cur.fetchone() is not None
    except Exception:
        return False
    finally:
        conn.close()


def keyword_search(query: str, limit: int = _CANDIDATES, include_checkpoints: bool = False,
                   states=("active",)) -> list[dict]:
    try:
        return _keyword_search(query, limit, include_checkpoints, states)
    except psycopg2.errors.UndefinedColumn:
        ensure_fts_index()          # table was created after startup
        return _keyword_search(query, limit, include_checkpoints, states)


def _like(term: str) -> str:
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _keyword_search(query: str, limit: int, include_checkpoints: bool = False, states=("active",)) -> list[dict]:
    """Full-text search weighted by term rarity (BM25 idf, no term frequency).

    Each query term that occurs in a memory adds its idf, so one rare
    identifier (morbius.prod.threshold.se) outweighs several common words
    (restart, pod). Identifier-like terms also match as a substring, because
    Postgres indexes pg-main-rw-lb.postgres.svc as one token and would never
    match the query term pg-main-rw. Terms found in more than 20% of memories
    are dropped unless nothing rarer is left.
    """
    terms = _query_terms(query)
    if not terms:
        return []

    def cond(term: str) -> tuple[str, list]:
        sql, params = f"{_FTS_DOC} @@ plainto_tsquery('simple', %s)", [term]
        if _IDENTIFIER.search(term):
            sql, params = f"({sql} OR payload->>'data' ILIKE %s)", [term, _like(term)]
        return sql, params

    # same population for the idf statistics and the results
    user = "payload->>'user_id' = %s AND " + _population(include_checkpoints, states)
    conn = _db_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            conds = [cond(t) for t in terms]
            # unique aliases: RealDictCursor keeps one value per column name
            counts = ", ".join(f"count(*) FILTER (WHERE {c}) AS df{i}" for i, (c, _) in enumerate(conds))
            cur.execute(f"SELECT count(*) AS n, {counts} FROM {_MEM_TABLE} WHERE {user}",
                        [p for _, ps in conds for p in ps] + [settings.mem0_user_id])
            row = cur.fetchone()
            n, dfs = row["n"], [row[f"df{i}"] for i in range(len(conds))]
            weighted = [(c, df) for c, df in zip(conds, dfs) if df]
            if not weighted or not n:
                return []
            rare = [(c, df) for c, df in weighted if df <= 0.2 * n]
            weighted = rare or sorted(weighted, key=lambda x: x[1])[:2]
            score_sql, score_params = [], []
            for (c, ps), df in weighted:
                score_sql.append(f"CASE WHEN {c} THEN %s ELSE 0 END")
                score_params += ps + [math.log((n - df + 0.5) / (df + 0.5) + 1)]
            any_sql = " OR ".join(c for (c, _), _ in weighted)
            any_params = [p for (_, ps), _ in weighted for p in ps]
            cur.execute(
                f"SELECT id, payload->>'data' AS memory, payload->>'agent_id' AS agent_id, "
                f"payload->>'created_at' AS created_at, payload->>'updated_at' AS updated_at, "
                f"{_META}->>'category' AS category, "
                f"CASE WHEN jsonb_typeof({_META}->'tags') = 'array' THEN {_META}->'tags' END AS tags, "
                f"payload->>'state' AS state, payload->>'source' AS source, "
                f"payload->>'review_reason' AS review_reason, payload->>'project' AS project, "
                f"payload->>'superseded_by' AS superseded_by, "
                f"({' + '.join(score_sql)}) AS rank "
                f"FROM {_MEM_TABLE} WHERE {user} AND ({any_sql}) "
                f"ORDER BY rank DESC, length(payload->>'data') ASC LIMIT %s",
                score_params + [settings.mem0_user_id] + any_params + [limit])
            rows = cur.fetchall()
    except psycopg2.errors.UndefinedTable:
        return []
    finally:
        conn.close()
    out = []
    for r in rows:
        item = _row_to_result(r)
        item["keyword_rank"] = float(r["rank"])
        out.append(item)
    return out


def hybrid_search(query: str, limit: int = 5, include_checkpoints: bool = False, states=("active",)) -> list[dict]:
    """Merge keyword and vector results with reciprocal rank fusion.

    score is the fused score scaled to 0..1 (1.0 = ranked first by both).
    vector_score is the cosine similarity when the vector side found the item.
    If the vector side fails (Ollama offline), keyword results are returned alone.
    """
    pool = max(_CANDIDATES, limit)
    keyword = keyword_search(query, pool, include_checkpoints, states)
    try:
        vector = search(query, limit=pool, include_checkpoints=include_checkpoints, states=states)
    except Exception as e:
        logger.warning("Vector search unavailable (%s); keyword results only", type(e).__name__)
        vector = []
    # Keyword matches are decisive for identifiers (prod-k8s, 0.2.6, c46e6ae8)
    # but mostly noise for plain-language questions, where vectors do better.
    kw_weight = 1.0 if any(_IDENTIFIER.search(t) for t in _query_terms(query)) else KEYWORD_WEIGHT_PLAIN
    weights = {"vector": 1.0, "keyword": kw_weight}
    fused: dict[str, dict] = {}
    for source, items in (("vector", vector), ("keyword", keyword)):
        for rank, item in enumerate(items, start=1):
            mid = str(item.get("id", ""))
            entry = fused.setdefault(mid, {"item": item, "rrf": 0.0, "sources": []})
            entry["rrf"] += weights[source] / (_RRF_K + rank)
            entry["sources"].append(source)
            if source == "vector":
                entry["vector_score"] = item.get("score")
    best = (1.0 + kw_weight) / (_RRF_K + 1)
    ranked = sorted(fused.values(), key=lambda e: e["rrf"], reverse=True)[:limit]
    results = []
    for e in ranked:
        item = dict(e["item"])
        item["score"] = round(e["rrf"] / best, 4)
        item["vector_score"] = e.get("vector_score")
        item["match"] = "+".join(e["sources"])
        results.append(item)
    return results


def search(query: str, limit: int = 5, agent_id: str = "", include_checkpoints: bool = False,
           states=("active",)) -> list[dict]:
    """Vector search: cosine similarity over the stored embeddings.

    Deliberately not mem0's Memory.search(): with mem0ai 2.0.2 its fused
    pipeline returned near-constant scores and found 0 of 30 test questions
    whose answers plain cosine ranking found 16 of 30 (2026-10-14 eval on the
    prod data). The query is embedded with mem0's own embedder, the same one
    used when memories were written.
    """
    embedding = get_memory().embedding_model.embed(query, "search")
    sql = (f"SELECT id, payload->>'data' AS memory, payload->>'agent_id' AS agent_id, "
           f"payload->>'created_at' AS created_at, payload->>'updated_at' AS updated_at, "
           f"{_META}->>'category' AS category, "
           f"CASE WHEN jsonb_typeof({_META}->'tags') = 'array' THEN {_META}->'tags' END AS tags, "
           f"payload->>'state' AS state, payload->>'source' AS source, "
                f"payload->>'review_reason' AS review_reason, payload->>'project' AS project, "
                f"payload->>'superseded_by' AS superseded_by, "
           f"1 - (vector <=> %s::vector) AS score "
           f"FROM {_MEM_TABLE} WHERE payload->>'user_id' = %s "
           + f"AND {_population(include_checkpoints, states)} " +
           f"ORDER BY vector <=> %s::vector LIMIT %s")
    vec = str(list(embedding))
    conn = _db_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, [vec, settings.mem0_user_id, vec, limit])
            rows = cur.fetchall()
    except psycopg2.errors.UndefinedTable:
        return []
    finally:
        conn.close()
    out = []
    for r in rows:
        item = _row_to_result(r)
        item["score"] = round(float(r["score"]), 4)
        out.append(item)
    return out


def get_all(agent_id: str = "") -> list[dict]:
    m = get_memory()
    filters = {"user_id": settings.mem0_user_id}
    if agent_id:
        filters["agent_id"] = agent_id
    result = m.get_all(filters=filters, top_k=10000)
    if isinstance(result, dict) and "results" in result:
        return result["results"]
    return result if isinstance(result, list) else []


def save_checkpoint(session_id: str, content: str, agent_id: str = "", project: str = "",
                    captured_at: str = "", capture_id: str = "") -> dict:
    """Store the one replaceable checkpoint for a session (hook snapshot).

    Ordered by capture time, not write time: after writing, only the newest
    capture of the session is kept. Two concurrent saves therefore keep the
    same row, and a stale retry (captured earlier than what is stored) removes
    itself. There is never a moment without a checkpoint. Excluded from
    search; read back with get_checkpoint(session_id).
    """
    if not session_id:
        raise ValueError("session_id required")
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    try:
        base = _dt.datetime.fromisoformat((captured_at or "").replace("Z", "+00:00")) if captured_at else now
        if base.tzinfo is None:
            base = base.replace(tzinfo=_dt.timezone.utc)
    except ValueError:
        base = now
    # one fixed format (UTC, microseconds) so the text ordering below is the time ordering
    captured = base.astimezone(_dt.timezone.utc).isoformat(timespec="microseconds")
    expires = (base + _dt.timedelta(days=CHECKPOINT_TTL_DAYS)).isoformat()
    import uuid as _uuid
    capture_id = capture_id or str(_uuid.uuid4())   # stable tie-breaker, kept across retries
    get_memory().add(
        redact(content), user_id=settings.mem0_user_id, agent_id=_agent_id(agent_id), infer=False,
        metadata={"kind": "checkpoint", "source": "hook", "session_id": session_id, "project": project,
                  "category": "session", "captured_at": captured, "capture_id": capture_id,
                  "expires_at": expires})
    conn = _db_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM {_MEM_TABLE} WHERE payload->>'kind' = 'checkpoint' AND payload->>'session_id' = %s "
                    f"AND id <> (SELECT id FROM {_MEM_TABLE} WHERE payload->>'kind' = 'checkpoint' "
                    f"AND payload->>'session_id' = %s "
                    f"ORDER BY coalesce(payload->>'captured_at', payload->>'created_at') DESC, "
                    f"coalesce(payload->>'capture_id', '') DESC, id DESC LIMIT 1)",
                    (session_id, session_id))
                removed = cur.rowcount
    finally:
        conn.close()
    return {"session_id": session_id, "captured_at": captured, "removed": removed}


def get_checkpoint(session_id: str) -> Optional[dict]:
    """Latest checkpoint of a session, by exact id (never by similarity)."""
    conn = _db_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"SELECT id, payload->>'data' AS memory, "
                        f"coalesce(payload->>'captured_at', payload->>'created_at') AS created_at, "
                        f"payload->>'project' AS project FROM {_MEM_TABLE} "
                        f"WHERE payload->>'kind' = 'checkpoint' AND payload->>'session_id' = %s "
                        f"AND payload->>'user_id' = %s "
                        f"AND (NOT payload ? 'expires_at' OR (payload->>'expires_at')::timestamptz > now()) "
                        f"ORDER BY coalesce(payload->>'captured_at', payload->>'created_at') DESC, "
                        f"coalesce(payload->>'capture_id', '') DESC, id DESC LIMIT 1",
                        (session_id, settings.mem0_user_id))
            row = cur.fetchone()
            return dict(row) if row else None
    except psycopg2.errors.UndefinedTable:
        return None
    finally:
        conn.close()


def content_hash(text: str) -> str:
    """Hash agents pass back as expected_hash (md5 of the stored text, = SQL md5(payload->>'data'))."""
    import hashlib
    return hashlib.md5((text or "").encode("utf-8")).hexdigest()


def get_memory_detail(memory_id: str) -> Optional[dict]:
    """One memory by exact id, with lifecycle links and its content hash."""
    conn = _db_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"SELECT id, payload FROM {_MEM_TABLE} WHERE id::text = %s AND payload->>'user_id' = %s",
                        (memory_id, settings.mem0_user_id))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    p = row["payload"]
    keys = ("state", "superseded_by", "supersedes_ids", "supersede_reason", "superseded_at", "source",
            "project", "agent_id", "created_at", "kind", "review_reason", "reviewed_by")
    nested = p.get("metadata") if isinstance(p.get("metadata"), dict) else {}
    category = nested.get("category") or p.get("category") or ""
    tags = nested.get("tags") if isinstance(nested.get("tags"), list) else p.get("tags")
    return {"id": str(row["id"]), "memory": p.get("data", ""), "hash": content_hash(p.get("data", "")),
            **{k: p.get(k) for k in keys if p.get(k) is not None}, "state": p.get("state") or "active",
            "category": category, "tags": tags if isinstance(tags, list) else []}


def _now() -> str:
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _retire(cur, old_ids: list[str], new_id: str, reason: str, actor: str) -> None:
    cur.execute(f"UPDATE {_MEM_TABLE} SET payload = payload || %s::jsonb WHERE id::text = ANY(%s)",
                (json.dumps({"state": "superseded", "superseded_by": new_id, "superseded_at": _now(),
                             "supersede_reason": reason[:500], "superseded_by_actor": actor[:64]}), old_ids))


def _request_hash(old_ids, new_content, as_candidate) -> str:
    import hashlib
    return hashlib.md5(json.dumps([sorted(old_ids), new_content, bool(as_candidate)]).encode()).hexdigest()


def _reserve_key(key: str, request_hash: str) -> Optional[dict]:
    """Reserve an idempotency key. None = reserved for this call; otherwise the
    result to return (replay, key reused for another request, or still in progress)."""
    conn = _db_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO idempotency_keys (key, request_hash) VALUES (%s, %s) "
                            "ON CONFLICT (key) DO NOTHING RETURNING key", (key, request_hash))
                if cur.fetchone():
                    return None
                cur.execute("SELECT request_hash, result FROM idempotency_keys WHERE key = %s", (key,))
                stored_hash, result = cur.fetchone()
    finally:
        conn.close()
    if stored_hash != request_hash:
        return {"ok": False, "error": "idempotency key already used for a different request"}
    if result is None:
        return {"ok": False, "error": "in progress", "retry": True}
    return {**result, "replayed": True}


def _finish_key(key: str, result: dict) -> None:
    if not key:
        return
    conn = _db_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                if result.get("error") == "replacement not stored":
                    cur.execute("DELETE FROM idempotency_keys WHERE key = %s", (key,))   # allow a real retry
                else:
                    cur.execute("UPDATE idempotency_keys SET result = %s, memory_id = %s WHERE key = %s",
                                (json.dumps(result), result.get("id"), key))
    finally:
        conn.close()


def _inherited_metadata(details: list[dict]) -> dict:
    """What a replacement keeps from the memories it replaces: category and project when
    they agree, tags as a union (an edit keeps the original's tags)."""
    meta: dict = {}
    cats = {d.get("category") for d in details if d.get("category")}
    if len(cats) == 1:
        meta["category"] = cats.pop()
    projects = {d.get("project") for d in details if d.get("project")}
    if len(projects) == 1:
        meta["project"] = projects.pop()
    tags = sorted({t for d in details for t in (d.get("tags") or []) if isinstance(t, str)})
    if tags:
        meta["tags"] = tags
    return meta


def supersede(old_ids: list[str], new_content: str, reason: str, actor: str = "agent",
              expected_hashes: Optional[dict] = None, idempotency_key: str = "",
              as_candidate: bool = False, agent_id: str = "", source: str = "agent") -> dict:
    """Replace active memories by a new one, keeping the old rows (state=superseded,
    superseded_by=<new>) instead of overwriting them.

    1. idempotency_key is reserved atomically and bound to the request: a retry
       replays the first result, reusing the key for another request fails
    2. the new row is written as 'pending' (never searchable)
    3. one transaction locks the old rows AND the pending row, re-checks that the
       old ones are still active and, when given, unchanged (expected_hashes), then
       marks them superseded and the new row active. Any mismatch — including the
       pending row having vanished — rejects the replacement and returns a conflict.
    as_candidate=True (LLM merge proposals): the new row becomes a candidate with
    supersedes_ids and the originals stay active until review() promotes it.
    """
    old_ids = sorted({str(i) for i in old_ids if i})
    if not old_ids or not new_content.strip():
        return {"ok": False, "error": "old id(s) and new content are required"}
    if not reason.strip():
        return {"ok": False, "error": "reason is required"}
    if idempotency_key:
        earlier = _reserve_key(idempotency_key, _request_hash(old_ids, new_content, as_candidate))
        if earlier is not None:
            return earlier
    try:
        result = _supersede(old_ids, new_content, reason, actor, expected_hashes, idempotency_key,
                            as_candidate, agent_id, source)
    except Exception:
        _finish_key(idempotency_key, {"error": "replacement not stored"})   # release the key for a retry
        raise
    _finish_key(idempotency_key, result)
    return result


def _supersede(old_ids, new_content, reason, actor, expected_hashes, idempotency_key,
               as_candidate, agent_id, source) -> dict:
    before = {i: get_memory_detail(i) for i in old_ids}
    bad = {i: (d or {}).get("state", "missing") for i, d in before.items() if not d or d["state"] != "active"}
    if bad:
        return {"ok": False, "error": "conflict", "states": bad,
                "superseded_by": {i: before[i].get("superseded_by") for i in bad if before.get(i)}}
    metadata = {**_inherited_metadata(list(before.values())), "state": "pending", "supersedes_ids": old_ids,
                "supersede_reason": reason[:500], "source": source if source in VALID_SOURCES else "agent"}
    if idempotency_key:
        metadata["idempotency_key"] = idempotency_key
    result = get_memory().add(redact(new_content), user_id=settings.mem0_user_id, agent_id=_agent_id(agent_id),
                              infer=False, metadata=metadata)
    new_id = next((str(r.get("id")) for r in (result.get("results", []) if isinstance(result, dict) else result or [])
                   if r.get("id")), "")
    if not new_id:
        return {"ok": False, "error": "replacement not stored"}
    conn = _db_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT 1 FROM {_MEM_TABLE} WHERE id::text = %s AND payload->>'state' = 'pending' "
                            f"FOR UPDATE", (new_id,))
                if not cur.fetchone():
                    return {"ok": False, "error": "replacement vanished before it was finalised"}
                cur.execute(f"SELECT id::text, coalesce(payload->>'state', 'active'), md5(coalesce(payload->>'data', '')) "
                            f"FROM {_MEM_TABLE} WHERE id::text = ANY(%s) FOR UPDATE", (old_ids,))
                now = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
                stale = {i: now.get(i, ("missing", ""))[0] for i in old_ids if now.get(i, ("missing",))[0] != "active"}
                changed = [i for i, h in (expected_hashes or {}).items() if i in now and h and now[i][1] != h]
                if stale or changed:
                    cur.execute(f"UPDATE {_MEM_TABLE} SET payload = payload || %s::jsonb WHERE id::text = %s",
                                (json.dumps({"state": "rejected", "review_note": "supersede conflict"}), new_id))
                    return {"ok": False, "error": "conflict", "states": stale, "changed": changed, "id": new_id}
                final = ({"state": "candidate", "review_reason": "merge-proposal"} if as_candidate
                         else {"state": "active"})
                cur.execute(f"UPDATE {_MEM_TABLE} SET payload = payload || %s::jsonb "
                            f"WHERE id::text = %s AND payload->>'state' = 'pending'", (json.dumps(final), new_id))
                if cur.rowcount != 1:
                    raise RuntimeError("replacement could not be finalised")   # rolls back the transaction
                if as_candidate:
                    return {"ok": True, "id": new_id, "state": "candidate", "supersedes_ids": old_ids}
                _retire(cur, old_ids, new_id, reason, actor)
                return {"ok": True, "id": new_id, "state": "active", "superseded": old_ids}
    finally:
        conn.close()


def review(memory_id: str, action: str, reviewer: str = "user", note: str = "") -> dict:
    """Promote a candidate to active or reject it, with an audit trail.

    Only candidates can be reviewed; anything else returns a conflict with the
    current state. Everything happens in one transaction with the candidate row
    locked. Promoting a merge proposal also locks its originals: if any of them
    is no longer active (corrected or replaced meanwhile), the promotion is
    refused with a conflict so the stale merge does not come back; otherwise the
    originals are superseded atomically.
    """
    target = {"promote": "active", "reject": "rejected"}.get(action)
    if not target:
        return {"ok": False, "error": "action must be promote or reject"}
    audit = {"state": target, "reviewed_at": _now(), "reviewed_by": reviewer[:64], "review_note": note[:500]}
    conn = _db_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT coalesce(payload->>'state', 'active'), payload->'supersedes_ids' "
                            f"FROM {_MEM_TABLE} WHERE id::text = %s FOR UPDATE", (memory_id,))
                row = cur.fetchone()
                if not row:
                    return {"ok": False, "error": "not found"}
                state, supersedes = row
                if state != "candidate":
                    return {"ok": False, "error": "conflict", "state": state}
                originals = [str(i) for i in (supersedes or [])]
                if target == "active" and originals:
                    cur.execute(f"SELECT id::text, coalesce(payload->>'state', 'active') FROM {_MEM_TABLE} "
                                f"WHERE id::text = ANY(%s) FOR UPDATE", (originals,))
                    states = dict(cur.fetchall())
                    gone = {i: states.get(i, "missing") for i in originals if states.get(i) != "active"}
                    if gone:
                        return {"ok": False, "error": "conflict", "state": "candidate", "originals": gone}
                cur.execute(f"UPDATE {_MEM_TABLE} SET payload = payload || %s::jsonb WHERE id::text = %s",
                            (json.dumps(audit), memory_id))
                out = {"ok": True, "id": memory_id, "state": target}
                if target == "active" and originals:
                    _retire(cur, originals, memory_id, "merge proposal promoted in review", reviewer)
                    out["superseded"] = sorted(originals)
                return out
    finally:
        conn.close()


def update(memory_id: str, content: str, original: Optional[str] = None, actor: str = "ui") -> dict:
    """Edit = supersede: the old text is kept (state=superseded) and linked to the
    new row. original (the text the editor started from) turns a concurrent change
    into a conflict instead of a silent overwrite."""
    expected = {memory_id: content_hash(original)} if original is not None else None
    return supersede([memory_id], content, "edited", actor=actor, expected_hashes=expected,
                     source="ui", agent_id=(get_memory_detail(memory_id) or {}).get("agent_id", ""))


def delete(memory_id: str) -> dict:
    m = get_memory()
    return m.delete(memory_id)


def update_tags(memory_id: str, tags: list) -> bool:
    """Directly patch the tags field in the DB payload, preserving all other metadata."""
    conn = _db_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                # FOR UPDATE: a review committing between read and write must not be undone
                cur.execute("SELECT payload FROM mem0 WHERE id = %s FOR UPDATE", (memory_id,))
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
ensure_fts_index()
