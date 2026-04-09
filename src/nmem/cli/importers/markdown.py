"""
Markdown directory importer.

Imports a directory of .md files as LTM entries.
First H1 becomes the title, rest becomes content.
"""

from __future__ import annotations

import re
from pathlib import Path

from tqdm import tqdm

from nmem.cli.importers.base import ImportResult

_H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)


def _parse_markdown(path: Path) -> tuple[str, str]:
    """Parse a markdown file into (title, content).

    Returns:
        (title, content) tuple. Title from first H1, or filename if no H1.
    """
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return path.stem.replace("_", " ").replace("-", " ").title(), ""

    match = _H1_RE.search(text)
    if match:
        title = match.group(1).strip()
        # Content is everything after the H1 line
        content = text[match.end():].strip()
    else:
        title = path.stem.replace("_", " ").replace("-", " ").title()
        content = text

    return title, content


def _sanitize_key(path: Path, base_dir: Path) -> str:
    """Generate a key from the file path relative to base directory."""
    try:
        relative = path.relative_to(base_dir)
    except ValueError:
        relative = Path(path.name)
    # Remove extension, lowercase, replace separators
    key = str(relative.with_suffix("")).lower()
    key = re.sub(r"[^a-z0-9/]", "_", key)
    key = re.sub(r"_+", "_", key).strip("_")
    return key


async def import_markdown(
    mem,
    directory: Path,
    agent_id: str = "default",
    category: str = "knowledge",
    compress: bool = False,
) -> ImportResult:
    """Import a directory of markdown files as LTM entries.

    Args:
        mem: Initialized MemorySystem.
        directory: Directory to scan for .md files.
        agent_id: Agent ID for imported entries.
        category: LTM category for all entries.
        compress: Whether to LLM-compress content.

    Returns:
        ImportResult with counts.
    """
    result = ImportResult()
    files = sorted(directory.rglob("*.md"))

    if not files:
        result.details.append(f"No .md files found in {directory}")
        return result

    # Phase 1: Parse all files
    batch: list[dict] = []
    for path in tqdm(files, desc="Parsing", unit="file"):
        try:
            title, content = _parse_markdown(path)
            if not content:
                result.skipped += 1
                continue

            key = _sanitize_key(path, directory)
            batch.append({
                "agent_id": agent_id,
                "category": category,
                "key": key,
                "content": f"{title}\n\n{content}" if title else content,
                "importance": 5,
            })
            result.details.append(f"[LTM/{category}] {key}")

        except Exception as e:
            result.errors += 1
            result.details.append(f"Error {path.name}: {e}")

    # Phase 2: Batch-embed and save
    if batch:
        try:
            saved = await mem.ltm.save_batch(batch, compress=compress)
            result.imported += len(saved)
        except Exception as e:
            result.errors += len(batch)
            result.details.append(f"Batch save failed: {e}")

    return result
