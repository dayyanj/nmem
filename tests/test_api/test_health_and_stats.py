"""Tests for /v1/health, /v1/version, /v1/stats."""

from __future__ import annotations

import pytest


async def test_health_returns_ok(client):
    r = await client.get("/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["database"] == "SQLite"


async def test_version_has_schema(client):
    r = await client.get("/v1/version")
    assert r.status_code == 200
    body = r.json()
    assert "version" in body
    assert body["schema_version"] == 2


async def test_stats_shows_all_tiers(client):
    r = await client.get("/v1/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["total_entries"] == 0
    tier_names = {t["tier"] for t in body["tiers"]}
    assert "journal" in tier_names
    assert "ltm" in tier_names
    assert "shared" in tier_names
    assert "entity" in tier_names
    assert body["database"] == "SQLite"
    assert body["embedding_provider"] == "noop"


async def test_request_id_header(client):
    r = await client.get("/v1/health")
    assert "X-Request-ID" in r.headers
    assert "X-Response-Time-Ms" in r.headers


async def test_openapi_schema(client):
    r = await client.get("/openapi.json")
    assert r.status_code == 200
    schema = r.json()
    assert schema["info"]["title"] == "nmem API"
    # Spot-check some critical paths
    paths = schema["paths"]
    assert "/v1/journal" in paths
    assert "/v1/search" in paths
    assert "/v1/ltm/{agent_id}/{key}" in paths
