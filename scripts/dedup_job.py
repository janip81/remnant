#!/usr/bin/env python3
"""
Memory deduplication and consolidation job.

Designed to run as a Kubernetes CronJob during off-peak hours.
All originals are archived to mem0_archive before any modification —
the archive is append-only and never touched by this script after writing.

Phase 1 — Hash dedup (fast, exact):
  Find rows sharing the same payload->>'hash'. Keep the oldest, archive the rest.

Phase 2 — Semantic dedup (slow, Ollama):
  Find clusters of memories with cosine similarity > SEMANTIC_THRESHOLD.
  For each cluster: archive originals → ask Ollama to merge → store merged (infer=False).

Usage:
  python dedup_job.py [--dry-run] [--phase1-only] [--phase2-only] [--threshold 0.92]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import uuid
from typing import Optional

import psycopg2
import psycopg2.extras
import requests

log = logging.getLogger("dedup")
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

DATABASE_URL = os.environ.get("DATABASE_URL", "")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2")
MEMORY_URL = os.environ.get("CLAUDE_MEMORY_URL", "http://claude-memory:8080")
MEMORY_TOKEN = os.environ.get("CLAUDE_MEMORY_TOKEN", "")

ARCHIVE_TABLE = "mem0_archive"
MEM_TABLE = "mem0"

# Memories closer than this (cosine distance) are considered near-duplicates
DEFAULT_SEMANTIC_THRESHOLD = 0.92
# Only merge clusters of at least this many members (2 = any pair)
MIN_CLUSTER_SIZE = 2


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def connect() -> psycopg2.extensions.connection:
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    psycopg2.extras.register_uuid(conn)
    return conn


def ensure_archive_table(cur: psycopg2.extensions.cursor) -> None:
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {ARCHIVE_TABLE} (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            original_id     UUID NOT NULL,
            payload         JSONB NOT NULL,
            archived_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            archive_reason  TEXT NOT NULL,
            merged_into_id  UUID
        );
        CREATE INDEX IF NOT EXISTS {ARCHIVE_TABLE}_original_id_idx
            ON {ARCHIVE_TABLE}(original_id);
        CREATE INDEX IF NOT EXISTS {ARCHIVE_TABLE}_archived_at_idx
            ON {ARCHIVE_TABLE}(archived_at DESC);
    """)


def archive_rows(
    cur: psycopg2.extensions.cursor,
    row_ids: list[uuid.UUID],
    reason: str,
    merged_into_id: Optional[uuid.UUID] = None,
) -> None:
    """Copy rows from mem0 → mem0_archive before deletion."""
    for rid in row_ids:
        cur.execute(
            f"""
            INSERT INTO {ARCHIVE_TABLE} (original_id, payload, archive_reason, merged_into_id)
            SELECT id, payload, %s, %s
            FROM {MEM_TABLE}
            WHERE id = %s
            """,
            (reason, merged_into_id, rid),
        )


def delete_rows(cur: psycopg2.extensions.cursor, row_ids: list[uuid.UUID]) -> None:
    cur.execute(
        f"DELETE FROM {MEM_TABLE} WHERE id = ANY(%s)",
        (row_ids,),
    )


# ---------------------------------------------------------------------------
# Phase 1 — Hash-based exact dedup
# ---------------------------------------------------------------------------

def phase1_hash_dedup(conn, dry_run: bool) -> int:
    log.info("Phase 1: hash-based exact dedup")
    removed = 0

    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        ensure_archive_table(cur)

        # Find hashes with more than one row, ordered so we keep the oldest
        cur.execute(f"""
            SELECT payload->>'hash' AS hash,
                   array_agg(id ORDER BY (payload->>'created_at') ASC) AS ids
            FROM {MEM_TABLE}
            WHERE payload->>'hash' IS NOT NULL
              AND payload->>'hash' != ''
            GROUP BY payload->>'hash'
            HAVING COUNT(*) > 1
        """)
        clusters = cur.fetchall()

        log.info("Found %d hash clusters with duplicates", len(clusters))

        for row in clusters:
            keep_id = row["ids"][0]
            drop_ids = [uuid.UUID(str(i)) for i in row["ids"][1:]]
            log.info("Hash dedup: keeping %s, dropping %d duplicates", keep_id, len(drop_ids))

            if not dry_run:
                archive_rows(cur, drop_ids, "hash-dedup")
                delete_rows(cur, drop_ids)
            removed += len(drop_ids)

        if not dry_run:
            conn.commit()

    log.info("Phase 1 done: removed %d exact duplicates%s", removed, " (dry run)" if dry_run else "")
    return removed


# ---------------------------------------------------------------------------
# Phase 2 — Semantic near-dedup via pgvector
# ---------------------------------------------------------------------------

def find_semantic_clusters(
    cur: psycopg2.extensions.cursor, threshold: float
) -> list[list[uuid.UUID]]:
    """
    Find clusters of memories whose cosine similarity exceeds threshold.
    Uses a greedy union-find approach: each row checks its nearest neighbours.
    Returns list of clusters (each cluster is a list of UUIDs, len >= MIN_CLUSTER_SIZE).
    """
    cosine_distance = 1.0 - threshold

    # Find all pairs within the threshold — pgvector <=> is cosine distance
    cur.execute(
        f"""
        SELECT a.id AS id_a, b.id AS id_b,
               1 - (a.vector <=> b.vector) AS similarity
        FROM {MEM_TABLE} a
        JOIN {MEM_TABLE} b ON a.id < b.id
        WHERE (a.vector <=> b.vector) < %s
        ORDER BY similarity DESC
        """,
        (cosine_distance,),
    )
    pairs = cur.fetchall()
    log.info("Found %d near-duplicate pairs (threshold=%.2f)", len(pairs), threshold)

    if not pairs:
        return []

    # Union-Find to cluster transitively connected pairs
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent.get(x, x), x)
            x = parent[x]
        return x

    def union(x: str, y: str) -> None:
        px, py = find(x), find(y)
        if px != py:
            parent[py] = px

    for row in pairs:
        union(str(row[0]), str(row[1]))

    # Group by root
    groups: dict[str, list[str]] = {}
    all_ids: set[str] = set()
    for row in pairs:
        all_ids.add(str(row[0]))
        all_ids.add(str(row[1]))
    for nid in all_ids:
        root = find(nid)
        groups.setdefault(root, []).append(nid)

    clusters = [
        [uuid.UUID(i) for i in ids]
        for ids in groups.values()
        if len(ids) >= MIN_CLUSTER_SIZE
    ]
    log.info("Clustered into %d semantic groups", len(clusters))
    return clusters


def ollama_merge(texts: list[str]) -> Optional[str]:
    """Ask Ollama to merge a list of similar memories into one canonical entry."""
    numbered = "\n\n".join(f"Memory {i+1}:\n{t}" for i, t in enumerate(texts))
    prompt = (
        f"You have {len(texts)} similar memory entries. "
        "Produce a single, comprehensive merged memory that captures all unique information. "
        "Be concise but complete. Preserve specific details like dates, names, file paths, "
        "and technical values. Output ONLY the merged memory text, nothing else.\n\n"
        f"{numbered}"
    )
    try:
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except Exception as e:
        log.warning("Ollama merge failed: %s", e)
        return None


def store_memory_direct(content: str) -> Optional[str]:
    """Store a merged memory via the MCP HTTP endpoint with infer=False."""
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": "add_memory",
            "arguments": {"content": content, "infer": False},
        },
        "id": 1,
    }
    try:
        resp = requests.post(
            f"{MEMORY_URL}/mcp",
            headers={
                "Authorization": f"Bearer {MEMORY_TOKEN}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json=payload,
            timeout=30,
            stream=True,
        )
        resp.raise_for_status()
        for line in resp.iter_lines():
            decoded = line.decode() if isinstance(line, bytes) else line
            if decoded.startswith("data:"):
                obj = json.loads(decoded[5:].strip())
                result = obj.get("result", {})
                if result.get("isError"):
                    log.warning("store error: %s", result)
                    return None
                # Extract new memory ID from result if available
                text = (result.get("content") or [{}])[0].get("text", "")
                try:
                    data = json.loads(text)
                    results = data.get("results", [])
                    if results:
                        return results[0].get("id")
                except Exception:
                    pass
                return "stored"
    except Exception as e:
        log.warning("store_memory_direct failed: %s", e)
    return None


def phase2_semantic_dedup(conn, threshold: float, dry_run: bool) -> int:
    log.info("Phase 2: semantic dedup (threshold=%.2f)", threshold)
    merged = 0

    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        ensure_archive_table(cur)
        clusters = find_semantic_clusters(cur, threshold)

        for cluster_ids in clusters:
            # Fetch texts for this cluster
            cur.execute(
                f"SELECT id, payload FROM {MEM_TABLE} WHERE id = ANY(%s)",
                (cluster_ids,),
            )
            rows = cur.fetchall()
            texts = [r["payload"].get("data", "") for r in rows]
            texts = [t for t in texts if t.strip()]

            if len(texts) < MIN_CLUSTER_SIZE:
                continue

            log.info("Merging cluster of %d memories", len(texts))
            for t in texts:
                log.info("  - %s", t[:120])

            if dry_run:
                merged += len(cluster_ids)
                continue

            merged_text = ollama_merge(texts)
            if not merged_text:
                log.warning("Merge failed for cluster — skipping")
                continue

            log.info("  → merged: %s", merged_text[:120])

            # Store merged memory first, get its new ID
            new_id_str = store_memory_direct(merged_text)

            # Archive originals (pointing to merged entry if we have its ID)
            merged_into = uuid.UUID(new_id_str) if new_id_str and new_id_str != "stored" else None
            archive_rows(cur, cluster_ids, "semantic-dedup", merged_into_id=merged_into)
            delete_rows(cur, cluster_ids)
            conn.commit()
            merged += len(cluster_ids)

    log.info(
        "Phase 2 done: processed %d memories into merged entries%s",
        merged,
        " (dry run)" if dry_run else "",
    )
    return merged


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Memory deduplication job")
    parser.add_argument("--dry-run", action="store_true", help="Report without making changes")
    parser.add_argument("--phase1-only", action="store_true", help="Only run hash dedup")
    parser.add_argument("--phase2-only", action="store_true", help="Only run semantic dedup")
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_SEMANTIC_THRESHOLD,
        help=f"Cosine similarity threshold for semantic dedup (default: {DEFAULT_SEMANTIC_THRESHOLD})",
    )
    args = parser.parse_args()

    if not DATABASE_URL:
        log.error("DATABASE_URL not set")
        sys.exit(1)

    log.info("Starting dedup job (dry_run=%s, threshold=%.2f)", args.dry_run, args.threshold)

    conn = connect()
    try:
        total_removed = 0

        if not args.phase2_only:
            total_removed += phase1_hash_dedup(conn, args.dry_run)

        if not args.phase1_only:
            total_removed += phase2_semantic_dedup(conn, args.threshold, args.dry_run)

        log.info("Dedup complete. Total processed: %d", total_removed)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
