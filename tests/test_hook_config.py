"""plugin/hooks/hooks.json is the source of truth for the Claude Code hooks.

settings-template.json must carry the same hooks block, and the plugin README's hook
table must list the same event/script pairs. Fails when they drift apart (#46)."""
import json
import os
import re
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "plugin"
HOOKS = PLUGIN / "hooks"


def _load(name):
    return json.loads((HOOKS / name).read_text())


def _registered():
    """{(event, script)} from hooks.json."""
    pairs = set()
    for event, groups in _load("hooks.json")["hooks"].items():
        for group in groups:
            for hook in group["hooks"]:
                m = re.fullmatch(r"\$\{REMNANT_PLUGIN_DIR\}/hooks/([\w.-]+)", hook["command"])
                assert m, f"{event}: unexpected command {hook['command']!r}"
                pairs.add((event, m.group(1)))
    return pairs


def test_plugin_manifest_points_at_hooks_json():
    manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())
    assert (PLUGIN / ".claude-plugin" / manifest["hooks"]).resolve() == HOOKS / "hooks.json"


def test_settings_template_matches_hooks_json():
    assert _load("settings-template.json")["hooks"] == _load("hooks.json")["hooks"]


def test_registered_scripts_exist_and_are_executable():
    for event, script in _registered():
        path = HOOKS / script
        assert path.is_file(), f"{event}: {script} is missing"
        assert os.access(path, os.X_OK), f"{event}: {script} is not executable"


def test_readme_hook_table_matches_hooks_json():
    readme = (PLUGIN / "README.md").read_text()
    table = set(re.findall(r"^\| `(\w+)` \| `([\w.-]+\.sh)`", readme, re.M))
    assert table == _registered()


def test_every_hook_script_is_registered_or_documented_as_optional():
    readme = (PLUGIN / "README.md").read_text()
    registered = {script for _, script in _registered()}
    for script in sorted(p.name for p in HOOKS.glob("*.sh")):
        if script in registered:
            continue
        # Optional hooks: named in the README together with the event they belong to.
        m = re.search(rf"`{re.escape(script)}` \((\w+)", readme)
        assert m, f"{script} is neither in hooks.json nor documented as an optional hook"
