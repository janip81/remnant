#!/usr/bin/env python3.11
"""Install or migrate Codex Remnant definitions; never alter trust or MCP settings."""
import argparse
import copy
import datetime
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile

sys.dont_write_bytecode = True
EVENTS = ('SessionStart', 'UserPromptSubmit', 'PreCompact', 'Stop')
LEGACY_HOOK = Path('/opt/git/prod-k8s/agent-tools/remnant-hooks.py')


def script_path(command):
    if not isinstance(command, str):
        return None
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    if not words or Path(words[0]).name not in ('python', 'python3', 'python3.11'):
        return None
    args = words[1:]
    while args and args[0] in ('-B', '-u'):
        args.pop(0)
    return Path(args[0]) if len(args) == 1 else None


def definition(event, command):
    value = {'type': 'command', 'command': command, 'timeout': 12,
             'statusMessage': 'Remnant memory context' if event in EVENTS[:2]
             else 'Saving Codex session context'}
    if event in EVENTS[:2]:
        value['additionalContextLimit'] = 1500
    return value


def install_document(original, hook, migrate=False):
    result = copy.deepcopy(original)
    hooks = result.setdefault('hooks', {})
    if not isinstance(hooks, dict):
        raise ValueError('hooks must be an object')
    command = shlex.join(['python3.11', str(hook)])
    known = {hook, LEGACY_HOOK}
    # An unknown same-named script may be another installation: do not silently
    # append a second Remnant handler or delete it without an explicit migration.
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            raise ValueError('hook event groups must be arrays')
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get('hooks', []), list):
                raise ValueError('invalid hook group')
            for handler in group.get('hooks', []):
                if not isinstance(handler, dict):
                    raise ValueError('invalid hook handler')
                script = script_path(handler.get('command'))
                if script and script.name == 'remnant-hooks.py':
                    if script not in known or event not in EVENTS:
                        raise ValueError('unrecognized Remnant handler; refusing ambiguous migration')
                    if script == LEGACY_HOOK and not migrate:
                        raise ValueError('legacy Remnant handlers found; run with --migrate')
    for event in EVENTS:
        groups = hooks.setdefault(event, [])
        found = False
        for group in groups:
            kept = []
            for handler in group.get('hooks', []):
                if script_path(handler.get('command')) in known:
                    if found:
                        if not migrate:
                            raise ValueError('duplicate Remnant handlers; run with --migrate')
                        continue
                    # Keep timing/status/other existing handler settings and all
                    # group metadata/unrelated handlers; replace only the command.
                    handler['command'] = command
                    kept.append(handler)
                    found = True
                else:
                    kept.append(handler)
            group['hooks'] = kept
        if not found:
            groups.append({'hooks': [definition(event, command)]})
    for event in EVENTS:
        owned = [h for g in hooks[event] for h in g.get('hooks', [])
                 if script_path(h.get('command')) in known]
        if len(owned) != 1 or script_path(owned[0].get('command')) != hook:
            raise ValueError('expected exactly one new Remnant handler per event')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--migrate', action='store_true', help='Replace old handlers and remove Remnant duplicates')
    parser.add_argument('--hook-path', type=Path, default=Path(__file__).with_name('remnant-hooks.py'),
                        help='Target script (defaults to installer sibling)')
    args = parser.parse_args(argv)
    hook = args.hook_path.expanduser().resolve()
    if not hook.is_file():
        parser.error('target hook script does not exist')
    home = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
    path = home / 'hooks.json'
    try:
        before = path.read_text() if path.exists() else None
        original = json.loads(before) if before is not None else {}
        if not isinstance(original, dict):
            raise ValueError('hook document must be an object')
        result = install_document(original, hook, args.migrate)
        content = json.dumps(result, indent=2) + '\n'
        if before != content:
            home.mkdir(parents=True, exist_ok=True)
            if before is not None:
                stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
                backup = path.with_name('hooks.json.backup-' + stamp)
                backup.write_text(before)
                backup.chmod(0o600)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', dir=home, delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(content)
                temporary.chmod(0o600)
                # Refuse to overwrite another session's intervening edits.
                current = path.read_text() if path.exists() else None
                if current != before:
                    raise ValueError('hooks.json changed during installation; retry after reviewing it')
                temporary.replace(path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
    except (OSError, ValueError, TypeError) as exc:
        # Do not echo document contents/commands, which may include credentials.
        print('Remnant installation failed: ' + (str(exc) if isinstance(exc, ValueError)
              and not isinstance(exc, json.JSONDecodeError) else 'invalid configuration or filesystem error'), file=sys.stderr)
        return 1
    print('Installed exactly one Remnant handler per event:', path)
    print('Restart Codex and use /hooks to review and re-trust the changed definitions.')
    print('MCP configuration and hook trust were not modified.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
