import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

from config import settings

mcp = FastMCP("claude-memory", stateless_http=True)


@mcp.tool()
async def add_memory(content: str) -> str:
    """Store a fact or observation in memory."""
    return f"[stub] Would store: {content}"


@mcp.tool()
async def search_memory(query: str, limit: int = 5) -> str:
    """Search memory semantically. Returns top matching facts."""
    return f"[stub] Would search for: {query} (limit={limit})"


@mcp.tool()
async def get_all_memories() -> str:
    """List all stored memories."""
    return "[stub] Would return all memories"


@mcp.tool()
async def delete_memory(memory_id: str) -> str:
    """Delete a memory by ID."""
    return f"[stub] Would delete memory: {memory_id}"


class BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, token: str):
        super().__init__(app)
        self.token = token

    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or auth[7:] != self.token:
            return Response("Unauthorized", status_code=401)
        return await call_next(request)


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "claude-memory"})


app = Starlette(
    routes=[
        Route("/health", health),
        Mount("/mcp", app=mcp.streamable_http_app()),
    ],
    middleware=[
        Middleware(BearerAuthMiddleware, token=settings.bearer_token),
    ],
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=settings.app_port)
