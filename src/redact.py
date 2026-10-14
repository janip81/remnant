"""Strip credentials from memory text before it is stored.

Applied server-side in memory.add/update and before queueing, so every client
(hooks, Claude, Codex, UI, import, dedup job) is covered. Patterns aim at real
secret shapes and credential key=value assignments; references such as AVP
`<path:...>` placeholders, shell/template variables (`$TOKEN`, `${TOKEN}`,
`{{ .Values.x }}`) and secret *names* (`existingSecret`, `*Ref`) are kept.

All patterns are linear: keys only match at the start of an identifier run
(lookbehind), the reference check uses offsets, and every repetition consumes
each character through exactly one branch.
"""
import re

MASK = "[REDACTED]"

# Value prefixes that are references, not secrets.
_REF = r"(?!<path:|\$|\{\{|\[REDACTED\])"
_REF_PREFIX = re.compile(r"[\"']?(?:<path:|\$|\{\{|\[REDACTED\])")

# Key normalisation: lowercase, drop separators. A key is a credential when its
# last dotted segment ends with one of these words (REMNANT_TOKEN, db-password,
# totpSecret, discordBot.token) ...
_CREDENTIAL_SUFFIXES = (
    "password", "passwd", "passphrase", "pwd", "secret", "token", "apikey",
    "accesskey", "secretkey", "privatekey", "clientsecret", "credential",
    "credentials",
)
# ... unless it names a secret object or is a known non-secret setting.
_KEEP_SUFFIXES = ("ref", "name", "names", "type", "path", "url", "file", "id", "ids", "pullsecret")
_KEEP_PREFIXES = ("existingsecret",)
# Short values are allowed for password-like keys (password=abcde); other
# credential keys need a longer value so prose like "token: 5" stays intact.
_SHORT_OK = ("password", "passwd", "passphrase", "pwd")


def _norm(key: str) -> str:
    last = key.rsplit(".", 1)[-1]
    return re.sub(r"[-_]", "", last).lower()


def _is_credential_key(key: str) -> bool:
    k = _norm(key)
    if k.startswith(_KEEP_PREFIXES) or k.endswith(_KEEP_SUFFIXES):
        return False
    return k.endswith(_CREDENTIAL_SUFFIXES)


# Values that are placeholders or variable names, not secrets
# (pwd=password, Password: blank, <from vault>, ****, ...).
_PLACEHOLDER = re.compile(
    r"(?i)(?:<.*|\*+|\.+|x+|password|passwd|secret|token|blank|none|null|empty|"
    r"changeme|change-me|redacted|your[-_a-z]*|example|placeholder|password\)?|\.\.\..*)"
)


def _min_len(key: str) -> int:
    return 4 if _norm(key).endswith(_SHORT_OK) else 8


# key = value / key: value / "key": "value" / key='multi word value'
_ASSIGN = re.compile(
    r"(?<![A-Za-z0-9_.\-])([A-Za-z][A-Za-z0-9_.\-]{0,63})"   # key, start of run only
    r"([\"']?[ \t]*[:=][ \t]*)"                              # separator
    r"(?:\"((?:[^\"\\\n]|\\.)+)\"|'((?:[^'\\\n]|\\.)+)'|"  # quoted value (spaces, \" ok)
    r"([^\s\"'`,;)}\]]+)|"                                   # or bare value
    r"[\"']([^\n]+))"                                         # or unterminated quote: to end of line
    # Unbounded but linear: each character is consumed by exactly one branch.
)


def _assign_repl(m: re.Match) -> str:
    key, sep = m.group(1), m.group(2)
    value = next(g for g in m.groups()[2:] if g is not None)
    if not _is_credential_key(key) or len(value) < _min_len(key):
        return m.group(0)
    # References/already-masked values: look at the original text, because a
    # bare value stops before "]" and would see "[REDACTED" without it.
    if _REF_PREFIX.match(m.string, m.end(2)):  # offset match, no suffix copy
        return m.group(0)
    if _PLACEHOLDER.fullmatch(value):
        return m.group(0)
    if m.group(3) is not None or m.group(4) is not None:
        q = m.string[m.end(2)]
        return f"{key}{sep}{q}{MASK}{q}"
    if m.group(6) is not None:
        return f"{key}{sep}{m.string[m.end(2)]}{MASK}"
    return f"{key}{sep}{MASK}"


_PATTERNS: list[tuple[re.Pattern, object]] = [
    # PEM private keys (also truncated ones without an END line)
    (re.compile(r"(-----BEGIN [A-Z ]{0,40}PRIVATE KEY-----)[\r\n \t]*[A-Za-z0-9+/=][A-Za-z0-9+/=\r\n \t]*"),
     r"\1\n" + MASK + "\n"),
    # Authorization headers / bearer tokens (any case); never ends on a dot
    (re.compile(r"(?i)(\bbearer[ \t]+)" + _REF + r"[A-Za-z0-9._~+/\-]{15,}[A-Za-z0-9_~+/\-]=*"), r"\1" + MASK),
    # Well-known token formats
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{8,}"), MASK),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{16,}"), MASK),
    (re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_\-]{20,}"), MASK),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"), MASK),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), MASK),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}(?:\.[A-Za-z0-9_\-]+)?"), MASK),
    # Credentials embedded in URLs: scheme://user:pass@host
    (re.compile(r"(://[^\s:/@]{1,64}:)" + _REF + r"[^\s@/]+(@)"), r"\1" + MASK + r"\2"),
    # Credential key=value pairs
    (_ASSIGN, _assign_repl),
    # --password value / --token=value CLI flags
    (re.compile(r"(?i)(--(?:password|passwd|token|secret|api-key)(?:=|[ \t]+)[\"']?)" + _REF + r"[^\s\"']+"),
     r"\1" + MASK),
]


def redact(text: str) -> str:
    if not text:
        return text
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text
