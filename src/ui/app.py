"""
Web UI for claude-memory.
Calls /api/memories on the MCP server internally — no mem0/DB dependency in this pod.
"""
import json
import os
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

MCP_URL = os.environ.get("MCP_INTERNAL_URL", "http://claude-memory:8080")
BEARER_TOKEN = os.environ.get("BEARER_TOKEN", "")
UI_PORT = int(os.environ.get("UI_PORT", "8081"))

app = FastAPI(title="Claude Memory UI")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

_HEADERS = {"Authorization": f"Bearer {BEARER_TOKEN}"}


def _get(q: str = "") -> list[dict]:
    params = {"q": q} if q else {}
    r = httpx.get(f"{MCP_URL}/api/memories", headers=_HEADERS, params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def _add(content: str) -> None:
    httpx.post(f"{MCP_URL}/api/memories", headers=_HEADERS,
               content=f"content={httpx.URL('', params={'content': content}).params}",
               timeout=30)


def _delete(memory_id: str) -> None:
    httpx.delete(f"{MCP_URL}/api/memories/{memory_id}", headers=_HEADERS, timeout=10)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "claude-memory-ui"}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    memories = _get()
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "memories": memories, "total": len(memories)},
    )


@app.get("/memories", response_class=HTMLResponse)
async def list_memories(request: Request, q: str = ""):
    memories = _get(q.strip())
    return templates.TemplateResponse(
        "partials/memory_rows.html",
        {"request": request, "memories": memories, "query": q},
    )


@app.post("/memories", response_class=HTMLResponse)
async def add_memory_route(request: Request, content: str = Form(...)):
    content = content.strip()
    if content:
        httpx.post(f"{MCP_URL}/api/memories", headers=_HEADERS,
                   data={"content": content}, timeout=30)
    memories = _get()
    total = len(memories)
    rows_html = templates.TemplateResponse(
        "partials/memory_rows.html",
        {"request": request, "memories": memories, "query": ""},
    ).body.decode()
    oob = f'<span id="total-count" hx-swap-oob="true">{total}</span>'
    return HTMLResponse(rows_html + oob)


@app.delete("/memories/{memory_id}", response_class=HTMLResponse)
async def delete_memory_route(memory_id: str):
    httpx.delete(f"{MCP_URL}/api/memories/{memory_id}", headers=_HEADERS, timeout=10)
    return HTMLResponse("")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=UI_PORT)
