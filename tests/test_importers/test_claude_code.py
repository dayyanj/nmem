"""Tests for Claude Code memory importer."""

import asyncio
import pytest

from nmem.cli.importers.claude_code import (
    _parse_memory_file,
    discover_memory_files,
    import_claude_code,
)


@pytest.fixture
def claude_dir(tmp_path):
    """Create a fake ~/.claude/ structure."""
    project = tmp_path / "projects" / "-test-project" / "memory"
    project.mkdir(parents=True)

    (project / "test_memory.md").write_text(
        "---\nname: Test Memory\ndescription: A test project memory\ntype: project\n---\n"
        "This is the content of the test memory.\n\nIt has multiple paragraphs."
    )
    (project / "feedback_rule.md").write_text(
        "---\nname: Important Rule\ndescription: Never do X\ntype: feedback\n---\n"
        "Never deploy on Fridays."
    )
    (project / "MEMORY.md").write_text("# Index\n- [test](test_memory.md)")

    # Project CLAUDE.md
    project_root = tmp_path / "projects" / "-test-project"
    (project_root / "CLAUDE.md").write_text("Project instructions here.")

    return tmp_path


def test_parse_memory_file(tmp_path):
    f = tmp_path / "test.md"
    f.write_text(
        "---\nname: My Memory\ndescription: Desc here\ntype: reference\n---\nContent body."
    )
    parsed = _parse_memory_file(f)
    assert parsed["name"] == "My Memory"
    assert parsed["description"] == "Desc here"
    assert parsed["type"] == "reference"
    assert parsed["content"] == "Content body."


def test_parse_no_frontmatter(tmp_path):
    f = tmp_path / "no_front.md"
    f.write_text("Just plain markdown content.")
    parsed = _parse_memory_file(f)
    assert parsed["content"] == "Just plain markdown content."
    assert parsed["type"] == "project"  # default


def test_discover_files(claude_dir):
    files = discover_memory_files(claude_dir, include_global=False)
    # Should find 2 memory files + 1 CLAUDE.md, skip MEMORY.md
    types = [f[2] for f in files]
    assert types.count("memory") == 2
    assert types.count("claude_md") == 1


def test_discover_skips_memory_md(claude_dir):
    files = discover_memory_files(claude_dir)
    filenames = [f[1].name for f in files]
    assert "MEMORY.md" not in filenames


def test_import_maps_types(claude_dir):
    """Import correctly maps Claude Code types to nmem categories."""
    from nmem import MemorySystem, NmemConfig

    async def _test():
        config = NmemConfig(
            database_url="sqlite+aiosqlite:///:memory:",
            embedding={"provider": "noop"},
            llm={"provider": "noop"},
        )
        mem = MemorySystem(config)
        await mem.initialize()

        result = await import_claude_code(mem, claude_dir, agent_id="test")
        assert result.imported >= 3  # 2 memories + 1 CLAUDE.md
        assert result.skipped == 0
        assert result.errors == 0

        # Check that feedback type got importance 7
        details = "\n".join(result.details)
        assert "LTM/feedback" in details
        assert "LTM/project" in details
        assert "Shared/instructions" in details

        await mem.close()

    asyncio.run(_test())
