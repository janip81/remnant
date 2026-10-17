"""Draft contracts (contracts/): validators, conflict proposals and renderers."""
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "contracts"
sys.path.insert(0, str(ROOT))
import contracts as c  # noqa: E402

FIX = {p.stem: json.loads(p.read_text()) for p in (ROOT / "fixtures").glob("*.json")}


def test_fixtures_are_valid():
    for name in ("a", "b"):
        assert c.validate_fact(FIX["fact-conflict"][name]) == []
    assert c.validate_handoff(FIX["handoff-example"]) == []
    assert c.validate_incident(FIX["incident-example"]) == []


def test_unknown_stays_unknown_and_self_confidence_is_refused():
    assert c.validate_fact({}) == []                                  # everything optional
    assert c.validate_fact({"confidence": 0.9}) == ["confidence is not accepted: use evidence + verification"]
    assert c.validate_fact({"observed_at": "2026-10-01"})             # needs a timezone
    assert c.validate_fact({"valid_from": "2026-10-02T00:00:00+00:00", "valid_until": "2026-10-01T00:00:00+00:00"})
    assert c.validate_fact({"subject": "x"})                          # subject without property


def test_verification_needs_matching_evidence():
    inferred = [{"kind": "agent_inference", "ref": "I think so"}]
    assert c.validate_fact({"verification": "tool_verified", "evidence": inferred})
    assert c.validate_fact({"verification": "user_confirmed", "evidence": inferred})
    assert c.validate_fact({"verification": "user_confirmed",
                            "evidence": [{"kind": "user_statement", "ref": "chat 2026-10-16"}]}) == []
    assert c.validate_fact({"evidence": [{"kind": "rumour", "ref": "x"}]})


def test_conflict_proposes_review_and_never_invalidates():
    a, b = FIX["fact-conflict"]["a"], FIX["fact-conflict"]["b"]
    prop = c.conflict_proposal(a, b)
    assert prop == {"action": "propose_review", "reason": "conflicting values for the same subject/property/scope",
                    "suggested_current": "b"}
    assert a["value"] == "db-main-1"                                  # inputs untouched
    assert c.conflict_proposal(a, {**b, "value": "db-main-1"}) is None             # same value
    assert c.conflict_proposal(a, {**b, "scope": {"project": "other"}}) is None     # other scope
    assert c.conflict_proposal(a, {**b, "property": "replicas"}) is None
    no_time = {k: v for k, v in b.items() if k != "observed_at"}
    assert c.conflict_proposal(a, no_time)["suggested_current"] is None            # unknown time: no suggestion
    old = {**a, "valid_until": "2026-09-01T00:00:00+00:00"}
    later = {**b, "valid_from": "2026-10-01T00:00:00+00:00"}
    assert c.conflict_proposal(old, later) is None                                 # disjoint validity
    assert c.conflict_proposal({**a, "valid_until": "2026-09-01T00:00:00+00:00"}, b)  # unknown start overlaps


def test_handoff_rules():
    h = FIX["handoff-example"]
    bad = copy.deepcopy(h)
    bad["hypotheses"][1] = {"text": "it is DNS", "status": "supported"}            # no evidence
    assert c.validate_handoff(bad) == ["hypotheses[1]: supported needs evidence other than agent_inference"]
    assert c.validate_handoff({**h, "next": []}) == ["an open handoff needs at least one next step"]
    assert c.validate_handoff({**h, "state": "done", "next": []}) == []
    assert c.validate_handoff({**h, "schema": "x", "task_id": ""})


def test_incident_rules():
    inc = FIX["incident-example"]
    bad = copy.deepcopy(inc)
    del bad["timeline"][3]["evidence"]
    assert c.validate_incident(bad) == ["timeline[3]: a worked attempt needs evidence other than agent_inference"]
    bad = copy.deepcopy(inc)
    bad["timeline"] = bad["timeline"][:4]
    assert c.validate_incident(bad) == ["resolved_at needs an outcome entry"]
    bad = copy.deepcopy(inc)
    bad["timeline"][1]["at"] = "2026-10-10T05:00:00+00:00"
    assert "timeline[1] is before started_at" in c.validate_incident(bad)
    bad = copy.deepcopy(inc)
    bad["root_cause"]["evidence"] = []
    assert c.validate_incident(bad) == [
        "root_cause: tool_verified needs evidence of kind ['commit', 'file', 'tool_output']"]


def test_renderers_are_bounded_and_flag_refuted_and_unverified():
    text = c.render_handoff(FIX["handoff-example"])
    assert "Refuted — do not retry: The job fails because credentials expire" in text
    assert text.index("Next:") < text.index("Done:")                              # next steps first
    assert len(c.render_handoff(FIX["handoff-example"], limit=120)) <= 140
    inc = c.render_incident(FIX["incident-example"])
    assert "Root cause [tool_verified]" in inc and "attempt:failed — Restarted the gateway" in inc


def test_eval_case_references_existing_evidence():
    case = FIX["eval-continuation-1"]
    assert (ROOT / "fixtures" / case["evidence"]).exists() and len(case["hidden_checks"]) >= 3


def test_inference_alone_never_verifies_success():
    inc = copy.deepcopy(FIX["incident-example"])
    inc["timeline"][3]["evidence"] = [{"kind": "agent_inference", "ref": "looks fixed"}]
    assert c.validate_incident(inc) == ["timeline[3]: a worked attempt needs evidence other than agent_inference"]
    inc = copy.deepcopy(FIX["incident-example"])
    inc["timeline"][4] = {"at": inc["timeline"][4]["at"], "kind": "outcome", "text": "seems fine"}
    assert c.validate_incident(inc) == []                                    # unverified outcome is allowed ...
    assert "outcome [unverified] — seems fine" in c.render_incident(inc)      # ... and shown as such
    inc["timeline"][4]["verification"] = "tool_verified"
    assert c.validate_incident(inc)                                          # but can't claim verification
    h = copy.deepcopy(FIX["handoff-example"])
    h["hypotheses"][1].update(status="supported", evidence=[{"kind": "agent_inference", "ref": "probably"}])
    assert c.validate_handoff(h) == ["hypotheses[1]: supported needs evidence other than agent_inference"]


def test_malformed_records_report_problems_instead_of_crashing():
    assert c.validate_fact({"valid_from": "2026-10-01T00:00:00", "valid_until": "2026-10-02T00:00:00+00:00"})
    assert c.validate_fact(None) == ["fact metadata must be an object"]
    assert c.validate_fact({"evidence": None}) == []
    h = {**FIX["handoff-example"], "hypotheses": [None, "x", {"status": "untested", "text": None}]}
    probs = c.validate_handoff(h)
    assert "hypotheses[0] must be an object" in probs and "hypotheses[1] must be an object" in probs
    assert any(x.startswith("hypotheses[2] needs text") for x in probs)
    assert c.validate_incident({**FIX["incident-example"], "timeline": [None]}) == \
        ["timeline[0] must be an object", "resolved_at needs an outcome entry"]
    assert c.validate_incident({**FIX["incident-example"], "root_cause": "dns"}) == ["root_cause must be an object"]
    assert c.validate_handoff(None) == ["handoff must be an object"]


def test_evidence_ref_must_be_a_real_string():
    for ref in (None, 5, {"a": 1}, "  "):
        assert c.validate_fact({"verification": "tool_verified", "evidence": [{"kind": "tool_output", "ref": ref}]})


def test_eval_arms_carry_the_same_facts():
    case = FIX["eval-continuation-1"]
    structured = c.render_handoff(FIX[case["evidence"][:-5]], limit=10_000) + json.dumps(FIX[case["evidence"][:-5]])
    for term in case["parity_terms"]:
        assert term in case["freeform"], term
        assert term in structured, term


def test_unhashable_enum_values_are_problems_not_crashes():
    bad = [["x"], {"a": 1}]
    for v in bad:
        assert c.validate_fact({"verification": v})
        assert c.validate_fact({"evidence": [{"kind": v, "ref": "r"}]})
        assert c.validate_handoff({**FIX["handoff-example"], "state": v})
        assert c.validate_handoff({**FIX["handoff-example"], "hypotheses": [{"text": "t", "status": v}]})
        inc = copy.deepcopy(FIX["incident-example"])
        inc["timeline"][2]["result"] = v
        assert c.validate_incident(inc)
        inc["timeline"][0]["kind"] = v
        assert c.validate_incident(inc)
