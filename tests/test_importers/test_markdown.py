"""Tests for markdown directory importer."""

import asyncio
import pytest

from nmem.cli.importers.markdown import _parse_markdown, import_markdown


def test_parse_with_h1(tmp_path):
    f = tmp_path / "test.md"
    f.write_text("# My Title\n\nSome content here.\n\nMore content.")
    title, content = _parse_markdown(f)
    assert title == "My Title"
    assert "Some content here" in content


def test_parse_without_h1(tmp_path):
    f = tmp_path / "my_notes.md"
    f.write_text("Just some text without a heading.")
    title, content = _parse_markdown(f)
    assert title == "My Notes"  # Derived from filename
    assert "Just some text" in content


def test_parse_empty(tmp_path):
    f = tmp_path / "empty.md"
    f.write_text("")
    title, content = _parse_markdown(f)
    assert content == ""


def test_import_recursive(tmp_path):
    """Finds .md files in subdirectories."""
    sub = tmp_path / "docs" / "guides"
    sub.mkdir(parents=True)
    (tmp_path / "docs" / "readme.md").write_text("# Readme\nTop-level doc.")
    (sub / "setup.md").write_text("# Setup Guide\nHow to set up.")

    from nmem import MemorySystem, NmemConfig

    async def _test():
        config = NmemConfig(
            database_url="sqlite+aiosqlite:///:memory:",
            embedding={"provider": "noop"},
            llm={"provider": "noop"},
        )
        mem = MemorySystem(config)
        await mem.initialize()

        result = await import_markdown(mem, tmp_path / "docs")
        assert result.imported == 2
        assert result.errors == 0

        await mem.close()

    asyncio.run(_test())
