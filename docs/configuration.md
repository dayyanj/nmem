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
| `postgresql+asyncpg://user:pass@host:port/db` | Production. Full hybrid search (pgvector + FTS). |
| `sqlite+aiosqlite:///nmem.db` | Development/testing. No Docker needed. Fallback search (slower for large datasets). |

**Tradeoff:** SQLite works for demos and small datasets (<1000 entries) but lacks pgvector's HNSW indexes. PostgreSQL is 2-3x faster for search at scale.

## Embedding provider

```toml
[embedding]
provider = "sentence-transformers"
model = "all-MiniLM-L6-v2"
device = "cpu"
```

| Provider | Pros | Cons |
|----------|------|------|
| `sentence-transformers` | Local, free, fast, no API key needed. 384 dimensions. | Requires `torch` (~2GB install). Model loads in 2-3s. |
| `openai` | Best quality (text-embedding-3-small). 1536 dimensions. | Requires API key. Network latency per call. |
| `noop` | Instant. No dependencies. Hash-based similarity. | Not semantically meaningful. For testing only. |

**`device`**: Set to `"cpu"` (default) to avoid GPU conflicts with LLM inference servers. Set to `"cuda"` if you have a dedicated GPU for embeddings.

**`model`**: Any HuggingFace sentence-transformers model works. `all-MiniLM-L6-v2` is the sweet spot of quality vs size (80MB, 384d). For better quality at 5x the size: `all-mpnet-base-v2` (420MB, 768d).

**`dimensions`**: Must match the model output. Auto-detected for sentence-transformers. Specify for OpenAI models.

## LLM provider

```toml
[llm]
provider = "openai"
base_url = "http://localhost:11434/v1"
model = "qwen3"
```

The LLM is used for three operations:
1. **Content compression**: distill verbose text into dense facts on write
2. **Nightly synthesis**: extract cross-cutting patterns from journal entries
3. **Dedup merging**: combine similar LTM entries into a single merged entry

| Provider | Setup | Notes |
|----------|-------|-------|
| `openai` | Works with OpenAI, vLLM, Ollama, LiteLLM (any OpenAI-compatible API) | Set `base_url` for local servers. Use `api_key = "EMPTY"` for keyless servers. |
| `anthropic` | Anthropic Claude API | Requires `api_key`. |
| `noop` | Disables LLM features | Compression falls back to truncation. Synthesis is skipped. Dedup uses simple concatenation. |

**`compression_max_chars`** (default: 200): Maximum characters for compressed content. Lower = more aggressive compression, fewer tokens in prompts. Higher = more detail preserved.

**`compression_max_tokens`** (default: 128): Token budget for the compression LLM call.

**`synthesis_max_tokens`** (default: 1024): Token budget for nightly synthesis pattern extraction.

## Journal (Tier 2)

```toml
[journal]
default_expiry_days = 30
auto_promote_importance = 7
auto_promote_access_count = 5
dedup_similarity_threshold = 0.92
max_chars_in_prompt = 1500
```

**`default_expiry_days`** (default: 30): Journal entries expire after this many days. Expired entries with high importance are promoted to LTM; low-importance ones are deleted.

**`auto_promote_importance`** (default: 7): Entries at or above this importance level are immediately promoted to LTM by the consolidation engine. **Tradeoff:** Lower = more entries promoted (LTM grows faster, more context available). Higher = stricter promotion (LTM stays lean, only truly important knowledge persists).

**`auto_promote_access_count`** (default: 5): Entries accessed this many times are promoted regardless of importance. Frequently-retrieved knowledge has proven its value.

**`dedup_similarity_threshold`** (default: 0.92): Cosine similarity above this threshold triggers deduplication on write. The existing entry is bumped instead of creating a duplicate. **Tradeoff:** Lower = catches more duplicates but risks merging distinct-but-similar entries. Higher = only deduplicates near-identical entries.

**`max_chars_in_prompt`** (default: 1500): Maximum characters for journal entries in prompt injection. Limits context window consumption.

## Long-Term Memory (Tier 3)

```toml
[ltm]
staleness_days = 90
salience_decay_rate = 0.02
salience_decay_rate_accessed = 0.05
min_salience = 0.3
shared_promote_importance = 8
shared_promote_min_agents = 2
shared_promote_min_access = 3
max_chars_in_prompt = 4000
```

> **Note on terminology**: LTM entries carry a `salience` score (formerly `confidence`). Salience reflects how strongly the entry should influence current reasoning, not whether it is true. It starts at 1.0 and decays with staleness. For truth / grounding certainty, see `record_type` and `grounding` (or use entity memory, which has its own `confidence` field with strict grounding semantics).

**`staleness_days`** (default: 90): Days without access before salience decay begins. Knowledge that nobody retrieves for 3 months starts to fade.

**`salience_decay_rate`** (default: 0.02): Salience points subtracted per consolidation cycle for unaccessed entries. At the default 6-hour cycle interval, an unaccessed entry drops from 1.0 to 0.3 (minimum) in about 5 months.

**`salience_decay_rate_accessed`** (default: 0.05): Faster decay rate for entries that were accessed but never re-validated. A read without re-confirmation is weak evidence of relevance.

**`min_salience`** (default: 0.3): Floor for salience decay. Entries never drop below this, so they remain searchable but rank lower.

**`shared_promote_importance`** (default: 8): Minimum importance for LTM→Shared promotion.

**`shared_promote_min_agents`** (default: 2): Minimum distinct agents that must have accessed the LTM entry for it to promote to shared. This ensures only genuinely cross-agent knowledge promotes, no LLM guessing.

**`shared_promote_min_access`** (default: 3): Minimum total accesses. Prevents single-access flukes from triggering promotion.

## Consolidation engine

```toml
[consolidation]
enabled = true
interval_hours = 6
similarity_merge_threshold = 0.85
micro_cycle_cooldown_minutes = 5
nightly_synthesis_hour_utc = 23
nightly_synthesis_min_entries = 10
```

**`enabled`** (default: true): Set to false to disable background consolidation entirely. Useful for testing.

**`interval_hours`** (default: 6): Hours between full consolidation cycles. Each cycle runs all 7 steps (decay, promote, dedup, etc.).

**`similarity_merge_threshold`** (default: 0.85): Cosine similarity threshold for merging duplicate LTM entries. Entries above this threshold are clustered and merged via LLM. **Tradeoff:** Lower = more aggressive dedup (fewer entries, cleaner memory). Higher = only merges very similar entries (preserves nuance).

**`micro_cycle_cooldown_minutes`** (default: 5): Minimum minutes between reactive micro-cycles. When a high-importance entry is written, the consolidator wakes for a fast promotion pass, but not more often than this.

**`nightly_synthesis_hour_utc`** (default: 23): UTC hour to run nightly synthesis. Set to your quietest hour.

**`nightly_synthesis_min_entries`** (default: 10): Minimum journal entries in the last 24 hours to trigger synthesis. Prevents running on days with too little data.

## Entity memory (Tier 5)

```toml
[entity]
max_chars_in_prompt = 2000
```

**`write_permissions`**: Control which agents can write to which entity types. Empty (default) means full access for all agents.

```python
NmemConfig(entity={"write_permissions": {
    "support": ["customer", "ticket"],
    "sales": ["lead", "customer"],
    "engineering": ["*"],  # Full access
}})
```

## Policy memory (Tier 6)

```toml
[policy]
max_chars_in_prompt = 1000
```

**`writers`** (default: `{"system"}`): Agent IDs allowed to create active policies directly.

**`proposers`** (default: empty): Agent IDs that can propose policies (created with `status="proposed"`, require approval by a writer).

```python
NmemConfig(policy={
    "writers": {"system", "admin"},
    "proposers": {"support", "engineering"},
})
```

## Retrospective (dreamstate)

```toml
[retrospective]
enabled = true
lookback_days = 14
min_lessons = 3
max_llm_calls_per_run = 5
skip_if_validated_within_days = 3
lesson_record_types = ["lesson", "lesson_learned"]
```

**`enabled`** (default: true): Enable/disable the nightly retrospective. When disabled, the step is a complete no-op.

**`lookback_days`** (default: 14): How far back to scan for candidate lessons. Lessons older than this fall out of scope entirely.

**`min_lessons`** (default: 3): Minimum candidate lessons before retrospection fires. Below this, the step is a no-op (avoids noise on quiet days).

**`max_llm_calls_per_run`** (default: 5): Maximum LLM classifications per nightly run. Bounds cost regardless of how many lessons are in scope. With nightly cadence, this means ~150 classifications per month.

**`skip_if_validated_within_days`** (default: 3): Lessons with `last_validated_at` newer than this are excluded from review. Prevents the same lesson from being re-reviewed every night. Set to 0 to disable the guard (not recommended).

**`lesson_record_types`** (default: `["lesson", "lesson_learned"]`): Which LTM `record_type` values the retrospective considers as "lessons". Extend this if your application uses custom record types for learnings.

## Clustering

```toml
[clustering]
similarity_threshold = 0.65
```

**`similarity_threshold`** (default: 0.65): Cosine similarity threshold for assigning entries to the same context thread. Entries more similar than this join an existing thread; dissimilar entries create a new thread.
