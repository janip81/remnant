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


def test_mcp_dns_rebinding_protection_follows_allowed_hosts():
    from config import Settings, mcp_transport_security
    base = dict(bearer_token="x" * 32)
    off = mcp_transport_security(Settings(**base))
    assert off.enable_dns_rebinding_protection is False                     # unchanged default
    on = mcp_transport_security(Settings(**base, mcp_allowed_hosts="remnant:8080, remnant-mcp.example.se",
                                         mcp_allowed_origins="https://ui.example.se"))
    assert on.enable_dns_rebinding_protection is True
    assert on.allowed_hosts[:2] == ["remnant:8080", "remnant-mcp.example.se"]
    assert "127.0.0.1:*" in on.allowed_hosts and on.allowed_origins == ["https://ui.example.se"]
