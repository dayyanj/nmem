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

    compression_tiers: bool = True
    """Scale the compressed-content ceiling by the memory's importance
    (a ceiling, not a target — the distiller uses only what the facts need,
    up to the bound). When False, every memory uses the flat
    `compression_max_chars` (legacy behaviour)."""

    compression_max_chars: int = 200
    """Compressed-content ceiling for low-importance memories (importance <= 3).
    Also the flat ceiling when `compression_tiers` is disabled."""

    compression_max_chars_mid: int = 500
    """Compressed-content ceiling for mid-importance memories (importance 4-6)."""

    compression_max_chars_high: int = 1000
    """Compressed-content ceiling for high-importance memories (importance 7-8)."""

    compression_max_chars_max: int = 2000
    """Compressed-content ceiling for top-importance memories (importance 9-10)."""

    compression_input_max_chars: int = 8000
    """How many characters of the source the distiller actually reads. The full
    original is preserved verbatim in `raw_content` regardless — this only
    bounds what the summarizer sees, protecting the LLM context window on very
    long inputs (was hardcoded to 1000)."""

    compression_max_tokens: int = 128
    """Floor for the compression LLM call's max_tokens. The effective value
    scales up with the char ceiling (see `compression_tokens_for`) so higher
    tiers aren't silently clipped by the token limit."""

    synthesis_max_tokens: int = 1024
    """Maximum tokens for nightly synthesis LLM call."""

    def compression_ceiling_for(self, importance: int | None) -> int:
        """Return the compressed-content char ceiling for an importance score.

        A ceiling, not a target: the distiller is instructed to use only as
        many characters as the facts require, up to this bound. When
        `compression_tiers` is disabled, always returns the flat
        `compression_max_chars`.
        """
        if not self.compression_tiers:
            return self.compression_max_chars
        imp = importance if importance is not None else 5
        if imp >= 9:
            return self.compression_max_chars_max
        if imp >= 7:
            return self.compression_max_chars_high
        if imp >= 4:
            return self.compression_max_chars_mid
        return self.compression_max_chars

    def compression_tokens_for(self, max_chars: int) -> int:
        """max_tokens for a compression call sized to its char ceiling.

        ~1 token ≈ 3 chars for English prose; add headroom and never drop
        below the configured floor, so a raised ceiling is not clipped by the
        token limit before it reaches the char cap.
        """
        return max(self.compression_max_tokens, (max_chars // 3) + 16)


class WorkingConfig(BaseModel):
    """Tier 1: Working memory settings."""

    enabled: bool = False
    """Wire working memory into cognition: the autonomous pursuit loop writes the agent's
    current focus / last outcome and reads it back into each proposal, and chat turns record
    the current task. Off by default (byte-identical) — opt in via NMEM_WORKING__ENABLED.
    Independent of the external adapters, which use the working tier regardless."""

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

    score_at_write: bool = True
    """Score auto-importance deterministically AT WRITE TIME (the record_type/grounding
    heuristic, no I/O) instead of a flat-5 placeholder rescored only at the ~6h consolidation.
    A confirmed fact lands at its real importance immediately, so the high-importance ->
    consolidator.signal() -> micro-cycle -> promote -> ltm.saved -> extract chain fires as
    knowledge is learned rather than waiting for the batch — the platform default (the flat-5
    placeholder was a 'for now' stopgap). Retained as a lever during stabilization; the
    placeholder path can be retired once settled. See docs/proposals/nmem-adaptive-consolidation.md."""

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

    salience_rank_weight: float = 0.0
    """Weight for blending LTM salience into the search score. Default 0.0
    preserves the deliberate decision that salience is a lifecycle signal, NOT
    a retrieval signal — at 0.0 the ranking is byte-for-byte identical to not
    blending at all (implemented as an explicit bypass branch, never
    `relevance + 0*salience`). Set > 0 to let high-salience entries rank higher.
    Applied identically in per-agent and all-agents LTM search."""


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

    focus_expansion: bool = True
    """Deep-recall / focus expansion. When True, query-driven LTM recall
    (`LTMTier.build_prompt`, used by `PromptBuilder` for in-process cognition
    and the `memory_context` MCP tool) resolves the top-k most-relevant entries
    to a query-relevant passage of their verbatim `raw_content`, reallocating
    WITHIN the section budget — depth for the focus items, breadth traded from
    the tail. It never inflates the prompt, degrades to the compact summary if a
    deep line won't fit, and is a no-op when nothing richer than the stored
    summary exists (so it's safe when `raw_content` is absent). On by default;
    set False to restore the flat renderer. Requires a query."""

    focus_expansion_top_k: int = 2
    """How many top relevance-ranked LTM entries get deep passage expansion."""

    focus_expansion_max_chars: int = 800
    """Per-item char ceiling for an expanded focus entry's passage."""


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


class CommitmentDetectionConfig(BaseModel):
    """Detect commitments in journal content and impose them as obligations.

    A nightly LLM step scans recent journal entries for commitment language
    ("I'll have the benchmark to the founder by Friday") and, above
    `min_confidence` and with a resolvable deadline, records a commitment
    (source='detected') — which nmem forwards to the cognitive backend. The
    nmem→nmem-sym mirror of curiosity flowing the other way. Off by default.
    """

    enabled: bool = False
    """Enable/disable content-based commitment detection."""

    lookback_hours: int = 24
    """How far back to scan journal entries for commitments."""

    max_entries: int = 50
    """Max journal entries fed to the LLM per run (bounds cost + context)."""

    min_confidence: float = 0.6
    """Minimum LLM confidence to record a detected commitment."""

    default_authority: float = 0.5
    """Authority assigned to requestors discovered from content."""


class PolicyAlignmentConfig(BaseModel):
    """Nightly policy alignment sweep — demotes memory that contradicts
    active governance policy.

    Policies are approved standing decisions (highest authority in the
    hierarchy), but they live outside conflict scanning: a policy write
    never triggers belief revision against shared/LTM rows, so stale
    knowledge that predates a policy change keeps circulating as
    'validated' — and consolidation can even re-synthesize it nightly
    (the echo-chamber failure mode).

    This sweep closes the loop: for each active policy, semantically
    similar validated shared/LTM rows are collected and an LLM judges
    which of them *contradict* the policy (acting on the row would
    violate it, or it asserts a state the policy superseded). Contradicting
    rows get `grounding='disputed'` — demoted in belief revision, search
    ranking, and importance scoring, but never deleted.

    Rows already disputed are skipped, so a stable corpus converges to
    zero LLM calls. One LLM call per policy with candidates.
    """

    enabled: bool = True
    """Enable/disable the policy alignment sweep."""

    max_policies_per_run: int = 10
    """Maximum active policies swept per night (most recently updated
    first, so fresh policy changes are checked before old stable ones)."""

    top_k: int = 8
    """Maximum candidate rows fetched per policy per table."""

    min_similarity: float = 0.35
    """Cosine similarity floor for a row to count as a candidate.

    Deliberately permissive: recall lives here, precision lives in the
    LLM judgment. Small sentence-transformer models score topically
    related but differently-phrased texts in the 0.35-0.50 band —
    production validation showed policy-contradicting rows at 0.40-0.47,
    which a 0.55 floor silently excluded. `top_k` bounds the prompt size
    regardless of how permissive this floor is."""

    max_llm_calls_per_run: int = 10
    """Maximum LLM judgments per nightly run (one call covers all of a
    policy's candidates). Bounds cost regardless of policy count."""

    classification_max_tokens: int = 4096
    """Token budget for the per-policy classification call. Reasoning
    models (e.g. Qwen3) spend heavily on internal deliberation before
    emitting the JSON verdict — at the generic 1024 synthesis budget a
    16-candidate audit exhausts the budget mid-reasoning and returns
    empty content, which reads as 'no contradictions'. Sized so the
    verdict always fits after deliberation."""


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


class SkillsConfig(BaseModel):
    """Conscious skills — "a process that worked / one that didn't".

    nmem's durable, vectorized record of procedure outcomes (see SkillModel).
    Recorded skills mirror into nmem-sym's live `symbol_procedures` ledger when
    a cognitive backend is attached (Option-B ownership), and work standalone
    otherwise. Everything here is OFF by default — an existing host sees no
    behavior change until it opts in. The manager itself honors `enabled`, so
    `mem.skills.record()/find()` are inert (no writes, empty results) when off —
    the guard is library-wide, not just at the MCP surface.
    """

    enabled: bool = False
    """Master switch. When False, mem.skills.* are no-ops / empty."""

    similarity_threshold: float = 0.35
    """Minimum cosine similarity for a skill to match a query in find()."""

    find_limit: int = 3
    """Default number of skills returned by find()."""

    dedup_enabled: bool = False
    """Enable the optional consolidation step that supersedes near-duplicate
    skills into the stronger one (Slice 1C)."""

    dedup_threshold: float = 0.85
    """Cosine similarity above which two skills are considered duplicates."""

    canonicalize_enabled: bool = False
    """record() LLM-normalizes `what` into a canonical_key (a low-entropy slug) when
    the caller doesn't supply one, so paraphrases of one lesson coalesce. Reuses the
    existing nmem LLM provider; off / noop-LLM / any failure → no key (embedding-only
    dedup, unchanged). The intelligence lives in nmem, not each bot."""

    rank_by_salience: bool = False
    """find() blends a reinforcement bonus (ln(trial_count+1)) into the ranking so a
    lesson learned many times outranks a one-off at similar similarity. Off = pure
    cosine ordering (byte-identical to before). Only meaningful once coalescing
    concentrates recurrence into one row (canonical-key dedup)."""

    salience_rank_weight: float = 0.05
    """Weight of the ln(trial_count+1) reinforcement bonus subtracted from cosine
    distance when rank_by_salience is on. Small so it nudges ties, not dominates."""

    chronic_trial_threshold: int = 0
    """When a skill's trial_count CROSSES this on reinforce, emit `skill.chronic` so
    the host can escalate (change strategy / stop re-recording a known lesson) instead
    of re-learning it forever. 0 = disabled (no event)."""

    decay_enabled: bool = False
    """Enable salience decay + retirement of stale, low-trial skills (Slice 1C)."""

    decay_rate: float = 0.02
    """Salience lost per consolidation cycle by an un-reinforced active skill."""

    retire_salience: float = 0.15
    """A faded skill at/below this salience with few trials is retired."""

    retire_max_trials: int = 1
    """Only decay-retire skills with at most this many trials (unproven ones);
    proven skills fade in salience but are kept."""

    reinforce_salience_boost: float = 0.1
    """Salience restored to a skill each time it is successfully reinforced, so
    used skills don't decay away."""

    include_in_briefing: bool = False
    """Surface matching skills in mem.briefing() output (Slice 1C)."""

    include_in_prompt: bool = False
    """Surface matching skills as a PromptBuilder section (Slice 1C)."""


class AutonomyConfig(BaseModel):
    """Autonomous memorize/retrieve — nmem decides when to capture a skill and
    when to proactively surface relevant memory, without the host asking.

    All work runs OFF the write path (backgrounded via create_task) and only
    ever *offers* results via the `memory.surfaced` event — nmem never forces
    injection. OFF by default. LLM-based skill classification is deliberately
    out of Phase 1 (heuristic capture only).
    """

    enabled: bool = False
    """Master switch for the autonomy layer."""

    auto_capture_skills: bool = False
    """Capture skills from qualifying journal entries automatically."""

    skill_entry_types: list[str] = ["decision", "outcome", "retro", "lesson"]
    """journal record_type/entry_type values that trigger skill capture."""

    proactive_retrieve: bool = False
    """Proactively search + emit `memory.surfaced` on qualifying journal writes."""

    surface_recognition_threshold: float = 0.5
    """Minimum recognition score (compute_recognition) for a candidate to be
    surfaced. ~0.5 corresponds to FAMILIAR."""

    novelty_threshold: float = 0.6
    """Only surface when the trigger is novel vs recent context (cosine below
    this). Prevents re-surfacing what the agent already has loaded."""

    surface_top_k: int = 5
    """Max candidates considered per proactive retrieve."""

    cooldown_seconds: int = 120
    """Per-agent minimum interval between proactive surfaces."""


class SelfEngineeringConfig(BaseModel):
    """Self-engineering — nmem distills reliable skills + memory into reusable
    context recipes it injects into its OWN assembled context, and (2B) proposes
    sub-agent specs. Makes bounded, single-turn LLM calls during consolidation.

    OFF by default and bounded on every axis: a hard per-run LLM CALL cap AND
    per-prompt INPUT-size caps (a call cap alone can't stop huge prompts). Recipes
    are advisory, lowest-priority, near-exact-match-gated, decay when stale, and a
    host `disable(reason)` both removes a recipe and TOMBSTONES its source cluster
    so it isn't re-distilled. Recipes are never sourced from other recipes.
    """

    enabled: bool = False
    """Master switch. When off, all self-engineering is inert."""

    # ── LLM budget (count AND size) ──
    max_llm_calls_per_run: int = 3
    """Hard cap on complete_json calls per consolidation run."""
    max_skills_per_cluster: int = 5
    """Max skills fed into one recipe-distillation prompt."""
    max_related_memories: int = 5
    """Max related memories fed into one distillation prompt."""
    max_input_chars: int = 600
    """Per-source truncation of skill/memory text fed to the LLM."""
    recipe_max_chars: int = 800
    """Max length of a distilled recipe body (acceptance gate)."""

    # ── candidate selection ──
    min_reliability: float = 0.7
    """Min success_count/trial_count for a skill to seed a recipe."""
    min_trials: int = 3
    """Min trial_count for a skill to seed a recipe."""
    dedup_threshold: float = 0.85
    """Cosine above which a new recipe duplicates an existing one."""

    # ── injection ──
    include_in_prompt: bool = False
    """Inject matching recipes as advisory guidance in PromptBuilder."""
    find_limit: int = 2
    """Max recipes considered for injection per prompt."""
    min_match_similarity: float = 0.6
    """Near-exact gate — a recipe injects only when the situation truly matches."""
    recipes_section_max_chars: int = 1200
    """Total budget for the whole injected recipes block."""

    # ── lifecycle ──
    tombstone_base_cooldown_days: int = 14
    """Base suppression window after a recipe is disabled (grows on repeats)."""
    decay_stale_days: int = 60
    """Recipes not matched/injected within this window demote and stop surfacing."""

    # ── 2B (sub-agent proposals) ──
    propose_subagents: bool = False
    """Enable sub-agent spec proposals (2B)."""
    subagent_min_reliability: float = 0.8
    """Reliability bar for a skill to warrant a proposed sub-agent."""


class NmemConfig(BaseSettings):
    """Root configuration for nmem.

    Can be loaded from environment variables with NMEM_ prefix:
        NMEM_DATABASE_URL=postgresql+asyncpg://...
        NMEM_EMBEDDING__PROVIDER=sentence-transformers
        NMEM_LLM__PROVIDER=openai
        NMEM_LLM__BASE_URL=http://localhost:11434/v1

    Or via named profiles::

        config = NmemConfig.from_profile("multi_agent", database_url="...")
    """

    database_url: str = "postgresql+asyncpg://localhost/nmem"
    """SQLAlchemy async database URL (PostgreSQL + pgvector only)."""

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
    commitment_detection: CommitmentDetectionConfig = CommitmentDetectionConfig()
    """Nightly retrospective (lesson validation against new evidence)."""

    policy_alignment: PolicyAlignmentConfig = PolicyAlignmentConfig()
    """Nightly policy alignment sweep (dispute memory contradicting policy)."""

    recognition: RecognitionConfig = RecognitionConfig()
    """Recognition signal computation (KNOWN/FAMILIAR/UNCERTAIN)."""

    skills: SkillsConfig = SkillsConfig()
    """Conscious skills — record/find/mirror of procedure outcomes. Off by default."""

    autonomy: AutonomyConfig = AutonomyConfig()
    """Autonomous memorize/retrieve layer. Off by default."""

    self_engineering: SelfEngineeringConfig = SelfEngineeringConfig()
    """Self-engineering — context recipes + sub-agent proposals. Off by default."""

    model_config = {"env_prefix": "NMEM_", "env_nested_delimiter": "__"}

    @classmethod
    def from_profile(
        cls, profile: str = "neutral", **kwargs: object,
    ) -> "NmemConfig":
        """Create a config pre-seeded with a named profile's defaults.

        Profile overrides are deep-merged under user-supplied ``kwargs``
        so explicit values always win::

            config = NmemConfig.from_profile(
                "multi_agent",
                database_url="postgresql+asyncpg://...",
                consolidation={"nightly_synthesis_hour_utc": 4},
            )

        Available profiles: ``"neutral"`` (generic, no domain assumptions)
        and ``"multi_agent"`` (tuned for a fleet of specialized agents).
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
