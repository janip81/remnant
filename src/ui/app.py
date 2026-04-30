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


# Serve React SPA — must come after API routes
if DIST.exists():
    app.mount("/", StaticFiles(directory=str(DIST), html=True), name="static")
else:
    @app.get("/{full_path:path}")
    async def spa_fallback():
        return {"error": "UI build not found — run: cd ui && npm run build"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=UI_PORT)
