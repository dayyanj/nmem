"""
JSONL structured importer.

Each line: {"title": "...", "content": "...", "tier": "ltm", "importance": 5, ...}

When ``preserve_timestamps`` is True, the importer reads ``created_at`` (and
optionally ``expires_at``) from each JSON line and passes them through to the
tier's add/save method.  This ensures that imported historical entries expire
based on their original age, not the import date.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from tqdm import tqdm

from nmem.cli.importers.base import ImportResult


def _parse_timestamp(value) -> datetime | None:
    """Parse an ISO-8601 string or epoch float into a datetime."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.utcfromtimestamp(value)
    if isinstance(value, str):
        # Accept both "2025-01-15T12:00:00" and "2025-01-15 12:00:00"
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
    return None


async def import_jsonl(
    mem,
    file: Path,
    agent_id: str = "default",
    compress: bool = False,
    preserve_timestamps: bool = False,
) -> ImportResult:
    """Import a JSONL file into nmem.

    Each line should be a JSON object with at minimum a "content" field.
    Optional fields: title, tier (journal|ltm|shared), importance, category,
    agent_id, key, tags, entry_type, created_at, expires_at.

    Args:
        mem: Initialized MemorySystem.
        file: Path to .jsonl file.
        agent_id: Default agent ID (overridden by per-line agent_id).
        compress: Whether to LLM-compress content.
        preserve_timestamps: When True, pass created_at/expires_at from source
            data so entries expire based on their original age, not import date.

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

        # Parse timestamps for historical imports
        created_at = _parse_timestamp(entry.get("created_at")) if preserve_timestamps else None
        expires_at = _parse_timestamp(entry.get("expires_at")) if preserve_timestamps else None

        # Extract optional fields shared across tiers
        record_type = entry.get("record_type")
        grounding = entry.get("grounding")
        project_scope = entry.get("project_scope")

        try:
            if tier == "journal":
                entry_type = entry.get("entry_type", "imported")
                kwargs: dict = {}
                if record_type:
                    kwargs["record_type"] = record_type
                if grounding:
                    kwargs["grounding"] = grounding
                await mem.journal.add(
                    agent_id=entry_agent,
                    entry_type=entry_type,
                    title=title,
                    content=content,
                    importance=importance,
                    tags=tags,
                    compress=compress,
                    created_at=created_at,
                    expires_at=expires_at,
                    **kwargs,
                )
            elif tier == "shared":
                kwargs = {}
                if record_type:
                    kwargs["record_type"] = record_type
                if grounding:
                    kwargs["grounding"] = grounding
                if project_scope is not None:
                    kwargs["project_scope"] = project_scope
                await mem.shared.save(
                    agent_id=entry_agent,
                    category=category,
                    key=key,
                    content=content,
                    importance=importance,
                    **kwargs,
                )
            else:  # Default: ltm
                kwargs = {}
                if record_type:
                    kwargs["record_type"] = record_type
                if grounding:
                    kwargs["grounding"] = grounding
                if project_scope is not None:
                    kwargs["project_scope"] = project_scope
                await mem.ltm.save(
                    agent_id=entry_agent,
                    category=category,
                    key=key,
                    content=content,
                    importance=importance,
                    compress=compress,
                    created_at=created_at,
                    **kwargs,
                )

            result.imported += 1

        except Exception as e:
            result.errors += 1
            result.details.append(f"Line {i}: {e}")

    return result
