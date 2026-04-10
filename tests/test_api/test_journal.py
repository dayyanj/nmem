"""Tests for journal endpoints."""

from __future__ import annotations

import pytest


async def test_create_journal_entry(client):
    r = await client.post("/v1/journal", json={
        "agent_id": "test-agent",
        "entry_type": "observation",
        "title": "First journal entry",
        "content": "Testing the REST API",
        "importance": 5,
        "compress": False,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["id"] > 0
    assert body["agent_id"] == "test-agent"
    assert body["title"] == "First journal entry"
    assert body["importance"] == 5


async def test_create_journal_with_tags(client):
    r = await client.post("/v1/journal", json={
        "agent_id": "tag-agent",
        "entry_type": "decision",
        "title": "Decision entry",
        "content": "We decided X",
        "importance": 6,
        "tags": ["decision", "architecture"],
        "compress": False,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["tags"] == ["decision", "architecture"]


async def test_invalid_importance_rejected(client):
    r = await client.post("/v1/journal", json={
        "agent_id": "test-agent",
        "title": "X",
        "content": "Y",
        "importance": 99,  # out of range
    })
    assert r.status_code == 422  # validation error
    assert r.json()["error"]["code"] == "validation_error"


async def test_recent_entries(client):
    # Create 3 entries
    for i in range(3):
        await client.post("/v1/journal", json={
            "agent_id": "recent-agent",
            "entry_type": "observation",
            "title": f"Entry {i}",
            "content": f"Content {i}",
            "importance": 5,
            "compress": False,
        })

    r = await client.get("/v1/journal/recent?agent_id=recent-agent&limit=5")
    assert r.status_code == 200
    entries = r.json()
    assert len(entries) == 3
    # Most recent first
    assert entries[0]["title"] in ("Entry 0", "Entry 1", "Entry 2")


async def test_recent_requires_agent_id(client):
    r = await client.get("/v1/journal/recent")
    assert r.status_code == 422
