# Changelog

All notable changes to nmem are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.7.0] — 2026-04-21

**Theme: Scale & Correctness** — fixes discovered during the 360-day healthcare
benchmark (4,638 patients, 23K encounters, 5 agents). All changes improve
production reliability at scale.

### Performance

- **Parallel LTM compression**: expired + importance promotions now run in
  batches of 3 via `asyncio.gather`, spreading load across multiple LLM
  backends. ~3x throughput on promotion-heavy consolidation cycles.
- **PyTorch thread explosion fix**: embedding provider now sets
  `torch.set_num_threads(1)` and `torch.set_num_interop_threads(1)` at init.
  Previously, each `asyncio.to_thread` call spawned a 24-thread PyTorch pool,
  accumulating 890+ threads and severe GIL contention over time. Eliminates
  progressive slowdown where journal.add degraded from 5ms to 1.4s per call.
- **Incremental dedup**: `_dedup_similar_memories` now only compares entries
  created since `_last_full_cycle` against the existing corpus, instead of
  all-pairs comparison. O(new × total) instead of O(total²).
- **Persisted `_last_full_cycle`**: consolidation timestamp now persists to
  `nmem_metadata` table and loads on startup, eliminating the expensive
  first-cycle all-pairs fallback after every restart.

### Fixes

- **TOCTOU upsert race condition** (critical): `_promote_entry` now uses
  `INSERT ... ON CONFLICT DO UPDATE` via SQLAlchemy's `pg_insert()`. Previously,
  concurrent batch promotions could both pass the "does key exist?" check and
  one would fail with `UniqueViolationError`. Affects any deployment with
  concurrent writes or BATCH > 1.
- **Nightly synthesis hook bypass**: `run_nightly_synthesis` no longer returns
  early when `skip_synthesis=True`, ensuring registered hooks (symbol dreamstate,
  clustering) always execute regardless of synthesis outcome.
- **Event emission on LTM promotion**: `ltm.saved` event now fires correctly on
  the promotion path, enabling downstream listeners (e.g. nmem-sym extraction)
  to react to new LTM entries.

### Added

- **Step-level timing**: each consolidation step now logs
  `[step-timing] step_name: Xs` for steps exceeding 1 second, enabling
  performance profiling at scale without code changes.

### Benchmark results

Healthcare 360-day v2 benchmark (Qwen3-14B, consumer hardware):
- nmem vs baseline: **+0.50** mean score improvement, **77% win rate**
- Full report: `docs/benchmarks/healthcare-360d-v2.md`

---

## [0.3.0] — 2026-04-12

**Theme: Belief & Importance Refactor** — addressing community feedback on
semantic accuracy, missing cognitive capabilities, and generic-library
positioning. 11 commits, 48 files changed, ~5,100 lines added.

### Breaking changes

- **`LTMModel.confidence` renamed to `LTMModel.salience`** across all LTM
  paths: column, ORM, types, API schemas, CLI output, config. The field
  starts at 1.0 and decays with staleness — that's salience (how strongly
  an entry should influence reasoning), not confidence (whether it's true).
  `EntityMemoryModel.confidence` is unchanged (there it genuinely means
  grounding certainty).
  - Config: `confidence_decay_rate` → `salience_decay_rate`,
    `min_confidence` → `min_salience`.
  - Schema migration v2→v3 renames the column automatically.

- **`importance` parameter changed from `int` default to `int | None`** on
  `journal.add()` and `ltm.save()`. When `None` (the new default), the
  entry is marked `auto_importance=True` and rescored at consolidation.
  Pass an explicit integer to opt out. Existing code passing `importance=N`
  is unaffected.

- **`LTMModel.supersedes_id` renamed to `superseded_by_id`** to match
  forward-pointer semantics. Migration v2→v3 handles the rename.

### Added

- **Belief revision system** — full conflict detection → resolution pipeline:
  - `scan_conflicts()` runs on every LTM and Shared write, detecting
    contradictions via text overlap + vector divergence.
  - `resolve_conflict()` picks winners at consolidation using:
    grounding rank → agent trust → recency → importance.
  - Losers marked `superseded` with `superseded_by_id` pointing at the
    winner. Excluded from search by default (`include_superseded=False`).
  - New `[belief]` config section: `agent_trust` dict, `grounding_priority`
    list, `auto_resolve_grounding_gap`, `default_trust`,
    `scan_candidates_limit`.

- **Auto-importance scoring** — heuristic rescoring at consolidation time
  for entries marked `auto_importance=True`. Factors: record_type weight,
  grounding rank, access velocity, content density. Retroactive boost from
  nightly synthesis respects the flag. New `[importance]` config section.

- **Nightly retrospective ("dreamstate")** — validates past lessons against
  new evidence, piggybacked on `run_nightly_synthesis()`:
  - Pulls LTM lessons from last 14 days, skips recently-validated (3-day
    guard), cross-agent journal search for outcome evidence.
  - LLM classifies: `reinforces` → refresh salience + bump importance;
    `contradicts` → mark `grounding='disputed'`; `neutral` → skip-guard
    bumped.
  - Bounded: `max_llm_calls_per_run = 5` per night.
  - Writes `retrospective_synthesis` shared knowledge entry.
  - New `[retrospective]` config section.

- **Configuration profiles** — named preset collections:
  - `NmemConfig.from_profile("neutral")` — bare defaults, no domain
    assumptions.
  - `NmemConfig.from_profile("refinery")` — pre-seeded agent trust for 6
    roles, tighter synthesis thresholds.
  - `register_profile()` for custom profiles. Deep-merge: profile defaults
    fill gaps, user values always win.
  - New `docs/profiles.md` with 5 suggested configs by use case.

- **Token trends measurement** — automatic tracking of prompt injection
  sizes and LLM operation costs:
  - Every `prompt.build()` records per-section token estimates to
    `nmem_metadata`.
  - LLM operations (synthesis, retrospective) log their token usage.
  - New CLI: `nmem token-trends [--days 30] [--agent X] [--json]`
  - New API: `GET /v1/token-trends?days=30&agent_id=X`

- **`nmem conflicts list [--pending]`** CLI command.
- **`PromptContext.section_tokens`** property — per-section token breakdown.
- **`list_profiles()` and `register_profile()`** in public API.

### Changed

- Consolidation engine expanded from 7 steps to 10: added auto-importance
  (step 5), belief revision (step 6), knowledge links (step 9).
- `ConsolidationStats` gains `auto_importance_rescored`,
  `conflicts_auto_resolved`, `conflicts_needs_review`,
  `lessons_validated`, `lessons_disputed` fields.
- Tier 4 docs reframed as "Shared Knowledge (social learning)" — describes
  the observe → journal → promote → share loop.
- LangChain adapter: `BaseMemory` inheritance (graceful fallback), Python
  3.12-safe sync wrappers, configurable `memory_key` and `input_key`.
- CrewAI adapter: `build_context()` for prompt injection, `reset()`,
  configurable `importance` and `entry_type` on `save()`.
- `docs/configuration.md` expanded with `[belief]`, `[importance]`,
  `[retrospective]`, and `[knowledge_links]` sections.
- `nmem.example.toml` expanded with all new config sections.
- README updated with 10-step consolidation diagrams, social learning
  framing, token trends, profiles, and new CLI commands.

### Fixed

- Schema migration failures were silently swallowed at DEBUG level; now
  logged at WARNING with the failing SQL statement.
- `_find_lesson_outcomes` searched same-agent only — now cross-agent.
- `_find_lesson_outcomes` had no `project_scope` filtering — scoped
  lessons could be validated by evidence from a different project.
- Retrospective candidate ordering was `.asc()` with a comment claiming
  "fresh arrivals prioritized" — flipped to `.desc()`.
- `lesson.key` could be `None` in retrospective synthesis output.
- `record_llm_usage()` was defined but never called — now wired into
  synthesis and retrospective LLM paths.
- Test `_clean_tables()` was missing `nmem_memory_conflicts`,
  `nmem_knowledge_links`, `nmem_metadata`, and `nmem_policy_memory`.

### Upgrade notes for existing PostgreSQL installations

Schema migration v2→v3 runs automatically on `initialize()`. Manual SQL
if needed:

```sql
ALTER TABLE nmem_long_term_memory RENAME COLUMN confidence TO salience;
ALTER TABLE nmem_long_term_memory RENAME COLUMN supersedes_id TO superseded_by_id;
ALTER TABLE nmem_long_term_memory ADD COLUMN auto_importance BOOLEAN DEFAULT TRUE;
ALTER TABLE nmem_journal_entries ADD COLUMN auto_importance BOOLEAN DEFAULT TRUE;
```

---

## [0.2.0] — 2026-04-12

### Added
- `memory_write_entity` MCP tool — write typed entity records with
  explicit record_type and grounding lifecycle.
- `memory_write_policy` MCP tool — write governance policies with
  upsert semantics on (scope, key).
- `memory_check_conflicts` MCP tool — list conflicts detected by the
  scanner, with status/agent/scope/time filters and `all_scopes` param.
- `memory_mark_grounding` MCP tool — transition an entity record's
  grounding value (inferred → confirmed / disputed) with audit trail.
- `memory_search` now accepts "policy" in its `tiers` parameter.
- `list_conflicts()` function in `nmem.conflicts`.
- `EntityTier.update_grounding()` method.
- `PolicyTier.search()` method (FTS on postgres, LIKE on sqlite).
- Default policy writers expanded to include "default" and "mcp" for
  out-of-box MCP tool usage.
- `project_scope` column on `nmem_memory_conflicts` table — conflicts
  are now scoped to the project that produced them. Populated
  automatically by the scanner at write time.

### Changed
- `nmem setup` CLAUDE.md and AGENTS.md snippets restructured into
  Retrieval / Writes / Integrity groups, with a "which tier" decision
  tree. `memory_linked` now documented (was missing in 0.1.x).
- `cross_tier_search` accepts optional `policy` keyword argument.

### Upgrade notes for existing PostgreSQL installations
- Run: `ALTER TABLE nmem_memory_conflicts ADD COLUMN project_scope VARCHAR(300);`
- Run: `CREATE INDEX ix_nmem_conflict_project_scope ON nmem_memory_conflicts (project_scope);`
- Existing conflict rows will have `project_scope = NULL` (treated as global).

### Notes
- All changes are additive. Existing 8 MCP tools and their parameters
  are unchanged. Agents built against 0.1.x continue to work.
- Policy tier has no embedding column — search uses FTS (postgres) or
  LIKE (sqlite) instead of hybrid vector+FTS.
- Policy tier is globally scoped (no project_scope column). Searching
  with `tiers="policy"` returns the same policies regardless of the
  current project scope.
