# Wiring nmem-act into DJ-AI — integration plan

*Status: PLAN (no DJ-AI production code touched yet). Companion to
`capability-model.md`, `executive-experiential-loop.md`, and the Evidence #3 entry in
`executive-experiential-loop-buildlog.md`.*

Evidence #3 shipped the `llm_tool_call` **surface** in nmem-act (a `ToolCallingExecutor`
that lets an LLM drive a gated multi-tool loop, recorded as one composite Episode). This
plan wires that surface into DJ-AI — the primary host — using DJ-AI's **real** Qwen
tool-caller and its existing tool surface, gates, prediction, and learning. It is
deliberately scoped read-only-first and evidence-gated.

Mapping below is grounded in a three-agent read of the DJ-AI codebase
(`/mnt/nas_projects/apps/DJ-AI`); file:line refs are from that read.

---

## 1. What this actually buys DJ-AI (the honest value-prop)

DJ-AI already has outward actuators *and* a learning loop, so "add an execution path" is
**not** the win here. The specific gap this closes:

- **The autonomous deep cycle can't do fine-grained, multi-step tool reasoning.** It
  proposes ≤3 coarse actions per cycle (`investigate`, `delegate`, `code_fix`, …),
  executes them single-shot, and the results only feed the *next* cycle (event-driven /
  4h strategic fallback). It cannot "check status → read the runbook → decide → act"
  inside one decision.
- **DJ-AI's rich read-only tools are locked to human chat.** Surface B
  (`teams_chat_tools.py`: `query_targets`, `query_pipeline`, `query_agent_status`,
  `query_recent_actions`, `search_knowledge_base`, `query_db` [SELECT-only], …) is wired
  only to the Teams/voice/web conversation endpoint. **The autonomous executive has no
  access to them.**
- **Tool loops today are neither gated nor recorded.** DJ-AI's existing multi-step tool
  loop (`/api/converse` `gen()`, `server.py:663-718`) has no autonomy gate and writes no
  episode — it's a chat surface.

`llm_tool_call` gives DJ-AI's *autonomous* executive a **gated, recorded, bounded,
multi-step tool-reasoning capability over the read-only surface** (and, later, selected
mutating tools behind approval) — feeding the **same** learning loop (`record_skill` /
`reinforce_skill` + prediction grounding, so A2 utility-weighting applies to
tool-investigation strategies). And because the loop lives in the install-agnostic
nmem-act package, it realizes the capability-model vision concretely: the LLM is *another
proposer* into the same gated loop, reusable beyond DJ-AI.

**Non-goals:** we do NOT replace DJ-AI's gates, prediction, or recording; we do NOT
rebuild proposal parsing; we do NOT touch the human-chat `gen()` loop (a later, optional
unification). We reuse everything DJ-AI already has and add only the loop body + the
tool-surface bridge.

---

## 2. What DJ-AI already provides (reuse, don't rebuild)

| Concern | DJ-AI mechanism | file:line |
|---|---|---|
| LLM tool-calling primitive | `stream_chat_with_tools(messages, tools, tool_choice)` → yields `("content", …)` or `("tool_calls", [{id,name,arguments}])` | `local_llm_client.py:381-460` |
| Tool schemas (OpenAI fn format) | `TEAMS_CHAT_TOOL_DEFS` + `TOOL_EXECUTORS` name→coroutine map | `teams_chat_tools.py:81-210, 595-604` |
| Reference tool loop (analogue) | `/api/converse` `gen()` — call → collect tool_calls → execute → feed result back → re-ask, with a forced-terminal round | `server.py:663-718` |
| The one execution loop | `DeepCycle._execute` dispatch ladder (only loop that runs actuators) | `deep_cycle.py:457-612` |
| Gate A — founder approval | `_hold_actuator` (**fail-closed**; `_approved` bypass; scoped to `code_fix`/`update_drive_priorities`) | `deep_cycle.py:1005-1038`, `approval_gate.py` |
| Gate B — policy | `check_action_against_policies` (**fail-open**, LLM-reasoned over `operational_constraint` policies; all actuators) | `nmem_bridge.py:771-885` |
| Predict → ground | `predict_action_outcome` → `predict_causal(store=True)`; grounded overnight → `prediction_error` curiosity | `nmem_bridge.py:918-985, 1001-1070` |
| Outcome sink | `log_episode` (triple-write) + `record_skill`/`reinforce_skill` (LTP/LTD analogue) | `action_log.py:17`, `nmem_bridge.py:2241-2323` |
| Anti-spoof | strips leading-`_` keys from model output before dispatch | `deep_cycle.py:332-340` |

---

## 3. nmem-act ↔ DJ-AI mapping

| nmem-act concept | DJ-AI binding |
|---|---|
| `ToolSelector.next_call(goal, tools, history)` | thin adapter over `stream_chat_with_tools` (drain the generator → return `tool_calls[0]` or `Done(text)`); frame goal+history as messages exactly like `server.py:658-709` |
| `ActionRegistry` (the tools) | built from `TEAMS_CHAT_TOOL_DEFS` + `TOOL_EXECUTORS`, each tagged with a `CapabilityClass` |
| `CapabilityClass` | Surface-B read tools → `READ_ONLY`; `delegate`/`communicate` → `MUTATING`; `code_fix`/`update_drive_priorities` → `HIGH_RISK` (mirrors DJ-AI's epistemic/actuator + `actuator_approval` split) |
| inner `AutonomyGate` | scopes the **envelope** (which tools the selector sees) + re-gates each call; **read_only** for slice 1 |
| `ApprovalHook` | wraps `_hold_actuator` — high-risk sub-calls route to founder approval (fail-closed) |
| `OutcomeSink` | wraps `log_episode` + `record_skill`/`reinforce_skill` — one composite episode per run |
| `ToolCallingExecutor` | the body of a new `_handle_llm_tool_call` deep-cycle handler |

### Two-layer gating (this is the key design point)

nmem-act does **not** flatten DJ-AI's gates into one. Two layers, each authoritative in
its domain:

- **Outer (DJ-AI, per deep-cycle action):** add `"llm_tool_call"` to
  `_ACTUATOR_ACTION_TYPES` (`deep_cycle.py:374`) so the **policy gate** (Gate B) decides
  whether the executive may launch an LLM tool-loop *at all*, and (optionally) register
  it in `actuator_approval` so **founder approval** (Gate A) is required to grant the
  capability. This treats *delegating to an autonomous selector* as its own gated
  actuator — exactly the "delegation is its own privilege" property nmem-act enforces
  internally.
- **Inner (nmem-act, per tool call):** the `AutonomyGate` scopes the envelope and
  re-gates every sub-call (defense in depth). Slice 1 runs the inner gate at
  **`read_only`**, so the selector can *only* touch Surface-B read tools regardless of
  what it proposes. Later slices raise the inner level to `tiered` with an explicit
  allowlist (e.g. `delegate`) — and, per nmem-act's rule, that also requires
  `llm_tool_call` itself to be allowlisted, so a mutating tool is never handed to the
  selector implicitly.

Note the fail direction: DJ-AI's outer policy gate is fail-*open* (a decision aid that
must not block on infra errors); nmem-act's inner gate is fail-*closed* by construction
(`read_only` default). The inner is strictly stricter, so the composition is safe.

---

## 4. Where the adapter lives

- **nmem-act stays generic and clean** (it's slated to be public). No DJ-AI specifics,
  no vLLM/httpx, no OpenAI-schema opinions leak in.
- **DJ-AI gets a new `service/nmem_act_bridge.py`** holding: `DjaiToolSelector`
  (wraps `stream_chat_with_tools`), `build_tool_registry()` (from `TEAMS_CHAT_TOOL_DEFS`
  + `TOOL_EXECUTORS` + a capability table), and the `outcome_sink`/`approval` adapters
  over `log_episode`/`_hold_actuator`.
- **Deferred decision:** if a *second* host later needs the same OpenAI-tool-calling
  adapter, extract a generic `openai_tool_calling` helper into nmem-act then. YAGNI now
  — keep the public package pure.

---

## 5. Slices (evidence-gated, codex-reviewed per feature)

**Slice D1 — the bridge + selector (no DJ-AI execution changes).**
Build `DjaiToolSelector` and `build_tool_registry()` in DJ-AI's `nmem_act_bridge.py`.
Unit-test with a **mock vLLM** (no live model, no daemon): a scripted `stream_chat_with_tools`
returns tool_calls then content; assert the selector yields the right calls then `Done`,
and that the registry carries correct capability classes. This is fully testable in
isolation and touches **no** execution path. → codex review.

**Slice D2 — the handler (touches DJ-AI's deep cycle — confirm scope first).**
Add `_handle_llm_tool_call` to the dispatch ladder beside `_handle_computer_use`;
add `"llm_tool_call"` to `_ACTUATOR_ACTION_TYPES`; add the type + its fields to the
deep-cycle prompt enum (`prompts/deep_cycle_system.md`). Handler wiring:
`goal = action.get("instruction"|"task"|"reasoning")`; instantiate
`ToolCallingExecutor(registry, DjaiToolSelector(...), gate=AutonomyGate(READ_ONLY),
approval=_hold_actuator_adapter, outcome_sink=log_episode+record_skill,
max_steps=~6)`; record the composite episode. → codex review.

**Slice D3 — evidence (real deep cycle, real Qwen, real DB).**
Trigger a deep cycle with a goal needing multi-step read investigation (e.g. "assess
pipeline health": `query_pipeline` → `query_agent_status` → `search_knowledge_base` →
conclude). Verify: bounded multi-step run, **one composite episode** persisted, a skill
recorded/reinforced, prediction stored+grounded. This is the real-agent evidence the
executive-loop critique asked for (utility-weighted learning on real work, not a
mechanism benchmark).

**Later / deferred:** raise inner autonomy to `tiered` with an allowlisted *mutating*
tool (e.g. `delegate`) behind founder approval (the gated-mutating-actuator evidence);
optionally unify the human-chat `gen()` loop onto the same executor (gating + recording
for free); the ARC-AGI-3 big-swing eval.

---

## 6. Honest caveats / risks

1. **New control-flow shape.** The deep cycle is plan-then-execute; `llm_tool_call`
   nests an iterative loop inside one action. Mitigated by nmem-act's hard `max_steps`
   + incomplete-on-cap semantics (a truncated run is FAILURE, no false credit).
2. **Latency/cost.** Each run makes N Qwen calls (one per step). Acceptable at
   deep-cycle cadence (≤3 actions, event/4h), but set `max_steps` conservatively and
   consider the 8B backend for selection to keep it cheap.
3. **Coarse prediction.** `predict_action_outcome` predicts a *single* action; for a
   composite loop the prediction is necessarily coarse ("run a tool investigation for
   X"). Fine as a canary; don't over-read the grounding signal for these.
4. **Coarse skill capture.** One `record_skill(what=goal, worked=success)` per run; the
   per-tool detail lives in the episode trace but isn't separately reinforced. Adequate
   for v1; per-tool credit assignment is a later refinement.
5. **Two tool-loops over one model.** `gen()` (chat) and `ToolCallingExecutor`
   (autonomous) coexist. Intentional for now (different needs: chat has no
   gating/recording). Unify later if worthwhile.

---

## 7. Open decisions for the owner

1. **First-slice scope** — recommend **read-only-first** (slice 1 inner gate = read_only,
   Surface-B tools only). Immediately useful, lowest blast radius, and it's the honest
   evidence path. (Alternative: go straight to tiered mutating — more risk, less clean
   evidence.)
2. **Build D1 now?** D1 (bridge + selector + mock-vLLM tests) touches **no** DJ-AI
   execution path and is safe to build immediately; D2 touches the production daemon and
   should wait for explicit scope sign-off.
3. **Adapter location** — recommend DJ-AI-local `nmem_act_bridge.py` (keep public
   nmem-act pure); revisit a generic OpenAI adapter only if a second host needs it.
