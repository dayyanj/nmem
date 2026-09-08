# Hivemind segregation — exploration (multi-agent on a shared DB)

**Status:** exploration only, no code changes (2026-09-08). Prompted by: *"agent-core writes
autonomous `drive_intent` goals into a shared `symbol_goals`, and each agent's pursuit loop
actuates them — so A could actuate B's goal. Principle: shared memory, agent-scoped agency."*

## Bottom line first

- **Today there is NO bug and NO shared hive.** Every agent has its **own physical database**
  (michelle → `michelle_ai` on :5433; DJ-AI → its own). Total isolation. The concern is real but
  **forward-looking** — it only bites if/when agents share one DB.
- The initial framing (scope the pursuit loop by owner) is **directionally right but understated.**
  It's not one query — `symbol_goals` is one of ~5 nmem-sym *agency* tables that are all unscoped.
- **Big asymmetry:** the **nmem** layer (conscious memory) is already hive-ready; the **nmem-sym**
  layer (symbol graph + cognition/agency) is almost entirely **single-agent-per-DB** by assumption.

## The asymmetry (the key finding)

**nmem (conscious memory) already implements "shared memory, agent-scoped agency" ✅**
- Private tiers — `working_memory`, `journal`, `long_term_memory`, `entity` — all carry an
  `agent_id` column + indexes; reads/writes are scoped to the author.
- `skills` and `context_recipes` use the exact hive pattern: `WHERE agent_id = :me OR agent_id IS NULL`
  (my own **or** shared). This is the reference pattern for everything below.
- `shared` + `policy` tiers are cross-agent **by design**.

**nmem-sym (symbol graph + cognition) is NOT scoped ✗**
- `symbol_nodes` / `symbol_edges` / `symbol_goals` / `symbol_concerns` / `symbol_pending_utterances`
  / `symbol_obligations` / `symbol_episodes` / `symbol_procedures` have **no `agent_id`/owner column.**
- The `SymbolGraph(domain=…)` param is **NOT a row scope** — it only merges per-domain promoted
  *edge-type vocabulary* (`symbol_canonical_edge_types`). Two agents with different domains in one DB
  still share every node, edge, and goal.
- `get_actionable_goals()` filters by `status` + `source_type` + `priority` only — **no owner.** In a
  shared DB, agent A's pursuit loop pulls agent B's `drive_intent` goals. (The flagged issue, confirmed.)
- **Exception:** `symbol_failures` already has an `agent_id` column — one table got it right.
- **Author-identity leak:** when nmem-sym writes extracted knowledge back into nmem it hardcodes
  `agent_id="nmem-sym"` (`api.py`, `bridge.py`) — in a shared DB every agent's extractions collide
  under one synthetic author.

## Segregation map (per data class, for a shared-DB hive)

| Data | Table(s) | Verdict in a hive |
|---|---|---|
| Private conscious memory | nmem working/journal/ltm/entity | ✅ already `agent_id`-scoped |
| Skills / recipes | nmem skills, context_recipes | ✅ already `= me OR NULL` (own-or-shared) |
| Shared knowledge | nmem `shared`, `policy` | ✅ cross-agent by design |
| Failure memory | `symbol_failures` | ✅ already has `agent_id` |
| **World-model / knowledge graph** | `symbol_nodes`, `symbol_edges`, hypotheses, predictions | ⚖️ **design choice** — shared collaborative world-model, or per-agent? Currently global-per-DB. |
| Edge-type vocabulary | `symbol_edge_type_proposals`, `_canonical_edge_types` | ✅ already `domain`-scoped |
| Compiled procedures | `symbol_procedures` | ⚖️ design choice — collective know-how vs per-agent |
| **Per-agent AGENCY** ⚠️ | `symbol_goals`, `symbol_concerns`, `symbol_pending_utterances`, `symbol_obligations`+`symbol_requestors`, `symbol_episodes` | ✗ **MUST be owner-scoped** — currently unscoped; this is where A acts on B |
| Author identity | nmem-sym → nmem writeback | ✗ hardcoded `agent_id="nmem-sym"` — should be the owning agent |
| In-memory / per-process | drive accumulator, recall `_BUF`, bridge instance | ✅ safe — each agent is its own OS process |

## Two hive models (this is the real decision)

**Model 1 — Physical isolation (today).** One DB (or schema) per agent. **Zero segregation work.**
"Hive" collaboration is achieved through the pieces that are *already* cross-agent and already built:
- the nmem **`shared` tier** (cross-agent canonical knowledge, already scoped),
- **nmem-exchange** peer messaging (`agent_core.peer`, just graduated),
- (future) shared nmem-viz.
Agents stay cognitively isolated (own graph, own goals, own drives) but *communicate* and *publish to
shared knowledge*. Simple, safe, and what michelle + DJ-AI do now.

**Model 2 — Shared substrate, scoped agency.** One DB/graph, many agents — a genuinely shared
world-model the agents collaboratively grow, with per-agent agency on top. This needs real work:
1. **Owner column** (`owner_agent`, nullable → NULL = shared) on the **agency** tables (goals,
   concerns, pending_utterances, obligations, requestors, episodes), and **every** query scoped:
   strict `= :me` for agency (goals/pursuit/concerns/drives), `= :me OR IS NULL` for anything
   shared-readable — mirroring nmem's skills/recipes pattern.
2. **A decision on the graph** (nodes/edges/procedures): keep shared (collaborative world-model; use
   `source_ids` → nmem entries for per-agent provenance) vs add an owner. Shared is the point of a
   hive; provenance already exists via `source_ids`.
3. **An agent-identity seam in nmem-sym**: the bridge/graph currently carry no `agent_id`. A hive needs
   `SymbolBridge(..., agent_id=…)` threaded into every agency query + the nmem writeback (fixing the
   hardcoded `"nmem-sym"` author).

## Recommendation (exploration verdict)

- **Default to Model 1.** It already delivers "shared memory (shared tier) + agent-scoped agency (own
  graph/goals)" with zero risk, and the collaboration primitives (peer exchange, shared tier) are done.
  A second agent joining as its own DB is safe **today**.
- **Only pursue Model 2** if there's a concrete need for a *single shared world-model* (agents reading/
  writing the same knowledge graph). If so, it's a proper design-doc effort: an `agent_id` seam in the
  bridge + `owner_agent` on the 5 agency tables + every agency query scoped + the writeback-author fix.
  It is **not** a one-line pursuit filter.
- **The pilot check the user named** ("A doesn't actuate B's goal") is the right *first* gate — but the
  map shows it generalizes: in Model 2 the same owner-scoping must cover concerns, pending utterances,
  obligations, and episodes, or those leak the same way. Validate the whole agency set, not just goals.

## Follow-ups (if Model 2 is chosen — not now)
- Design doc: `agent_id` seam through `SymbolBridge` + `owner_agent` migration for the 5 agency tables.
- Decide graph sharing (shared world-model + `source_ids` provenance is the likely answer).
- Fix the `agent_id="nmem-sym"` writeback to author as the owning agent.
- Add a hive integration test: two bridges, one DB, assert agent A's pursuit never claims B's goal and
  neither sees the other's concerns/pending/obligations/episodes.
