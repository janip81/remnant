"""UI proxy: browser writes from another origin must not reach the MCP API."""
import importlib.util
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

_spec = importlib.util.spec_from_file_location("ui_app", Path(__file__).parent.parent / "src/ui/app.py")
ui_app = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ui_app)


@pytest.fixture
def client(monkeypatch):
    calls = []

    async def fake_request(self, method, url, headers, content, timeout):
        calls.append((method, url))
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    c = TestClient(ui_app.app, base_url="https://remnant.example.com")
    c.calls = calls
    return c


@pytest.mark.parametrize("headers", [
    {"origin": "https://evil.example.com"},
    {"origin": "https://other.remnant.example.com"},
    {"origin": "null"},
    {"sec-fetch-site": "same-site"},
    {"sec-fetch-site": "cross-site"},
])
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_cross_origin_writes_are_rejected(client, method, headers):
    r = client.request(method, "/api/memories", headers=headers, content=b'{"content":"x"}')
    assert r.status_code == 403
    assert client.calls == []


@pytest.mark.parametrize("headers", [
    {"origin": "https://remnant.example.com"},
    {"origin": "https://REMNANT.example.com", "sec-fetch-site": "same-origin"},
    {"sec-fetch-site": "same-origin"},
    {},  # non-browser client (curl): no Origin, no Sec-Fetch-Site
])
def test_same_origin_writes_are_forwarded(client, headers):
    r = client.post("/api/memories", headers=headers, json={"content": "x"})
    assert r.status_code == 200
    assert client.calls == [("POST", f"{ui_app.MCP_URL}/api/memories")]


def test_cross_origin_reads_still_pass(client):
    # GETs don't change state; a cross-origin page can't read the response (no CORS headers)
    r = client.get("/api/stats", headers={"origin": "https://evil.example.com"})
    assert r.status_code == 200
