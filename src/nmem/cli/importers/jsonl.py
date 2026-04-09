"""
JSONL structured importer.

Each line: {"title": "...", "content": "...", "tier": "ltm", "importance": 5, ...}
"""

from __future__ import annotations

import json
from pathlib import Path

from tqdm import tqdm

from nmem.cli.importers.base import ImportResult


async def import_jsonl(
    mem,
    file: Path,
    agent_id: str = "default",
    compress: bool = False,
) -> ImportResult:
    """Import a JSONL file into nmem.

    Each line should be a JSON object with at minimum a "content" field.
    Optional fields: title, tier (journal|ltm|shared), importance, category,
    agent_id, key, tags, entry_type.

    Args:
        mem: Initialized MemorySystem.
        file: Path to .jsonl file.
        agent_id: Default agent ID (overridden by per-line agent_id).
        compress: Whether to LLM-compress content.

    Returns:
        ImportResult with counts.
    """
    result = ImportResult()

    lines = file.read_text(encoding="utf-8").strip().splitlines()
    if not lines:
        result.details.append(f"Empty file: {file}")
        return result

    for i, line in enumerate(tqdm(lines, desc="Importing", unit="entry"), 1):
        line = line.strip()
        if not line:
            continue

        try:
            entry = json.loads(line)
        except json.JSONDecodeError as e:
            result.errors += 1
            result.details.append(f"Line {i}: invalid JSON: {e}")
            continue

        if not isinstance(entry, dict) or "content" not in entry:
            result.errors += 1
            result.details.append(f"Line {i}: missing 'content' field")
            continue

        content = entry["content"]
        tier = entry.get("tier", "ltm")
        importance = entry.get("importance", 5)
        entry_agent = entry.get("agent_id", agent_id)
        title = entry.get("title", content[:100])
        category = entry.get("category", "imported")
        key = entry.get("key", title[:200].replace(" ", "_").lower())
        tags = entry.get("tags")

        try:
            if tier == "journal":
                entry_type = entry.get("entry_type", "imported")
                await mem.journal.add(
                    agent_id=entry_agent,
                    entry_type=entry_type,
                    title=title,
                    content=content,
                    importance=importance,
                    tags=tags,
                    compress=compress,
                )
            elif tier == "shared":
                await mem.shared.save(
                    agent_id=entry_agent,
                    category=category,
                    key=key,
                    content=content,
                    importance=importance,
                )
            else:  # Default: ltm
                await mem.ltm.save(
                    agent_id=entry_agent,
                    category=category,
                    key=key,
                    content=content,
                    importance=importance,
                    compress=compress,
                )

            result.imported += 1

        except Exception as e:
            result.errors += 1
            result.details.append(f"Line {i}: {e}")

    return result
