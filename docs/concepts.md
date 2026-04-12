# Concepts

nmem is a hierarchical memory system for AI agents. This page explains the mental model: what each tier is for, how entries flow between tiers, and how the consolidation engine works.

## The 6-tier hierarchy

Think of nmem like human memory:

```
Working Memory  →  Journal  →  LTM  →  Shared Knowledge
(what I'm doing)   (what happened)   (what I know)   (what everyone knows)

Entity Memory: per-person/object notebooks
Policy Memory: rules everyone must follow
```

### Tier 1: Working Memory

**What it is:** Ephemeral key-value slots for the current session. Like a scratchpad.

**Use it for:** Current task, active context, scratch notes. Cleared when the session ends.

**Example:**
```python
await mem.working.set("session-1", "agent-1", "current_task", "Debugging payment timeout")
```

**Lifespan:** Session only. Never promoted.

### Tier 2: Journal

**What it is:** A 30-day activity log. Every significant observation, decision, and outcome gets recorded here.

**Use it for:** Session summaries, decisions made, lessons learned, things you noticed. This is the primary write target: most agent interactions should create journal entries.

**Example:**
```python
await mem.journal.add(
    agent_id="support",
    entry_type="lesson_learned",
    title="Refunds over $100 need manager approval",
    content="Customer requested $150 refund. Discovered this requires...",
    importance=7,
)
```

**Lifespan:** 30 days, then expires. High-importance entries (≥7) auto-promote to LTM. Frequently-accessed entries (≥5 accesses) also promote.

**Entry types:** `observation`, `decision`, `lesson_learned`, `session_summary`, `investigation`, `interaction`, `analysis`

### Tier 3: Long-Term Memory (LTM)

**What it is:** Permanent per-agent knowledge. Facts, procedures, lessons that have proven their value.

**Use it for:** Knowledge that should persist permanently: procedures ("how to deploy"), facts ("team standup is at 9:30"), lessons ("never deploy on Fridays").

**Example:**
```python
await mem.ltm.save(
    agent_id="engineering",
    category="procedure",
    key="deploy_checklist",
    content="1. Run migrations, 2. Seed cache, 3. Verify health checks",
    importance=8,
)
```

**Lifespan:** Permanent. **Salience** (how strongly the entry influences current reasoning) decays if not accessed for 90+ days — the knowledge isn't deleted, it just ranks lower until something re-activates it. Automatically promotes to Shared when ≥2 different agents access it.

> **Salience vs grounding**: Salience is NOT a truth measure. An entry with low salience may still be true — it's just cold. For truth/grounding certainty, see `record_type` (`evidence` / `judgment` / `fact`) and `grounding` (`source_material` / `confirmed` / `inferred` / `disputed`). Entity memory uses a separate `confidence` field with strict grounding semantics.

**Categories:** `fact`, `procedure`, `lesson`, `pattern`, `policy`, `contact`, `troubleshooting`, `architecture`

### Tier 4: Shared Knowledge (social learning)

**What it is:** Cross-agent canonical facts. Visible to ALL agents.

**Use it for:** Company-wide knowledge: policies, vendor contacts, escalation procedures, architecture decisions that affect everyone.

This tier is the heart of **social learning** in nmem. Agents don't just maintain private memories — they contribute to a shared knowledge base that makes every agent smarter over time. The learning loop:

1. Agent observes something → writes to journal
2. If important enough → promoted to private LTM
3. If other agents start accessing it → auto-promoted to shared knowledge
4. Every agent's prompt injection now includes this knowledge

The result: when one agent learns a lesson, all agents benefit — without explicit programming or message-passing between agents.

**Example:**
```python
await mem.shared.save(
    agent_id="system",
    category="policy",
    key="refund_policy",
    content="30-day refund window. Over $100 needs manager approval.",
    importance=9,
)
```

**Lifespan:** Permanent. Versioned with change log. Three paths to creation:
1. **Direct save**: an agent saves shared knowledge explicitly
2. **Promotion**: an LTM entry is accessed by ≥2 distinct agents, proving cross-agent relevance
3. **Nightly synthesis**: the consolidation engine discovers cross-cutting patterns across all agents' journal entries

### Tier 5: Entity Memory

**What it is:** A collaborative notebook per business object (customer, bug, deployment, etc.). Multiple agents read and write.

**Use it for:** Building a dossier about a specific entity. Support notes about a customer, engineering notes about a bug, sales notes about a prospect, all in one place.

**Example:**
```python
await mem.entity.save(
    entity_type="customer",
    entity_id="cust-jane-doe",
    entity_name="Jane Doe",
    agent_id="support",
    content="Reported double charge on order #5678. Refund initiated.",
    record_type="evidence",
    confidence=1.0,
    grounding="source_material",
)
```

**Record types:**
- `evidence`: observed facts from source material (highest confidence)
- `judgment`: inferred conclusions (forced confidence <1.0)
- `task`: actions to take
- `summary`: aggregated summary of all records

**Grounding levels:**
- `source_material`: directly from primary source
- `inferred`: derived by reasoning
- `confirmed`: verified by multiple sources
- `disputed`: contradicted by another record

### Tier 6: Policy Memory

**What it is:** Governance rules with writer/proposer permissions.

**Use it for:** Hard rules the system must follow: access controls, safety guardrails, operational constraints. Only designated "writer" agents can create active policies; others can propose.

**Example:**
```python
await mem.policy.save(
    scope="global",
    category="safety",
    key="no_pii_in_logs",
    content="Never log PII (email, phone, SSN). Mask all PII before logging.",
    agent_id="system",
)
```

## How entries flow between tiers

```
         ┌──────────────────────────────────────────────┐
         │           Working Memory (session)            │
         └─────────────────────┬────────────────────────┘
                               │ session end
                               ▼
         ┌──────────────────────────────────────────────┐
         │              Journal (30 days)                │
         │                                              │
         │  importance ≥ 7  OR  access_count ≥ 5        │
         └─────────────────────┬────────────────────────┘
                               │ consolidation promotes
                               ▼
         ┌──────────────────────────────────────────────┐
         │           Long-Term Memory (permanent)        │
         │                                              │
         │  accessed by ≥ 2 agents  AND  importance ≥ 8 │
         └─────────────────────┬────────────────────────┘
                               │ access-based promotion
                               ▼
         ┌──────────────────────────────────────────────┐
         │        Shared Knowledge (cross-agent)         │
         └──────────────────────────────────────────────┘
```

Key principle: **entries earn their way up**. Nothing is promoted by an LLM's guess about "universality". Promotion is driven by importance scores set by authors and access patterns from other agents.

## Consolidation engine

The consolidation engine runs in the background (every 6 hours by default) and performs 7 steps:

| Step | What it does | Why it matters |
|------|-------------|----------------|
| 1. Decay expired | Delete expired journal entries with low importance | Prevents unbounded growth |
| 2. Promote to LTM | Move high-importance journal entries to permanent storage | Saves valuable knowledge |
| 3. Promote to Shared | Move cross-agent LTM entries to shared knowledge | Knowledge that multiple agents need becomes canonical |
| 4. Dedup LTM | Cluster similar entries (cosine >0.85), merge via LLM | Prevents redundant knowledge |
| 5. Salience decay | Reduce salience on stale entries (not accessed in 90+ days) | Stale knowledge fades from current reasoning (but isn't deleted — it just ranks lower) |
| 6. Custom hooks | Run application-specific steps | Extensibility |
| 7. Curiosity decay | Reduce scores on old unresolved curiosity signals | Prevents signal fatigue |

**Micro-cycles:** When a high-importance entry (≥7) is created, the consolidator wakes immediately for a fast promotion pass. Critical knowledge reaches LTM within seconds.

**Nightly synthesis:** Once daily, the engine analyzes all journal entries from the past 24 hours, uses an LLM to extract 2-3 cross-cutting patterns, and saves them as shared knowledge. It also retroactively boosts the importance of journal entries that contributed to discovered patterns.

**Retrospective (dreamstate):** After nightly synthesis completes, the engine reviews past lessons against new evidence — like the brain consolidating during sleep. It pulls LTM entries with `record_type` matching lesson patterns (default: `lesson`, `lesson_learned`) created within `lookback_days` (default 14), skipping any validated within `skip_if_validated_within_days` (default 3). For each, it searches the journal for outcome entries created *after* the lesson, then asks the LLM to classify: `reinforces` (bump salience + importance for auto-managed entries), `contradicts` (mark `grounding='disputed'`), or `neutral` (skip-guard bumped, revisit later). Bounded by `max_llm_calls_per_run` (default 5) per night. Findings are written as shared knowledge with `category="retrospective_synthesis"`.

## Hybrid search

Every search in nmem combines two signals:

- **Vector similarity (60%)**: pgvector cosine distance against embeddings
- **Full-text search (40%)**: PostgreSQL `tsvector` / `ts_rank_cd`

This means a search for "deploy process" finds entries that:
- Are semantically similar (vector catches "deployment procedure", "release workflow")
- Contain the exact words (FTS catches "deploy" even if the embedding model doesn't rank it highest)

The 60/40 weighting is tuned from production experience in the Spwig refinery system. Vector catches semantic meaning, FTS catches precise terminology.

## Context threads

nmem automatically clusters related entries into "context threads" using cosine similarity (threshold: 0.65). When you add a journal entry about "database backup started", and later add one about "database backup completed", they're assigned to the same thread.

This enables future features like thread-scoped search ("show me everything about the backup incident") and conversation summarization.

## Prompt injection

The prompt builder assembles memory context for injection into your agent's system prompt:

```python
ctx = await mem.prompt.build(agent_id="support", query="payment issue")
system_prompt = f"You are a support agent.\n\n{ctx.full_injection}"
```

The injection uses **tiered verbosity**:
- **Policies**: full text (rules must be complete)
- **Shared knowledge**: truncated stubs with keys
- **LTM**: category + key + truncated content
- **Journal**: date + type + title only (use `memory_search` for details)
- **Working memory**: slot name + content

This keeps the injection compact (~300-500 tokens) while giving the agent access to the most relevant context.

## Token trends

nmem tracks prompt injection sizes and LLM costs automatically. Every call to `mem.prompt.build()` records per-section token estimates, and consolidation operations (compression, synthesis, dedup) log their LLM usage. Query trends via the API (`GET /v1/token-trends`) or CLI (`nmem token-trends`).

Over time, you should see average tokens per prompt build **stabilize or decrease** as the consolidation engine deduplicates knowledge, compresses verbose entries, and the tiered verbosity keeps injections compact. If average tokens per call is climbing, it's a signal that consolidation thresholds may need tuning.

## Social learning in multi-agent systems

In a single-agent setup, nmem is a persistent memory that reduces re-investigation across sessions. In a **multi-agent** setup, nmem becomes a social learning system:

```
Agent A observes → journals → promotes to LTM
                                    ↓
                          Agent B accesses it
                                    ↓
                   Auto-promotes to Shared Knowledge
                                    ↓
                All agents receive it in prompt injection
```

Three mechanisms drive this:

1. **Access-based promotion**: When ≥2 different agents access the same LTM entry, the system recognizes it as cross-agent relevant and promotes it to Shared Knowledge automatically.

2. **Nightly synthesis**: The consolidation engine analyzes *all* agents' journal entries together, extracting patterns that no single agent could see. A support agent's customer complaints + an engineering agent's deployment logs → a synthesized insight about deployment timing.

3. **Belief revision**: When agents disagree (contradictory knowledge), the conflict detection system flags the contradiction and resolves it using grounding rank, agent trust, and recency — rather than silently overwriting with the last write.

The effect compounds: each agent's individual learning improves the collective knowledge base, and the collective knowledge base makes each agent's per-session context richer and more accurate.
