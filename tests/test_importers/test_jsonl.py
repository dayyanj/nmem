"""Tests for JSONL importer."""

import asyncio
import json

from nmem.cli.importers.jsonl import import_jsonl


def test_import_ltm_default(tmp_path):
    """Entries without tier field go to LTM."""
    f = tmp_path / "data.jsonl"
    f.write_text(json.dumps({"content": "Test fact", "key": "test_fact"}) + "\n")

    from nmem import MemorySystem, NmemConfig

    async def _test():
        config = NmemConfig(
            database_url="sqlite+aiosqlite:///:memory:",
            embedding={"provider": "noop"},
            llm={"provider": "noop"},
        )
        mem = MemorySystem(config)
        await mem.initialize()

        result = await import_jsonl(mem, f)
        assert result.imported == 1
        assert result.errors == 0

        await mem.close()

    asyncio.run(_test())


def test_import_journal(tmp_path):
    """tier: journal routes to journal tier."""
    f = tmp_path / "data.jsonl"
    f.write_text(json.dumps({
        "content": "Session notes",
        "title": "Session 1",
        "tier": "journal",
    }) + "\n")

    from nmem import MemorySystem, NmemConfig

    async def _test():
        config = NmemConfig(
            database_url="sqlite+aiosqlite:///:memory:",
            embedding={"provider": "noop"},
            llm={"provider": "noop"},
        )
        mem = MemorySystem(config)
        await mem.initialize()

        result = await import_jsonl(mem, f)
        assert result.imported == 1

        entries = await mem.journal.recent("default", days=1)
        assert len(entries) == 1
        assert entries[0].title == "Session 1"

        await mem.close()

    asyncio.run(_test())


def test_invalid_json_skipped(tmp_path):
    """Bad JSON lines increment errors, don't crash."""
    f = tmp_path / "data.jsonl"
    f.write_text(
        '{"content": "good line"}\n'
        'not valid json\n'
        '{"no_content_field": true}\n'
        '{"content": "another good line"}\n'
    )

    from nmem import MemorySystem, NmemConfig

    async def _test():
        config = NmemConfig(
            database_url="sqlite+aiosqlite:///:memory:",
            embedding={"provider": "noop"},
            llm={"provider": "noop"},
        )
        mem = MemorySystem(config)
        await mem.initialize()

        result = await import_jsonl(mem, f)
        assert result.imported == 2
        assert result.errors == 2  # 1 bad JSON + 1 missing content

        await mem.close()

    asyncio.run(_test())
