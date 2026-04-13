"""
nmem configuration — Pydantic Settings with env var support.

All settings can be configured via:
  - Python dict / constructor
  - Environment variables with NMEM_ prefix (e.g., NMEM_DATABASE_URL)
  - Nested env vars with __ delimiter (e.g., NMEM_EMBEDDING__PROVIDER)
"""

from __future__ import annotations

from pydantic import BaseModel
from pydantic_settings import BaseSettings


class EmbeddingConfig(BaseModel):
    """Embedding provider configuration."""

    provider: str = "noop"
    """Provider name: "sentence-transformers", "openai", "noop"."""

    model: str = "all-MiniLM-L6-v2"
    """Model name for the embedding provider."""

    dimensions: int = 384
    """Embedding vector dimensions. Must match the model output."""

    api_key: str | None = None
    """API key (for cloud providers like OpenAI)."""

    base_url: str | None = None
    """Base URL override (for self-hosted endpoints)."""

    device: str = "cpu"
    """Device for local models: "cpu", "cuda", or "cuda:0", etc."""


class LLMConfig(BaseModel):
    """LLM provider configuration for compression/synthesis."""

    provider: str = "noop"
    """Provider name: "openai", "anthropic", "noop"."""

    model: str = ""
    """Model name/ID."""

    api_key: str | None = None
    """API key."""

    base_url: str | None = None
    """Base URL override (for vLLM, Ollama, LiteLLM, etc.)."""

    compression_max_chars: int = 200
    """Maximum characters for compressed content."""

    compression_max_tokens: int = 128
    """Maximum tokens for compression LLM call."""

    synthesis_max_tokens: int = 1024
    """Maximum tokens for nightly synthesis LLM call."""


class WorkingConfig(BaseModel):
    """Tier 1: Working memory settings."""

    max_slots_per_session: int = 20
    """Maximum working memory slots per session."""

    max_chars_in_prompt: int = 1000
    """Maximum characters for working memory prompt section."""


class JournalConfig(BaseModel):
    """Tier 2: Journal settings."""

    default_expiry_days: int = 30
    """Days before journal entries expire."""

    auto_promote_importance: int = 7
    """Minimum importance for auto-promotion to LTM."""

    auto_promote_access_count: int = 5
    """Minimum access count for auto-promotion to LTM."""

    max_chars_in_prompt: int = 1500
    """Maximum characters for journal prompt section."""

    dedup_similarity_threshold: float = 0.92
    """Cosine similarity threshold for deduplication on write."""


class LTMConfig(BaseModel):
    """Tier 3: Long-term memory settings.

    Note: `salience` (formerly `confidence`) is the decayed field. It reflects
    how strongly an entry should influence reasoning right now, not whether
    the entry is true. For grounding certainty see `record_type` + `grounding`.
    """

    max_chars_in_prompt: int = 4000
    """Maximum characters for LTM prompt section."""

    staleness_days: int = 90
    """Days without access before salience decay begins."""

    salience_decay_rate: float = 0.02
    """Salience decay per consolidation cycle for never-accessed stale entries."""

    salience_decay_rate_accessed: float = 0.05
    """Faster salience decay for entries that were accessed but not validated."""

    min_salience: float = 0.3
    """Minimum salience before entry is flagged for review."""

    shared_promote_importance: int = 8
    """Minimum importance for LTM→Shared promotion."""

    shared_promote_min_agents: int = 2
    """Minimum distinct agents that must have accessed the entry."""

    shared_promote_min_access: int = 3
    """Minimum total access count for LTM→Shared promotion."""


class SharedConfig(BaseModel):
    """Tier 4: Shared knowledge settings."""

    max_chars_in_prompt: int = 1500
    """Maximum characters for shared knowledge prompt section."""


class EntityConfig(BaseModel):
    """Tier 5: Entity memory settings."""

    max_chars_in_prompt: int = 2000
    """Maximum characters for entity dossier prompt section."""

    write_permissions: dict[str, list[str]] = {}
    """Per-agent write permissions: {"agent_id": ["entity_type", ...]}. Empty = full access."""

    auto_journal_on_search: bool = True
    """Auto-create journal entries when entity search returns results."""

    auto_journal_min_results: int = 1
    """Minimum meaningful results to trigger auto-journaling."""

    auto_journal_min_score: float = 0.3
    """Minimum confidence/score for a result to count as meaningful."""

    auto_journal_importance: int = 3
    """Importance for auto-generated entity reference journal entries (low)."""


class PolicyConfig(BaseModel):
    """Tier 6: Policy memory settings."""

    max_chars_in_prompt: int = 1000
    """Maximum characters for policy prompt section."""

    writers: set[str] = {"system", "default", "mcp"}
    """Agent IDs allowed to directly create active policies."""

    proposers: set[str] = set()
    """Agent IDs allowed to propose policies (status='proposed', requires approval)."""


class KnowledgeLinksConfig(BaseModel):
    """Associative knowledge linking settings."""

    enabled: bool = True
    """Enable knowledge link construction during consolidation."""

    temporal_window_minutes: int = 5
    """Window for temporal proximity links (entries within this window are linked)."""

    min_shared_tags: int = 1
    """Minimum shared tags to create a tag-based link."""

    search_expansion_enabled: bool = True
    """Whether to expand search results with linked entries."""

    search_expansion_max: int = 3
    """Maximum additional entries to add via link expansion."""

    search_expansion_min_strength: float = 0.5
    """Minimum link strength for search expansion."""


class ClusteringConfig(BaseModel):
    """Semantic clustering settings for context threads."""

    similarity_threshold: float = 0.65
    """Cosine similarity threshold for assigning entries to context threads."""


class ConsolidationConfig(BaseModel):
    """Background consolidation engine settings."""

    enabled: bool = True
    """Enable/disable the background consolidation loop."""

    interval_hours: int = 6
    """Hours between full consolidation cycles."""

    similarity_merge_threshold: float = 0.85
    """Cosine similarity threshold for merging duplicate entries."""

    micro_cycle_cooldown_minutes: int = 5
    """Minimum minutes between reactive micro-cycles."""

    nightly_synthesis_hour_utc: int = 23
    """UTC hour to run nightly synthesis (0-23)."""

    nightly_synthesis_min_entries: int = 10
    """Minimum journal entries in 24h to trigger synthesis."""

    max_dreamstate_cycles: int = 5
    """Maximum consolidation cycles in a dreamstate batch."""

    convergence_threshold: int = 2
    """Stop cycling when total material actions falls below this for
    2 consecutive cycles. Material = promotions + merges + rescores."""


class SearchConfig(BaseModel):
    """Search scoring weights and parameters."""

    vector_weight: float = 0.6
    """Weight for vector similarity in hybrid search (0.0-1.0)."""

    fts_weight: float = 0.4
    """Weight for FTS score in hybrid search (0.0-1.0)."""

    recency_weight: float = 0.0
    """Weight for recency boost (0.0 = disabled). When > 0, vector_weight
    and fts_weight are scaled down proportionally."""

    recency_halflife_days: int = 30
    """Half-life for recency decay in days. An entry this old gets 50% of
    the recency boost that a brand-new entry gets."""

    min_vector_score: float = 0.0
    """Minimum vector similarity for candidates (0.0 = no filter,
    0.3 = recommended for large corpora)."""


class PromptConfig(BaseModel):
    """Global prompt injection budget settings."""

    max_total_tokens: int = 0
    """Maximum total tokens for the combined prompt injection.
    0 = disabled (use per-tier max_chars_in_prompt instead)."""

    section_weights: dict[str, float] = {
        "policy": 0.10,
        "shared": 0.15,
        "ltm": 0.30,
        "journal": 0.20,
        "working": 0.10,
        "entity": 0.15,
    }
    """Proportional weights for token budget distribution across sections.
    Weights are normalized at runtime."""


class ImportanceConfig(BaseModel):
    """Automatic importance scoring (consolidation time, heuristic-based).

    When a journal or LTM entry is written with `importance=None` (the
    default), the row is marked `auto_importance=True`. During every full
    consolidation cycle, the heuristic scorer rescores all such rows based
    on record_type, grounding, and access velocity. Rows written with an
    explicit importance integer are marked `auto_importance=False` and are
    never touched by the scorer.
    """

    enabled: bool = True
    """Enable/disable auto-importance rescoring at consolidation time."""

    llm_rescore_enabled: bool = False
    """Placeholder for future LLM-based rescoring. Not implemented in v1."""

    rescore_batch_size: int = 50
    """Maximum rows to rescore per consolidation cycle (bounds runtime)."""


class RetrospectiveConfig(BaseModel):
    """Nightly retrospective — validates past lessons against new evidence.

    Runs as a tail step inside `run_nightly_synthesis()` (matching the
    "dreamstate" metaphor: sleep + consolidation + reflection). Pulls LTM
    entries with record_type in `lesson_record_types` created within
    `lookback_days`, skips any whose `last_validated_at` is within
    `skip_if_validated_within_days`, then classifies each against recent
    outcome entries via the LLM — up to `max_llm_calls_per_run` per night.

    - reinforces  → bump `last_validated_at` (+ importance +1 for auto rows)
    - contradicts → mark `grounding='disputed'` + bump `last_validated_at`
    - neutral     → bump `last_validated_at` only

    The skip-recently-validated guard kills the "reviewed 30 times" waste
    while the `lookback_days` cap ensures genuinely old lessons eventually
    fall out of scope.
    """

    enabled: bool = True
    """Enable/disable the retrospective step."""

    lookback_days: int = 14
    """How far back to consider lessons for review (older = out of scope)."""

    min_lessons: int = 3
    """Minimum candidate lessons before retrospection fires (avoids noise
    on quiet days). Below this, the step is a no-op."""

    max_llm_calls_per_run: int = 5
    """Maximum LLM classifications per nightly run. Bounds cost regardless
    of how many lessons are in scope."""

    skip_if_validated_within_days: int = 3
    """Lessons with `last_validated_at` newer than this are excluded from
    review. Prevents re-reviewing the same lesson every night."""

    lesson_record_types: list[str] = ["lesson", "lesson_learned"]
    """Which LTM `record_type` values the retrospective considers lessons."""


class RecognitionConfig(BaseModel):
    """Recognition signal thresholds and scoring weights.

    Computes how well-established a memory is (KNOWN/FAMILIAR/UNCERTAIN)
    from grounding, access patterns, recency, multi-agent confirmation,
    and tier-specific signals.  Thresholds are configurable per agent
    via NmemConfig profiles.
    """

    known_threshold: float = 0.6
    """Minimum recognition score to classify as KNOWN."""

    familiar_threshold: float = 0.3
    """Minimum recognition score to classify as FAMILIAR."""

    grounding_weights: dict[str, float] = {
        "confirmed": 0.4,
        "source_material": 0.35,
        "inferred": 0.1,
        "disputed": -0.2,
    }
    """Score contribution per grounding level."""

    access_count_high: int = 5
    """Access count threshold for +0.2 bonus."""

    access_count_medium: int = 2
    """Access count threshold for +0.1 bonus."""

    recency_high_days: int = 7
    """Days within which last access gives +0.15 bonus."""

    recency_medium_days: int = 30
    """Days within which last access gives +0.05 bonus."""

    multi_agent_bonus: float = 0.15
    """Bonus when 2+ agents have accessed the entry."""

    salience_weight: float = 0.1
    """Multiplied by salience (0-1) for LTM entries."""

    confidence_weight: float = 0.3
    """Multiplied by confidence (0-1) for entity records."""


class BeliefRevisionConfig(BaseModel):
    """Conflict detection and resolution ("belief revision").

    When two records assert contradictory content, nmem records a conflict
    on write (via `scan_conflicts`) and resolves it at consolidation time
    using the priority:

        1. grounding rank  (source_material = confirmed > inferred > disputed)
        2. agent trust     (config-only dict, looked up by agent_id)
        3. recency         (newer wins, by updated_at)
        4. importance      (higher wins)

    If the winner's grounding rank is >= `auto_resolve_grounding_gap` above
    the loser's, the conflict is auto-resolved: the loser flips to
    `status='superseded'` and gets `superseded_by_id` pointing at the
    winner. Otherwise the conflict goes to `needs_review` and waits for
    a human (or a more confident record) to arrive.
    """

    enabled: bool = True
    """Enable/disable conflict detection + resolution."""

    grounding_priority: list[str] = [
        "source_material",
        "confirmed",
        "inferred",
        "disputed",
    ]
    """Grounding values in descending rank order. First wins over all others."""

    auto_resolve_grounding_gap: int = 1
    """Minimum rank gap between winner and loser to auto-resolve.
    Set to 0 to allow any tiebreaker (grounding → trust → recency → importance)
    to auto-resolve, or 2+ to make auto-resolve more conservative."""

    agent_trust: dict[str, float] = {}
    """Per-agent trust score 0.0-1.0. Config-only — no dynamic updates.
    Typical usage: seed higher trust for larger / more capable models
    (e.g. {"opus": 0.9, "sonnet": 0.7, "qwen-8b": 0.4}).
    Agents not listed fall back to `default_trust`."""

    default_trust: float = 0.5
    """Trust score for agents not explicitly listed in `agent_trust`."""

    text_similarity_threshold: float = 0.7
    """Jaccard threshold for "same topic" detection in conflict scanning."""

    vector_divergence_threshold: float = 0.85
    """Cosine threshold above which records are considered aligned.
    Pairs above the text threshold but below this vector threshold are
    flagged as potential conflicts."""

    scan_candidates_limit: int = 10
    """Max candidate records to consider when scanning for conflicts on
    a single write. Bounds the per-write cost."""


class NmemConfig(BaseSettings):
    """Root configuration for nmem.

    Can be loaded from environment variables with NMEM_ prefix:
        NMEM_DATABASE_URL=postgresql+asyncpg://...
        NMEM_EMBEDDING__PROVIDER=sentence-transformers
        NMEM_LLM__PROVIDER=openai
        NMEM_LLM__BASE_URL=http://localhost:11434/v1

    Or via named profiles::

        config = NmemConfig.from_profile("refinery", database_url="...")
    """

    database_url: str = "postgresql+asyncpg://localhost/nmem"
    """SQLAlchemy async database URL."""

    storage_provider: str = "auto"
    """Storage backend: "postgres", "sqlite", or "auto" (detect from URL)."""

    embedding: EmbeddingConfig = EmbeddingConfig()
    """Embedding provider settings."""

    llm: LLMConfig = LLMConfig()
    """LLM provider settings (for compression and synthesis)."""

    working: WorkingConfig = WorkingConfig()
    """Tier 1: Working memory settings."""

    journal: JournalConfig = JournalConfig()
    """Tier 2: Journal settings."""

    ltm: LTMConfig = LTMConfig()
    """Tier 3: Long-term memory settings."""

    shared: SharedConfig = SharedConfig()
    """Tier 4: Shared knowledge settings."""

    entity: EntityConfig = EntityConfig()
    """Tier 5: Entity memory settings."""

    policy: PolicyConfig = PolicyConfig()
    """Tier 6: Policy memory settings."""

    project_scope: str | None = None
    """Project scope for memory isolation. None = global (all projects).
    Set via NMEM_PROJECT_SCOPE env var for per-project MCP instances."""

    search: SearchConfig = SearchConfig()
    """Search scoring weights and parameters."""

    prompt: PromptConfig = PromptConfig()
    """Global prompt injection budget settings."""

    knowledge_links: KnowledgeLinksConfig = KnowledgeLinksConfig()
    """Associative knowledge linking settings."""

    clustering: ClusteringConfig = ClusteringConfig()
    """Semantic clustering settings."""

    consolidation: ConsolidationConfig = ConsolidationConfig()
    """Background consolidation engine settings."""

    importance: ImportanceConfig = ImportanceConfig()
    """Automatic importance scoring settings."""

    belief: BeliefRevisionConfig = BeliefRevisionConfig()
    """Conflict detection + resolution (belief revision) settings."""

    retrospective: RetrospectiveConfig = RetrospectiveConfig()
    """Nightly retrospective (lesson validation against new evidence)."""

    recognition: RecognitionConfig = RecognitionConfig()
    """Recognition signal computation (KNOWN/FAMILIAR/UNCERTAIN)."""

    model_config = {"env_prefix": "NMEM_", "env_nested_delimiter": "__"}

    @classmethod
    def from_profile(
        cls, profile: str = "neutral", **kwargs: object,
    ) -> "NmemConfig":
        """Create a config pre-seeded with a named profile's defaults.

        Profile overrides are deep-merged under user-supplied ``kwargs``
        so explicit values always win::

            config = NmemConfig.from_profile(
                "refinery",
                database_url="postgresql+asyncpg://...",
                consolidation={"nightly_synthesis_hour_utc": 4},
            )

        Available profiles: ``"neutral"`` (generic, no domain assumptions)
        and ``"refinery"`` (tuned for the Spwig multi-agent system).
        Use :func:`nmem.profiles.register_profile` to add custom profiles.
        """
        from nmem.profiles import get_profile_overrides

        overrides = get_profile_overrides(profile)

        # Deep-merge: for each section in the profile, only apply fields
        # the caller didn't explicitly provide in kwargs.
        merged: dict[str, object] = {}
        for section_name, section_defaults in overrides.items():
            if not isinstance(section_defaults, dict):
                # Top-level scalar override from profile
                if section_name not in kwargs:
                    merged[section_name] = section_defaults
                continue
            user_section = kwargs.get(section_name)
            if user_section is None:
                # User didn't touch this section — profile wins entirely
                merged[section_name] = section_defaults
            elif isinstance(user_section, dict):
                # Both profile and user have overrides — merge field by field
                combined = {**section_defaults, **user_section}
                merged[section_name] = combined
            else:
                # User passed a full config object — user wins
                pass

        # User kwargs always take precedence over merged profile values
        merged.update(kwargs)
        return cls(**merged)
