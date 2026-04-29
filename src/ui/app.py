import sys
from pathlib import Path

# Allow importing memory + config from parent src/ directory
sys.path.insert(0, str(Path(__file__).parent.parent))

import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import memory as mem_store
from config import settings

app = FastAPI(title="Claude Memory UI")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.get("/health")
async def health():
    return {"status": "ok", "service": "claude-memory-ui"}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    memories = mem_store.get_all()
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "memories": memories, "total": len(memories)},
    )


@app.get("/memories", response_class=HTMLResponse)
async def list_memories(request: Request, q: str = ""):
    if q.strip():
        memories = mem_store.search(q.strip(), limit=50)
    else:
        memories = mem_store.get_all()
    return templates.TemplateResponse(
        "partials/memory_rows.html",
        {"request": request, "memories": memories, "query": q},
    )


@app.post("/memories", response_class=HTMLResponse)
async def add_memory(request: Request, content: str = Form(...)):
    content = content.strip()
    if not content:
        return HTMLResponse("")
    mem_store.add(content)
    memories = mem_store.get_all()
    total = len(memories)
    rows_html = templates.TemplateResponse(
        "partials/memory_rows.html",
        {"request": request, "memories": memories, "query": ""},
    ).body.decode()
    oob = f'<span id="total-count" hx-swap-oob="true">{total}</span>'
    return HTMLResponse(rows_html + oob)


@app.delete("/memories/{memory_id}", response_class=HTMLResponse)
async def delete_memory(memory_id: str):
    mem_store.delete(memory_id)
    return HTMLResponse("")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8081)
