import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from redact import MASK, redact  # noqa: E402

FAKE_HEX = "4b35" + "a1" * 30
FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlLXZhbHVl"


@pytest.mark.parametrize("text, leaked", [
    (f'curl -H "Authorization: Bearer {FAKE_HEX}" https://x/mcp', FAKE_HEX),
    (f"REMNANT_TOKEN={FAKE_HEX}", FAKE_HEX),
    (f'TOKEN="{FAKE_HEX}" ./run.sh', FAKE_HEX),
    (f'{{"REMNANT_TOKEN": "{FAKE_HEX}"}}', FAKE_HEX),
    ("export GH_TOKEN=ghp_abcdefghijklmnop1234", "ghp_abcdefghijklmnop1234"),
    ("git push https://ghp_Abc123Xyz9QwErTy@github.com", "ghp_Abc123Xyz9QwErTy"),
    (f"HA token = {FAKE_JWT}", FAKE_JWT),
    ("password: hunter2hunter2", "hunter2hunter2"),
    ("db_password='S3cr3t-Value'", "S3cr3t-Value"),
    ("psql postgresql://remnant:pa55w0rdXYZ@db:5432/remnant", "pa55w0rdXYZ"),
    ("vault login --token=s.abcdefghij123456", "s.abcdefghij123456"),
    ("ANTHROPIC_API_KEY=sk-ant-api03-abcdefghijklmnopqrstuv", "sk-ant-api03-abcdefghijklmnopqrstuv"),
])
def test_secrets_are_masked(text, leaked):
    out = redact(text)
    assert leaked not in out
    assert MASK in out


@pytest.mark.parametrize("text", [
    "kubectl get secret argocd-initial-admin-secret -n argocd",
    'BEARER_TOKEN: "<path:kubernetes/data/prod-k8s/claude-memory#BEARER_TOKEN>"',
    'curl -H "Authorization: Bearer $REMNANT_TOKEN" https://x',
    "TOKEN=${REMNANT_TOKEN}",
    "token count went from 5 to 12",
    "Use a bearer token from Vault for the MCP endpoint",
    "password rotation is planned for next week",
])
def test_non_secrets_are_kept(text):
    assert redact(text) == text


def test_idempotent():
    once = redact(f"REMNANT_TOKEN={FAKE_HEX}")
    assert redact(once) == once


@pytest.mark.parametrize("text", [
    "    existingSecret: zomboid-rcon",
    '  iloSecretRef: "ilo-credentials"',
    "--set zomboid.existingSecret=zomboid-rcon",
    "secretName: remnant-auth",
    "      secretKeyRef:",
    "PasswordAuthentication=without-password",
    "argocd.argoproj.io/secret-type=cluster",
])
def test_secret_references_are_kept(text):
    assert redact(text) == text
