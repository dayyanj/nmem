# Changelog

All notable changes to nmem are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.0.0] — 2026-09-15

**First stable release.** nmem's public API and on-disk schema are now stable —
breaking changes bump the major version from here — and the package is promoted from
Alpha to Production/Stable. This release rolls up everything since 0.11.0: a headless
agent runtime, a hardened skills loop, self-engineering, the capability-activation
sweep, two additive schema steps, and the nmem-studio pull-and-run appliance.

### Added

- **nmem-studio — a pull-and-run appliance.** A single Docker image stands up one fully
  configured agent from a web wizard (pick a persona, point it at your LLM, choose how much
  of a mind it has) — no config files by hand. Bundles Postgres+pgvector and the nmem-viz
  brain in one container with all state on one volume; optional `identity` (writing-style
  recognition) and `perception` (embodied nmem-sandbox) compose profiles. Published on
  Docker Hub (`docker.io/dayyanj`), with a landing page at
  [huggingface.co/dayyanj/nmem-studio](https://huggingface.co/dayyanj/nmem-studio).

- **`nmem.agent_core` — a headless cognitive runtime.** A new agent is now *config +
  persona + (executor) + host I/O*, with no hand-rolled cognition. `AgentRuntime` owns
  the whole boot→run→shutdown lifecycle (memory + symbol graph + backend + persona
  seeding + the drive / pursuit / consolidation / recall / comms loops); `Persona` +
  `seed_persona` carry identity as data; `build_memory` / `build_symbol_graph` /
  `build_backend` are the order-safe bootstrap; `CommsLoop` / `ChannelSink` and
  `PeerExchange` give channel-agnostic communication; `SymbolGoalStore` and the recall
  consumer are proven adapters. Opt-in and lazily imported, so `import nmem` never pulls
  the nmem-sym / nmem-act cycle. Proven by extraction from a live agent and shipped with a
  four-file `examples/minimal_agent/`.
- **Skills capture→surface→apply loop (hardened).** Native LLM canonicalization of skill
  keys, canonical-key dedup that coalesces paraphrases of one lesson, salience-ranked
  `find()`, and `skill.chronic` escalation at milestones — the loop that lets an agent
  stop repeating its mistakes.
- **Self-engineering** — context recipes (advisory prompt fragments distilled from proven
  skills, behind an acceptance gate + host veto + staleness decay) and propose-only
  sub-agent specs. **Capability-activation sweep** — drives, autonomy, prediction,
  concern-persistence, and commitment-detection land as opt-in, default-off capabilities.

### Changed

- **`Development Status` → Production/Stable.**

### Schema

- **v4** — `nmem_journal_entries.entry_type` widened `VARCHAR(30)` → `VARCHAR(100)`.
- **v5** — `nmem_skills.canonical_key VARCHAR(200)` added (nullable) + partial index,
  backing canonical-key dedup.
- `CURRENT_SCHEMA_VERSION = 5`. All 0.9→1.0 schema deltas are additive and
  forward-compatible: an agent on an older nmem keeps reading and writing the shared
  tables unchanged.

### Compatibility

- Default-off / additive throughout. Adopting 1.0.0 alongside agents still on 0.9–0.11 on
  a **shared database** is safe — every new capability stays inert until enabled in that
  agent's `capabilities.env`.

## [0.11.0] — 2026-08-19

**Theme: self-engineering — nmem distills its own context and proposes its own
sub-agents.** Building on 0.10's skills, nmem now makes bounded, single-turn LLM
calls during consolidation to turn proven experience into reusable artifacts. All
opt-in (defaults OFF); with a noop LLM every step is a clean no-op.

### Added

- **Context recipes (2A)** — nmem distills reliable skills + their original
  memories into compact **advisory prompt fragments** (`nmem_context_recipes`),
  and injects the matching one into its OWN assembled context as a
  `## Learned Guidance (advisory)` block placed last and explicitly subordinate
  to policy/direct memory. Safety-first, because an auto-active recipe could
  otherwise silently degrade the prompt:
  - an **acceptance gate** (validate/shape/size + reject policy-override language)
    before a recipe is written `active`;
  - **near-exact match** + a **total section budget** on injection;
  - **host veto** — `mem.self_engineering.disable(id, reason)` removes a recipe
    AND **tombstones** its source cluster so nightly consolidation won't
    re-distill it (a cooldown that grows on repeated disables);
  - **staleness decay** (stale recipes demote themselves);
  - **no recursion** — recipes are distilled only from skills + memories, never
    from other recipes.
- **Sub-agent proposals (2B)** — from highly-reliable skills, nmem emits rich,
  **propose-only** sub-agent specs (`nmem_subagent_proposals`): name, system
  prompt, trigger conditions, a **snapshot** of the distilled recipe, suggested
  tools, source skills, and reliability evidence. nmem never runs them — the host
  inspects proposals (`subagent.proposed` event / MCP) and instantiates the ones
  it wants.
- **Bounded LLM spend** — a hard `max_llm_calls_per_run` cap AND per-prompt
  input-size caps (a call cap alone can't stop huge prompts); metered via
  `record_llm_usage` (ops `context_recipe`, `subagent_proposal`).
- **Events**: `recipe.distilled` / `recipe.rejected` / `recipe.disabled`,
  `subagent.proposed` / `subagent.rejected`.
- **MCP tools**: `memory_recipe_list`, `memory_recipe_disable`,
  `memory_subagent_proposals`, `memory_subagent_resolve`.
- **Config**: `SelfEngineeringConfig` (all OFF/bounded).

### Notes

- Fully backward-compatible: new tables auto-create; with `self_engineering.enabled`
  off, behavior is identical to 0.10.0. No nmem-sym changes (stays 0.10.0).

## [0.10.0] — 2026-08-19

**Theme: nmem gains action — conscious skills, autonomous memorize/retrieve, and
tightened decay.** Until now nmem was reactive: the host explicitly stored and
explicitly searched. 0.10 adds a first-class **skills** layer ("a process that
worked / one that didn't") and an **autonomy** layer that decides *when* to
capture a skill and *when* to proactively surface relevant memory — all opt-in
and standalone-safe. Everything defaults OFF; an existing host sees no behavior
change until it flips a flag.

### Added

- **Conscious skills** (`mem.skills`, `nmem_skills` table): `record` / `find` /
  `reinforce` / `supersede` / `list`. Durable, vectorized (pgvector + HNSW on
  `trigger_embedding`), retrieved by situation with reliability ordering. Records
  mirror into nmem-sym's live `symbol_procedures` ledger when a cognitive backend
  is attached (Option-B ownership via `sym_procedure_id`, the commitments
  pattern), and work fully standalone otherwise. Reverse channel
  `record_skill_event` (myelinated / reinforced / retired / superseded) is
  idempotent with fully-sticky terminal states.
- **Autonomy layer** (`mem.autonomy`, `AutonomyManager`): on qualifying journal
  writes it can (a) capture a skill heuristically and (b) run a bounded proactive
  search and emit a single `memory.surfaced` event OFFERING relevant memory +
  skills to the host. All work is backgrounded off the write path (bounded
  semaphore + timeout), refetches the journal row by id, tags its searches
  `source="autonomy"` so a cognitive backend ignores them (no feedback storm),
  and is per-agent cooldown-limited. `mem.request_surface(query, agent_id)` is
  the reverse channel a backend's recall drive uses.
- **Skill decay + dedup** (consolidation full-cycle step, opt-in): stale
  low-trial skills are retired; near-duplicate skills are superseded into the
  strongest within a scope. `reinforce(success)` refreshes salience so skills in
  active use don't fade.
- **Skill surfacing** into `PromptBuilder` (`## Relevant Skills`) and
  `briefing()`, opt-in via `skills.include_in_prompt` / `include_in_briefing`.
- **MCP tools**: `memory_skill_record`, `memory_skill_find`,
  `memory_skill_reinforce`, `memory_autonomy_surface`.
- **Opt-in salience→ranking blend** (`search.salience_rank_weight`, default
  0.0): lets high-salience LTM rank higher. At 0.0 the ranking is byte-for-byte
  identical to before (explicit bypass) — salience stays a lifecycle signal by
  default.
- Config: `SkillsConfig`, `AutonomyConfig`, `search.salience_rank_weight`. All
  new flags default OFF.

### Notes

- Pairs with **nmem-sym 0.10.0**, which adds the skill backend methods
  (`register_skill` / `reinforce_skill` / `supersede_skill` / `abandon_skill`)
  and an opt-in **recall drive** that asks nmem to surface via `request_surface`.
- Fully backward-compatible: new tables auto-create; with all flags off and no
  backend attached, behavior is identical to 0.9.2.

## [0.9.2] — 2026-08-16

**Theme: PostgreSQL + pgvector is now the only supported backend — SQLite is
dropped.** nmem is a concurrent multi-writer system (background consolidation,
the cognitive-backend commitment flush, the obligation reverse channel, drive
ticks) and its core feature is vector search. SQLite fit neither: on an in-memory
shared connection, a background write could silently clobber a committed
transaction, and vector search was only ever a degraded in-Python numpy fallback
(no pgvector, no HNSW). Every recent release also paid a SQLite-only portability
tax (GREATEST shims, `ALTER TABLE` limits, asyncpg-skip guards, aiosqlite/JSON
greenlet workarounds). Removing it deletes that whole class of complexity and
false CI failures.

### Removed

- **SQLite backend.** `DatabaseManager` now raises `ValueError` on a non-postgres
  URL. Removed the JSON-text vector fallback, the in-Python cosine
  `_sqlite_fallback_search`, and every `is_postgres`/`is_sqlite` branch across the
  DB layer, search, links, policy, MCP, CLI, and API. The `nmem[sqlite]` extra is
  gone; `nmem init --sqlite` is removed.
- The CI SQLite unit-test matrix. The full suite now runs against PostgreSQL +
  pgvector across Python 3.11–3.13.

### Changed

- `asyncpg` and `pgvector` moved from the `postgres` extra into core
  dependencies (they are required). `nmem[postgres]` is kept as a no-op alias so
  existing install commands still resolve.
- Tests run against a shared Postgres DB with per-test truncation for isolation
  (previously each test got a fresh SQLite `:memory:`).

### Fixed

- **`memory_check_conflicts(since_days=…)` crashed on PostgreSQL.** The cutoff was
  built with a tz-aware `datetime.now(utc)` and compared against the naive
  `TIMESTAMP` `created_at` column, which asyncpg rejects. Now uses a naive
  cutoff. (Latent bug masked by SQLite; surfaced by the Postgres-only test run.)

## [0.9.0] — 2026-08-16

**Theme: nmem is the conscious interface to the cognitive system.** Until now a
host could wire nmem-sym's obligation subsystem directly, bypassing nmem's own
memory. This release makes nmem the single front door: the host imposes
_commitments_ on nmem, nmem records them durably, and forwards them to the
registered cognitive backend (nmem-sym) which owns the live obligation pressure.
Lifecycle and execution events flow back up through nmem, so the host subscribes
in one place — `mem.on('commitment.*')` — and never talks to the subconscious
layer directly. nmem stays standalone: with no backend attached, commitments are
still recorded and are mirrored automatically once a backend registers.

### Added

- **`mem.commitments` — the conscious record of external obligations.**
  `impose(requester, description, deadline=None, *, authority, importance,
  source, project_scope)` records a commitment (`nmem_commitments` table) and
  forwards it to the backend; `confirm` / `abandon` / `renegotiate` / `list`
  drive the lifecycle (each returns `False` for an unknown id rather than lying).
  Ownership is "Option B": nmem keeps the narrative record, nmem-sym owns the
  live ledger, linked by `sym_obligation_id`. The record stores everything the
  backend would need to re-impose.
- **Cognitive-backend registration (IoC).** `mem.register_cognitive_backend(backend)`
  lets nmem-sym plug itself in without nmem ever importing it. Commitments imposed
  before a backend attaches are queued (the DB is the queue) and mirrored on
  attach via an idempotent `flush_pending`; mirroring is serialized under a lock
  with a re-check so an inline impose and a concurrent flush can't double-mirror.
- **Reverse channel.** `mem.record_obligation_event(kind, sym_obligation_id, data)`
  lets the backend report transitions (`fulfilled` / `breached` / `missed` /
  `acting`); terminal kinds update the record's status, and every kind re-emits as
  a host-facing `commitment.<kind>` event. The obligation **execution hook** now
  routes through here too: when an obligation wins nmem-sym's meta-arbiter, the
  host runs the deliverable by subscribing on `mem.on('commitment.acting')`.
- **Commitment detection from content (opt-in).** A nightly consolidation step
  (`commitment_detection`, off by default) scans recent journal entries with an
  LLM and imposes any commitments it finds (`source='detected'`), the mirror of
  curiosity flowing the other way. Scope-isolated (a scoped instance scans only
  its own scope plus global) and deduped against commitments created in the
  lookback window in any status, so a resolved commitment is never re-imposed.
  Config: `NmemConfig.commitment_detection` (`enabled`, `lookback_hours`,
  `max_entries`, `min_confidence`, `default_authority`).

### Deprecated

- `nmem_scheduled_followups` (open-loop / prospective-memory triggers) is retired
  in favour of commitments + nmem-sym obligation pressure. The table is kept for
  schema compatibility with old data and will be dropped in a future migration.

## [0.8.2] — 2026-08-09

Test-only release: identical package behavior to 0.8.1, cut so the release CI
is green.

### Fixed

- **SQLite CI job.** The two `+asyncpg` `DatabaseManager` tests in
  `test_v0_7_0_a_mcp_prereqs.py` errored with `ModuleNotFoundError: asyncpg`
  because the SQLite job doesn't install the postgres driver, and constructing a
  `postgresql+asyncpg` engine eagerly imports the dialect. Guarded with
  `pytest.importorskip("asyncpg")` — they're specifically about asyncpg DSN
  handling. (Pre-existing since v0.7.0; first surfaced by the 0.8.1 push.)

## [0.8.1] — 2026-08-09

**Curiosity signals become an accumulating, consumable queue.** Supports
nmem-sym 0.8.0's per-problem "Concerns", which mirror these signals and report
resolutions back.

### Added

- **`CognitiveEngine.list_pending_curiosity(min_composite=, limit=)`** and
  **`resolve_curiosity(signal_id, outcome=, resolved_by=)`** — the read/resolve
  surface a consumer needs to act on the curiosity queue and close the loop
  (nmem-sym mirrors pending signals into concerns and calls `resolve_curiosity`
  once a drive has spent a targeted action on one).

### Fixed

- **Curiosity `recurrence_score` was dead and `composite_score` could only
  decay.** `emit_curiosity` now dedups pending signals by `(trigger_type,
  entity)` (or summary when no entity) and *reinforces* on a repeat: it bumps
  the previously-unwritten `recurrence_score`, takes the stronger of each
  component score, recomputes `composite_score` (which can now climb toward its
  ceiling), and refreshes staleness. Re-encountering the same problem sharpens a
  single signal instead of spawning duplicate rows that each only ever decayed.

## [0.8.0] — 2026-07-20

Governance-aware consolidation. Closes the echo-chamber failure mode
where nightly synthesis re-derived conclusions that active policy had
already superseded, re-entering them as fresh high-grounding shared
knowledge that belief revision could never catch.

### Added

- **Policy alignment sweep** (`PolicyAlignmentConfig`, on by default):
  a nightly consolidation step — also callable on demand via
  `consolidation.run_policy_alignment()` — that semantically matches
  validated shared/LTM rows against active policy-tier rules and asks
  the LLM which rows *contradict* a policy (acting on the row would
  violate it, or it asserts a state the policy superseded). Contradicting
  rows get `grounding='disputed'` (demoted in belief revision, search
  ranking, and importance scoring — never deleted) plus a `change_log`
  entry naming the policy. Already-disputed rows are skipped, so a
  stable corpus converges to zero LLM calls. One LLM call per policy;
  bounded by `max_policies_per_run` / `max_llm_calls_per_run`. Candidate
  similarity floor defaults to 0.35 — tuned against production, where
  contradicting rows scored 0.40-0.47 with MiniLM embeddings. New
  `ConsolidationStats` fields: `policy_disputed_shared`,
  `policy_disputed_ltm`.
- **Policy-aware nightly synthesis**: the synthesis prompt now includes
  active policy-tier rules as an authoritative context block, with an
  instruction that mined patterns must not contradict them (activity
  explained by a policy — e.g. a deliberate pause — is reported as
  expected, not as an anomaly to fix). High-importance journal entries
  (importance ≥ 9, e.g. operator directives) now contribute a content
  snippet to the synthesis context instead of title-only, so a single
  authoritative instruction is not outvoted by many lower-stakes titles
  on the same topic.

### Changed

- **Shared-knowledge grounding defaults are now `'inferred'`**
  (were `'confirmed'`) across all write paths: the ORM model default,
  `shared.save()`, the REST `SharedEntryCreate` schema, and (via the tier
  default) the `memory_store_shared` MCP tool. `'confirmed'` outranks
  `'inferred'` in belief revision, so a silent default let unvetted
  writes win conflicts against honestly-labeled knowledge — and let
  agent self-assertions enter as top-grounding facts. Writers that mean
  `'confirmed'` must now say so explicitly. Consolidator-written
  synthesis rows (`daily_synthesis_*`, `retrospective_*`) always set
  `grounding='inferred'` explicitly — they are inference over the
  journal, not observed fact. Existing rows are unaffected (no
  migration).

## [0.7.1] — 2026-07-12

### Added

- **`nmem.migrate`**: lightweight forward-only migration runner shared
  across nmem, nmem-sym, and future projects. Plain SQL files in numbered
  `migrations/` directories, tracked per-project in a `schema_migrations`
  table. Works with both asyncpg pools and SQLAlchemy async engines;
  idempotent, checksum drift detection, `mark_applied()` bootstrap path.
  No new dependencies.
- **`MemorySystem.get_entry_tier()`**: readback API returning the current
  tier of a journal entry as `('journal' | 'ltm' | 'shared' | 'archived',
  entry | None)`. Prerequisite for consumers (e.g. nmem-sym) that need to
  ask "where did this end up?" to make importance-aware decisions about
  their own derived state. Cheap (one or two indexed lookups) and
  defensive against inconsistent consolidator state.

### Fixes

- **Embedding provider double-init crash**: `SentenceTransformersProvider`
  now guards `torch.set_num_threads` / `set_num_interop_threads` with
  `try/except RuntimeError`. Constructing a second provider in a process
  that had already done parallel encoding previously raised and broke
  downstream callers. Safe because the caps are process-wide.

---

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
