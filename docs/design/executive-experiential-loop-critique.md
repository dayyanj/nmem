# Critique: Why the Executive Experiential Loop Might Not Work

A deliberately adversarial assessment of what [the executive experiential loop](executive-experiential-loop.md)
actually delivers, written *after* the build (Slices A–C) so it reflects what shipped,
not what was planned. Companion to the [build log](executive-experiential-loop-buildlog.md).
Every claim of progress is treated as suspect until the evidence is examined. In the
spirit of [symbolic-cognition-critique.md](symbolic-cognition-critique.md).

## What was actually built

- **Slice A** (nmem-sym): honest outcome-gated discharge (A1), utility→procedure
  plasticity with a failure signal (A2), drive-state/intent surfacing (A3), a shared
  posterior over competing hypotheses (A4), drives→goals reconnected (A5).
- **Slice B** (nmem-act): a new zero-dependency package — action/outcome contract,
  tiered autonomy gate, reference runner.
- **Slice C**: the adapter + `symbol_episodes` + `record_action_outcome`, verified
  end-to-end against Postgres.

All of it is **opt-in and OFF by default.** That single fact frames every criticism
below.

## 1. "The loop closes" is true but load-bearing on the host

The end-to-end test proved a fired intent becomes a gated action becomes a persisted
episode. But read what actually ran it: a hand-written test harness that (a) started
the drive tick, (b) registered an actuator, (c) wired the adapter, and (d) pointed the
outcome sink at nmem-sym. **Remove the host and nothing happens.** The drive tick loop
(`bridge.tick_drives`) still only runs inside the MCP server with `DRIVES_ENABLED=1`;
the executor is external by design; the wiring is the host's job. So "the system
prompts itself to take actions" is only true *once a host runs the heartbeat and wires
four things together.* We closed the loop; we did not make it self-starting. Whether
that's correct (propose-only safety) or a dodge (the hard integration is punted to
DJ-AI) is a judgement call the benchmarks haven't settled.

## 2. The one falsifiable claim we tested is narrow

A2's benchmark passed convincingly (+0.478). But it is a *mechanism* benchmark: 40
synthetic situations, tie-similarity procedures, an epsilon-greedy bandit, a
Bernoulli success model. It proves the *plumbing* — reward EWMA + reward-ranked
retrieval — makes the system prefer procedures that achieve goals. It proves **nothing
about a real task.** No LLM, no real embeddings, no real procedures compiled from real
traces. The honest reading: "utility-weighting is wired correctly and behaves as
designed in a toy world." The claim the project is really making — that this improves
task success on real work — remains **untested.** Per the house rule, that claim is not
yet earned; it needs a run on nmem-bench with a real workload before anyone trusts it.

## 3. No real actuators ship — the hard part is deferred

`nmem-act` ships exactly one built-in action: a read-only `noop`. Every criticism of
"does it actually act?" lands here: the package defines *how to gate and route* an
action, not *how to safely perform* one. The genuinely hard problems of world-facing
autonomy — idempotency, rollback, rate limits, credential handling, partial failure,
side-effect attribution — are all on the host side of the contract, untouched. We
built a clean seam and a safe default; we did not demonstrate a single consequential
action executed and survived. The autonomy gate is only as good as the
`CapabilityClass` a host assigns each actuator, and nothing verifies those labels.

## 4. Episodes accumulate but nothing consolidates them

`symbol_episodes` is a first-class action→outcome record — the substrate for
experiential learning. But right now the *only* thing that reads an outcome is the
immediate A2 reward on the linked procedures. Nothing mines episodes for patterns, no
dreamstate step turns them into strategies or failure-memory, no self-model update
consumes them. So "learn from consequences" currently means "nudge one procedure's
EWMA." The richer promises from the original vision — strategy induction, structured
failure memory, a calibrated self-model, a world model — are **not built** (correctly
deferred, but absent). Episodes could pile up as write-only data if Slice D never comes.

## 5. Two reward paths, one risk of double-counting

A5 rewards a goal's procedures on achievement; C2 rewards an action's procedures on
outcome. If a host wires both and passes overlapping `procedure_ids`, a single success
reinforces twice. We documented "the host controls this," which is another way of
saying we moved the correctness burden to the integrator. A stricter design would
dedupe within a time/episode window.

## 6. The utility model is a guess

`UtilityVector.scalar()` uses hand-picked weights (task_success 1.0, risk 0.5, …). The
whole learning signal flows through those constants. We never validated them; a
different weighting could invert which actions look good. "A multi-dimensional reward"
sounds principled, but until the weights are tuned against outcomes it's an educated
guess with a decimal point.

## 7. The design surface was error-prone

Codex found roughly a dozen real defects across the build, including a **P1**: the
autonomy gate originally trusted the *proposal's* self-declared capability, so a
mislabelled proposal could slip a mutating action past a read-only gate. It was caught
and fixed — but a safety-critical gate that shipped wrong in its first draft is a
signal. The invariants (opt-in, byte-identical off, fail-open, propose-only) held, but
only because every slice was adversarially reviewed. That's a process strength and a
complexity warning at once. (One codex finding was itself wrong — a "bound method"
false positive on a `@property` — a reminder that the reviewer needs reviewing too.)

## 8. Off-by-default means unmeasured-in-production

Every flag defaults off, so DJ-AI (or any host) gets none of this until someone
deliberately enables five feature flags and writes an adapter. That's the right safety
posture, but it also means the loop has **zero production evidence.** Everything we
know comes from unit tests and one synthetic benchmark. The first time this meets real
traffic is the first time we'll learn whether outcome-gated discharge changes arbiter
dynamics in a bad way (e.g., a drive that can never satisfy itself thrashing on
cooldown), whether episode volume is manageable, whether utility-weighting starves
exploration.

## What is genuinely earned

To be fair to the build:
- The loop **does** close end-to-end, verified against a real database, not asserted.
- A2's mechanism **is** correct and benchmark-gated; the failure-signal fix (forced by
  the benchmark) is a real improvement over the naive design.
- The architecture's core advantage is **preserved**: everything is external,
  inspectable, reversible, opt-in. No neural weights changed; nothing is irreversible.
- `nmem-act` is a clean, zero-dependency, install-agnostic contract that a host can
  adopt incrementally, and its safety default (read-only, fail-closed approval,
  registry-authoritative capability) is conservative.

## Recommendation

The smallest next test that would move this from "plumbing verified" to "capability
demonstrated":

1. **Run A2's claim on a real nmem-bench workload** (real procedures, real embeddings,
   an LLM judge). If utility-weighting doesn't beat epistemic-only there, stop and
   reconsider before building on it.
2. **Ship one real read-only actuator set** (web search/fetch) and **one gated
   mutating actuator** behind the approval gate; run the loop on a single real scenario
   and inspect the episodes. Prove one consequential action end-to-end.
3. **Build the episode→consolidation path (Slice D)** — a dreamstate step over
   `symbol_episodes` producing failure-memory and/or strategies — or accept that
   episodes are currently write-mostly.
4. **Decide the heartbeat question**: either nmem-sym ships a supported standalone tick
   loop, or the docs make loud that "self-prompting" requires a host-run heartbeat.

Until (1) and (2) exist, the honest status is: **the executive loop is built,
wired, and safe, but its central benefit is unproven on real work.** That is a fine
place to be after three slices — provided the next move is evidence, not more
mechanism.
