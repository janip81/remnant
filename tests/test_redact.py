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


# --- Cases from the Codex review of PR #4 ---------------------------------

@pytest.mark.parametrize("text, leaked", [
    ('password="correct horse battery staple"', "horse battery staple"),
    ("password=abcde", "abcde"),
    ("Authorization: bearer " + "b" * 40, "b" * 40),
    ("-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQ\n"
     "-----END OPENSSH PRIVATE KEY-----", "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQ"),
    ("-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAtruncatedkeybody", "MIIEowIBAAKCAQEAtruncatedkeybody"),
    ("admin-password='CHOOSE-A-GOOD-ONE'", "CHOOSE-A-GOOD-ONE"),
    ("totpSecret: JBSWY3DPEHPK3PXP", "JBSWY3DPEHPK3PXP"),
    ("--set discordBot.token=MTIzNDU2Nzg5MDEy.abc", "MTIzNDU2Nzg5MDEy"),
    ("chpasswd: root:hunter22", "root:hunter22"),
])
def test_review_secrets_are_masked(text, leaked):
    out = redact(text)
    assert leaked not in out, out
    assert MASK in out


@pytest.mark.parametrize("text", [
    "tokenizer=sentence-transformers",
    "token_limit=100000",
    "max_tokens=4096",
    "context budget: token_budget=8000 per prompt",
    "imagePullSecret: ghcr-creds",
    "the token: we rotate it monthly",
])
def test_review_settings_are_kept(text):
    assert redact(text) == text


def test_bearer_keeps_trailing_full_stop():
    assert redact("Authorization: Bearer " + "a" * 40 + ". Next sentence") == \
        "Authorization: Bearer [REDACTED]. Next sentence"


@pytest.mark.parametrize("payload", [
    "a." * 50_000, "a-" * 50_000, "password" * 20_000, "a=" * 50_000,
    "x:" * 50_000, "Bearer " * 20_000, "://" * 30_000, "-" * 100_000,
])
def test_linear_time_on_pathological_input(payload):
    import time
    t = time.perf_counter()
    redact(payload)
    assert time.perf_counter() - t < 1.0


@pytest.mark.parametrize("text", [
    'password="correct horse battery staple"',
    "Authorization: bearer " + "b" * 40,
    "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA",
    "postgresql://u:pw123456@h/db",
    "--token=abcdefghij",
    "totpSecret: JBSWY3DPEHPK3PXP",
    f"REMNANT_TOKEN={FAKE_HEX}",
])
def test_idempotent_all_patterns(text):
    once = redact(text)
    assert redact(once) == once


@pytest.mark.parametrize("text", [
    "SmartConnect(host=host, user=user, pwd=password)",
    "Password: blank",
    'admin-password="<from vault kubernetes/data/x>"',
    "password: ****",
    "password=your-password-here",
])
def test_placeholders_are_kept(text):
    assert redact(text) == text


# --- Cases from the Codex review of hardening/p0-followup -----------------

@pytest.mark.parametrize("text, leaked", [
    ('password="' + "p" * 201 + '"', "p" * 30),
    ("password=" + "q" * 250, "q" * 30),
    (r'password="abc\"defghij"', "defghij"),
    ("https://user:" + "s" * 300 + "@host/x", "s" * 30),
    ("password=\"abc defghij", "abc"),
    ("token: \"Xm5Iabcdefghijkl unterminated", "Xm5Iabcdefghijkl"),
])
def test_long_and_escaped_values_fully_masked(text, leaked):
    out = redact(text)
    assert leaked not in out, out


def test_linear_with_many_assignments():
    import time
    t = time.perf_counter()
    redact("password=hunter2x " * 40_000)
    assert time.perf_counter() - t < 1.0
