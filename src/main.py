import asyncio
import hmac
import json
import logging
import urllib.parse
from typing import Optional

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

import memory as mem_store
from config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Fail fast for every launcher (python main.py, uvicorn main:app, tests):
# an empty token would otherwise make "Authorization: Bearer " valid.
settings.require_safe_token()

mcp = FastMCP(
    "claude-memory",
    stateless_http=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


# ---------------------------------------------------------------------------
# MCP tools — called by Claude Code / Cursor via MCP protocol
# ---------------------------------------------------------------------------

@mcp.tool()
async def add_memory(content: str, agent_id: str = "", infer: Optional[bool] = None, category: str = "",
                     tags: list[str] = [], source: str = "agent", session_id: str = "", project: str = "") -> str:
    """Store a fact or observation in memory, as written (one self-contained fact per call works best).
    category: project|feedback|reference|user|session|incident
    tags: free-form list, e.g. ["prod-k8s", "remnant", "cnpg"] — use app names, cluster names, topics.
    infer: omit to use the server default (stored as-is); True asks the LLM to extract facts first.
    source/session_id/project: provenance (hooks set source="hook" or "extraction")."""
    if infer is None:
        infer = settings.mem0_infer
    if category and category not in mem_store.VALID_CATEGORIES:
        return f"Invalid category '{category}'. Valid: {', '.join(sorted(mem_store.VALID_CATEGORIES) - {''})}"
    if settings.redis_url:
        from queue_worker import enqueue
        job_id = await enqueue(content, agent_id, infer, category, tags,
                               source=source, session_id=session_id, project=project)
        return json.dumps({"queued": True, "job_id": job_id})
    result = await asyncio.to_thread(mem_store.add, content, agent_id=agent_id, infer=infer, category=category,
                                     tags=tags, source=source, session_id=session_id, project=project)
    return json.dumps(result)


@mcp.tool()
async def search_memory(query: str, limit: int = 5, include_sessions: bool = False, format: str = "text",
                        include_history: bool = False, include_candidates: bool = False) -> str:
    """Search memory by meaning and exact terms (hostnames, versions, error codes, names).
    Each hit shows rank (fused ordering, 0..1), cos (cosine similarity, when the
    vector side matched) and which side matched. Neither is a calibrated
    probability: treat low-cos or keyword-only hits as weaker evidence.
    Session checkpoints are excluded unless include_sessions=True; to restore a
    session use get_checkpoint(session_id).
    format="json" returns [{id, memory, rank, cos, match, state}] for hooks/tools.
    Only active memories by default; include_history adds superseded ones,
    include_candidates adds unreviewed LLM output (state shown either way)."""
    states = ["active"] + (["superseded"] if include_history else []) + (["candidate"] if include_candidates else [])
    results = await asyncio.to_thread(mem_store.hybrid_search, query, limit=limit,
                                      include_checkpoints=include_sessions, states=states)
    if format == "json":
        return json.dumps([{"id": r.get("id"), "memory": r.get("memory", ""), "rank": r.get("score"),
                            "cos": r.get("vector_score"), "match": r.get("match", ""),
                            "state": r.get("state") or "active"} for r in results])
    if not results:
        return "No memories found."
    # rank = fused rank score (ordering only); cos = cosine similarity when the
    # vector side found it — use that, not rank, to judge how relevant a hit is.
    lines = []
    for r in results:
        text = r.get("memory", r.get("text", str(r)))
        cos = r.get("vector_score")
        evidence = f"cos {cos:.2f}" if cos is not None else "keyword only"
        state = r.get("state") or "active"
        flag = "" if state == "active" else f" | {state}"
        lines.append(f"[rank {r.get('score', 0):.2f} | {evidence} | {r.get('match', '')}{flag}] {text}")
    return "\n".join(lines)


@mcp.tool()
async def save_checkpoint(session_id: str, content: str, project: str = "", agent_id: str = "",
                          captured_at: str = "", capture_id: str = "", sync: bool = False) -> str:
    """Save the replaceable working-state checkpoint of a session (used by hooks).
    Keeps only the newest capture per session_id (captured_at, ISO 8601; defaults
    to now); never returned by search_memory; expires after a few days.
    sync=True writes before returning (use before compaction so a resume can read
    it); otherwise it is queued. Not for durable facts — use add_memory."""
    if not session_id or not content.strip():
        return "session_id and content are required"
    import datetime as _dt
    import uuid as _uuid
    captured_at = captured_at or _dt.datetime.now(_dt.timezone.utc).isoformat()
    capture_id = capture_id or str(_uuid.uuid4())   # fixed now so a queued retry keeps it
    if sync or not settings.redis_url:
        try:
            result = await asyncio.to_thread(mem_store.save_checkpoint, session_id, content, agent_id,
                                             project, captured_at, capture_id)
            return json.dumps({"saved": True, **result})
        except Exception as e:
            if not settings.redis_url:
                return f"checkpoint not saved: {type(e).__name__}"
            logger.warning("Synchronous checkpoint failed (%s); queueing", type(e).__name__)
    from queue_worker import enqueue
    job_id = await enqueue(content, agent_id, False, "session", [], kind="checkpoint",
                           session_id=session_id, project=project, captured_at=captured_at,
                           capture_id=capture_id)
    return json.dumps({"queued": True, "job_id": job_id})


@mcp.tool()
async def get_checkpoint(session_id: str) -> str:
    """Return the latest checkpoint of a session by exact session_id (for resume/compaction)."""
    row = await asyncio.to_thread(mem_store.get_checkpoint, session_id)
    if not row:
        return "No checkpoint for this session."
    return f"[checkpoint captured {row['created_at']} project={row.get('project') or '-'}]\n{row['memory']}"


@mcp.tool()
async def get_all_memories(include_history: bool = False, include_candidates: bool = False,
                           include_sessions: bool = False) -> str:
    """List stored memories: active ones by default (same eligibility as search_memory);
    include_history adds superseded, include_candidates adds unreviewed LLM output,
    include_sessions adds session checkpoints. Non-active rows are labelled."""
    states = ["active"] + (["superseded"] if include_history else []) + (["candidate"] if include_candidates else [])
    results = await asyncio.to_thread(mem_store.list_rows, "", "", states, include_sessions)
    if not results:
        return "No memories stored."
    lines = []
    for r in results:
        label = "" if (r.get("state") or "active") == "active" else f"[{r['state']}] "
        lines.append(label + r.get("memory", r.get("text", str(r))))
    return "\n".join(f"{i+1}. {m}" for i, m in enumerate(lines))


@mcp.tool()
async def delete_memory(memory_id: str) -> str:
    """Delete a memory by ID."""
    result = await asyncio.to_thread(mem_store.delete, memory_id)
    return json.dumps(result)


# Build the FastMCP Starlette app and extract the ASGI handler directly
# to bypass Starlette's redirect_slashes (caused 307s on POST /mcp).
_starlette_app = mcp.streamable_http_app()
_mcp_handler = _starlette_app.router.routes[0].app


# ---------------------------------------------------------------------------
# REST API helpers
# ---------------------------------------------------------------------------

def _json_response(data, status=200):
    body = json.dumps(data, default=str).encode()
    return status, [[b"content-type", b"application/json"],
                    [b"access-control-allow-origin", b"*"]], body


async def _send_response(send, status, headers, body):
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def _check_auth(headers: dict) -> bool:
    auth = headers.get(b"authorization", b"").decode()
    return auth.startswith("Bearer ") and hmac.compare_digest(auth[7:].encode(), settings.bearer_token.encode())


def _get_agent_id(headers: dict) -> str:
    return headers.get(b"x-agent-id", b"").decode().strip()


def _parse_qs(qs: str) -> dict:
    params = {}
    for part in qs.split("&"):
        if "=" in part:
            k, v = part.split("=", 1)
            params[k] = urllib.parse.unquote_plus(v)
        elif part:
            params[part] = ""
    return params


async def _read_body(receive) -> bytes:
    chunks = []
    while True:
        msg = await receive()
        chunks.append(msg.get("body", b""))
        if not msg.get("more_body"):
            break
    return b"".join(chunks)


def _make_receive(body: bytes, original_receive=None):
    """One-shot ASGI receive that replays a pre-read body.

    After the body is replayed, delegates to original_receive so that SSE
    EventSourceResponse can wait for actual client disconnect rather than
    getting an immediate http.disconnect signal.
    """
    done = False

    async def _receive():
        nonlocal done
        if not done:
            done = True
            return {"type": "http.request", "body": body, "more_body": False}
        if original_receive is not None:
            return await original_receive()
        return {"type": "http.disconnect"}

    return _receive


async def _mcp_handler_strip_output_schema(scope, receive, send):
    """Call the MCP handler and strip outputSchema from tools/list responses.

    outputSchema was added in MCP spec 2025-03-26. Claude Code 2.x uses
    2024-11-05 and silently drops all tools if outputSchema is present.
    """
    status_out = None
    headers_out = []
    body_parts = []

    async def _capture(event):
        nonlocal status_out, headers_out
        if event["type"] == "http.response.start":
            status_out = event["status"]
            headers_out = event.get("headers", [])
        elif event["type"] == "http.response.body":
            body_parts.append(event.get("body", b""))

    await _mcp_handler(scope, receive, _capture)

    raw = b"".join(body_parts).decode("utf-8", errors="replace")
    out = []
    for line in raw.splitlines():
        if line.startswith("data:"):
            try:
                obj = json.loads(line[5:].strip())
                for t in obj.get("result", {}).get("tools", []):
                    t.pop("outputSchema", None)
                line = "data: " + json.dumps(obj)
            except Exception:
                pass
        out.append(line)
    new_body = "\n".join(out).encode("utf-8")

    await send({"type": "http.response.start", "status": status_out or 200, "headers": headers_out})
    await send({"type": "http.response.body", "body": new_body})


def _extract_meta(r: dict) -> dict:
    """mem0 double-wraps metadata as {metadata: {category: ...}} — unwrap both forms."""
    raw = r.get("metadata") or {}
    # mem0 pgvector provider wraps: payload.metadata = {"metadata": {"category": "..."}}
    inner = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else None
    return inner if inner is not None else raw


def _memory_dict(r: dict) -> dict:
    """Normalize a mem0 result to a consistent API shape."""
    meta = _extract_meta(r)
    return {
        "id": r.get("id", ""),
        "memory": r.get("memory", r.get("text", "")),
        "agent_id": r.get("agent_id", ""),
        "category": meta.get("category", ""),
        "tags": meta.get("tags") or [],
        "created_at": r.get("created_at", ""),
        "updated_at": r.get("updated_at", ""),
        "score": r.get("score"),
        "state": r.get("state") or meta.get("state") or "active",
        "source": r.get("source") or meta.get("source") or "",
        "review_reason": r.get("review_reason") or meta.get("review_reason") or "",
        "project": r.get("project") or meta.get("project") or "",
        "kind": r.get("kind") or meta.get("kind") or "",
    }


# ---------------------------------------------------------------------------
# ASGI app
# ---------------------------------------------------------------------------

async def app(scope, receive, send):
    if scope["type"] == "lifespan":
        if settings.redis_url:
            # Start at startup so jobs left in Redis resume without waiting for a new add_memory.
            from queue_worker import ensure_worker
            ensure_worker()
        await _starlette_app(scope, receive, send)
        return

    if scope["type"] != "http":
        return

    path = scope.get("path", "")
    method = scope.get("method", "GET")
    headers = dict(scope.get("headers", []))

    # Health — no auth
    if path == "/health":
        await _send_response(send, *_json_response({"status": "ok", "service": "claude-memory"}))
        return

    # Prometheus metrics — no auth
    if path == "/metrics" and method == "GET":
        st = await asyncio.to_thread(mem_store.stats)
        categories, agents, tags = st["by_category"], st["by_agent"], st["by_tag"]
        lines = [
            "# HELP remnant_memories_total Total number of memories stored",
            "# TYPE remnant_memories_total gauge",
        ]
        for cat, count in sorted(categories.items()):
            lines.append(f'remnant_memories_total{{category="{cat}"}} {count}')
        lines += [
            "# HELP remnant_memories_by_agent Total memories per agent",
            "# TYPE remnant_memories_by_agent gauge",
        ]
        for agent, count in sorted(agents.items()):
            lines.append(f'remnant_memories_by_agent{{agent_id="{agent}"}} {count}')
        if tags:
            lines += [
                "# HELP remnant_memories_by_tag Total memories per tag",
                "# TYPE remnant_memories_by_tag gauge",
            ]
            for tag, count in sorted(tags.items()):
                lines.append(f'remnant_memories_by_tag{{tag="{tag}"}} {count}')
        if settings.redis_url:
            try:
                from queue_worker import queue_metrics
                q = await queue_metrics()
                lines += [
                    "# HELP remnant_queue_depth Jobs not yet written (pending + not yet delivered)",
                    "# TYPE remnant_queue_depth gauge",
                    f"remnant_queue_depth {q['depth']}",
                    "# HELP remnant_queue_pending Jobs delivered to a worker but not acknowledged",
                    "# TYPE remnant_queue_pending gauge",
                    f"remnant_queue_pending {q['pending']}",
                    "# HELP remnant_queue_dead Jobs in the dead-letter stream",
                    "# TYPE remnant_queue_dead gauge",
                    f"remnant_queue_dead {q['dead']}",
                    "# HELP remnant_queue_worker_up 1 when the queue worker loop ran within the last minute",
                    "# TYPE remnant_queue_worker_up gauge",
                    f"remnant_queue_worker_up {q['worker_up']}",
                    "# HELP remnant_queue_metrics_error 1 when queue state could not be read (backlog unknown)",
                    "# TYPE remnant_queue_metrics_error gauge",
                    f"remnant_queue_metrics_error {q['error']}",
                ]
            except Exception:
                pass
        lines.append("")
        body = "\n".join(lines).encode()
        await _send_response(send, 200, [[b"content-type", b"text/plain; version=0.0.4"]], body)
        return

    # All /api/* routes require bearer auth
    if path.startswith("/api/"):
        if not _check_auth(headers):
            await _send_response(send, 401, [[b"content-type", b"text/plain"]], b"Unauthorized")
            return

        agent_id = _get_agent_id(headers)
        qs = _parse_qs(scope.get("query_string", b"").decode())

        # GET /api/stats
        if path == "/api/stats" and method == "GET":
            st = await asyncio.to_thread(mem_store.stats)
            await _send_response(send, *_json_response(st))
            return

        # GET /api/tag-rules
        if path == "/api/tag-rules" and method == "GET":
            rules = await asyncio.to_thread(mem_store.list_tag_rules)
            grouped: dict = {}
            for r in rules:
                tag = r["tag"]
                if tag not in grouped:
                    grouped[tag] = {"tag": tag, "keywords": [], "source": r["source"]}
                grouped[tag]["keywords"].append(r["keyword"])
            await _send_response(send, *_json_response(list(grouped.values())))
            return

        # POST /api/tag-rules
        if path == "/api/tag-rules" and method == "POST":
            raw = await _read_body(receive)
            try:
                body_data = json.loads(raw)
                tag = body_data.get("tag", "").strip()
                keyword = body_data.get("keyword", "").strip()
            except Exception:
                await _send_response(send, 400, [[b"content-type", b"text/plain"]], b"Bad Request")
                return
            if not tag or not keyword:
                await _send_response(send, 400, [[b"content-type", b"text/plain"]], b"tag and keyword required")
                return
            ok = await asyncio.to_thread(mem_store.add_tag_rule, tag, keyword)
            await _send_response(send, *_json_response({"ok": ok}))
            return

        # DELETE /api/tag-rules/{tag}/{keyword}
        if path.startswith("/api/tag-rules/") and method == "DELETE":
            rest = path[len("/api/tag-rules/"):]
            if "/" in rest:
                tag_part, kw_part = rest.split("/", 1)
                ok = await asyncio.to_thread(
                    mem_store.delete_tag_rule,
                    urllib.parse.unquote(tag_part),
                    urllib.parse.unquote(kw_part),
                )
                await _send_response(send, *_json_response({"ok": ok}))
                return

        # GET /api/jobs
        if path == "/api/jobs" and method == "GET":
            jobs = await asyncio.to_thread(mem_store.list_job_runs)
            await _send_response(send, *_json_response(jobs))
            return

        # GET /api/jobs/{id}
        if path.startswith("/api/jobs/") and method == "GET":
            run_id = path[len("/api/jobs/"):]
            job = await asyncio.to_thread(mem_store.get_job_run, run_id)
            if job is None:
                await _send_response(send, 404, [[b"content-type", b"text/plain"]], b"Not Found")
                return
            await _send_response(send, *_json_response(job))
            return

        # GET /api/memories/export — download all memories as JSON
        if path == "/api/memories/export" and method == "GET":
            all_memories = await asyncio.to_thread(mem_store.list_rows)
            body = json.dumps([_memory_dict(r) for r in all_memories], default=str, indent=2).encode()
            await _send_response(send, 200, [
                [b"content-type", b"application/json"],
                [b"content-disposition", b"attachment; filename=\"memories.json\""],
            ], body)
            return

        # POST /api/memories/import — bulk import from JSON array
        if path == "/api/memories/import" and method == "POST":
            raw = await _read_body(receive)
            try:
                items = json.loads(raw)
                if not isinstance(items, list):
                    raise ValueError("expected JSON array")
            except Exception as e:
                await _send_response(send, 400, [[b"content-type", b"text/plain"]], str(e).encode())
                return
            imported = skipped = 0
            for item in items:
                content = item.get("memory") or item.get("text") or item.get("content", "")
                src_agent = item.get("agent_id", "") or agent_id
                if not content.strip():
                    continue
                if item.get("kind") == "checkpoint":
                    skipped += 1          # session checkpoints are not portable memories
                    continue
                # keep the lifecycle: an exported candidate/rejected row must not come back active
                state = item.get("state") if item.get("state") in mem_store.VALID_STATES else ""
                await asyncio.to_thread(mem_store.add, content.strip(), agent_id=src_agent, source="import",
                                        project=str(item.get("project") or ""), state=state)
                imported += 1
            await _send_response(send, *_json_response({"imported": imported, "skipped_checkpoints": skipped}))
            return

        # GET /api/memories — list / search
        if path == "/api/memories" and method == "GET":
            q = qs.get("q", "").strip()
            sort = qs.get("sort", "newest")  # newest | oldest | relevance
            filter_agent = qs.get("agent_id", "").strip()
            filter_category = qs.get("category", "").strip()
            filter_state = qs.get("state", "").strip()

            if q:
                # the UI browses everything (all states, checkpoints) unless a state filter is set
                states = [filter_state] if filter_state in mem_store.VALID_STATES else list(mem_store.VALID_STATES)
                results = await asyncio.to_thread(mem_store.hybrid_search, q, limit=200,
                                                  include_checkpoints=True, states=states)
                if sort in ("newest", "oldest"):
                    reverse = sort == "newest"
                    results.sort(key=lambda r: r.get("created_at", ""), reverse=reverse)
            else:
                results = await asyncio.to_thread(mem_store.list_rows, filter_agent, filter_state)
                reverse = sort != "oldest"
                results.sort(key=lambda r: r.get("created_at", ""), reverse=reverse)

            if filter_category:
                results = [r for r in results if _extract_meta(r).get("category", "") == filter_category]

            await _send_response(send, *_json_response([_memory_dict(r) for r in results]))
            return

        # POST /api/memories — add a memory
        if path == "/api/memories" and method == "POST":
            raw = await _read_body(receive)
            # Support both form-encoded and JSON body
            try:
                body_data = json.loads(raw)
                content = body_data.get("content", "").strip()
                src_agent = body_data.get("agent_id", "") or agent_id
            except Exception:
                params = dict(p.split("=", 1) for p in raw.decode().split("&") if "=" in p)
                content = urllib.parse.unquote_plus(params.get("content", "")).strip()
                src_agent = agent_id
            if content:
                if settings.redis_url:
                    from queue_worker import enqueue
                    await enqueue(content, src_agent, settings.mem0_infer, "", [], source="ui")
                else:
                    await asyncio.to_thread(mem_store.add, content, agent_id=src_agent, source="ui")
            await _send_response(send, *_json_response({"ok": True}))
            return

        # Routes with memory ID suffix
        if path.startswith("/api/memories/"):
            memory_id = path[len("/api/memories/"):]

            # POST /api/memories/{id}/review — {"action": "promote"|"reject", "note": "..."}
            if method == "POST" and memory_id.endswith("/review"):
                real_id = memory_id[: -len("/review")]
                try:
                    body = json.loads(await _read_body(receive) or b"{}")
                except ValueError:
                    body = {}
                result = await asyncio.to_thread(mem_store.review, real_id, str(body.get("action", "")),
                                                 _get_agent_id(headers) or "user", str(body.get("note", "")))
                status = 200 if result.get("ok") else (404 if result.get("error") == "not found"
                                                       else 409 if result.get("error") == "conflict" else 400)
                await _send_response(send, *_json_response(result, status))
                return

            # PUT /api/memories/{id} — edit content
            if method == "PUT":
                raw = await _read_body(receive)
                try:
                    body_data = json.loads(raw)
                    content = body_data.get("memory", body_data.get("content", "")).strip()
                except Exception:
                    params = dict(p.split("=", 1) for p in raw.decode().split("&") if "=" in p)
                    content = urllib.parse.unquote_plus(params.get("content", "")).strip()
                if content:
                    await asyncio.to_thread(mem_store.update, memory_id, content)
                await _send_response(send, *_json_response({"ok": True}))
                return

            # PATCH /api/memories/{id}/tags — update tags only
            if method == "PATCH" and memory_id.endswith("/tags"):
                real_id = memory_id[: -len("/tags")]
                raw = await _read_body(receive)
                try:
                    tags = json.loads(raw).get("tags", [])
                    if not isinstance(tags, list):
                        raise ValueError
                    tags = [str(t).strip() for t in tags if str(t).strip()]
                except Exception:
                    await _send_response(send, 400, [[b"content-type", b"text/plain"]], b"Bad Request")
                    return
                ok = await asyncio.to_thread(mem_store.update_tags, real_id, tags)
                await _send_response(send, *_json_response({"ok": ok}))
                return

            # DELETE /api/memories/{id}
            if method == "DELETE":
                await asyncio.to_thread(mem_store.delete, memory_id)
                await _send_response(send, *_json_response({"ok": True}))
                return

        await _send_response(send, 404, [[b"content-type", b"text/plain"]], b"Not Found")
        return

    # /mcp — MCP protocol (bearer auth required)
    if not _check_auth(headers):
        await _send_response(send, 401, [[b"content-type", b"text/plain"]], b"Unauthorized")
        return

    # Read body once so we can inspect method and replay it to the handler
    raw = await _read_body(receive)
    try:
        payload = json.loads(raw)
        method = payload.get("method")

        if method == "initialize":
            # In stateless_http=True mode, ServerSession starts as Initialized and
            # ClientRequest validation rejects "initialize" (INVALID_PARAMS).
            # Handle it here directly and return a valid capabilities response.
            init_opts = mcp._mcp_server.create_initialization_options()
            protocol_version = (payload.get("params") or {}).get(
                "protocolVersion", "2024-11-05"
            )
            result = {
                "protocolVersion": protocol_version,
                "capabilities": init_opts.capabilities.model_dump(exclude_none=True),
                "serverInfo": {
                    "name": init_opts.server_name,
                    "version": init_opts.server_version or "0.1.0",
                },
            }
            if init_opts.instructions:
                result["instructions"] = init_opts.instructions
            sse_event = f"data: {json.dumps({'jsonrpc':'2.0','id':payload.get('id'),'result':result})}\n\n"
            await send({"type": "http.response.start", "status": 200, "headers": [
                [b"content-type", b"text/event-stream"],
                [b"cache-control", b"no-cache, no-transform"],
            ]})
            await send({"type": "http.response.body", "body": sse_event.encode("utf-8")})
            return

        if method == "tools/list":
            await _mcp_handler_strip_output_schema(scope, _make_receive(raw, receive), send)
            return
    except Exception:
        pass
    await _mcp_handler(scope, _make_receive(raw, receive), send)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=settings.app_port)
