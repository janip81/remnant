"""
UI backend — static file server + transparent proxy to MCP /api/*.
No mem0 or DB dependency; only httpx for the proxy.
"""
import os
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import Response, FileResponse
from fastapi.staticfiles import StaticFiles

MCP_URL = os.environ.get("MCP_INTERNAL_URL", "http://claude-memory:8080")
BEARER_TOKEN = os.environ.get("BEARER_TOKEN", "")
UI_PORT = int(os.environ.get("UI_PORT", "8081"))
# 127.0.0.1 when an auth proxy sidecar fronts the UI, so the pod IP cannot bypass it
UI_HOST = os.environ.get("UI_HOST", "0.0.0.0")
DIST = Path(__file__).parent / "dist"

app = FastAPI(title="claude-memory-ui")

_HEADERS = {"Authorization": f"Bearer {BEARER_TOKEN}"}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "claude-memory-ui"}


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy(path: str, request: Request) -> Response:
    url = f"{MCP_URL}/api/{path}"
    qs = request.url.query
    if qs:
        url = f"{url}?{qs}"
    body = await request.body()
    content_type = request.headers.get("content-type", "")
    proxy_headers = dict(_HEADERS)
    if content_type:
        proxy_headers["content-type"] = content_type
    async with httpx.AsyncClient() as client:
        resp = await client.request(
            method=request.method,
            url=url,
            headers=proxy_headers,
            content=body,
            timeout=30,
        )
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers={"content-type": resp.headers.get("content-type", "application/json"),
                 "content-disposition": resp.headers.get("content-disposition", "")},
    )


@app.middleware("http")
async def no_cache_for_html(request: Request, call_next):
    """index.html must be revalidated on every load, or browsers keep an old copy that
    points at the previous bundle after a deploy. Hashed assets/* stay cacheable."""
    response = await call_next(request)
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Cache-Control"] = "no-cache"
    return response


# Serve React SPA — must come after API routes
if DIST.exists():
    app.mount("/", StaticFiles(directory=str(DIST), html=True), name="static")
else:
    @app.get("/{full_path:path}")
    async def spa_fallback():
        return {"error": "UI build not found — run: cd ui && npm run build"}


if __name__ == "__main__":
    uvicorn.run(app, host=UI_HOST, port=UI_PORT)
