# Design: Pressure-Driven Heartbeat

How the drive system should decide *when* to act. Today it polls
(`bridge.tick_drives(dt)` on a fixed ~1s timer inside the MCP server). This note
proposes replacing the fixed cadence with a **need-driven wake**: the system acts
when accumulated pressure (curiosity / anxiety / any drive) crosses threshold, and
otherwise sleeps. Deferred build — captured now so the decision is on record.

Status: **proposed** (build after the current evidence step). Owner decision: the
trigger should be the brain's drivers, not a clock.

## The problem with a fixed tick

A 1-second poll is arbitrary and wasteful: it runs the arbiter constantly whether or
not anything changed, and it couples "how responsive is the system" to "how often do
we burn a cycle." The *right* trigger is semantic — **enough pressure has
accumulated** — not temporal.

## Why "pure event-driven" isn't enough (the challenge)

The tempting fix is "run the arbiter only when an event raises pressure." Pressure is
already event-driven (`drive_emit` → `on_event`), so this is half-right. But two
things are genuinely time-based, and dropping the time base breaks them:

1. **Decay.** Pressure must bleed off, or a drive that can't be satisfied (its action
   keeps failing, or it keeps losing arbitration) stays pinned and re-fires on every
   subsequent event — a storm. Decay is the stabilizer and the "neglected need fades"
   semantics.
2. **Temporal awareness is itself a driver.** "It's been quiet for an hour",
   "this prediction is overdue", "this knowledge is stale" can only be noticed against
   a clock — and *silence is a curiosity trigger*: the quiet is exactly what should
   make the system restless. With no clock, nothing wakes to notice the quiet.

## The synthesis: wake on pressure, compute time lazily

Keep the intuition (act when pressure warrants) and preserve the dynamics:

1. **Event wake.** When an event raises a drive such that it *would* be ready
   (`peek_ready()` non-empty), evaluate the arbiter immediately — no waiting for a
   fixed tick.
2. **Lazy, compute-on-read decay.** Decay is closed-form: `effective(now) = pressure −
   decay_rate · (now − last_updated)` (clamped ≥ 0), plus the passive-drift term
   integrated over the same interval. Compute it whenever pressure is *read* (arbiter
   eval, state inspection). **No polling needed to decay.**
3. **A single self-rescheduling timer.** Instead of a fixed interval, schedule one
   wake-up at the *next* moment something will happen, `next_deadline = min(`
   - for each drive: the time it will cross threshold given current pressure + passive
     drift − decay (closed form; ∞ if it's decaying below threshold and no drift),
   - the next temporal-awareness deadline (silence window, staleness, overdue
     prediction),
   - the soonest cooldown expiry among over-threshold-but-cooling drives
   `)`. Sleep until `next_deadline` **or** until an event wakes us earlier; on wake,
   recompute effective pressures, run the arbiter, then recompute `next_deadline`.

The result is fully need-driven (no arbitrary cadence), sleeps when idle, and keeps
decay + temporal awareness intact. It's a timer *wheel* (next-event), not a poll.

## What changes in code

- `Drive`: add `last_updated`; make `effective_pressure(now)` the single source of
  truth (folds decay + passive drift), so `tick()`'s per-step accumulate/decay is no
  longer the only path pressure moves. `accumulate()` stamps `last_updated`.
- `DriveAccumulator`: a `next_deadline()` that returns the earliest wake time across
  drives + temporal deadlines + cooldowns (all closed-form). Keep `tick(dt)` for
  backwards compatibility (and tests), implemented in terms of the lazy math.
- The driver loop (`mcp_integration._drive_tick_loop`) becomes
  `wait_or_event(next_deadline)` instead of `sleep(interval)`: `await
  asyncio.wait_for(pressure_event.wait(), timeout=until_next_deadline)`, where
  `pressure_event` is set by `feed_event`/`inject_pressure` when they push a drive
  over threshold. On wake → `tick_drives()` (now cheap: lazy math + arbiter) →
  reschedule.
- Executive loop: an outward intent then fires only when pressure genuinely warrants
  it, not on a clock — which is the behaviour we actually want.

## Compatibility & safety

- **Opt-in.** Gate behind `NMEM_SYM_DRIVES_WAKE_MODE=event` (default `timer` =
  current). Off → byte-identical fixed tick. This lets the two run side by side and
  be A/B'd.
- **Correctness base.** The lazy `effective_pressure(now)` must match the summed
  fixed-tick decay to within float tolerance — a regression test asserts equivalence
  over a random event schedule (inject a fake clock; no real sleeping).
- **No missed wakes.** Every `feed_event`/`inject_pressure` that raises a drive
  toward threshold must set the wake event *and* invalidate the cached
  `next_deadline`. A missed set = a late action, not a wrong one (the next event or
  deadline still catches it), but the test suite should cover "event beats timer" and
  "timer fires with no events."

## Risks

- **Scheduling bugs are subtle** (a mis-computed `next_deadline` = the system naps
  through a need). Mitigation: keep a generous *maximum* sleep (e.g. cap
  `next_deadline` at a few minutes) so a scheduling error degrades to slow, never
  never-wakes; and the equivalence test above.
- **Thundering herd** (many drives cross at once): the winner-take-all arbiter already
  handles this — one intent per wake.
- **Clock source**: use a monotonic clock (as the drives already do for cooldown).

## Why this fits the architecture

It keeps the drive system's homeostatic character (accumulate → arbitrate → discharge
→ decay) while making *acting* a consequence of *need*, not of a timer — the same
propose-only, inspectable, opt-in discipline as the rest of the loop. It also removes
the "the loop only runs under the MCP server's 1s poll" caveat from the critique: a
host can drive it by feeding events and honouring the wake schedule, with no fixed
cadence.
