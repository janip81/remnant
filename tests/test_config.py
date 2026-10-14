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
