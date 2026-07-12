# Working Memory (Tier 1)

Ephemeral per-session scratchpad. Current task context, recent decisions, and temporary notes that exist only for the duration of a session. Cleared on session end or flushed to journal.

## Modules

| Module | Purpose |
|--------|---------|
| `tiers/working.py` | WorkingMemoryTier: slot-based storage, priority ordering, prompt building, flush to journal |

## Data Flow

```
Agent writes during session
    |
    v
working.set(session_id, agent_id, slot, content, priority)
    |
    ├── Stores in nmem_working_memory table
    ├── Priority 1 = highest, shown first in prompt
    └── Max slots per session: 20 (configurable)

Session ends:
    |
    ├── Option A: working.clear() ── discard everything
    └── Option B: flush_to_journal() ── summarize to journal entries
                      |
                      └── Each slot becomes a journal entry
                          importance inherited from priority
```

## Key Methods

| Method | Purpose |
|--------|---------|
| `set(session_id, agent_id, slot, content, priority, context_thread_id)` | Write or overwrite a named slot |
| `get(session_id, agent_id)` | Retrieve all slots for this session+agent |
| `clear(session_id, agent_id, slot=None)` | Clear one or all slots |
| `build_prompt(session_id, agent_id, max_chars)` | Format slots as prompt section, priority-ordered |
| `flush_to_journal(session_id, agent_id, journal_tier, clear_after=True)` | Promote all slots to journal entries |

## WorkingSlot Fields

| Field | Type | Purpose |
|-------|------|---------|
| `session_id` | str | Session identifier |
| `agent_id` | str | Which agent owns this slot |
| `slot` | str | Named slot (e.g., "current_task", "decision_log") |
| `content` | str | Slot content |
| `priority` | int | Display order (1=highest) |
| `context_thread_id` | str | Optional semantic thread grouping |
| `created_at` | datetime | When written |
| `updated_at` | datetime | Last modified |

## Links to Other Components

- **journal.py** (Journal): `flush_to_journal()` creates journal entries from slots
- **prompt.py** (Prompt Builder): working memory section included in `PromptContext.working`
- **memory.py** (MemorySystem): `end_session()` triggers flush

## Database Tables

| Table | Purpose |
|-------|---------|
| `nmem_working_memory` | session_id, agent_id, slot, content, priority, context_thread_id, project_scope |

## Key Config

```
working.max_slots_per_session = 20
working.max_chars_in_prompt = 1000
```
