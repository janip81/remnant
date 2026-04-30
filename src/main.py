import json
import logging
import urllib.parse

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

import memory as mem_store
from config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP(
    "claude-memory",
    stateless_http=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


# ---------------------------------------------------------------------------
# MCP tools — called by Claude Code / Cursor via MCP protocol
# ---------------------------------------------------------------------------

@mcp.tool()
async def add_memory(content: str, agent_id: str = "", infer: bool = True, category: str = "") -> str:
    """Store a fact or observation in memory. category: project|feedback|reference|user|session|incident. Set infer=False to store as-is."""
    if category and category not in mem_store.VALID_CATEGORIES:
        return f"Invalid category '{category}'. Valid: {', '.join(sorted(mem_store.VALID_CATEGORIES) - {''})}"
    result = mem_store.add(content, agent_id=agent_id, infer=infer, category=category)
    return json.dumps(result)


@mcp.tool()
async def search_memory(query: str, limit: int = 5) -> str:
    """Search memory semantically. Returns top matching facts."""
    results = mem_store.search(query, limit=limit)
    if not results:
        return "No memories found."
    lines = []
    for r in results:
        score = r.get("score", "")
        text = r.get("memory", r.get("text", str(r)))
        lines.append(f"[{score:.3f}] {text}" if score else text)
    return "\n".join(lines)


@mcp.tool()
async def get_all_memories() -> str:
    """List all stored memories."""
    results = mem_store.get_all()
    if not results:
        return "No memories stored."
    lines = [r.get("memory", r.get("text", str(r))) for r in results]
    return "\n".join(f"{i+1}. {m}" for i, m in enumerate(lines))


@mcp.tool()
async def delete_memory(memory_id: str) -> str:
    """Delete a memory by ID."""
    result = mem_store.delete(memory_id)
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
    return auth.startswith("Bearer ") and auth[7:] == settings.bearer_token


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


def _memory_dict(r: dict) -> dict:
    """Normalize a mem0 result to a consistent API shape."""
    metadata = r.get("metadata") or {}
    return {
        "id": r.get("id", ""),
        "memory": r.get("memory", r.get("text", "")),
        "agent_id": r.get("agent_id", ""),
        "category": metadata.get("category", ""),
        "created_at": r.get("created_at", ""),
        "updated_at": r.get("updated_at", ""),
        "score": r.get("score"),
    }


# ---------------------------------------------------------------------------
# ASGI app
# ---------------------------------------------------------------------------

async def app(scope, receive, send):
    if scope["type"] == "lifespan":
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

    # All /api/* routes require bearer auth
    if path.startswith("/api/"):
        if not _check_auth(headers):
            await _send_response(send, 401, [[b"content-type", b"text/plain"]], b"Unauthorized")
            return

        agent_id = _get_agent_id(headers)
        qs = _parse_qs(scope.get("query_string", b"").decode())

        # GET /api/stats
        if path == "/api/stats" and method == "GET":
            all_memories = mem_store.get_all()
            total = len(all_memories)
            dates = [r.get("created_at", "") for r in all_memories if r.get("created_at")]
            dates_sorted = sorted(dates)
            agents: dict = {}
            categories: dict = {}
            for r in all_memories:
                a = r.get("agent_id") or "unknown"
                agents[a] = agents.get(a, 0) + 1
                c = (r.get("metadata") or {}).get("category", "") or "uncategorized"
                categories[c] = categories.get(c, 0) + 1
            await _send_response(send, *_json_response({
                "total": total,
                "first_added": dates_sorted[0] if dates_sorted else None,
                "last_added": dates_sorted[-1] if dates_sorted else None,
                "by_agent": agents,
                "by_category": categories,
            }))
            return

        # GET /api/memories/export — download all memories as JSON
        if path == "/api/memories/export" and method == "GET":
            all_memories = mem_store.get_all()
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
            imported = 0
            for item in items:
                content = item.get("memory") or item.get("text") or item.get("content", "")
                src_agent = item.get("agent_id", "") or agent_id
                if content.strip():
                    mem_store.add(content.strip(), agent_id=src_agent)
                    imported += 1
            await _send_response(send, *_json_response({"imported": imported}))
            return

        # GET /api/memories — list / search
        if path == "/api/memories" and method == "GET":
            q = qs.get("q", "").strip()
            sort = qs.get("sort", "newest")  # newest | oldest | relevance
            filter_agent = qs.get("agent_id", "").strip()
            filter_category = qs.get("category", "").strip()

            if q:
                results = mem_store.search(q, limit=200)
                if sort in ("newest", "oldest"):
                    reverse = sort == "newest"
                    results.sort(key=lambda r: r.get("created_at", ""), reverse=reverse)
            else:
                results = mem_store.get_all(agent_id=filter_agent)
                reverse = sort != "oldest"
                results.sort(key=lambda r: r.get("created_at", ""), reverse=reverse)

            if filter_category:
                results = [r for r in results if (r.get("metadata") or {}).get("category", "") == filter_category]

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
                mem_store.add(content, agent_id=src_agent)
            await _send_response(send, *_json_response({"ok": True}))
            return

        # Routes with memory ID suffix
        if path.startswith("/api/memories/"):
            memory_id = path[len("/api/memories/"):]

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
                    mem_store.update(memory_id, content)
                await _send_response(send, *_json_response({"ok": True}))
                return

            # DELETE /api/memories/{id}
            if method == "DELETE":
                mem_store.delete(memory_id)
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
