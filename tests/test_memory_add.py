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
    assert fake.calls == [{"infer": False, "metadata": {"source": "agent", "job_id": "j1"}}]


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


def test_embedding_input_is_capped_but_signature_kept():
    seen = []

    class Embedder:
        def embed(self, text, memory_action=None):
            seen.append((len(text), memory_action))
            return [0.0]

    e = Embedder()
    memory._cap_embedding_input(e)
    e.embed("x" * (memory.MAX_EMBED_CHARS + 500), "add")
    e.embed("short", "search")
    assert seen == [(memory.MAX_EMBED_CHARS, "add"), (5, "search")]


def test_provenance_and_extraction_becomes_candidate(monkeypatch):
    fake = FakeMem0("ok")
    monkeypatch.setattr(memory, "get_memory", lambda: fake)
    memory.add("fact", infer=False, source="hook", session_id="s1", project="remnant")
    memory.add("extracted fact", infer=False, source="extraction", session_id="s1")
    memory.add("weird source", infer=False, source="bogus")
    a, b, c = (x["metadata"] for x in fake.calls)
    assert a == {"source": "hook", "session_id": "s1", "project": "remnant"}
    assert b["source"] == "extraction" and b["state"] == "candidate"
    assert c["source"] == "agent" and "state" not in c


def test_mem0_history_recording_is_disabled(monkeypatch):
    closed = []

    class FakeDB:                                # stands in for mem0's SQLiteManager
        def close(self): closed.append(True)

    class FakeMemory:
        def __init__(self, config):
            self.config, self.db = config, FakeDB()
            class E:
                def embed(self, text, memory_action=None): return [0.0]
            self.embedding_model = E()

        def reset(self):                         # like mem0 2.0.2: recreates the SQLite store
            self.db = FakeDB()

    monkeypatch.setattr(memory, "Memory", FakeMemory)
    monkeypatch.setattr(memory, "_mem", None)
    m = memory.get_memory()
    assert isinstance(m.db, memory._NoHistory) and closed == [True]
    assert m.config.history_db_path == ":memory:"
    assert m.db.add_history("id", None, "x", "ADD") is None and m.db.get_last_messages("s") == []
    m.reset()                                    # history must stay disabled after a reset
    assert isinstance(m.db, memory._NoHistory) and closed == [True, True]
