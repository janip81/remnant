import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from config import Settings  # noqa: E402


@pytest.mark.parametrize("token", ["", "change-me", "short"])
def test_unsafe_tokens_refused(token):
    with pytest.raises(SystemExit):
        Settings(bearer_token=token).require_safe_token()


def test_real_token_accepted():
    Settings(bearer_token="a" * 64).require_safe_token()


@pytest.mark.parametrize("var", ["REMNANT_USER_ID", "MEM0_USER_ID"])
def test_user_id_env_names(monkeypatch, var):
    monkeypatch.delenv("REMNANT_USER_ID", raising=False)
    monkeypatch.delenv("MEM0_USER_ID", raising=False)
    monkeypatch.setenv(var, "jani")
    assert Settings(_env_file=None).mem0_user_id == "jani"


def test_remnant_name_wins(monkeypatch):
    monkeypatch.setenv("REMNANT_USER_ID", "new")
    monkeypatch.setenv("MEM0_USER_ID", "old")
    assert Settings(_env_file=None).mem0_user_id == "new"


def test_mcp_dns_rebinding_protection_on_by_default_loopback_only(monkeypatch):
    from config import Settings, mcp_transport_security
    for var in ("MCP_ALLOWED_HOSTS", "MCP_DNS_REBINDING_PROTECTION", "MCP_ALLOWED_ORIGINS"):
        monkeypatch.delenv(var, raising=False)
    sec = mcp_transport_security(Settings(_env_file=None, bearer_token="x" * 32))
    assert sec.enable_dns_rebinding_protection is True
    assert set(sec.allowed_hosts) == {"localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*", "[::1]", "[::1]:*"}


def test_mcp_dns_rebinding_protection_allowed_hosts_and_origins():
    from config import Settings, mcp_transport_security
    on = mcp_transport_security(Settings(_env_file=None, bearer_token="x" * 32,
                                         mcp_allowed_hosts="remnant:8080, remnant-mcp.example.se",
                                         mcp_allowed_origins="https://ui.example.se"))
    assert on.enable_dns_rebinding_protection is True
    assert on.allowed_hosts[:2] == ["remnant:8080", "remnant-mcp.example.se"]
    assert "127.0.0.1:*" in on.allowed_hosts and on.allowed_origins == ["https://ui.example.se"]


def test_mcp_dns_rebinding_protection_explicit_opt_out(monkeypatch):
    from config import Settings, mcp_transport_security
    monkeypatch.setenv("MCP_DNS_REBINDING_PROTECTION", "false")
    off = mcp_transport_security(Settings(_env_file=None, bearer_token="x" * 32))
    assert off.enable_dns_rebinding_protection is False


@pytest.mark.parametrize("host,ok", [("localhost:8080", True), ("localhost", True), ("[::1]:8080", True),
                                     ("remnant-mcp.example.se", True), ("evil.example:8080", False)])
def test_mcp_host_validation(host, ok):
    from config import Settings, mcp_transport_security
    from mcp.server.transport_security import TransportSecurityMiddleware
    sec = mcp_transport_security(Settings(_env_file=None, bearer_token="x" * 32,
                                          mcp_allowed_hosts="remnant-mcp.example.se"))
    assert TransportSecurityMiddleware(sec)._validate_host(host) is ok


def test_embed_url_defaults_to_ollama_base_url(monkeypatch):
    monkeypatch.delenv("OLLAMA_EMBED_BASE_URL", raising=False)
    s = Settings(_env_file=None, ollama_base_url="http://gpu-host:11434")
    assert s.embed_base_url == "http://gpu-host:11434"


def test_separate_embed_url(monkeypatch):
    monkeypatch.setenv("OLLAMA_EMBED_BASE_URL", "http://ollama-embed:11434")
    s = Settings(_env_file=None, ollama_base_url="http://gpu-host:11434")
    assert (s.embed_base_url, s.ollama_base_url) == ("http://ollama-embed:11434", "http://gpu-host:11434")
