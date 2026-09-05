# Configuration

nmem is configured via Python, environment variables, or a TOML file. All settings have sensible defaults, so you only need to set what you want to change.

**New to nmem?** Start with a [profile](profiles.md) — named presets tuned for common scenarios:

```python
from nmem import NmemConfig

# Generic defaults — no domain assumptions
config = NmemConfig(database_url="...")

# Multi-agent ops platform (tuned thresholds + agent trust)
config = NmemConfig.from_profile("refinery", database_url="...")
```

See [profiles.md](profiles.md) for the full list and suggested configs by use case.

## Configuration sources

Priority (highest wins):

1. **Python constructor / `from_profile()`**: `NmemConfig(database_url="...")`
2. **Environment variables**: `NMEM_DATABASE_URL=...`
3. **`nmem.toml`** in current directory
4. **`~/.config/nmem/nmem.toml`**: user-level config
5. **Profile defaults** (if using `from_profile()`)
6. **Bare defaults**

Nested settings use `__` delimiter in env vars:
```bash
export NMEM_EMBEDDING__PROVIDER=sentence-transformers
export NMEM_LLM__BASE_URL=http://localhost:11434/v1
```

Copy `nmem.example.toml` to `nmem.toml` to get started:
```bash
cp nmem.example.toml nmem.toml
```

## Database

```toml
database_url = "postgresql+asyncpg://nmem:nmem@localhost:5433/nmem"
```

| Value | Use case |
|-------|----------|
| `postgresql+asyncpg://user:pass@host:port/db` | The only supported backend. Full hybrid search (pgvector + FTS). |

nmem is PostgreSQL + pgvector only. SQLite was dropped in 0.9.2 — nmem is a
concurrent multi-writer system and its core feature is vector search, neither of
which fits SQLite's single-writer, no-pgvector model. A non-postgres URL raises
at startup.


## Full configuration reference

Every field maps to a **TOML key** (under its `[section]`, or at the root for top-level
fields) and an **environment variable** (`NMEM_` prefix, `__` for nesting) — e.g.
`ltm.salience_decay_rate` ⇄ `[ltm] salience_decay_rate` ⇄ `NMEM_LTM__SALIENCE_DECAY_RATE`.

Derived from `src/nmem/config.py`, the authoritative pydantic source (each field carries a
docstring stating its purpose). Precedence is as in **Configuration sources** above
(constructor / `from_profile()` > env > `nmem.toml` > user config > profile > default). For
provider-selection examples and a copyable starting point see `nmem.example.toml` and
[profiles.md](profiles.md). Regenerate this reference after changing `config.py` with:

```bash
python scripts/gen_config_reference.py src/nmem/config.py
```


### Top-level

| TOML key | Env var | Type | Default | Purpose |
|---|---|---|---|---|
| `database_url` | `NMEM_DATABASE_URL` | str | `"postgresql+asyncpg://localho…` | SQLAlchemy async database URL (PostgreSQL + pgvector only). |
| `project_scope` | `NMEM_PROJECT_SCOPE` | str \| None | `None` | Project scope for memory isolation. None = global (all projects). Set via NMEM_PROJECT_SCOPE env var for per-project MCP instances. |

### `[embedding]` — Embedding provider configuration

| TOML key | Env var | Type | Default | Purpose |
|---|---|---|---|---|
| `embedding.provider` | `NMEM_EMBEDDING__PROVIDER` | str | `"noop"` | Provider name: "sentence-transformers", "openai", "noop". |
| `embedding.model` | `NMEM_EMBEDDING__MODEL` | str | `"all-MiniLM-L6-v2"` | Model name for the embedding provider. |
| `embedding.dimensions` | `NMEM_EMBEDDING__DIMENSIONS` | int | `384` | Embedding vector dimensions. Must match the model output. |
| `embedding.api_key` | `NMEM_EMBEDDING__API_KEY` | str \| None | `None` | API key (for cloud providers like OpenAI). |
| `embedding.base_url` | `NMEM_EMBEDDING__BASE_URL` | str \| None | `None` | Base URL override (for self-hosted endpoints). |
| `embedding.device` | `NMEM_EMBEDDING__DEVICE` | str | `"cpu"` | Device for local models: "cpu", "cuda", or "cuda:0", etc. |

### `[llm]` — LLM provider configuration for compression/synthesis

| TOML key | Env var | Type | Default | Purpose |
|---|---|---|---|---|
| `llm.provider` | `NMEM_LLM__PROVIDER` | str | `"noop"` | Provider name: "openai", "anthropic", "noop". |
| `llm.model` | `NMEM_LLM__MODEL` | str | `""` | Model name/ID. |
| `llm.api_key` | `NMEM_LLM__API_KEY` | str \| None | `None` | API key. |
| `llm.base_url` | `NMEM_LLM__BASE_URL` | str \| None | `None` | Base URL override (for vLLM, Ollama, LiteLLM, etc.). |
| `llm.compression_max_chars` | `NMEM_LLM__COMPRESSION_MAX_CHARS` | int | `200` | Maximum characters for compressed content. |
| `llm.compression_max_tokens` | `NMEM_LLM__COMPRESSION_MAX_TOKENS` | int | `128` | Maximum tokens for compression LLM call. |
| `llm.synthesis_max_tokens` | `NMEM_LLM__SYNTHESIS_MAX_TOKENS` | int | `1024` | Maximum tokens for nightly synthesis LLM call. |

### `[working]` — Tier 1: Working memory settings

| TOML key | Env var | Type | Default | Purpose |
|---|---|---|---|---|
| `working.max_slots_per_session` | `NMEM_WORKING__MAX_SLOTS_PER_SESSION` | int | `20` | Maximum working memory slots per session. |
| `working.max_chars_in_prompt` | `NMEM_WORKING__MAX_CHARS_IN_PROMPT` | int | `1000` | Maximum characters for working memory prompt section. |

### `[journal]` — Tier 2: Journal settings

| TOML key | Env var | Type | Default | Purpose |
|---|---|---|---|---|
| `journal.default_expiry_days` | `NMEM_JOURNAL__DEFAULT_EXPIRY_DAYS` | int | `30` | Days before journal entries expire. |
| `journal.auto_promote_importance` | `NMEM_JOURNAL__AUTO_PROMOTE_IMPORTANCE` | int | `7` | Minimum importance for auto-promotion to LTM. |
| `journal.auto_promote_access_count` | `NMEM_JOURNAL__AUTO_PROMOTE_ACCESS_COUNT` | int | `5` | Minimum access count for auto-promotion to LTM. |
| `journal.max_chars_in_prompt` | `NMEM_JOURNAL__MAX_CHARS_IN_PROMPT` | int | `1500` | Maximum characters for journal prompt section. |
| `journal.dedup_similarity_threshold` | `NMEM_JOURNAL__DEDUP_SIMILARITY_THRESHOLD` | float | `0.92` | Cosine similarity threshold for deduplication on write. |

### `[ltm]` — Tier 3: Long-term memory settings

| TOML key | Env var | Type | Default | Purpose |
|---|---|---|---|---|
| `ltm.max_chars_in_prompt` | `NMEM_LTM__MAX_CHARS_IN_PROMPT` | int | `4000` | Maximum characters for LTM prompt section. |
| `ltm.staleness_days` | `NMEM_LTM__STALENESS_DAYS` | int | `90` | Days without access before salience decay begins. |
| `ltm.salience_decay_rate` | `NMEM_LTM__SALIENCE_DECAY_RATE` | float | `0.02` | Salience decay per consolidation cycle for never-accessed stale entries. |
| `ltm.salience_decay_rate_accessed` | `NMEM_LTM__SALIENCE_DECAY_RATE_ACCESSED` | float | `0.05` | Faster salience decay for entries that were accessed but not validated. |
| `ltm.min_salience` | `NMEM_LTM__MIN_SALIENCE` | float | `0.3` | Minimum salience before entry is flagged for review. |
| `ltm.shared_promote_importance` | `NMEM_LTM__SHARED_PROMOTE_IMPORTANCE` | int | `8` | Minimum importance for LTM→Shared promotion. |
| `ltm.shared_promote_min_agents` | `NMEM_LTM__SHARED_PROMOTE_MIN_AGENTS` | int | `2` | Minimum distinct agents that must have accessed the entry. |
| `ltm.shared_promote_min_access` | `NMEM_LTM__SHARED_PROMOTE_MIN_ACCESS` | int | `3` | Minimum total access count for LTM→Shared promotion. |

### `[shared]` — Tier 4: Shared knowledge settings

| TOML key | Env var | Type | Default | Purpose |
|---|---|---|---|---|
| `shared.max_chars_in_prompt` | `NMEM_SHARED__MAX_CHARS_IN_PROMPT` | int | `1500` | Maximum characters for shared knowledge prompt section. |

### `[entity]` — Tier 5: Entity memory settings

| TOML key | Env var | Type | Default | Purpose |
|---|---|---|---|---|
| `entity.max_chars_in_prompt` | `NMEM_ENTITY__MAX_CHARS_IN_PROMPT` | int | `2000` | Maximum characters for entity dossier prompt section. |
| `entity.write_permissions` | `NMEM_ENTITY__WRITE_PERMISSIONS` | dict[str, list[str]] | `{}` | Per-agent write permissions: {"agent_id": ["entity_type", ...]}. Empty = full access. |
| `entity.auto_journal_on_search` | `NMEM_ENTITY__AUTO_JOURNAL_ON_SEARCH` | bool | `True` | Auto-create journal entries when entity search returns results. |
| `entity.auto_journal_min_results` | `NMEM_ENTITY__AUTO_JOURNAL_MIN_RESULTS` | int | `1` | Minimum meaningful results to trigger auto-journaling. |
| `entity.auto_journal_min_score` | `NMEM_ENTITY__AUTO_JOURNAL_MIN_SCORE` | float | `0.3` | Minimum confidence/score for a result to count as meaningful. |
| `entity.auto_journal_importance` | `NMEM_ENTITY__AUTO_JOURNAL_IMPORTANCE` | int | `3` | Importance for auto-generated entity reference journal entries (low). |

### `[policy]` — Tier 6: Policy memory settings

| TOML key | Env var | Type | Default | Purpose |
|---|---|---|---|---|
| `policy.max_chars_in_prompt` | `NMEM_POLICY__MAX_CHARS_IN_PROMPT` | int | `1000` | Maximum characters for policy prompt section. |
| `policy.writers` | `NMEM_POLICY__WRITERS` | set[str] | `{"system", "default", "mcp"}` | Agent IDs allowed to directly create active policies. |
| `policy.proposers` | `NMEM_POLICY__PROPOSERS` | set[str] | `set()` | Agent IDs allowed to propose policies (status='proposed', requires approval). |

### `[search]` — Search scoring weights and parameters

| TOML key | Env var | Type | Default | Purpose |
|---|---|---|---|---|
| `search.vector_weight` | `NMEM_SEARCH__VECTOR_WEIGHT` | float | `0.6` | Weight for vector similarity in hybrid search (0.0-1.0). |
| `search.fts_weight` | `NMEM_SEARCH__FTS_WEIGHT` | float | `0.4` | Weight for FTS score in hybrid search (0.0-1.0). |
| `search.recency_weight` | `NMEM_SEARCH__RECENCY_WEIGHT` | float | `0.0` | Weight for recency boost (0.0 = disabled). When > 0, vector_weight and fts_weight are scaled down proportionally. |
| `search.recency_halflife_days` | `NMEM_SEARCH__RECENCY_HALFLIFE_DAYS` | int | `30` | Half-life for recency decay in days. An entry this old gets 50% of the recency boost that a brand-new entry gets. |
| `search.min_vector_score` | `NMEM_SEARCH__MIN_VECTOR_SCORE` | float | `0.0` | Minimum vector similarity for candidates (0.0 = no filter, 0.3 = recommended for large corpora). |
| `search.salience_rank_weight` | `NMEM_SEARCH__SALIENCE_RANK_WEIGHT` | float | `0.0` | Weight for blending LTM salience into the search score. Default 0.0 preserves the deliberate decision that salience is a lifecycle signal, NOT a retrieval signal — at 0.0 the rank… |

### `[prompt]` — Global prompt injection budget settings

| TOML key | Env var | Type | Default | Purpose |
|---|---|---|---|---|
| `prompt.max_total_tokens` | `NMEM_PROMPT__MAX_TOTAL_TOKENS` | int | `0` | Maximum total tokens for the combined prompt injection. 0 = disabled (use per-tier max_chars_in_prompt instead). |
| `prompt.section_weights` | `NMEM_PROMPT__SECTION_WEIGHTS` | dict[str, float] | `{` |  |

### `[knowledge_links]` — Associative knowledge linking settings

| TOML key | Env var | Type | Default | Purpose |
|---|---|---|---|---|
| `knowledge_links.enabled` | `NMEM_KNOWLEDGE_LINKS__ENABLED` | bool | `True` | Enable knowledge link construction during consolidation. |
| `knowledge_links.temporal_window_minutes` | `NMEM_KNOWLEDGE_LINKS__TEMPORAL_WINDOW_MINUTES` | int | `5` | Window for temporal proximity links (entries within this window are linked). |
| `knowledge_links.min_shared_tags` | `NMEM_KNOWLEDGE_LINKS__MIN_SHARED_TAGS` | int | `1` | Minimum shared tags to create a tag-based link. |
| `knowledge_links.search_expansion_enabled` | `NMEM_KNOWLEDGE_LINKS__SEARCH_EXPANSION_ENABLED` | bool | `True` | Whether to expand search results with linked entries. |
| `knowledge_links.search_expansion_max` | `NMEM_KNOWLEDGE_LINKS__SEARCH_EXPANSION_MAX` | int | `3` | Maximum additional entries to add via link expansion. |
| `knowledge_links.search_expansion_min_strength` | `NMEM_KNOWLEDGE_LINKS__SEARCH_EXPANSION_MIN_STRENGTH` | float | `0.5` | Minimum link strength for search expansion. |

### `[clustering]` — Semantic clustering settings for context threads

| TOML key | Env var | Type | Default | Purpose |
|---|---|---|---|---|
| `clustering.similarity_threshold` | `NMEM_CLUSTERING__SIMILARITY_THRESHOLD` | float | `0.65` | Cosine similarity threshold for assigning entries to context threads. |

### `[consolidation]` — Background consolidation engine settings

| TOML key | Env var | Type | Default | Purpose |
|---|---|---|---|---|
| `consolidation.enabled` | `NMEM_CONSOLIDATION__ENABLED` | bool | `True` | Enable/disable the background consolidation loop. |
| `consolidation.interval_hours` | `NMEM_CONSOLIDATION__INTERVAL_HOURS` | int | `6` | Hours between full consolidation cycles. |
| `consolidation.similarity_merge_threshold` | `NMEM_CONSOLIDATION__SIMILARITY_MERGE_THRESHOLD` | float | `0.85` | Cosine similarity threshold for merging duplicate entries. |
| `consolidation.micro_cycle_cooldown_minutes` | `NMEM_CONSOLIDATION__MICRO_CYCLE_COOLDOWN_MINUTES` | int | `5` | Minimum minutes between reactive micro-cycles. |
| `consolidation.nightly_synthesis_hour_utc` | `NMEM_CONSOLIDATION__NIGHTLY_SYNTHESIS_HOUR_UTC` | int | `23` | UTC hour to run nightly synthesis (0-23). |
| `consolidation.nightly_synthesis_min_entries` | `NMEM_CONSOLIDATION__NIGHTLY_SYNTHESIS_MIN_ENTRIES` | int | `10` | Minimum journal entries in 24h to trigger synthesis. |
| `consolidation.max_dreamstate_cycles` | `NMEM_CONSOLIDATION__MAX_DREAMSTATE_CYCLES` | int | `5` | Maximum consolidation cycles in a dreamstate batch. |
| `consolidation.convergence_threshold` | `NMEM_CONSOLIDATION__CONVERGENCE_THRESHOLD` | int | `2` | Stop cycling when total material actions falls below this for 2 consecutive cycles. Material = promotions + merges + rescores. |

### `[importance]` — Automatic importance scoring (consolidation time, heuristic-based)

| TOML key | Env var | Type | Default | Purpose |
|---|---|---|---|---|
| `importance.enabled` | `NMEM_IMPORTANCE__ENABLED` | bool | `True` | Enable/disable auto-importance rescoring at consolidation time. |
| `importance.llm_rescore_enabled` | `NMEM_IMPORTANCE__LLM_RESCORE_ENABLED` | bool | `False` | Placeholder for future LLM-based rescoring. Not implemented in v1. |
| `importance.rescore_batch_size` | `NMEM_IMPORTANCE__RESCORE_BATCH_SIZE` | int | `50` | Maximum rows to rescore per consolidation cycle (bounds runtime). |

### `[belief]` — Conflict detection and resolution ("belief revision")

| TOML key | Env var | Type | Default | Purpose |
|---|---|---|---|---|
| `belief.enabled` | `NMEM_BELIEF__ENABLED` | bool | `True` | Enable/disable conflict detection + resolution. |
| `belief.grounding_priority` | `NMEM_BELIEF__GROUNDING_PRIORITY` | list[str] | `[` |  |
| `belief.auto_resolve_grounding_gap` | `NMEM_BELIEF__AUTO_RESOLVE_GROUNDING_GAP` | int | `1` | Minimum rank gap between winner and loser to auto-resolve. Set to 0 to allow any tiebreaker (grounding → trust → recency → importance) to auto-resolve, or 2+ to make auto-resolve… |
| `belief.agent_trust` | `NMEM_BELIEF__AGENT_TRUST` | dict[str, float] | `{}` | Per-agent trust score 0.0-1.0. Config-only — no dynamic updates. Typical usage: seed higher trust for larger / more capable models (e.g. {"opus": 0.9, "sonnet": 0.7, "qwen-8b": 0.… |
| `belief.default_trust` | `NMEM_BELIEF__DEFAULT_TRUST` | float | `0.5` | Trust score for agents not explicitly listed in `agent_trust`. |
| `belief.text_similarity_threshold` | `NMEM_BELIEF__TEXT_SIMILARITY_THRESHOLD` | float | `0.7` | Jaccard threshold for "same topic" detection in conflict scanning. |
| `belief.vector_divergence_threshold` | `NMEM_BELIEF__VECTOR_DIVERGENCE_THRESHOLD` | float | `0.85` | Cosine threshold above which records are considered aligned. Pairs above the text threshold but below this vector threshold are flagged as potential conflicts. |
| `belief.scan_candidates_limit` | `NMEM_BELIEF__SCAN_CANDIDATES_LIMIT` | int | `10` | Max candidate records to consider when scanning for conflicts on a single write. Bounds the per-write cost. |

### `[retrospective]` — Nightly retrospective — validates past lessons against new evidence

| TOML key | Env var | Type | Default | Purpose |
|---|---|---|---|---|
| `retrospective.enabled` | `NMEM_RETROSPECTIVE__ENABLED` | bool | `True` | Enable/disable the retrospective step. |
| `retrospective.lookback_days` | `NMEM_RETROSPECTIVE__LOOKBACK_DAYS` | int | `14` | How far back to consider lessons for review (older = out of scope). |
| `retrospective.min_lessons` | `NMEM_RETROSPECTIVE__MIN_LESSONS` | int | `3` | Minimum candidate lessons before retrospection fires (avoids noise on quiet days). Below this, the step is a no-op. |
| `retrospective.max_llm_calls_per_run` | `NMEM_RETROSPECTIVE__MAX_LLM_CALLS_PER_RUN` | int | `5` | Maximum LLM classifications per nightly run. Bounds cost regardless of how many lessons are in scope. |
| `retrospective.skip_if_validated_within_days` | `NMEM_RETROSPECTIVE__SKIP_IF_VALIDATED_WITHIN_DAYS` | int | `3` | Lessons with `last_validated_at` newer than this are excluded from review. Prevents re-reviewing the same lesson every night. |
| `retrospective.lesson_record_types` | `NMEM_RETROSPECTIVE__LESSON_RECORD_TYPES` | list[str] | `["lesson", "lesson_learned"]` | Which LTM `record_type` values the retrospective considers lessons. |

### `[commitment_detection]` — Detect commitments in journal content and impose them as obligations

| TOML key | Env var | Type | Default | Purpose |
|---|---|---|---|---|
| `commitment_detection.enabled` | `NMEM_COMMITMENT_DETECTION__ENABLED` | bool | `False` | Enable/disable content-based commitment detection. |
| `commitment_detection.lookback_hours` | `NMEM_COMMITMENT_DETECTION__LOOKBACK_HOURS` | int | `24` | How far back to scan journal entries for commitments. |
| `commitment_detection.max_entries` | `NMEM_COMMITMENT_DETECTION__MAX_ENTRIES` | int | `50` | Max journal entries fed to the LLM per run (bounds cost + context). |
| `commitment_detection.min_confidence` | `NMEM_COMMITMENT_DETECTION__MIN_CONFIDENCE` | float | `0.6` | Minimum LLM confidence to record a detected commitment. |
| `commitment_detection.default_authority` | `NMEM_COMMITMENT_DETECTION__DEFAULT_AUTHORITY` | float | `0.5` | Authority assigned to requestors discovered from content. |

### `[policy_alignment]` — Nightly policy alignment sweep — demotes memory that contradicts

| TOML key | Env var | Type | Default | Purpose |
|---|---|---|---|---|
| `policy_alignment.enabled` | `NMEM_POLICY_ALIGNMENT__ENABLED` | bool | `True` | Enable/disable the policy alignment sweep. |
| `policy_alignment.max_policies_per_run` | `NMEM_POLICY_ALIGNMENT__MAX_POLICIES_PER_RUN` | int | `10` | Maximum active policies swept per night (most recently updated first, so fresh policy changes are checked before old stable ones). |
| `policy_alignment.top_k` | `NMEM_POLICY_ALIGNMENT__TOP_K` | int | `8` | Maximum candidate rows fetched per policy per table. |
| `policy_alignment.min_similarity` | `NMEM_POLICY_ALIGNMENT__MIN_SIMILARITY` | float | `0.35` | Cosine similarity floor for a row to count as a candidate. Deliberately permissive: recall lives here, precision lives in the LLM judgment. Small sentence-transformer models score… |
| `policy_alignment.max_llm_calls_per_run` | `NMEM_POLICY_ALIGNMENT__MAX_LLM_CALLS_PER_RUN` | int | `10` | Maximum LLM judgments per nightly run (one call covers all of a policy's candidates). Bounds cost regardless of policy count. |
| `policy_alignment.classification_max_tokens` | `NMEM_POLICY_ALIGNMENT__CLASSIFICATION_MAX_TOKENS` | int | `4096` | Token budget for the per-policy classification call. Reasoning models (e.g. Qwen3) spend heavily on internal deliberation before emitting the JSON verdict — at the generic 1024 sy… |

### `[recognition]` — Recognition signal thresholds and scoring weights

| TOML key | Env var | Type | Default | Purpose |
|---|---|---|---|---|
| `recognition.known_threshold` | `NMEM_RECOGNITION__KNOWN_THRESHOLD` | float | `0.6` | Minimum recognition score to classify as KNOWN. |
| `recognition.familiar_threshold` | `NMEM_RECOGNITION__FAMILIAR_THRESHOLD` | float | `0.3` | Minimum recognition score to classify as FAMILIAR. |
| `recognition.grounding_weights` | `NMEM_RECOGNITION__GROUNDING_WEIGHTS` | dict[str, float] | `{` |  |
| `recognition.access_count_high` | `NMEM_RECOGNITION__ACCESS_COUNT_HIGH` | int | `5` | Access count threshold for +0.2 bonus. |
| `recognition.access_count_medium` | `NMEM_RECOGNITION__ACCESS_COUNT_MEDIUM` | int | `2` | Access count threshold for +0.1 bonus. |
| `recognition.recency_high_days` | `NMEM_RECOGNITION__RECENCY_HIGH_DAYS` | int | `7` | Days within which last access gives +0.15 bonus. |
| `recognition.recency_medium_days` | `NMEM_RECOGNITION__RECENCY_MEDIUM_DAYS` | int | `30` | Days within which last access gives +0.05 bonus. |
| `recognition.multi_agent_bonus` | `NMEM_RECOGNITION__MULTI_AGENT_BONUS` | float | `0.15` | Bonus when 2+ agents have accessed the entry. |
| `recognition.salience_weight` | `NMEM_RECOGNITION__SALIENCE_WEIGHT` | float | `0.1` | Multiplied by salience (0-1) for LTM entries. |
| `recognition.confidence_weight` | `NMEM_RECOGNITION__CONFIDENCE_WEIGHT` | float | `0.3` | Multiplied by confidence (0-1) for entity records. |

### `[skills]` — Conscious skills — "a process that worked / one that didn't"

| TOML key | Env var | Type | Default | Purpose |
|---|---|---|---|---|
| `skills.enabled` | `NMEM_SKILLS__ENABLED` | bool | `False` | Master switch. When False, mem.skills.* are no-ops / empty. |
| `skills.similarity_threshold` | `NMEM_SKILLS__SIMILARITY_THRESHOLD` | float | `0.35` | Minimum cosine similarity for a skill to match a query in find(). |
| `skills.find_limit` | `NMEM_SKILLS__FIND_LIMIT` | int | `3` | Default number of skills returned by find(). |
| `skills.dedup_enabled` | `NMEM_SKILLS__DEDUP_ENABLED` | bool | `False` | Enable the optional consolidation step that supersedes near-duplicate skills into the stronger one (Slice 1C). |
| `skills.dedup_threshold` | `NMEM_SKILLS__DEDUP_THRESHOLD` | float | `0.85` | Cosine similarity above which two skills are considered duplicates. |
| `skills.decay_enabled` | `NMEM_SKILLS__DECAY_ENABLED` | bool | `False` | Enable salience decay + retirement of stale, low-trial skills (Slice 1C). |
| `skills.decay_rate` | `NMEM_SKILLS__DECAY_RATE` | float | `0.02` | Salience lost per consolidation cycle by an un-reinforced active skill. |
| `skills.retire_salience` | `NMEM_SKILLS__RETIRE_SALIENCE` | float | `0.15` | A faded skill at/below this salience with few trials is retired. |
| `skills.retire_max_trials` | `NMEM_SKILLS__RETIRE_MAX_TRIALS` | int | `1` | Only decay-retire skills with at most this many trials (unproven ones); proven skills fade in salience but are kept. |
| `skills.reinforce_salience_boost` | `NMEM_SKILLS__REINFORCE_SALIENCE_BOOST` | float | `0.1` | Salience restored to a skill each time it is successfully reinforced, so used skills don't decay away. |
| `skills.include_in_briefing` | `NMEM_SKILLS__INCLUDE_IN_BRIEFING` | bool | `False` | Surface matching skills in mem.briefing() output (Slice 1C). |
| `skills.include_in_prompt` | `NMEM_SKILLS__INCLUDE_IN_PROMPT` | bool | `False` | Surface matching skills as a PromptBuilder section (Slice 1C). |

### `[autonomy]` — Autonomous memorize/retrieve — nmem decides when to capture a skill and

| TOML key | Env var | Type | Default | Purpose |
|---|---|---|---|---|
| `autonomy.enabled` | `NMEM_AUTONOMY__ENABLED` | bool | `False` | Master switch for the autonomy layer. |
| `autonomy.auto_capture_skills` | `NMEM_AUTONOMY__AUTO_CAPTURE_SKILLS` | bool | `False` | Capture skills from qualifying journal entries automatically. |
| `autonomy.skill_entry_types` | `NMEM_AUTONOMY__SKILL_ENTRY_TYPES` | list[str] | `["decision", "outcome", "retr…` | journal record_type/entry_type values that trigger skill capture. |
| `autonomy.proactive_retrieve` | `NMEM_AUTONOMY__PROACTIVE_RETRIEVE` | bool | `False` | Proactively search + emit `memory.surfaced` on qualifying journal writes. |
| `autonomy.surface_recognition_threshold` | `NMEM_AUTONOMY__SURFACE_RECOGNITION_THRESHOLD` | float | `0.5` | Minimum recognition score (compute_recognition) for a candidate to be surfaced. ~0.5 corresponds to FAMILIAR. |
| `autonomy.novelty_threshold` | `NMEM_AUTONOMY__NOVELTY_THRESHOLD` | float | `0.6` | Only surface when the trigger is novel vs recent context (cosine below this). Prevents re-surfacing what the agent already has loaded. |
| `autonomy.surface_top_k` | `NMEM_AUTONOMY__SURFACE_TOP_K` | int | `5` | Max candidates considered per proactive retrieve. |
| `autonomy.cooldown_seconds` | `NMEM_AUTONOMY__COOLDOWN_SECONDS` | int | `120` | Per-agent minimum interval between proactive surfaces. |

### `[self_engineering]` — Self-engineering — nmem distills reliable skills + memory into reusable

| TOML key | Env var | Type | Default | Purpose |
|---|---|---|---|---|
| `self_engineering.enabled` | `NMEM_SELF_ENGINEERING__ENABLED` | bool | `False` | Master switch. When off, all self-engineering is inert. |
| `self_engineering.max_llm_calls_per_run` | `NMEM_SELF_ENGINEERING__MAX_LLM_CALLS_PER_RUN` | int | `3` | Hard cap on complete_json calls per consolidation run. |
| `self_engineering.max_skills_per_cluster` | `NMEM_SELF_ENGINEERING__MAX_SKILLS_PER_CLUSTER` | int | `5` | Max skills fed into one recipe-distillation prompt. |
| `self_engineering.max_related_memories` | `NMEM_SELF_ENGINEERING__MAX_RELATED_MEMORIES` | int | `5` | Max related memories fed into one distillation prompt. |
| `self_engineering.max_input_chars` | `NMEM_SELF_ENGINEERING__MAX_INPUT_CHARS` | int | `600` | Per-source truncation of skill/memory text fed to the LLM. |
| `self_engineering.recipe_max_chars` | `NMEM_SELF_ENGINEERING__RECIPE_MAX_CHARS` | int | `800` | Max length of a distilled recipe body (acceptance gate). |
| `self_engineering.min_reliability` | `NMEM_SELF_ENGINEERING__MIN_RELIABILITY` | float | `0.7` | Min success_count/trial_count for a skill to seed a recipe. |
| `self_engineering.min_trials` | `NMEM_SELF_ENGINEERING__MIN_TRIALS` | int | `3` | Min trial_count for a skill to seed a recipe. |
| `self_engineering.dedup_threshold` | `NMEM_SELF_ENGINEERING__DEDUP_THRESHOLD` | float | `0.85` | Cosine above which a new recipe duplicates an existing one. |
| `self_engineering.include_in_prompt` | `NMEM_SELF_ENGINEERING__INCLUDE_IN_PROMPT` | bool | `False` | Inject matching recipes as advisory guidance in PromptBuilder. |
| `self_engineering.find_limit` | `NMEM_SELF_ENGINEERING__FIND_LIMIT` | int | `2` | Max recipes considered for injection per prompt. |
| `self_engineering.min_match_similarity` | `NMEM_SELF_ENGINEERING__MIN_MATCH_SIMILARITY` | float | `0.6` | Near-exact gate — a recipe injects only when the situation truly matches. |
| `self_engineering.recipes_section_max_chars` | `NMEM_SELF_ENGINEERING__RECIPES_SECTION_MAX_CHARS` | int | `1200` | Total budget for the whole injected recipes block. |
| `self_engineering.tombstone_base_cooldown_days` | `NMEM_SELF_ENGINEERING__TOMBSTONE_BASE_COOLDOWN_DAYS` | int | `14` | Base suppression window after a recipe is disabled (grows on repeats). |
| `self_engineering.decay_stale_days` | `NMEM_SELF_ENGINEERING__DECAY_STALE_DAYS` | int | `60` | Recipes not matched/injected within this window demote and stop surfacing. |
| `self_engineering.propose_subagents` | `NMEM_SELF_ENGINEERING__PROPOSE_SUBAGENTS` | bool | `False` | Enable sub-agent spec proposals (2B). |
| `self_engineering.subagent_min_reliability` | `NMEM_SELF_ENGINEERING__SUBAGENT_MIN_RELIABILITY` | float | `0.8` | Reliability bar for a skill to warrant a proposed sub-agent. |

