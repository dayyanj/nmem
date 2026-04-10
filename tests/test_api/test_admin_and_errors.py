"""Tests for admin endpoints and error envelope consistency."""

from __future__ import annotations

import pytest


async def test_consolidate_returns_stats(client):
    r = await client.post("/v1/admin/consolidate")
    assert r.status_code == 200
    body = r.json()
    assert "promoted_to_ltm" in body
    assert "duration_seconds" in body
    assert "links_created" in body


async def test_error_envelope_shape_404(client):
    r = await client.get("/v1/ltm/ghost/missing")
    assert r.status_code == 404
    body = r.json()
    assert "error" in body
    assert body["error"]["code"] == "not_found"
    assert "message" in body["error"]
    assert "meta" in body
    assert "request_id" in body["meta"]


async def test_error_envelope_shape_422(client):
    r = await client.post("/v1/journal", json={"agent_id": "x"})  # missing fields
    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"] == "validation_error"
    assert "errors" in body["error"]["details"]


async def test_method_not_allowed(client):
    r = await client.delete("/v1/journal")
    assert r.status_code == 405
    body = r.json()
    assert body["error"]["code"] == "method_not_allowed"


async def test_unknown_route_404(client):
    r = await client.get("/v1/nonexistent")
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "not_found"
