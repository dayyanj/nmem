# Design: The Capability Model — actuators are surfaces, not a tool catalog

How the executive loop's "hands" scale without writing thousands of actuators, and how
safety survives once a general reasoner (an LLM with tool-calling, or a VM) is doing the
fine-grained work. Decision captured after a design debate; companion to
[executive-experiential-loop.md](executive-experiential-loop.md) and its
[build log](executive-experiential-loop-buildlog.md).

Status: **agreed** (build direction for the actuator layer). Owner steer: lean on the
LLM's native tool-calling (Qwen) and the VM rather than a per-tool catalog.

## The question

`nmem-act` gates and executes an action via a registry entry (`Action` = name +
capability class + handler). Taken literally, every capability (send email, post to
Slack, git commit, click a button…) would be its own hand-written actuator — thousands
of them — which is both unmaintainable and redundant with an LLM that already does
tool-calling and a VM that can do anything a user can.

## Decision: actuators are capability *surfaces* (a small, finite set)

An actuator is **a gated way to reach a class of effects**, not a single tool. The
breadth lives *inside* the surfaces. (DJ-AI already works this way — its action
registry is `delegate` / `code_fix` / `computer_use` / `communicate`: ~4 surfaces, not
thousands.) Three layers:

1. **Typed primitive actuators** — for *known, cheap, or high-stakes* paths you want
   deterministic and precisely gated: `http_get`, memory ops, and things like
   `deploy` / `git_commit`. You do **not** want an LLM improvising a production deploy;
   you want a typed actuator with an exact capability class and an approval gate. Small,
   finite set.
2. **General meta-actuators** — for the open-ended long tail:
   - `llm_tool_call` — hand the proposal to the LLM (Qwen); its tool-calling selects
     and invokes tools.
   - `computer_use` — drive the VM (the VM is the sandbox).
   - `delegate` / `sub_agent` — spawn an agent to accomplish a goal.
3. **The tools themselves** — sourced from the **MCP / function-schema ecosystem**, not
   hand-authored en masse. They are *adapted* into the registry so each carries a
   capability class; you don't write them.

So the count is a few primitives + a few surfaces + the MCP tools you already have —
bounded and mostly free.

## The reframe: the LLM is just another *proposer*

The brain (drives) proposes a high-level intent → the `llm_tool_call` surface hands it
to the LLM → **the LLM emits concrete tool-call proposals** → each flows back through
*the same nmem-act gate + registry + outcome path*. The LLM isn't outside the loop; it
is a second proposer feeding it. `http_get` is a cheap primitive, not the template for
everything — the LLM + registry cover the breadth.

## The crux: keeping the autonomy gate meaningful

A naive "let the LLM do anything" makes `read_only` meaningless. Reconciliation:

- **The autonomy level scopes the *envelope*.** Under `read_only`, the LLM is handed
  *only* read-only tools — it cannot call a mutating one because it isn't in its
  toolset. Under `tiered`: read-only + an allowlisted mutating set; high-risk calls hold
  for approval. Under `full`: everything + the VM.
- **The gate runs on the LLM's *actual* tool calls**, not the high-level proposal. Each
  call is checked against its tool's capability class *before* execution; a denied call
  is returned to the LLM ("not permitted at this level") so it re-plans.
- **The VM can't be finely gated** (a GUI can do anything), so `computer_use` is
  inherently `high_risk` → approval + `full` only, contained by VM snapshots/rollback +
  network egress policy — nmem-act gates the *surface*; the VM sandbox does containment.

Capability classes live on **tools / surfaces**; the autonomy level decides **which are
in scope**; general reasoners operate **within that envelope**.

## Where nmem-act's boundary sits

nmem-act stays the **gate + capability manifest + outcome/learning contract**. It does
**not** implement tools or embed an LLM (zero-dependency, install-agnostic). The
`llm_tool_call` and `computer_use` surfaces are **host** actuators (they need the host's
LLM client, MCP tools, VM) — the DJ-AI code we'd lift.

**No redesign required** — an `Action` handler can already *be* "run the LLM tool loop"
or "drive the VM." What to add:
- the **meta-actuator pattern** (host-side surfaces that delegate to the LLM / VM);
- **envelope scoping** by autonomy level (which tools/surfaces are exposed);
- an **MCP-tool → `Action` adapter** so ecosystem tools carry capability classes for
  gating;
- **composite-Episode** handling (a multi-tool LLM run is one episode with sub-steps,
  so attribution captures the whole sequence).

## Tradeoffs (why hybrid, not one or the other)

- **Typed primitives** buy determinism, exact gating, and clean attribution — worth it
  for high-stakes/known actions.
- **General surfaces** buy breadth for free but blur attribution (which of the LLM's
  tool calls caused the outcome?) and make the capability class only as tight as the
  envelope. Mitigation: gate per-call + record the whole tool sequence.
- **The VM** is the ultimate general actuator *and* the ultimate gating problem — rely
  on its sandbox for containment, not on nmem-act.

## Recommendation & next

Hybrid: a small set of typed primitives for known/high-stakes paths, plus
`llm_tool_call` (LLM) and `computer_use` (VM) as the general surfaces, capability
classes on tools, autonomy level scoping the envelope. **Don't build a tool catalog;
build the gated envelope and let the LLM + VM fill it.**

Next evidence step (proposed): prototype the `llm_tool_call` surface — Qwen tool-calling
→ per-call gate → composite Episode — as Evidence #3, which is more compelling than a
single mutating actuator and directly exercises this model.
