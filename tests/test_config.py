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
