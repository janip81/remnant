"""Strip credentials from memory text before it is stored.

Applied server-side in memory.add/update, so every client (hooks, Claude,
Codex, UI, import) is covered. Patterns aim at real secret shapes and
key=value assignments; references such as AVP `<path:...>` placeholders and
shell variables (`$TOKEN`, `${TOKEN}`, `$(cmd)`) are left alone.
"""
import re

MASK = "[REDACTED]"

# Values that are references, not secrets.
_NOT_A_SECRET = r"(?!<path:|\$|\[REDACTED\])"

def _keep_key(key: str) -> bool:
    """Keys whose value is a reference to a secret, not the secret itself."""
    k = key.lower().replace("-", "").replace("_", "")
    return k.endswith(("ref", "name", "names", "existingsecret", "tokenurl", "tokenpath",
                         "secrets", "secrettype", "authentication", "pullsecret")) \
        or k.startswith("existingsecret")


_PATTERNS: list[tuple[re.Pattern, object]] = [
    # Authorization headers / bearer tokens
    (re.compile(r"(\bBearer\s+)" + _NOT_A_SECRET + r"[A-Za-z0-9._~+/=\-]{16,}"), r"\1" + MASK),
    # Well-known token formats
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{8,}"), MASK),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{16,}"), MASK),
    (re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_\-]{20,}"), MASK),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"), MASK),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), MASK),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}(?:\.[A-Za-z0-9_\-]+)?"), MASK),
    # Credentials embedded in URLs: scheme://user:pass@host
    (re.compile(r"(://[^\s:/@]+:)" + _NOT_A_SECRET + r"[^\s@/]+(@)"), r"\1" + MASK + r"\2"),
    # key=value / key: value / "key": "value" for secret-looking keys
    # (keys naming a Secret object, e.g. existingSecret/secretName/*Ref, are kept: see _keep_key)
    (re.compile(
        r"(?i)(\b([A-Za-z0-9_.\-]*(?:token|passw(?:or)?d|passwd|secret|api[_-]?key|access[_-]?key|private[_-]?key)"
        r"[A-Za-z0-9_.\-]*)[\"']?\s*[:=]\s*[\"']?)" + _NOT_A_SECRET + r"([^\s\"'`,;)}\]]{6,})"),
     lambda m: m.group(0) if _keep_key(m.group(2)) else m.group(1) + MASK),
    # --password value / --token=value CLI flags
    (re.compile(r"(?i)(--(?:password|passwd|token|secret|api-key)[= ]\s*[\"']?)" + _NOT_A_SECRET + r"[^\s\"']+"),
     r"\1" + MASK),
]


def redact(text: str) -> str:
    if not text:
        return text
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text
