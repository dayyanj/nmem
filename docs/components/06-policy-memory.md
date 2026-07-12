# Policy Memory (Tier 6)

Governance rules with role-based permissions. Policies define how agents should behave, scoped from global to per-agent to per-entity-type. Writers create active policies; proposers submit drafts that need approval.

## Modules

| Module | Purpose |
|--------|---------|
| `tiers/policy.py` | PolicyTier: save with role checking, approval workflow, search, get, list, prompt building |

## Data Flow

```
Agent creates a policy
    |
    v
policy.save(scope, category, key, content, agent_id)
    |
    ├── ROLE CHECK:
    |       Writers → status="active", approved_by=agent_id
    |       Proposers → status="proposed", approved_by=None
    |       Others → PermissionError
    |
    ├── UPSERT by (scope, key)
    |       Existing: increment version, append change_log
    |
    └── FTS INDEX (PostgreSQL) or LIKE fallback (SQLite)

Proposed policies require approval:
    policy.approve(policy_id, agent_id)
        → status changes to "active"
        → approved_by = agent_id
```

## Scope System

Policies are scoped hierarchically:

| Scope | Example | Applies To |
|-------|---------|-----------|
| `global` | `global` | All agents |
| `agent:{id}` | `agent:researcher` | Specific agent |
| `entity_type:{type}` | `entity_type:patient` | Specific entity type operations |

## Prompt Injection

`build_prompt(agent_id)` returns:
1. All global active policies
2. All agent-specific active policies for this agent_id
3. Formatted as markdown with scope/category headers

## Links to Other Components

- **prompt.py** (Prompt Builder): policy section in `PromptContext.policy` (10% of token budget)
- **search.py** (Search): FTS search within policies
- **memory.py** (MemorySystem): `start_consolidation()` does not touch policies -- they are manually managed

## Database Tables

| Table | Purpose |
|-------|---------|
| `nmem_policy_memory` | scope, category, key, content, created_by, approved_by, status (active/proposed), version, change_log |

Unique constraint: (scope, key)
Indexes: (scope, category), status

## Key Config

```
policy.max_chars_in_prompt = 1000
policy.writers = ["orchestrator"]
policy.proposers = ["researcher", "writer"]
```
