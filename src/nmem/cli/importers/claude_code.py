"""
Claude Code memory importer.

Parses ~/.claude/projects/*/memory/*.md files with YAML frontmatter
and imports them as LTM entries. Also imports project CLAUDE.md as
shared knowledge.
"""

from __future__ import annotations

import re
from pathlib import Path

from tqdm import tqdm

from nmem.cli.importers.base import ImportResult

# Claude Code memory type → nmem mapping
_TYPE_MAP = {
    "project": ("project", 6),
    "reference": ("reference", 5),
    "feedback": ("feedback", 7),
    "user": ("user_context", 5),
}

# Regex for simple YAML frontmatter (3 flat key: value lines)
_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL
)
_FIELD_RE = re.compile(r"^(\w+):\s*(.+)$", re.MULTILINE)


def _parse_memory_file(path: Path) -> dict | None:
    """Parse a Claude Code memory file with YAML frontmatter.

    Returns:
        Dict with keys: name, description, type, content. Or None if unparseable.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None

    match = _FRONTMATTER_RE.match(text)
    if not match:
        # No frontmatter — treat entire file as content
        return {
            "name": path.stem.replace("_", " ").title(),
            "description": "",
            "type": "project",
            "content": text.strip(),
        }

    frontmatter_block = match.group(1)
    content = match.group(2).strip()

    fields = dict(_FIELD_RE.findall(frontmatter_block))

    return {
        "name": fields.get("name", path.stem.replace("_", " ").title()),
        "description": fields.get("description", ""),
        "type": fields.get("type", "project"),
        "content": content,
    }


def discover_memory_files(
    claude_dir: Path, include_global: bool = False
) -> list[tuple[str, Path, str]]:
    """Discover all Claude Code memory files.

    Returns:
        List of (project_label, file_path, file_type) tuples.
        file_type is "memory" for memory/*.md, "claude_md" for CLAUDE.md.
    """
    files = []

    projects_dir = claude_dir / "projects"
    if projects_dir.is_dir():
        for project_dir in sorted(projects_dir.iterdir()):
            if not project_dir.is_dir():
                continue

            project_label = project_dir.name

            # Memory files
            memory_dir = project_dir / "memory"
            if memory_dir.is_dir():
                for md_file in sorted(memory_dir.glob("*.md")):
                    if md_file.name == "MEMORY.md":
                        continue  # Skip index files
                    files.append((project_label, md_file, "memory"))

            # Project CLAUDE.md
            claude_md = project_dir / "CLAUDE.md"
            if claude_md.is_file():
                files.append((project_label, claude_md, "claude_md"))

    # Global CLAUDE.md (only if explicitly requested)
    if include_global:
        global_claude = claude_dir / "CLAUDE.md"
        if global_claude.is_file():
            files.append(("global", global_claude, "claude_md"))

    return files


async def import_claude_code(
    mem,
    claude_dir: Path,
    agent_id: str = "claude-code",
    compress: bool = False,
    include_global: bool = False,
) -> ImportResult:
    """Import Claude Code memory files into nmem.

    Args:
        mem: Initialized MemorySystem.
        claude_dir: Path to ~/.claude directory.
        agent_id: Agent ID for imported entries.
        compress: Whether to LLM-compress content.
        include_global: Whether to import global CLAUDE.md.

    Returns:
        ImportResult with counts.
    """
    result = ImportResult()
    files = discover_memory_files(claude_dir, include_global)

    if not files:
        result.details.append(f"No memory files found in {claude_dir}")
        return result

    for project_label, file_path, file_type in tqdm(files, desc="Importing", unit="file"):
        try:
            if file_type == "memory":
                parsed = _parse_memory_file(file_path)
                if not parsed or not parsed["content"]:
                    result.skipped += 1
                    continue

                mem_type = parsed["type"]
                category, importance = _TYPE_MAP.get(mem_type, ("project", 5))
                key = file_path.stem  # filename without extension

                # Include project context in content
                content = parsed["content"]
                if parsed["description"]:
                    content = f"{parsed['description']}\n\n{content}"

                await mem.ltm.save(
                    agent_id=agent_id,
                    category=category,
                    key=f"{project_label}/{key}",
                    content=content,
                    importance=importance,
                    compress=compress,
                )
                result.imported += 1
                result.details.append(f"[LTM/{category}] {project_label}/{key}")

            elif file_type == "claude_md":
                content = file_path.read_text(encoding="utf-8").strip()
                if not content:
                    result.skipped += 1
                    continue

                key = f"claude_md/{project_label}"
                await mem.shared.save(
                    agent_id=agent_id,
                    category="instructions",
                    key=key,
                    content=content[:4000],  # Cap at 4K chars
                    importance=8,
                )
                result.imported += 1
                result.details.append(f"[Shared/instructions] {key}")

        except Exception as e:
            result.errors += 1
            result.details.append(f"Error {file_path.name}: {e}")

    return result
