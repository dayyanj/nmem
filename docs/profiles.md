# Profiles

nmem ships with named configuration profiles — preset collections of tuned defaults for common deployment scenarios. Profiles save you from guessing at thresholds and give you a working starting point.

## Using profiles

```python
from nmem import NmemConfig

# Neutral (default) — generic, no domain assumptions
config = NmemConfig.from_profile("neutral", database_url="...")

# Refinery — tuned for multi-agent operations platforms
config = NmemConfig.from_profile("refinery", database_url="...")
```

Your explicit overrides always win over profile defaults:

```python
config = NmemConfig.from_profile("refinery",
    database_url="postgresql+asyncpg://...",
    consolidation={"nightly_synthesis_hour_utc": 4},  # override just this field
    belief={"agent_trust": {"my_agent": 0.9}},        # replace agent trust
)
```

## Built-in profiles

### `neutral` (default)

The bare defaults. No domain-specific assumptions, no pre-seeded agent trust, generic thresholds. Use this when you're building something new and want to tune from scratch.

This is what you get with plain `NmemConfig()` — `from_profile("neutral")` is equivalent.

### `refinery`

Tuned for multi-agent operations platforms (extracted from ~6 months of production usage with 6-8 agents). Key differences from neutral:

| Setting | Neutral | Refinery | Why |
|---------|---------|----------|-----|
| `consolidation.nightly_synthesis_min_entries` | 5 | 10 | High-throughput systems generate more journal entries; 10 avoids synthesizing on quiet days |
| `journal.default_expiry_days` | 30 | 30 | Same — 30 days is a good baseline for both |
| `journal.auto_promote_importance` | 7 | 7 | Same — importance 7+ earns LTM promotion |
| `ltm.staleness_days` | 90 | 90 | Same — 3 months before salience decay kicks in |
| `belief.agent_trust` | `{}` (empty) | Pre-seeded for 6 agent roles | Larger models get higher trust; smaller/faster models get lower |
| `retrospective.lookback_days` | 14 | 14 | Same — 2 weeks of lesson review |

The agent trust presets for refinery:

```python
{
    "orchestrator": 0.8,  # coordination agent, sees the full picture
    "researcher": 0.7,    # information gathering, generally reliable
    "writer": 0.6,        # creative output, lower factual certainty
    "critic": 0.7,        # quality control, good judgment
    "coder": 0.7,         # technical accuracy
    "sales_head": 0.6,    # persuasion-oriented, may overstate
}
```

## Custom profiles

Register your own profiles at runtime:

```python
from nmem import register_profile, NmemConfig

register_profile("my_app", {
    "journal": {"default_expiry_days": 14},
    "belief": {"agent_trust": {"my_bot": 0.9}},
    "consolidation": {"nightly_synthesis_min_entries": 3},
})

config = NmemConfig.from_profile("my_app", database_url="...")
```

## Suggested configurations by use case

These aren't shipped as profiles, but they're good starting points to copy into your config. All assume you're using `NmemConfig()` (neutral defaults) and overriding specific fields.

### Customer support bot (single agent)

```python
NmemConfig(
    journal={"default_expiry_days": 14},      # shorter memory — support context is transient
    ltm={"staleness_days": 60},               # facts go stale faster in support
    consolidation={
        "nightly_synthesis_min_entries": 3,    # low-volume system
    },
    retrospective={"min_lessons": 2},         # fewer lessons needed to trigger
    entity={"auto_journal_on_search": True},   # auto-log entity lookups
)
```

### Research assistant (single agent, knowledge-heavy)

```python
NmemConfig(
    journal={"default_expiry_days": 60},      # longer memory — research spans weeks
    ltm={
        "staleness_days": 180,                # knowledge stays relevant longer
        "salience_decay_rate": 0.01,          # slower decay — research is less perishable
    },
    consolidation={
        "similarity_merge_threshold": 0.80,   # more aggressive dedup (research repeats topics)
        "nightly_synthesis_min_entries": 5,
    },
    retrospective={"lookback_days": 30},      # longer lookback for slow-moving research
)
```

### Game NPC / persona (single agent, personality-heavy)

```python
NmemConfig(
    journal={"default_expiry_days": 7},       # NPCs have short attention spans
    ltm={
        "staleness_days": 30,                 # personality facts decay if unused
        "min_salience": 0.5,                  # higher floor — NPCs shouldn't forget core traits
    },
    consolidation={
        "interval_hours": 1,                  # fast consolidation for responsive NPCs
        "nightly_synthesis_min_entries": 2,
    },
    retrospective={"enabled": False},         # NPCs don't need lesson validation
    belief={"enabled": False},                # no contradiction tracking for fiction
)
```

### Multi-agent team (3-10 agents)

```python
NmemConfig.from_profile("refinery",          # good starting point for multi-agent
    belief={
        "agent_trust": {
            "manager": 0.8,
            "analyst": 0.7,
            "executor": 0.5,
        },
    },
    consolidation={
        "nightly_synthesis_min_entries": 8,
    },
    ltm={"shared_promote_min_agents": 2},     # 2 agents accessing = shared knowledge
)
```

### Personal AI assistant (single agent, long-running)

```python
NmemConfig(
    journal={"default_expiry_days": 90},      # long memory for personal assistant
    ltm={
        "staleness_days": 365,                # user preferences persist for a year
        "salience_decay_rate": 0.005,         # very slow decay
    },
    consolidation={
        "nightly_synthesis_min_entries": 1,    # even one interaction is worth synthesizing
    },
    retrospective={"min_lessons": 1},         # validate even single lessons
    policy={
        "writers": {"system", "user"},        # user can set policies directly
    },
)
```
