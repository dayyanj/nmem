"""
Pydantic request/response schemas for the nmem REST API.

Mirrors the dataclasses in nmem.types and adds Create/Update models for writes.
All responses wrap payloads in a consistent envelope:
    {"data": {...}, "meta": {"request_id": "..."}}
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field


# ── Envelope ──────────────────────────────────────────────────────────────────

T = TypeVar("T")


class Meta(BaseModel):
    request_id: str = ""
    duration_ms: int | None = None


class Envelope(BaseModel, Generic[T]):
    """Standard response envelope: {data, meta}."""

    data: T
    meta: Meta = Field(default_factory=Meta)


def to_dict(obj: Any) -> dict[str, Any]:
    """Convert a dataclass (or list of them) to a dict for JSON response."""
    if obj is None:
        return None  # type: ignore
    if isinstance(obj, list):
        return [to_dict(item) for item in obj]  # type: ignore
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    if isinstance(obj, dict):
        return obj
    return obj


# ── Journal ───────────────────────────────────────────────────────────────────


class JournalEntryCreate(BaseModel):
    agent_id: str
    entry_type: str = "observation"
    title: str
    content: str
    importance: int = Field(default=5, ge=1, le=10)
    session_id: str | None = None
    tags: list[str] | None = None
    record_type: str = "evidence"
    grounding: str = "inferred"
    compress: bool = True
    project_scope: str | None = None


class JournalEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    agent_id: str
    entry_type: str
    title: str
    content: str
    importance: int
    relevance_score: float
    access_count: int
    expires_at: datetime | None = None
    promoted_to_ltm: bool
    context_thread_id: str | None = None
    record_type: str
    grounding: str
    status: str
    tags: list[str] | None = None
    pointers: list[dict[str, Any]] | None = None
    project_scope: str | None = None
    created_at: datetime | None = None


# ── LTM ───────────────────────────────────────────────────────────────────────


class LTMEntryCreate(BaseModel):
    category: str = "fact"
    content: str
    importance: int = Field(default=5, ge=1, le=10)
    source: str = "agent"
    record_type: str = "fact"
    grounding: str = "inferred"
    compress: bool = True
    project_scope: str | None = None


class LTMEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    agent_id: str
    category: str
    key: str
    content: str
    importance: int
    salience: float
    access_count: int
    source: str
    record_type: str
    grounding: str
    status: str
    version: int
    context_thread_id: str | None = None
    project_scope: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ── Shared ────────────────────────────────────────────────────────────────────


class SharedEntryCreate(BaseModel):
    category: str
    content: str
    agent_id: str
    importance: int = Field(default=5, ge=1, le=10)
    record_type: str = "fact"
    grounding: str = "confirmed"
    project_scope: str | None = None


class SharedEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    category: str
    key: str
    content: str
    created_by: str
    last_updated_by: str
    confirmed: bool
    importance: int
    record_type: str
    grounding: str
    status: str
    version: int
    change_log: list[dict[str, Any]] | None = None
    project_scope: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ── Entity ────────────────────────────────────────────────────────────────────


class EntityRecordCreate(BaseModel):
    entity_type: str
    entity_id: str
    entity_name: str
    agent_id: str
    content: str
    record_type: str = "evidence"
    confidence: float = 0.8
    grounding: str = "inferred"
    tags: list[str] | None = None
    evidence_refs: list[dict[str, Any]] | None = None
    project_scope: str | None = None


class EntityRecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    entity_type: str
    entity_id: str
    entity_name: str
    agent_id: str
    record_type: str
    content: str
    confidence: float
    grounding: str
    status: str
    evidence_refs: list[dict[str, Any]] | None = None
    tags: list[str] | None = None
    context_thread_id: str | None = None
    version: int
    project_scope: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class EntitySearchRequest(BaseModel):
    query: str
    entity_type: str | None = None
    entity_id: str | None = None
    top_k: int = Field(default=5, ge=1, le=50)
    agent_id: str | None = None
    project_scope: str | None = None


# ── Search ────────────────────────────────────────────────────────────────────


class SearchRequest(BaseModel):
    query: str
    agent_id: str = "default"
    tiers: list[str] | None = None
    top_k: int = Field(default=10, ge=1, le=100)
    all_scopes: bool = False
    project_scope: str | None = None


class SearchResultItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tier: str
    id: int
    score: float
    content: str
    title: str | None = None
    key: str | None = None
    agent_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# ── Context (Prompt) ──────────────────────────────────────────────────────────


class ContextRequest(BaseModel):
    query: str
    agent_id: str = "default"


class ContextResponse(BaseModel):
    working: str = ""
    journal: str = ""
    ltm: str = ""
    shared: str = ""
    entity: str = ""
    policy: str = ""
    deja_vu: str = ""
    full_injection: str = ""
    token_estimate: int = 0


# ── Knowledge Links ───────────────────────────────────────────────────────────


class KnowledgeLinkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_id: int
    source_tier: str
    target_id: int
    target_tier: str
    link_type: str
    strength: float
    evidence: str | None = None
    created_at: datetime | None = None


# ── Stats / Health ────────────────────────────────────────────────────────────


class TierStats(BaseModel):
    tier: str
    count: int


class StatsResponse(BaseModel):
    tiers: list[TierStats]
    total_entries: int
    database: str
    embedding_provider: str
    embedding_model: str
    llm_provider: str
    project_scope: str | None = None


class HealthResponse(BaseModel):
    status: str = "ok"
    database: str
    schema_version: int | None = None


class VersionResponse(BaseModel):
    version: str
    schema_version: int | None = None


# ── Consolidation ─────────────────────────────────────────────────────────────


class ConsolidationStatsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    expired_deleted: int = 0
    expired_promoted: int = 0
    promoted_to_ltm: int = 0
    promoted_to_shared: int = 0
    duplicates_merged: int = 0
    salience_decayed: int = 0
    curiosity_decayed: int = 0
    patterns_synthesized: int = 0
    links_created: int = 0
    duration_seconds: float = 0.0
