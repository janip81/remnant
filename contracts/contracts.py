"""Draft validators and renderers for remnant.fact/1, remnant.handoff/1 and
remnant.incident/1 (see README.md). Plain dicts in, lists of problems out: the
records are meant to live in the existing JSONB payload, so no schema library or
new service is needed. Nothing here is wired into the server.
"""
from __future__ import annotations

from datetime import datetime

EVIDENCE_KINDS = {"tool_output", "file", "commit", "url", "user_statement", "agent_inference"}
VERIFICATIONS = {"unverified", "tool_verified", "user_confirmed"}
VERIFIED_BY = {"tool_verified": {"tool_output", "file", "commit"}, "user_confirmed": {"user_statement"}}
HANDOFF_STATES = {"open", "blocked", "done"}
HYPOTHESIS_STATES = {"untested", "supported", "refuted"}
TIMELINE_KINDS = {"observation", "hypothesis", "attempt", "outcome"}
ATTEMPT_RESULTS = {"failed", "partial", "worked"}


def _time(value) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _check_time(rec: dict, key: str, problems: list, required: bool = False):
    if key not in rec:
        if required:
            problems.append(f"{key} is required")
        return None
    t = _time(rec[key])
    if t is None or t.tzinfo is None:
        problems.append(f"{key} must be an ISO-8601 time with a timezone")
    return t


def check_evidence(items, problems: list, where: str = "evidence") -> set:
    kinds = set()
    if not isinstance(items, list):
        problems.append(f"{where} must be a list")
        return kinds
    for i, e in enumerate(items):
        if not isinstance(e, dict) or e.get("kind") not in EVIDENCE_KINDS or not str(e.get("ref", "")).strip():
            problems.append(f"{where}[{i}] needs kind in {sorted(EVIDENCE_KINDS)} and a non-empty ref")
            continue
        _check_time(e, "observed_at", problems)
        kinds.add(e["kind"])
    return kinds


def _check_verification(level: str, kinds: set, problems: list, where: str):
    if level not in VERIFICATIONS:
        problems.append(f"{where}: verification must be one of {sorted(VERIFICATIONS)}")
    elif level in VERIFIED_BY and not kinds & VERIFIED_BY[level]:
        problems.append(f"{where}: {level} needs evidence of kind {sorted(VERIFIED_BY[level])}")


def validate_fact(meta: dict) -> list[str]:
    """Temporal/evidence metadata of one memory. Every field is optional."""
    p: list[str] = []
    if "confidence" in meta:
        p.append("confidence is not accepted: use evidence + verification")
    _check_time(meta, "observed_at", p)
    start, end = _check_time(meta, "valid_from", p), _check_time(meta, "valid_until", p)
    if start and end and end < start:
        p.append("valid_until is before valid_from")
    scope = meta.get("scope", {})
    if not isinstance(scope, dict) or set(scope) - {"project", "environment"}:
        p.append("scope may only contain project and environment")
    if bool(meta.get("subject")) != bool(meta.get("property")):
        p.append("subject and property go together")
    kinds = check_evidence(meta.get("evidence", []), p)
    _check_verification(meta.get("verification", "unverified"), kinds, p, "fact")
    return p


def _overlap(a: dict, b: dict) -> bool:
    """Unknown bounds count as overlapping: absence of evidence is not separation."""
    a0, a1, b0, b1 = (_time(x.get(k)) if x.get(k) else None
                      for x, k in ((a, "valid_from"), (a, "valid_until"), (b, "valid_from"), (b, "valid_until")))
    return not ((a1 and b0 and a1 < b0) or (b1 and a0 and b1 < a0))


def conflict_proposal(a: dict, b: dict) -> dict | None:
    """A review proposal when two active facts disagree, else None. Never an
    automatic invalidation: the result is meant to become a candidate."""
    keys = ("subject", "property")
    if not all(a.get(k) and a.get(k) == b.get(k) for k in keys):
        return None
    if (a.get("scope") or {}) != (b.get("scope") or {}) or a.get("value") == b.get("value"):
        return None
    if not _overlap(a, b):
        return None
    ta, tb = _time(a.get("observed_at", "")), _time(b.get("observed_at", ""))
    newer = None if not (ta and tb) or ta == tb else ("a" if ta > tb else "b")
    return {"action": "propose_review", "reason": "conflicting values for the same subject/property/scope",
            "suggested_current": newer}          # a suggestion only; None when time is unknown


def validate_handoff(h: dict) -> list[str]:
    p: list[str] = []
    if h.get("schema") != "remnant.handoff/1":
        p.append("schema must be remnant.handoff/1")
    for k in ("task_id", "from_agent", "goal"):
        if not str(h.get(k, "")).strip():
            p.append(f"{k} is required")
    _check_time(h, "created_at", p, required=True)
    if h.get("state") not in HANDOFF_STATES:
        p.append(f"state must be one of {sorted(HANDOFF_STATES)}")
    for k in ("done", "next", "constraints", "open_questions"):
        if k in h and not (isinstance(h[k], list) and all(isinstance(x, str) and x.strip() for x in h[k])):
            p.append(f"{k} must be a list of non-empty strings")
    if h.get("state") == "open" and not h.get("next"):
        p.append("an open handoff needs at least one next step")
    check_evidence(h.get("evidence", []), p)
    for i, hy in enumerate(h.get("hypotheses", [])):
        if hy.get("status") not in HYPOTHESIS_STATES or not str(hy.get("text", "")).strip():
            p.append(f"hypotheses[{i}] needs text and status in {sorted(HYPOTHESIS_STATES)}")
        elif hy["status"] == "supported" and not check_evidence(hy.get("evidence", []), p, f"hypotheses[{i}].evidence"):
            p.append(f"hypotheses[{i}]: supported needs evidence")
    return p


def validate_incident(inc: dict) -> list[str]:
    p: list[str] = []
    if inc.get("schema") != "remnant.incident/1":
        p.append("schema must be remnant.incident/1")
    for k in ("incident_id", "title"):
        if not str(inc.get(k, "")).strip():
            p.append(f"{k} is required")
    started = _check_time(inc, "started_at", p, required=True)
    resolved = _check_time(inc, "resolved_at", p)
    timeline = inc.get("timeline", [])
    last = None
    for i, e in enumerate(timeline):
        t = _check_time(e, "at", p, required=True)
        if e.get("kind") not in TIMELINE_KINDS or not str(e.get("text", "")).strip():
            p.append(f"timeline[{i}] needs text and kind in {sorted(TIMELINE_KINDS)}")
            continue
        if t and last and t < last:
            p.append(f"timeline[{i}] is out of order")
        if t and started and t < started:
            p.append(f"timeline[{i}] is before started_at")
        last = t or last
        kinds = check_evidence(e.get("evidence", []), p, f"timeline[{i}].evidence")
        if e["kind"] == "attempt":
            if e.get("result") not in ATTEMPT_RESULTS:
                p.append(f"timeline[{i}]: attempt needs result in {sorted(ATTEMPT_RESULTS)}")
            elif e["result"] == "worked" and not kinds:
                p.append(f"timeline[{i}]: a worked attempt needs evidence")
    if resolved and not any(e.get("kind") == "outcome" for e in timeline):
        p.append("resolved_at needs an outcome entry")
    rc = inc.get("root_cause")
    if rc is not None:
        kinds = check_evidence(rc.get("evidence", []), p, "root_cause.evidence")
        _check_verification(rc.get("verification", "unverified"), kinds, p, "root_cause")
    return p


def _clip(lines: list[str], limit: int) -> str:
    out, used = [], 0
    for line in lines:
        if used + len(line) + 1 > limit:
            out.append("… (truncated)")
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)


def render_handoff(h: dict, limit: int = 1500) -> str:
    lines = [f"Handoff {h['task_id']} from {h['from_agent']} ({h['state']}): {h['goal']}"]
    if h.get("repo"):
        r = h["repo"]
        lines.append(f"Repo: {r.get('path', '?')} @ {r.get('branch', '?')} {str(r.get('commit', ''))[:10]}")
    lines += [f"Next: {x}" for x in h.get("next", [])]
    lines += [f"Constraint: {x}" for x in h.get("constraints", [])]
    lines += [f"Refuted — do not retry: {x['text']}" for x in h.get("hypotheses", []) if x["status"] == "refuted"]
    lines += [f"Untested idea: {x['text']}" for x in h.get("hypotheses", []) if x["status"] == "untested"]
    lines += [f"Done: {x}" for x in h.get("done", [])]
    lines += [f"Open question: {x}" for x in h.get("open_questions", [])]
    return _clip(lines, limit)


def render_incident(inc: dict, limit: int = 1500) -> str:
    lines = [f"Incident {inc['incident_id']}: {inc['title']}" + (" (resolved)" if inc.get("resolved_at") else " (open)")]
    rc = inc.get("root_cause")
    if rc:
        lines.append(f"Root cause [{rc.get('verification', 'unverified')}]: {rc['text']}")
    for e in inc.get("timeline", []):
        tag = e["kind"] + (f":{e['result']}" if e["kind"] == "attempt" else "")
        lines.append(f"{e['at']} {tag} — {e['text']}")
    return _clip(lines, limit)
