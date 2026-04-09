"""
nmem public types — dataclasses and TypedDicts for all return values.

These types decouple consumers from SQLAlchemy models so the public API
returns plain Python objects rather than ORM instances.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


# ── Tier Results ──────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class WorkingSlot:
    """A single working memory slot."""

    session_id: str
    agent_id: str
    slot: str
    content: str
    priority: int = 5
    context_thread_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class JournalEntry:
    """A journal entry (Tier 2)."""

    id: int
    agent_id: str
    entry_type: str
    title: str
    content: str
    importance: int = 5
    relevance_score: float = 0.5
    access_count: int = 0
    expires_at: datetime | None = None
    promoted_to_ltm: bool = False
    context_thread_id: str | None = None
    record_type: str = "evidence"
    grounding: str = "inferred"
    status: str = "draft"
    tags: list[str] | None = None
    pointers: list[dict[str, Any]] | None = None
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class LTMEntry:
    """A long-term memory entry (Tier 3)."""

    id: int
    agent_id: str
    category: str
    key: str
    content: str
    importance: int = 5
    confidence: float = 1.0
    access_count: int = 0
    source: str = "agent"
    record_type: str = "fact"
    grounding: str = "inferred"
    status: str = "validated"
    version: int = 1
    context_thread_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SharedEntry:
    """A shared knowledge entry (Tier 4)."""

    id: int
    category: str
    key: str
    content: str
    created_by: str
    last_updated_by: str
    confirmed: bool = False
    importance: int = 5
    record_type: str = "fact"
    grounding: str = "confirmed"
    status: str = "validated"
    version: int = 1
    change_log: list[dict[str, Any]] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class EntityRecord:
    """An entity memory record (Tier 5)."""

    id: int
    entity_type: str
    entity_id: str
    entity_name: str
    agent_id: str
    record_type: str
    content: str
    confidence: float = 0.8
    grounding: str = "inferred"
    status: str = "draft"
    evidence_refs: list[dict[str, Any]] | None = None
    tags: list[str] | None = None
    context_thread_id: str | None = None
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PolicyEntry:
    """A policy memory entry (Tier 6)."""

    id: int
    scope: str
    category: str
    key: str
    content: str
    created_by: str
    approved_by: str | None = None
    status: str = "active"
    version: int = 1
    change_log: list[dict[str, Any]] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ── Search Results ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SearchResult:
    """A single search result from any tier."""

    tier: str  # "journal", "ltm", "shared", "entity", "policy", "delegation"
    id: int
    score: float
    content: str
    title: str | None = None
    key: str | None = None
    agent_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# ── Prompt Context ────────────────────────────────────────────────────────────


@dataclass(slots=True)
class PromptContext:
    """Assembled memory context ready for injection into agent prompts."""

    working: str = ""
    journal: str = ""
    ltm: str = ""
    shared: str = ""
    entity: str = ""
    policy: str = ""
    deja_vu: str = ""

    @property
    def full_injection(self) -> str:
        """All memory sections combined with headers, ready for system prompt."""
        sections: list[str] = []
        if self.policy:
            sections.append(f"## Active Policies\n{self.policy}")
        if self.shared:
            sections.append(f"## Shared Knowledge\n{self.shared}")
        if self.ltm:
            sections.append(f"## Your Long-Term Memory\n{self.ltm}")
        if self.journal:
            sections.append(f"## Recent Activity\n{self.journal}")
        if self.working:
            sections.append(f"## Current Session\n{self.working}")
        if self.entity:
            sections.append(f"## Entity Dossier\n{self.entity}")
        if self.deja_vu:
            sections.append(f"## Similar Past Experience\n{self.deja_vu}")
        if not sections:
            return ""
        return "# Agent Memory\n\n" + "\n\n".join(sections)

    @property
    def token_estimate(self) -> int:
        """Rough token estimate (chars / 4)."""
        return len(self.full_injection) // 4


# ── Conflict ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class MemoryConflictInfo:
    """Information about a detected memory conflict."""

    id: int
    record_a_table: str
    record_a_id: int
    record_b_table: str
    record_b_id: int
    agent_a: str
    agent_b: str
    similarity_score: float
    description: str
    status: str = "open"
    created_at: datetime | None = None


# ── Consolidation Stats ──────────────────────────────────────────────────────


@dataclass(slots=True)
class ConsolidationStats:
    """Statistics from a consolidation cycle."""

    expired_deleted: int = 0
    expired_promoted: int = 0
    promoted_to_ltm: int = 0
    promoted_to_shared: int = 0
    duplicates_merged: int = 0
    confidence_decayed: int = 0
    curiosity_decayed: int = 0
    patterns_synthesized: int = 0
    duration_seconds: float = 0.0


# ── Curiosity Signal ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CuriositySignalInfo:
    """A curiosity-driven exploration signal."""

    id: int
    source_agent: str
    trigger_type: str
    summary: str
    composite_score: float
    novelty_score: float = 0.5
    uncertainty_score: float = 0.5
    conflict_score: float = 0.0
    recurrence_score: float = 0.0
    business_impact: float = 0.5
    status: str = "pending"
    entity_type: str | None = None
    entity_id: str | None = None
    created_at: datetime | None = None


# ── Delegation (for déjà vu) ─────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DelegationRecord:
    """A past task delegation record (used for déjà vu matching)."""

    id: int
    delegating_agent: str
    target_agent: str
    task_type: str
    instruction: str
    status: str
    result_summary: str | None = None
    created_at: datetime | None = None
    completed_at: datetime | None = None
