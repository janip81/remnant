import json
import logging

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

import memory as mem_store
from config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Disable FastMCP's built-in DNS-rebinding protection — we authenticate via
# Bearer token and run behind a gateway with TLS, so the extra host check
# would block requests from any non-localhost hostname.
mcp = FastMCP(
    "claude-memory",
    stateless_http=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


@mcp.tool()
async def add_memory(content: str) -> str:
    """Store a fact or observation in memory."""
    result = mem_store.add(content)
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


# Build the FastMCP Starlette app (used for lifespan / session manager init).
_starlette_app = mcp.streamable_http_app()
# Extract StreamableHTTPASGIApp directly to bypass Starlette's redirect_slashes
# behaviour, which caused POST /mcp → 307 on the cached-old-image node.
_mcp_handler = _starlette_app.router.routes[0].app

HEALTH_RESPONSE = json.dumps({"status": "ok", "service": "claude-memory"}).encode()


async def app(scope, receive, send):
    if scope["type"] == "lifespan":
        # Run the Starlette lifespan so the session manager task group is initialised.
        await _starlette_app(scope, receive, send)
        return

    if scope["type"] != "http":
        return

    path = scope.get("path", "")

    # Health — no auth required
    if path == "/health":
        await send({"type": "http.response.start", "status": 200,
                    "headers": [[b"content-type", b"application/json"]]})
        await send({"type": "http.response.body", "body": HEALTH_RESPONSE})
        return

    # Auth check for all other paths
    headers = dict(scope.get("headers", []))
    auth = headers.get(b"authorization", b"").decode()
    if not auth.startswith("Bearer ") or auth[7:] != settings.bearer_token:
        await send({"type": "http.response.start", "status": 401,
                    "headers": [[b"content-type", b"text/plain"]]})
        await send({"type": "http.response.body", "body": b"Unauthorized"})
        return

    # Dispatch directly to StreamableHTTPASGIApp, skipping Starlette routing.
    await _mcp_handler(scope, receive, send)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=settings.app_port)
