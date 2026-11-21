import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("mem0")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import memory  # noqa: E402


def test_default_seed_is_generic(monkeypatch):
    monkeypatch.delenv("TAG_RULES_SEED_FILE", raising=False)
    rules = memory._load_seed_rules()
    assert "helm" in rules and "argocd" in rules
    for homelab_only in ("prod-k8s", "valheim", "frigate", "gameledger"):
        assert homelab_only not in rules


def test_seed_from_file(monkeypatch, tmp_path):
    f = tmp_path / "rules.json"
    f.write_text(json.dumps({"my-app": ["my-app", "myapp"]}))
    monkeypatch.setenv("TAG_RULES_SEED_FILE", str(f))
    assert memory._load_seed_rules() == {"my-app": ["my-app", "myapp"]}


@pytest.mark.parametrize("bad", ['["x"]', '{"t": "notalist"}', '{"t": [1]}'])
def test_seed_file_rejects_bad_shape(monkeypatch, tmp_path, bad):
    f = tmp_path / "rules.json"
    f.write_text(bad)
    monkeypatch.setenv("TAG_RULES_SEED_FILE", str(f))
    with pytest.raises(ValueError):
        memory._load_seed_rules()


def test_example_file_is_valid(monkeypatch):
    monkeypatch.setenv("TAG_RULES_SEED_FILE", str(Path(__file__).resolve().parents[1] / "deploy" / "tag-rules.example.json"))
    assert memory._load_seed_rules()["my-app"] == ["my-app", "myapp"]
