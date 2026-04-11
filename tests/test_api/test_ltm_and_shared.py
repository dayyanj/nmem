"""Tests for LTM and shared endpoints."""

from __future__ import annotations

import pytest


async def test_save_ltm(client):
    r = await client.put("/v1/ltm/test-agent/db_host", json={
        "category": "fact",
        "content": "Database is at db.example.com:5432",
        "importance": 7,
        "compress": False,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["key"] == "db_host"
    assert body["agent_id"] == "test-agent"
    assert body["category"] == "fact"
    assert body["version"] == 1


async def test_get_ltm(client):
    await client.put("/v1/ltm/agent-a/key1", json={
        "category": "fact",
        "content": "Some fact",
        "importance": 5,
        "compress": False,
    })
    r = await client.get("/v1/ltm/agent-a/key1")
    assert r.status_code == 200
    assert r.json()["content"] == "Some fact"


async def test_ltm_response_shape_uses_salience(client):
    """Phase 1 guard: LTM responses expose `salience`, not `confidence`."""
    await client.put("/v1/ltm/shape-agent/shape-key", json={
        "category": "fact",
        "content": "shape check",
        "importance": 5,
        "compress": False,
    })
    r = await client.get("/v1/ltm/shape-agent/shape-key")
    assert r.status_code == 200
    body = r.json()
    assert "salience" in body
    assert "confidence" not in body
    assert isinstance(body["salience"], (int, float))


async def test_get_ltm_not_found(client):
    r = await client.get("/v1/ltm/nobody/nothing")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


async def test_list_ltm_keys(client):
    await client.put("/v1/ltm/agent-x/key1", json={"category": "fact", "content": "A", "compress": False})
    await client.put("/v1/ltm/agent-x/key2", json={"category": "fact", "content": "B", "compress": False})

    r = await client.get("/v1/ltm/agent-x")
    assert r.status_code == 200
    keys = r.json()["keys"]
    assert "key1" in keys
    assert "key2" in keys


async def test_save_shared(client):
    r = await client.put("/v1/shared/company_name", json={
        "category": "fact",
        "content": "Acme Corporation",
        "agent_id": "admin",
        "importance": 9,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["key"] == "company_name"
    assert body["created_by"] == "admin"
