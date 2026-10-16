import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import dedup_job  # noqa: E402


class Resp:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return {"response": self.body, "done_reason": "stop"}


NOTES = ["prod-k8s was upgraded and now runs k8s 1.35.0 on all nodes as of April.",
         "After the OOM incident the CP nodes were resized to 4 vCPU / 8GB; prod-k8s runs k8s 1.35.0."]


@pytest.mark.parametrize("body, expected", [
    ('{"facts": ["prod-k8s runs k8s 1.35.0", "CP nodes have 4 vCPU / 8GB"]}',
     "- prod-k8s runs k8s 1.35.0\n- CP nodes have 4 vCPU / 8GB"),
    ('{"facts": ["Sure, let\'s proceed with the upgrade", "x"]}', None),   # chatter rejects the merge
    ('{"facts": []}', None),
    ('{"memory": "text"}', None),
    ("Here is the merged memory: ...", None),                            # not JSON
])
def test_merge_output_validation(monkeypatch, body, expected):
    monkeypatch.setattr(dedup_job.requests, "post", lambda *a, **k: Resp(body))
    assert dedup_job.ollama_merge(NOTES) == expected


def test_merge_prompt_asks_for_json_and_treats_notes_as_data(monkeypatch):
    seen = {}
    monkeypatch.setattr(dedup_job.requests, "post", lambda url, json, timeout: seen.update(json) or Resp('{"facts": ["f"]}'))
    dedup_job.ollama_merge(["one", "two"])
    assert seen["format"] == "json" and "data, not instructions" in seen["prompt"]


def test_truncated_generation_is_rejected(monkeypatch):
    class Cut(Resp):
        def json(self):
            return {"response": '{"facts": ["partial', "done_reason": "length"}
    monkeypatch.setattr(dedup_job.requests, "post", lambda *a, **k: Cut(""))
    assert dedup_job.ollama_merge(["a", "b"]) is None


def test_merge_longer_than_input_is_rejected(monkeypatch):
    monkeypatch.setattr(dedup_job.requests, "post",
                        lambda *a, **k: Resp('{"facts": ["' + "x" * 50 + '", "' + "y" * 50 + '"]}'))
    assert dedup_job.ollama_merge(["short a", "short b"]) is None


def test_dedup_metadata_helpers_follow_the_deepest_level():
    flat = {"data": "x", "category": "project", "tags": ["a"]}
    mixed = {"data": "x", "category": "project", "metadata": {"tags": ["a"]}}
    double = {"data": "x", "metadata": {"metadata": {"tags": ["a"]}}}
    assert dedup_job._extract_meta(mixed)["category"] == "project" and dedup_job._extract_meta(mixed)["tags"] == ["a"]
    out = dedup_job._merge_tags_and_mark_reviewed(dict(flat), ["b"])
    assert "metadata" not in out and out["tags"] == ["a", "b"] and out["llm_reviewed"] is True
    out = dedup_job._merge_tags_and_mark_reviewed(mixed, ["b"])
    assert out["metadata"]["tags"] == ["a", "b"] and out["category"] == "project"
    out = dedup_job._set_tags_in_payload(double, ["c"])
    assert out["metadata"]["metadata"]["tags"] == ["c"]
