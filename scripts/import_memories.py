#!/usr/bin/env python3
"""
import_memories.py — migrate wiki log files into claude-memory MCP server.

Reads all *.md files in one or more directories, strips frontmatter, splits by
## sections, and calls add_memory for each chunk via the MCP HTTP endpoint.

Usage:
    python3 scripts/import_memories.py [--dry-run] [--file path] [--force]
                                       [--dir /path/to/dir ...]

Environment:
    CLAUDE_MEMORY_URL    Base URL of the MCP server (required)
    CLAUDE_MEMORY_TOKEN  Bearer token (required)
    WIKI_LOGS_DIR        Default source directory (default: /opt/git/wiki/logs)
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

WIKI_LOGS_DIR = os.environ.get("WIKI_LOGS_DIR", "/opt/git/wiki/logs")
IMPORTED_RECORD = Path(__file__).parent / "imported.json"
MIN_CHUNK_CHARS = 80


def load_imported() -> set:
    if IMPORTED_RECORD.exists():
        return set(json.loads(IMPORTED_RECORD.read_text()).get("files", []))
    return set()


def save_imported(imported: set):
    IMPORTED_RECORD.write_text(json.dumps({"files": sorted(imported)}, indent=2))


def tracking_key(path: Path) -> str:
    return str(path.resolve())


def strip_frontmatter(text: str) -> str:
    """Remove YAML frontmatter block (--- ... ---)."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:].lstrip("\n")
    return text


def split_sections(body: str, filename_stem: str) -> list[tuple[str, str]]:
    """
    Split body into (heading, content) chunks by ## headings.
    Falls back to the whole body if no ## headings found.
    Returns list of (heading, content) tuples.
    """
    parts = re.split(r"(?m)^## ", body)
    if len(parts) <= 1:
        return [("", body.strip())]
    chunks = []
    for part in parts[1:]:  # parts[0] is text before first ##
        lines = part.split("\n", 1)
        heading = lines[0].strip()
        content = lines[1].strip() if len(lines) > 1 else ""
        chunks.append((heading, content))
    return chunks


def build_memory_text(date: str, topic: str, heading: str, content: str) -> str:
    prefix = f"[{date} — {topic}]"
    if heading:
        return f"{prefix} {heading}\n\n{content}"
    return f"{prefix}\n\n{content}"


def parse_filename(name: str) -> tuple[str, str]:
    """Extract date and topic from YYYY-MM-DD_topic.md filename."""
    stem = Path(name).stem
    parts = stem.split("_", 1)
    if len(parts) == 2 and re.match(r"\d{4}-\d{2}-\d{2}", parts[0]):
        return parts[0], parts[1].replace("_", " ").replace("-", " ")
    return "", stem


def call_add_memory(url: str, token: str, content: str, dry_run: bool) -> bool:
    if dry_run:
        print(f"  [dry-run] would add: {content[:80].replace(chr(10), ' ')}…")
        return True
    try:
        resp = requests.post(
            f"{url}/mcp",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "add_memory", "arguments": {"content": content}},
                "id": 1,
            },
            timeout=30,
            stream=True,
        )
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line:
                continue
            decoded = line.decode("utf-8") if isinstance(line, bytes) else line
            if decoded.startswith("data: "):
                try:
                    data = json.loads(decoded[6:])
                    if "error" in data:
                        print(f"  ERROR from server: {data['error']}", file=sys.stderr)
                        return False
                except Exception:
                    pass
        return True
    except Exception as e:
        print(f"  ERROR: {e}", file=sys.stderr)
        return False


def process_file(
    path: Path, url: str, token: str, dry_run: bool
) -> tuple[int, int]:
    """Returns (chunks_added, chunks_skipped)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    body = strip_frontmatter(text)
    date, topic = parse_filename(path.name)
    chunks = split_sections(body, path.stem)

    added = skipped = 0
    for heading, content in chunks:
        if len(content) < MIN_CHUNK_CHARS:
            skipped += 1
            continue
        memory_text = build_memory_text(date, topic, heading, content)
        ok = call_add_memory(url, token, memory_text, dry_run)
        if ok:
            added += 1
        else:
            skipped += 1
        if not dry_run and len(chunks) > 1:
            time.sleep(0.3)  # avoid overwhelming Ollama embedder

    return added, skipped


def main():
    parser = argparse.ArgumentParser(description="Import wiki log files into claude-memory")
    parser.add_argument("--dry-run", action="store_true", help="Print without writing")
    parser.add_argument("--file", help="Import a single file")
    parser.add_argument("--force", action="store_true", help="Re-import already-imported files")
    parser.add_argument("--dir", action="append", dest="dirs", metavar="DIR",
                        help="Source directory (repeatable; defaults to WIKI_LOGS_DIR)")
    args = parser.parse_args()

    url = os.environ.get("CLAUDE_MEMORY_URL", "").rstrip("/")
    token = os.environ.get("CLAUDE_MEMORY_TOKEN", "")

    if not url or not token:
        print("ERROR: CLAUDE_MEMORY_URL and CLAUDE_MEMORY_TOKEN must be set", file=sys.stderr)
        sys.exit(1)

    logs_dir = Path(WIKI_LOGS_DIR)
    if not logs_dir.exists():
        print(f"ERROR: WIKI_LOGS_DIR not found: {logs_dir}", file=sys.stderr)
        sys.exit(1)

    imported = load_imported()

    if args.file:
        files = [Path(args.file)]
    else:
        source_dirs = [Path(d) for d in args.dirs] if args.dirs else [logs_dir]
        files = []
        for d in source_dirs:
            if not d.exists():
                print(f"WARNING: directory not found, skipping: {d}", file=sys.stderr)
                continue
            files.extend(sorted(f for f in d.glob("*.md") if f.name != "MEMORY.md"))

    total_added = total_skipped = total_files = 0

    for path in files:
        if not path.exists():
            print(f"SKIP: {path} not found")
            continue
        key = tracking_key(path)
        if not args.force and key in imported:
            print(f"SKIP: {path.name} (already imported, use --force to reimport)")
            continue

        print(f"Importing {path.name}…")
        added, skipped = process_file(path, url, token, args.dry_run)
        print(f"  → {added} chunks added, {skipped} skipped")
        total_added += added
        total_skipped += skipped
        total_files += 1

        if not args.dry_run:
            imported.add(key)
            save_imported(imported)

    print(f"\nDone: {total_files} files, {total_added} chunks added, {total_skipped} skipped")
    if args.dry_run:
        print("(dry-run — nothing was written)")


if __name__ == "__main__":
    main()
