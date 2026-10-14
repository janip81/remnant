import logging
import sys
from pathlib import Path

import pytest

pytest.importorskip("mem0")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import memory  # noqa: E402  (schema setup fails harmlessly without a DB)

ADDED = {"results": [{"id": "1", "memory": "fact", "event": "ADD"}]}


class FakeMem0:
    """Mimics mem0 2.0.2 add(): infer=True behaviour is configurable."""

    def __init__(self, infer_mode="ok"):
        self.infer_mode, self.calls = infer_mode, []

    def add(self, content, infer, metadata=None, **kw):
        self.calls.append({"infer": infer, "metadata": metadata})
        if not infer:
            return ADDED
        if self.infer_mode == "swallowed":
            logging.getLogger("mem0.memory.main").error("LLM extraction failed: model 'x' not found")
            return {"results": []}
        if self.infer_mode == "misformatted":       # qwen2.5 answered {"message": ...}
            return {"results": []}
        if self.infer_mode == "crash":              # {"memory": ["plain string"]}
            raise AttributeError("'str' object has no attribute 'get'")
        return ADDED


@pytest.fixture(autouse=True)
def no_tag_db(monkeypatch):
    monkeypatch.setattr(memory, "_auto_tag", lambda text: [])


@pytest.mark.parametrize("mode, reason", [
    ("swallowed", "LLM extraction failed"),
    ("misformatted", "inference extracted nothing"),
    ("crash", "AttributeError"),
])
def test_failed_inference_falls_back_to_raw_storage(monkeypatch, mode, reason):
    fake = FakeMem0(mode)
    monkeypatch.setattr(memory, "get_memory", lambda: fake)
    assert memory.add("fact", infer=True, category="project") == ADDED
    first, second = fake.calls
    assert first["infer"] is True and second["infer"] is False
    assert reason in second["metadata"]["infer_fallback"]
    assert second["metadata"]["category"] == "project"


def test_successful_inference_is_not_duplicated(monkeypatch):
    fake = FakeMem0("ok")
    monkeypatch.setattr(memory, "get_memory", lambda: fake)
    assert memory.add("fact", infer=True) == ADDED
    assert len(fake.calls) == 1


def test_infer_false_is_a_single_raw_write(monkeypatch):
    fake = FakeMem0("swallowed")
    monkeypatch.setattr(memory, "get_memory", lambda: fake)
    memory.add("fact", infer=False, job_id="j1")
    assert fake.calls == [{"infer": False, "metadata": {"job_id": "j1"}}]


def test_fallback_failure_propagates(monkeypatch):
    class Down(FakeMem0):
        def add(self, content, infer, metadata=None, **kw):
            if not infer:
                raise ConnectionError("embedding service down")
            return {"results": []}
    monkeypatch.setattr(memory, "get_memory", lambda: Down())
    with pytest.raises(ConnectionError):
        memory.add("fact", infer=True)


def test_handler_removed_after_call(monkeypatch):
    monkeypatch.setattr(memory, "get_memory", lambda: FakeMem0("crash"))
    before = list(logging.getLogger("mem0.memory.main").handlers)
    memory.add("fact", infer=True)
    assert logging.getLogger("mem0.memory.main").handlers == before
