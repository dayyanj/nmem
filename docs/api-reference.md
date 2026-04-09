# API Reference

All methods are async. The primary entry point is `MemorySystem`.

## MemorySystem

```python
from nmem import MemorySystem, NmemConfig

mem = MemorySystem(NmemConfig(database_url="...", embedding={...}, llm={...}))
await mem.initialize()
# ... use mem ...
await mem.close()
```

### Lifecycle

| Method | Description |
|--------|-------------|
| `await mem.initialize()` | Create tables, indexes, validate config. Idempotent. |
| `await mem.close()` | Stop consolidator, close database connections. |
| `mem.start_consolidation() -> Task` | Start background consolidation loop. |
| `mem.stop_consolidation()` | Stop background consolidation. |

### Cross-tier search

```python
results = await mem.search(
    agent_id="support",
    query="payment timeout",
    tiers=("journal", "ltm", "shared"),  # optional, default: all
    top_k=10,
)
```

Returns `list[SearchResult]` ranked by score. Each `SearchResult` has:
- `tier` — which tier the result came from
- `id` — entry ID
- `score` — relevance score (0.0 to 1.0)
- `content` — entry content
- `title` / `key` — entry title or key
- `agent_id` — owning agent (if applicable)
- `metadata` — tier-specific metadata dict

### Prompt building

```python
ctx = await mem.prompt.build(
    agent_id="support",
    session_id="session-1",       # optional, for working memory
    query="payment issues",       # optional, enables relevance ranking
    entity_type="customer",       # optional, for entity dossier
    entity_id="cust-jane-doe",    # optional, for entity dossier
)

print(ctx.full_injection)   # formatted memory block for system prompt
print(ctx.token_estimate)   # estimated token count
```

### Tier access

| Property | Type | Description |
|----------|------|-------------|
| `mem.working` | `WorkingMemoryTier` | Tier 1 |
| `mem.journal` | `JournalTier` | Tier 2 |
| `mem.ltm` | `LTMTier` | Tier 3 |
| `mem.shared` | `SharedTier` | Tier 4 |
| `mem.entity` | `EntityTier` | Tier 5 |
| `mem.policy` | `PolicyTier` | Tier 6 |
| `mem.cognitive` | `CognitiveEngine` | Deja vu, curiosity |
| `mem.consolidation` | `Consolidator` | Background engine |

---

## Working Memory (Tier 1)

Ephemeral per-session key-value slots.

### `set`

```python
slot = await mem.working.set(
    session_id="session-1",
    agent_id="support",
    slot="current_task",          # slot name
    content="Debugging payment timeout",
    priority=5,                   # 1-10, lower = higher priority
)
```

### `get`

```python
slots = await mem.working.get("session-1", "support")
# Returns list[WorkingSlot] ordered by priority
```

### `clear`

```python
# Clear a specific slot
await mem.working.clear("session-1", "support", slot="current_task")

# Clear all slots for a session/agent
count = await mem.working.clear("session-1", "support")
```

### `build_prompt`

```python
text = await mem.working.build_prompt("session-1", "support", max_chars=1000)
```

---

## Journal (Tier 2)

30-day activity log with auto-promotion.

### `add`

```python
entry = await mem.journal.add(
    agent_id="support",
    entry_type="lesson_learned",    # observation, decision, lesson_learned, etc.
    title="Refunds need manager approval",
    content="Full details here...",
    importance=7,                   # 1-10 (>=7 auto-promotes to LTM)
    session_id="session-1",         # optional
    tags=["billing", "refund"],     # optional
    record_type="evidence",         # evidence, fact, judgment, task, rule, summary
    grounding="inferred",           # source_material, inferred, confirmed, disputed
    compress=True,                  # LLM-compress if content > 200 chars
)
```

Returns `JournalEntry`.

### `search`

```python
entries = await mem.journal.search(
    agent_id="support",
    query="refund approval",
    top_k=5,
    entry_type="lesson_learned",   # optional filter
    min_importance=5,              # optional filter
)
```

Returns `list[JournalEntry]` ranked by hybrid search score. Bumps `access_count` on returned entries.

### `recent`

```python
entries = await mem.journal.recent("support", days=7, limit=10)
```

Returns `list[JournalEntry]` in reverse chronological order.

### `activity_summary`

```python
text = await mem.journal.activity_summary("support", days=1)
# "Activity summary (1d, 5 entries):\n  - [observation] Server crashed..."
```

### `build_prompt`

```python
text = await mem.journal.build_prompt("support", query="payment issues", max_chars=1500)
```

---

## Long-Term Memory (Tier 3)

Permanent per-agent knowledge.

### `save`

```python
entry = await mem.ltm.save(
    agent_id="engineering",
    category="procedure",           # fact, procedure, lesson, pattern, etc.
    key="deploy_checklist",         # unique within (agent_id, key)
    content="1. Run migrations...",
    importance=8,
    source="agent",                 # agent, promotion, consolidation
    record_type="fact",
    grounding="confirmed",
    compress=True,
)
```

**Upserts**: If `(agent_id, key)` exists, the entry is updated and `version` is incremented.

### `save_batch`

```python
entries = await mem.ltm.save_batch([
    {"agent_id": "eng", "category": "fact", "key": "k1", "content": "...", "importance": 5},
    {"agent_id": "eng", "category": "fact", "key": "k2", "content": "...", "importance": 6},
], compress=False)
```

Embeds all entries in a single batch call. 1.6x faster than individual `save()` calls at 100 entries.

### `search`

```python
entries = await mem.ltm.search("engineering", "deployment process", top_k=5, category="procedure")
```

Returns `list[LTMEntry]`. Bumps `access_count` and records the searching agent in `accessed_by_agents`.

### `get`

```python
entry = await mem.ltm.get("engineering", "deploy_checklist")  # O(1) by key
```

### `list_keys`

```python
keys = await mem.ltm.list_keys("engineering", category="procedure")
```

### `delete`

```python
deleted = await mem.ltm.delete("engineering", "old_key")  # Returns bool
```

---

## Shared Knowledge (Tier 4)

Cross-agent canonical facts.

### `save`

```python
entry = await mem.shared.save(
    agent_id="system",
    category="policy",
    key="refund_policy",
    content="30-day window. Over $100 needs manager approval.",
    importance=9,
)
```

### `search`

```python
entries = await mem.shared.search("refund process", top_k=5, category="policy")
```

### `get`

```python
entry = await mem.shared.get("refund_policy")  # By key
```

---

## Entity Memory (Tier 5)

Per-object collaborative workspace.

### `save`

```python
record = await mem.entity.save(
    entity_type="customer",
    entity_id="cust-jane-doe",
    entity_name="Jane Doe",
    agent_id="support",
    content="Reported double charge on order #5678.",
    record_type="evidence",        # evidence, judgment, task, summary
    confidence=1.0,                # forced <1.0 for judgments
    grounding="source_material",
    tags=["billing"],
    evidence_refs=[{"source": "ticket-1234"}],
)
```

### `get`

```python
records = await mem.entity.get("customer", "cust-jane-doe", record_type="evidence")
```

### `search`

```python
records = await mem.entity.search("billing issue", entity_type="customer", top_k=5)
```

### `get_summary`

```python
summary = await mem.entity.get_summary("customer", "cust-jane-doe")
# Returns the latest record_type="summary" entry, or None
```

### `build_prompt`

```python
dossier = await mem.entity.build_prompt("customer", "cust-jane-doe", max_chars=2000)
```

---

## Policy Memory (Tier 6)

Governance rules with permissions.

### `save`

```python
entry = await mem.policy.save(
    scope="global",             # global, team, agent
    category="safety",
    key="no_pii_in_logs",
    content="Never log PII. Mask before logging.",
    agent_id="system",          # must be in config.policy.writers
)
```

Raises `PermissionError` if the agent is not a configured writer or proposer.

### `approve`

```python
entry = await mem.policy.approve(policy_id=1, agent_id="system")
```

### `get_active`

```python
policies = await mem.policy.get_active("global")
```

---

## Cognitive Engine

### `find_similar_experience` (deja vu)

```python
similar = await mem.cognitive.find_similar_experience(
    instruction="Deploy the payment service to production",
    agent_id="engineering",
    threshold=0.8,
    top_k=1,
)
# Returns list[DelegationRecord] of similar past tasks
```

### `emit_curiosity`

```python
signal = await mem.cognitive.emit_curiosity(
    source_agent="support",
    trigger_type="recurring_issue",
    summary="3 customers hit the same onboarding bug this week",
    novelty_score=0.8,
    business_impact=0.7,
)
```

---

## Consolidator

### `run_full_cycle`

```python
stats = await mem.consolidation.run_full_cycle()
print(stats.promoted_to_ltm, stats.duplicates_merged, stats.duration_seconds)
```

### `run_micro_cycle`

```python
stats = await mem.consolidation.run_micro_cycle("high_importance_entry")
```

### `run_nightly_synthesis`

```python
stats = await mem.consolidation.run_nightly_synthesis()
print(stats.patterns_synthesized)
```

### Custom hooks

```python
async def my_custom_step():
    # Run custom logic during consolidation
    pass

mem.consolidation.register_full_cycle_step("my_step", my_custom_step)
mem.consolidation.register_nightly_step("my_nightly", my_custom_step)
```

---

## Event system

```python
@mem.on("journal.added")
async def on_journal(entry):
    print(f"New journal entry: {entry.title}")

# Supported events:
# - "journal.added"
# - "ltm.saved"
# - "shared.saved"
# - "conflict.detected"
# - "consolidation.promoted"
```

---

## Framework adapters

### LangChain

```python
from nmem.adapters.langchain import NmemLangChainMemory

memory = NmemLangChainMemory(mem_system=mem, agent_id="my_agent")
variables = await memory.aload_memory_variables({"input": "What about refunds?"})
# {"memory_context": "# Agent Memory\n\n## Shared Knowledge\n..."}
```

### CrewAI

```python
from nmem.adapters.crewai import NmemCrewAIMemory

memory = NmemCrewAIMemory(mem_system=mem, agent_id="researcher")
await memory.save("Key finding about market trends", metadata={"source": "report"})
results = await memory.search("market trends", limit=5)
```
