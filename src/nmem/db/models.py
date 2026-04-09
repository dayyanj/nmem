"""
nmem SQLAlchemy models — all memory tier tables.

These models are designed to work with both PostgreSQL (pgvector) and SQLite
(numpy fallback). The Vector column is conditionally defined based on
pgvector availability.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    func,
    types,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# PostgreSQL-specific types with fallbacks for portability
try:
    from pgvector.sqlalchemy import Vector as PgVector

    def VectorColumn(dim: int):  # noqa: N802
        return mapped_column(PgVector(dim), nullable=True)

    HAS_PGVECTOR = True
except ImportError:
    from sqlalchemy import LargeBinary

    def VectorColumn(dim: int):  # noqa: N802
        return mapped_column(LargeBinary, nullable=True)

    HAS_PGVECTOR = False


class JSONType(types.TypeDecorator):
    """JSONB on PostgreSQL, JSON on everything else."""

    from sqlalchemy import JSON
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import JSONB
            return dialect.type_descriptor(JSONB())
        from sqlalchemy import JSON
        return dialect.type_descriptor(JSON())


class TSVType(types.TypeDecorator):
    """TSVECTOR on PostgreSQL, TEXT on everything else."""

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import TSVECTOR
            return dialect.type_descriptor(TSVECTOR())
        return dialect.type_descriptor(Text())


class Base(DeclarativeBase):
    """nmem declarative base."""

    type_annotation_map = {
        dict: JSONType,
        list: JSONType,
    }


# ── Tier 1: Working Memory ──────────────────────────────────────────────────


class WorkingMemory(Base):
    """Ephemeral per-session context — current task, decisions, scratchpad.
    Cleared on session end."""

    __tablename__ = "nmem_working_memory"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(100))
    agent_id: Mapped[str] = mapped_column(String(100))
    slot: Mapped[str] = mapped_column(String(50))
    content: Mapped[str] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer, default=5)  # 1=highest
    context_thread_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_nmem_wm_session", "session_id"),
        Index("ix_nmem_wm_agent_slot", "agent_id", "slot"),
    )


# ── Tier 2: Journal ─────────────────────────────────────────────────────────


class JournalEntryModel(Base):
    """Short-term activity log — 30-day decay, access tracking, promotion.
    High-importance entries auto-promote to LTM."""

    __tablename__ = "nmem_journal_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(100))
    session_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    entry_type: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text)
    content_tsv = mapped_column(TSVType, nullable=True)

    # Importance and decay
    importance: Mapped[int] = mapped_column(Integer, default=5)
    relevance_score: Mapped[float] = mapped_column(Float, default=0.5)
    access_count: Mapped[int] = mapped_column(Integer, default=0)
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    promoted_to_ltm: Mapped[bool] = mapped_column(Boolean, default=False)

    # Semantic clustering
    context_thread_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    embedding = VectorColumn(384)

    # Metadata
    tags: Mapped[list | None] = mapped_column(nullable=True)
    related_entities: Mapped[dict | None] = mapped_column(nullable=True)
    pointers: Mapped[list | None] = mapped_column(nullable=True)

    # Record typing
    record_type: Mapped[str] = mapped_column(String(20), default="evidence")
    grounding: Mapped[str] = mapped_column(String(20), default="inferred")
    status: Mapped[str] = mapped_column(String(20), default="draft")

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_nmem_journal_agent", "agent_id"),
        Index("ix_nmem_journal_type", "entry_type"),
        Index("ix_nmem_journal_created", "created_at"),
        Index("ix_nmem_journal_expires", "expires_at"),
        Index("ix_nmem_journal_importance", "agent_id", "importance"),
        Index("ix_nmem_journal_thread", "context_thread_id"),
        Index("ix_nmem_journal_record_type", "record_type"),
        Index("ix_nmem_journal_status", "status"),
    )


# ── Tier 3: Long-Term Memory ────────────────────────────────────────────────


class LTMModel(Base):
    """Permanent per-agent knowledge — categorized, versioned, confidence decay."""

    __tablename__ = "nmem_long_term_memory"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(100))
    category: Mapped[str] = mapped_column(String(50))
    key: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    content_tsv = mapped_column(TSVType, nullable=True)

    # Importance and staleness
    importance: Mapped[int] = mapped_column(Integer, default=5)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    access_count: Mapped[int] = mapped_column(Integer, default=0)
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_validated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    accessed_by_agents: Mapped[list | None] = mapped_column(nullable=True)

    # Shared promotion tracking
    promoted_to_shared: Mapped[bool] = mapped_column(default=False)

    # Source tracking
    source: Mapped[str] = mapped_column(String(30), default="agent")
    source_journal_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Semantic clustering
    context_thread_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    embedding = VectorColumn(384)

    # Record typing
    record_type: Mapped[str] = mapped_column(String(20), default="fact")
    grounding: Mapped[str] = mapped_column(String(20), default="inferred")
    status: Mapped[str] = mapped_column(String(20), default="validated")

    # Versioning
    version: Mapped[int] = mapped_column(Integer, default=1)
    supersedes_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_nmem_ltm_agent_category", "agent_id", "category"),
        Index("ix_nmem_ltm_agent_key", "agent_id", "key", unique=True),
        Index("ix_nmem_ltm_importance", "agent_id", "importance"),
        Index("ix_nmem_ltm_updated", "updated_at"),
        Index("ix_nmem_ltm_thread", "context_thread_id"),
        Index("ix_nmem_ltm_record_type", "record_type"),
        Index("ix_nmem_ltm_status", "status"),
    )


# ── Tier 4: Shared Knowledge ────────────────────────────────────────────────


class SharedKnowledgeModel(Base):
    """Cross-agent knowledge — writable by any agent, readable by all."""

    __tablename__ = "nmem_shared_knowledge"

    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[str] = mapped_column(String(50))
    key: Mapped[str] = mapped_column(String(200), unique=True)
    content: Mapped[str] = mapped_column(Text)
    content_tsv = mapped_column(TSVType, nullable=True)

    # Authorship
    created_by: Mapped[str] = mapped_column(String(100))
    last_updated_by: Mapped[str] = mapped_column(String(100))

    # Validation
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    importance: Mapped[int] = mapped_column(Integer, default=5)

    # Semantic search
    embedding = VectorColumn(384)

    # Record typing
    record_type: Mapped[str] = mapped_column(String(20), default="fact")
    grounding: Mapped[str] = mapped_column(String(20), default="confirmed")
    status: Mapped[str] = mapped_column(String(20), default="validated")

    # Change tracking
    version: Mapped[int] = mapped_column(Integer, default=1)
    change_log: Mapped[list | None] = mapped_column(nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_nmem_shared_category", "category"),
        Index("ix_nmem_shared_importance", "importance"),
        Index("ix_nmem_shared_status", "status"),
        Index("ix_nmem_shared_updated", "updated_at"),
    )


# ── Tier 5: Entity Memory ───────────────────────────────────────────────────


class EntityMemoryModel(Base):
    """Collaborative workspace per business object.
    Multiple agents read/write with evidence-based grounding."""

    __tablename__ = "nmem_entity_memory"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[str] = mapped_column(String(100))
    entity_name: Mapped[str] = mapped_column(String(300))
    agent_id: Mapped[str] = mapped_column(String(100))

    record_type: Mapped[str] = mapped_column(String(20), default="evidence")
    content: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.8)
    grounding: Mapped[str] = mapped_column(String(20), default="inferred")
    status: Mapped[str] = mapped_column(String(20), default="draft")

    evidence_refs: Mapped[list | None] = mapped_column(nullable=True)
    tags: Mapped[list | None] = mapped_column(nullable=True)
    embedding = VectorColumn(384)
    content_tsv = mapped_column(TSVType, nullable=True)
    context_thread_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    version: Mapped[int] = mapped_column(Integer, default=1)
    superseded_by: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_nmem_entity_type_id", "entity_type", "entity_id"),
        Index("ix_nmem_entity_agent", "agent_id"),
        Index("ix_nmem_entity_status", "entity_type", "status"),
        Index("ix_nmem_entity_record", "record_type"),
    )


# ── Tier 6: Policy Memory ───────────────────────────────────────────────────


class PolicyMemoryModel(Base):
    """Behavioral rules and governance for the agent organization.
    Versioned and auditable with writer/proposer permissions."""

    __tablename__ = "nmem_policy_memory"

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(100))
    category: Mapped[str] = mapped_column(String(50))
    key: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)

    created_by: Mapped[str] = mapped_column(String(100))
    approved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")

    version: Mapped[int] = mapped_column(Integer, default=1)
    change_log: Mapped[list | None] = mapped_column(nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_nmem_policy_scope_cat", "scope", "category"),
        Index("ix_nmem_policy_scope_key", "scope", "key", unique=True),
        Index("ix_nmem_policy_status", "status"),
    )


# ── Memory Conflicts ────────────────────────────────────────────────────────


class MemoryConflictModel(Base):
    """Tracks contradictions between memory entries across agents."""

    __tablename__ = "nmem_memory_conflicts"

    id: Mapped[int] = mapped_column(primary_key=True)
    record_a_table: Mapped[str] = mapped_column(String(50))
    record_a_id: Mapped[int] = mapped_column(Integer)
    record_b_table: Mapped[str] = mapped_column(String(50))
    record_b_id: Mapped[int] = mapped_column(Integer)
    agent_a: Mapped[str] = mapped_column(String(100))
    agent_b: Mapped[str] = mapped_column(String(100))

    similarity_score: Mapped[float] = mapped_column(Float)
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open")
    resolved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (Index("ix_nmem_conflict_status", "status"),)


# ── Curiosity Signals ───────────────────────────────────────────────────────


class CuriositySignalModel(Base):
    """Curiosity-driven exploration signal — detected gaps, conflicts, unknowns."""

    __tablename__ = "nmem_curiosity_signals"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_agent: Mapped[str] = mapped_column(String(100))
    entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    trigger_type: Mapped[str] = mapped_column(String(30))
    summary: Mapped[str] = mapped_column(Text)
    novelty_score: Mapped[float] = mapped_column(Float, default=0.5)
    uncertainty_score: Mapped[float] = mapped_column(Float, default=0.5)
    conflict_score: Mapped[float] = mapped_column(Float, default=0.0)
    recurrence_score: Mapped[float] = mapped_column(Float, default=0.0)
    business_impact: Mapped[float] = mapped_column(Float, default=0.5)
    composite_score: Mapped[float] = mapped_column(Float, default=0.0)
    resolution_cost: Mapped[float] = mapped_column(Float, default=0.5)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    resolution_outcome: Mapped[str | None] = mapped_column(String(30), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    delegation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_nmem_curiosity_status", "status"),
        Index("ix_nmem_curiosity_trigger", "trigger_type"),
        Index("ix_nmem_curiosity_score", "composite_score"),
        Index("ix_nmem_curiosity_created", "created_at"),
    )


# ── Delegation History (for deja vu) ────────────────────────────────────────


class DelegationModel(Base):
    """Task delegation history — used for deja vu similarity matching."""

    __tablename__ = "nmem_delegations"

    id: Mapped[int] = mapped_column(primary_key=True)
    delegating_agent: Mapped[str] = mapped_column(String(100))
    target_agent: Mapped[str] = mapped_column(String(100))
    task_type: Mapped[str] = mapped_column(String(50))
    instruction: Mapped[str] = mapped_column(Text)
    context_data: Mapped[dict | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_data: Mapped[dict | None] = mapped_column(nullable=True)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    embedding = VectorColumn(384)
    content_tsv = mapped_column(TSVType, nullable=True)

    __table_args__ = (
        Index("ix_nmem_deleg_agents", "delegating_agent", "target_agent"),
        Index("ix_nmem_deleg_status", "status"),
        Index("ix_nmem_deleg_created", "created_at"),
    )


# ── Performance Scores ──────────────────────────────────────────────────────


class PerformanceScoreModel(Base):
    """Rolling performance scores per agent — updated by consolidator."""

    __tablename__ = "nmem_performance_scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(100), index=True)
    period_start: Mapped[datetime] = mapped_column(DateTime)
    period_end: Mapped[datetime] = mapped_column(DateTime)
    delegations_completed: Mapped[int] = mapped_column(Integer, default=0)
    delegations_failed: Mapped[int] = mapped_column(Integer, default=0)
    approvals: Mapped[int] = mapped_column(Integer, default=0)
    rejections: Mapped[int] = mapped_column(Integer, default=0)
    composite_score: Mapped[float] = mapped_column(Float, default=5.0)
    extra_data: Mapped[dict | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (Index("ix_nmem_perf_agent_period", "agent_id", "period_end"),)


# ── Scheduled Followups (Prospective Memory) ────────────────────────────────


class ScheduledFollowupModel(Base):
    """Time-based and condition-based follow-up triggers.

    Supports: time_based (fire at time), condition_based (prospective memory),
    open_loop (nagging for unresolved tasks).
    """

    __tablename__ = "nmem_scheduled_followups"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    scheduled_by: Mapped[str] = mapped_column(String(100))
    assigned_to: Mapped[str] = mapped_column(String(100))
    follow_up_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    reason: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(String(50), default="check")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Cognitive capabilities
    trigger_type: Mapped[str] = mapped_column(String(30), default="time_based")
    trigger_condition: Mapped[str | None] = mapped_column(Text, nullable=True)
    trigger_embedding = VectorColumn(384)
    cycle_count: Mapped[int] = mapped_column(Integer, default=0)
    source_delegation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        Index("ix_nmem_followup_trigger", "trigger_type", "status"),
        Index("ix_nmem_followup_assigned", "assigned_to", "status"),
    )


# ── Metadata ────────────────────────────────────────────────────────────────


class NmemMetadata(Base):
    """System metadata — tracks embedding dimensions, schema version, etc."""

    __tablename__ = "nmem_metadata"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(100), unique=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
