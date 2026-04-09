"""Tests for ChatGPT conversations.json importer."""

import asyncio
import json

from nmem.cli.importers.chatgpt import _walk_active_branch, import_chatgpt


def _make_conversation(title, messages, create_time=1700000000):
    """Build a minimal ChatGPT conversation structure."""
    mapping = {}
    # Root node
    root_id = "root-000"
    mapping[root_id] = {"id": root_id, "message": None, "parent": None, "children": []}

    prev_id = root_id
    for i, (role, text) in enumerate(messages):
        node_id = f"msg-{i:03d}"
        mapping[node_id] = {
            "id": node_id,
            "message": {
                "id": node_id,
                "author": {"role": role},
                "create_time": create_time + i * 60,
                "content": {"content_type": "text", "parts": [text]},
                "status": "finished_successfully",
                "weight": 1.0,
            },
            "parent": prev_id,
            "children": [],
        }
        mapping[prev_id]["children"].append(node_id)
        prev_id = node_id

    return {
        "title": title,
        "create_time": create_time,
        "mapping": mapping,
        "default_model_slug": "gpt-4",
    }


def test_walk_active_branch():
    conv = _make_conversation("Test", [
        ("user", "Hello"),
        ("assistant", "Hi there!"),
        ("user", "How are you?"),
        ("assistant", "I'm doing well."),
    ])
    messages = _walk_active_branch(conv["mapping"])
    assert len(messages) == 4
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "Hello"
    assert messages[3]["content"] == "I'm doing well."


def test_walk_skips_system_messages():
    """System messages should not appear in the result."""
    mapping = {}
    root_id = "root"
    mapping[root_id] = {"id": root_id, "message": None, "parent": None, "children": ["sys"]}
    mapping["sys"] = {
        "id": "sys",
        "message": {"id": "sys", "author": {"role": "system"}, "content": {"content_type": "text", "parts": ["You are ChatGPT"]}, "weight": 1.0},
        "parent": root_id,
        "children": ["u1"],
    }
    mapping["u1"] = {
        "id": "u1",
        "message": {"id": "u1", "author": {"role": "user"}, "content": {"content_type": "text", "parts": ["Hello"]}, "weight": 1.0},
        "parent": "sys",
        "children": [],
    }
    messages = _walk_active_branch(mapping)
    assert len(messages) == 1
    assert messages[0]["role"] == "user"


def test_short_conversations_skipped(tmp_path):
    """Conversations shorter than min_messages are skipped."""
    data = [
        _make_conversation("Short chat", [
            ("user", "Hi"),
            ("assistant", "Hello!"),
        ]),
    ]
    f = tmp_path / "conversations.json"
    f.write_text(json.dumps(data))

    from nmem import MemorySystem, NmemConfig

    async def _test():
        config = NmemConfig(
            database_url="sqlite+aiosqlite:///:memory:",
            embedding={"provider": "noop"},
            llm={"provider": "noop"},
        )
        mem = MemorySystem(config)
        await mem.initialize()

        result = await import_chatgpt(mem, f, min_messages=4)
        assert result.imported == 0
        assert result.skipped == 1

        await mem.close()

    asyncio.run(_test())


def test_import_creates_journal(tmp_path):
    """Imported conversations become journal entries."""
    data = [
        _make_conversation("Deep discussion about databases", [
            ("user", "Tell me about PostgreSQL indexing"),
            ("assistant", "PostgreSQL supports B-tree, Hash, GiST, and GIN indexes..."),
            ("user", "What about vector indexes?"),
            ("assistant", "pgvector adds HNSW and IVFFlat index types for vector similarity..."),
            ("user", "How do I choose?"),
            ("assistant", "HNSW is generally preferred for its recall quality..."),
        ]),
    ]
    f = tmp_path / "conversations.json"
    f.write_text(json.dumps(data))

    from nmem import MemorySystem, NmemConfig

    async def _test():
        config = NmemConfig(
            database_url="sqlite+aiosqlite:///:memory:",
            embedding={"provider": "noop"},
            llm={"provider": "noop"},
        )
        mem = MemorySystem(config)
        await mem.initialize()

        result = await import_chatgpt(mem, f, min_messages=4)
        assert result.imported == 1
        assert result.errors == 0

        entries = await mem.journal.recent("chatgpt", days=365)
        assert len(entries) == 1
        assert "database" in entries[0].title.lower()

        await mem.close()

    asyncio.run(_test())
