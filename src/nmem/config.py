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
    """Tier 3: Long-term memory settings."""

    max_chars_in_prompt: int = 4000
    """Maximum characters for LTM prompt section."""

    staleness_days: int = 90
    """Days without access before confidence decay begins."""

    confidence_decay_rate: float = 0.02
    """Confidence decay per consolidation cycle for stale entries."""

    confidence_decay_rate_accessed: float = 0.05
    """Faster decay rate for entries that were accessed but not validated."""

    min_confidence: float = 0.3
    """Minimum confidence before entry is flagged for review."""

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

    writers: set[str] = {"system"}
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


class NmemConfig(BaseSettings):
    """Root configuration for nmem.

    Can be loaded from environment variables with NMEM_ prefix:
        NMEM_DATABASE_URL=postgresql+asyncpg://...
        NMEM_EMBEDDING__PROVIDER=sentence-transformers
        NMEM_LLM__PROVIDER=openai
        NMEM_LLM__BASE_URL=http://localhost:11434/v1
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

    knowledge_links: KnowledgeLinksConfig = KnowledgeLinksConfig()
    """Associative knowledge linking settings."""

    clustering: ClusteringConfig = ClusteringConfig()
    """Semantic clustering settings."""

    consolidation: ConsolidationConfig = ConsolidationConfig()
    """Background consolidation engine settings."""

    model_config = {"env_prefix": "NMEM_", "env_nested_delimiter": "__"}
