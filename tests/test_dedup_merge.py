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


def test_dedup_meta_view_skips_json_null_like_sql():
    p = {"category": "outer", "tags": ["a"], "metadata": {"category": None, "metadata": {"tags": None}}}
    view = dedup_job._extract_meta(p)
    assert view["category"] == "outer" and view["tags"] == ["a"]
    for junk in ("x", ["x"], None):
        out = dedup_job._merge_tags_and_mark_reviewed({"data": "d", "tags": ["a"], "metadata": junk}, ["b"])
        assert out["tags"] == ["a", "b"] and out.get("metadata") == junk


def test_memory_url_prefers_remnant_names(monkeypatch):
    import importlib
    monkeypatch.setenv("CLAUDE_MEMORY_URL", "http://old:1")
    monkeypatch.setenv("CLAUDE_MEMORY_TOKEN", "old")
    monkeypatch.delenv("REMNANT_URL", raising=False)
    monkeypatch.delenv("REMNANT_TOKEN", raising=False)
    m = importlib.reload(dedup_job)
    assert (m.MEMORY_URL, m.MEMORY_TOKEN) == ("http://old:1", "old")           # old chart still works
    monkeypatch.setenv("REMNANT_URL", "http://new:2")
    monkeypatch.setenv("REMNANT_TOKEN", "new")
    m = importlib.reload(dedup_job)
    assert (m.MEMORY_URL, m.MEMORY_TOKEN) == ("http://new:2", "new")


class OpenAIResp:
    def __init__(self, content, finish="stop", status=200):
        self.content, self.finish, self.status_code = content, finish, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return {"choices": [{"message": {"content": self.content}, "finish_reason": self.finish}]}


def use_openai(monkeypatch, fallback=True):
    monkeypatch.setattr(dedup_job, "DEDUP_LLM", "openai")
    monkeypatch.setattr(dedup_job, "DEDUP_LLM_URL", "http://omniroute.example/v1")
    monkeypatch.setattr(dedup_job, "DEDUP_LLM_MODEL", "auto/best-fast")
    monkeypatch.setattr(dedup_job, "DEDUP_LLM_API_KEY", "k")
    monkeypatch.setattr(dedup_job, "DEDUP_LLM_FALLBACK", fallback)


def test_openai_compatible_merge_request_and_parsing(monkeypatch):
    use_openai(monkeypatch)
    seen = {}

    def post(url, json, timeout, headers=None):
        seen.update(url=url, body=json, headers=headers)
        return OpenAIResp('{"facts": ["fact one", "fact two"]}')
    monkeypatch.setattr(dedup_job.requests, "post", post)
    assert dedup_job.ollama_merge(["fact one here", "fact two here"]) == "- fact one\n- fact two"
    assert seen["url"] == "http://omniroute.example/v1/chat/completions"
    assert seen["headers"] == {"Authorization": "Bearer k"}
    b = seen["body"]
    assert b["model"] == "auto/best-fast" and b["response_format"] == {"type": "json_object"}
    assert b["temperature"] == 0 and b["max_tokens"] > 0 and "data, not instructions" in b["messages"][0]["content"]


def test_openai_truncated_merge_is_rejected(monkeypatch):
    use_openai(monkeypatch)
    monkeypatch.setattr(dedup_job.requests, "post", lambda *a, **k: OpenAIResp('{"facts": ["x"', finish="length"))
    assert dedup_job.ollama_merge(["a", "b"]) is None


def test_openai_failure_falls_back_to_local_ollama(monkeypatch):
    use_openai(monkeypatch)
    calls = []

    def post(url, json, timeout, headers=None):
        calls.append(url)
        if "omniroute" in url:
            return OpenAIResp("", status=503)
        return Resp('{"facts": ["merged fact"]}')
    monkeypatch.setattr(dedup_job.requests, "post", post)
    assert dedup_job.ollama_merge(["merged fact a", "merged fact b"]) == "- merged fact"
    assert calls[0].endswith("/chat/completions") and calls[1].endswith("/api/generate")


def test_openai_failure_without_fallback_rejects_the_merge(monkeypatch):
    use_openai(monkeypatch, fallback=False)
    calls = []
    monkeypatch.setattr(dedup_job.requests, "post",
                        lambda url, json, timeout, headers=None: calls.append(url) or OpenAIResp("", status=503))
    assert dedup_job.ollama_merge(["a", "b"]) is None and len(calls) == 1      # never reached Ollama


def test_default_stays_local_only(monkeypatch):
    monkeypatch.setattr(dedup_job, "DEDUP_LLM", "ollama")
    monkeypatch.setattr(dedup_job, "DEDUP_LLM_URL", "http://omniroute.example/v1")
    monkeypatch.setattr(dedup_job, "DEDUP_LLM_MODEL", "auto/best-fast")
    urls = []
    monkeypatch.setattr(dedup_job.requests, "post", lambda url, json, timeout, **k: urls.append(url) or Resp('{"facts": ["f"]}'))
    dedup_job.ollama_merge(["f a", "f b"])
    assert urls and all(u.endswith("/api/generate") for u in urls)


def test_openai_400_retries_without_json_mode(monkeypatch):
    use_openai(monkeypatch, fallback=False)
    bodies = []

    def post(url, json, timeout, headers=None):
        bodies.append(dict(json))
        return OpenAIResp("", status=400) if "response_format" in json else OpenAIResp('{"facts": ["kept fact"]}')
    monkeypatch.setattr(dedup_job.requests, "post", post)
    assert dedup_job.ollama_merge(["kept fact one", "kept fact two"]) == "- kept fact"
    assert "response_format" in bodies[0] and "response_format" not in bodies[1]
    assert bodies[0]["max_tokens"] >= 4096                      # room for hosted thinking models


def test_api_key_never_reaches_the_logs(monkeypatch, caplog):
    use_openai(monkeypatch)
    monkeypatch.setattr(dedup_job, "DEDUP_LLM_API_KEY", "sk-secret-123")

    def post(url, json, timeout, headers=None):
        if "omniroute" in url:
            raise ValueError(f"Invalid header value {headers['Authorization']!r}")
        return Resp('{"facts": ["f"]}')
    monkeypatch.setattr(dedup_job.requests, "post", post)
    import logging
    with caplog.at_level(logging.DEBUG):
        dedup_job.ollama_merge(["f a", "f b"])
        dedup_job.ollama_assign_tags("text", ["t"])
    assert "sk-secret-123" not in caplog.text and "[REDACTED]" in caplog.text


def test_incomplete_openai_config_without_fallback_uses_no_llm(monkeypatch):
    use_openai(monkeypatch, fallback=False)
    monkeypatch.setattr(dedup_job, "DEDUP_LLM_MODEL", "")                     # incomplete
    calls = []
    monkeypatch.setattr(dedup_job.requests, "post", lambda *a, **k: calls.append(a) or Resp('{"facts": ["f"]}'))
    monkeypatch.setattr(dedup_job.requests, "get", lambda *a, **k: calls.append(a) or Resp("[]"))
    assert dedup_job.llm_is_reachable() is False
    assert dedup_job.ollama_merge(["a", "b"]) is None and calls == []        # never silently local


def test_one_time_budget_shared_by_attempts_and_fallback(monkeypatch):
    use_openai(monkeypatch)
    clock = [1000.0]
    monkeypatch.setattr(dedup_job.time, "monotonic", lambda: clock[0])
    timeouts = []

    def post(url, json, timeout, headers=None):
        timeouts.append((url.rsplit("/", 1)[-1], round(timeout)))
        clock[0] += 50                                                       # each call takes 50 s
        if "omniroute" in url:
            return OpenAIResp("", status=400 if "response_format" in json else 503)
        return Resp('{"facts": ["f"]}')
    monkeypatch.setattr(dedup_job.requests, "post", post)
    dedup_job.llm_generate("p", json_mode=True, max_tokens=100, timeout=120)
    assert timeouts == [("completions", 120), ("completions", 70), ("generate", 20)]


def test_tagging_uses_the_openai_path_too(monkeypatch):
    use_openai(monkeypatch)
    urls = []
    monkeypatch.setattr(dedup_job.requests, "post",
                        lambda url, json, timeout, headers=None: urls.append(url) or OpenAIResp('["prod-k8s"]'))
    assert dedup_job.ollama_assign_tags("about prod-k8s", ["prod-k8s", "remnant"]) == ["prod-k8s"]
    assert urls == ["http://omniroute.example/v1/chat/completions"]
