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
    auto_importance: bool = True
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
    project_scope: str | None = None
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class LTMEntry:
    """A long-term memory entry (Tier 3).

    `salience` (formerly `confidence`) reflects how strongly this entry should
    influence reasoning right now — it decays with staleness. It is NOT a
    certainty/truth measure; for grounding see the `grounding` field.

    `auto_importance` marks entries whose importance is managed by the
    consolidation heuristic scorer. If the caller passed an explicit
    importance at save time, this flag is False and the scorer will leave
    the value alone.
    """

    id: int
    agent_id: str
    category: str
    key: str
    content: str
    importance: int = 5
    auto_importance: bool = True
    salience: float = 1.0
    access_count: int = 0
    source: str = "agent"
    record_type: str = "fact"
    grounding: str = "inferred"
    status: str = "validated"
    version: int = 1
    context_thread_id: str | None = None
    project_scope: str | None = None
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
    project_scope: str | None = None
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
    project_scope: str | None = None
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
    recognition: str = "UNCERTAIN"  # "KNOWN", "FAMILIAR", "UNCERTAIN"
    recognition_score: float = 0.0
    recognition_reasons: tuple[str, ...] = ()
    passage: str | None = None  # Best matching passage within content (for long entries)


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
    skills: str = ""
    context_recipes: str = ""

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
        if self.skills:
            sections.append(f"## Relevant Skills\n{self.skills}")
        if self.journal:
            sections.append(f"## Recent Activity\n{self.journal}")
        if self.working:
            sections.append(f"## Current Session\n{self.working}")
        if self.entity:
            sections.append(f"## Entity Dossier\n{self.entity}")
        if self.deja_vu:
            sections.append(f"## Similar Past Experience\n{self.deja_vu}")
        # Advisory, lowest-priority: learned local heuristics. Placed last and
        # explicitly subordinate — defer to policy and direct memory above.
        if self.context_recipes:
            sections.append(
                "## Learned Guidance (advisory)\n"
                "_Distilled from past experience; defer to policies and direct "
                "memory above._\n"
                f"{self.context_recipes}")
        if not sections:
            return ""
        return "# Agent Memory\n\n" + "\n\n".join(sections)

    @property
    def token_estimate(self) -> int:
        """Rough token estimate (chars / 4)."""
        return len(self.full_injection) // 4

    @property
    def section_tokens(self) -> dict[str, int]:
        """Per-section token estimates (chars / 4)."""
        return {
            name: len(getattr(self, name, "") or "") // 4
            for name in ("policy", "shared", "ltm", "skills", "journal",
                         "working", "entity", "deja_vu", "context_recipes")
            if getattr(self, name, "")
        }


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
    project_scope: str | None = None
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
    auto_importance_rescored: int = 0
    conflicts_auto_resolved: int = 0
    conflicts_needs_review: int = 0
    lessons_validated: int = 0
    lessons_disputed: int = 0
    policy_disputed_shared: int = 0
    policy_disputed_ltm: int = 0
    salience_decayed: int = 0
    curiosity_decayed: int = 0
    patterns_synthesized: int = 0
    links_created: int = 0
    duration_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class KnowledgeLink:
    """An associative link between two memory entries."""

    id: int
    source_id: int
    source_tier: str
    target_id: int
    target_tier: str
    link_type: str
    strength: float
    evidence: str | None = None
    created_at: datetime | None = None


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


@dataclass(frozen=True, slots=True)
class BriefingResult:
    """Structured return from MemorySystem.briefing()."""

    content: str
    """Formatted briefing text, within token budget."""

    token_estimate: int
    """Approximate token count of the content."""

    facts_included: int
    """Number of facts included in the briefing."""

    facts_available: int
    """Total facts available (shows coverage)."""

    recognition_breakdown: dict[str, int] = field(default_factory=dict)
    """Count per recognition level, e.g. {"KNOWN": 5, "FAMILIAR": 3}."""


# ── Continuity / Wake ────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SymContinuityInputs:
    """Read-only cognitive inputs projected from nmem-sym for the wake snapshot.

    The nmem-sym bridge plugs a provider that returns this (see
    ``MemorySystem.register_continuity_provider``). It is deliberately small and
    all-prose: drive state is surfaced as its *consequence*, never as raw
    scalars (a numeric dashboard invites the LLM to perform its internal state
    rather than act on it). Every field is optional so an isolated / sym-less
    agent degrades cleanly to memory-only continuity.
    """

    self_model_summary: str | None = None
    """One or two sentences of durable self-knowledge (capabilities/limitations)."""

    drive_state_prose: str | None = None
    """Current internal state as a consequence, e.g. "an unresolved contradiction
    between A and B is pulling attention" — NOT numbers."""

    active_goals: tuple[str, ...] = ()
    """Short labels of the agent's own currently-actionable goals."""


@dataclass(frozen=True, slots=True)
class OpenLoop:
    """One unresolved thread in the unified open-loop view.

    Ranks commitments (prospective obligations) and curiosity signals (epistemic
    gaps) on one salience scale so the highest-tension threads — regardless of
    kind — surface in the wake state.
    """

    kind: str  # "commitment" | "curiosity"
    salience: float  # 0..1, higher = more pressing
    text: str  # one-line rendering for the prompt
    due: datetime | None = None


@dataclass(frozen=True, slots=True)
class ContinuityResult:
    """Structured return from MemorySystem.wake() / .continuity().

    Unlike a query-driven briefing, this is assembled fresh every call and is
    present even with no stimulus (the "Morning." case) — it answers "where am I
    right now" rather than "what is relevant to this query".
    """

    content: str
    """Formatted continuity snapshot, within token budget."""

    token_estimate: int
    """Approximate token count of the content."""

    sections: tuple[str, ...] = ()
    """Names of the sections that made it into the snapshot, in order."""

    n_commitments: int = 0
    """Open commitments considered."""

    n_open_loops_shown: int = 0
    """Unified open loops rendered (after the top-k cut)."""

    n_open_loops_total: int = 0
    """Unified open loops available before the cut (shows what was dropped)."""

    n_goals: int = 0
    """Active goals surfaced from the sym seam."""

    has_self_model: bool = False
    has_drive_state: bool = False
