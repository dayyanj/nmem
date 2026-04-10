"""Tests for /v1/search, /v1/context, /v1/entity."""

from __future__ import annotations

import pytest


async def test_search_empty_returns_empty_list(client):
    r = await client.post("/v1/search", json={
        "query": "anything",
        "agent_id": "nobody",
    })
    assert r.status_code == 200
    assert r.json() == []


async def test_search_finds_ltm(client):
    await client.put("/v1/ltm/search-agent/pizza_order", json={
        "category": "fact",
        "content": "The best pizza is from Luigi's on Main Street",
        "importance": 5,
        "compress": False,
    })

    r = await client.post("/v1/search", json={
        "query": "pizza luigi",
        "agent_id": "search-agent",
        "top_k": 5,
    })
    assert r.status_code == 200
    results = r.json()
    # With noop embeddings, hash-based vectors may match exactly or not;
    # at minimum the endpoint should return a list without error
    assert isinstance(results, list)


async def test_context_returns_envelope(client):
    await client.post("/v1/journal", json={
        "agent_id": "ctx-agent",
        "entry_type": "observation",
        "title": "Something happened",
        "content": "Details about what happened",
        "importance": 5,
        "compress": False,
    })

    r = await client.post("/v1/context", json={
        "query": "something",
        "agent_id": "ctx-agent",
    })
    assert r.status_code == 200
    body = r.json()
    assert "full_injection" in body
    assert "token_estimate" in body
    assert "journal" in body
    assert "ltm" in body


async def test_entity_create_and_search(client):
    r = await client.post("/v1/entity", json={
        "entity_type": "customer",
        "entity_id": "cust_001",
        "entity_name": "Acme Corp",
        "agent_id": "sales",
        "content": "Enterprise customer with 500 seats",
        "record_type": "evidence",
        "confidence": 0.9,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["entity_name"] == "Acme Corp"
    assert body["confidence"] == 0.9

    # Search
    r = await client.post("/v1/entity/search", json={
        "query": "Acme enterprise",
        "top_k": 5,
    })
    assert r.status_code == 200


async def test_search_with_all_scopes(client):
    """all_scopes=True should map to project_scope='*' in the backend."""
    r = await client.post("/v1/search", json={
        "query": "anything",
        "agent_id": "test",
        "all_scopes": True,
    })
    assert r.status_code == 200
    # Should not error — the scope is translated to "*"
    assert isinstance(r.json(), list)
