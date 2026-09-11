# Adopting nmem 0.10 – 0.11: skills, autonomy, self-engineering

This guide is for **host applications that embed nmem** that are
already on **0.9.x** and want to turn on the capabilities added in **0.10.0** and
**0.11.0**. It covers what each capability is, the config to enable it, the Python
API, the MCP tools, and the events to subscribe to.

**The one thing to know up front:** every new capability is **opt-in and OFF by
default**. If you upgrade the package and change nothing, behavior is identical to
0.9.x. Nothing here activates until you flip a flag — so the host gets none of it
"for free"; adoption is deliberate.

There are no breaking changes in 0.10/0.11. (The last breaking change was the
0.9.2 Postgres-only move — see [upgrading-0.9.md](upgrading-0.9.md).)

---

## TL;DR — the three new capabilities

| Capability | Version | What it does | Turn on with |
|------------|---------|--------------|--------------|
| **Skills** | 0.10 | Durable, vectorized record of "a process that worked / one that didn't", retrievable by situation | `skills.enabled` |
| **Autonomy** | 0.10 | nmem decides on its own *when* to capture a skill and *when* to proactively surface relevant memory | `autonomy.enabled` |
| **Self-engineering** | 0.11 | nmem distills reliable skills into **context recipes** it injects into its own prompt, and **proposes sub-agent specs** the host runs | `self_engineering.enabled` |

Config is nested under `NmemConfig`. In code: `NmemConfig(skills={"enabled": True}, …)`.
Via environment (for the MCP server): `NMEM_SKILLS__ENABLED=1`,
`NMEM_AUTONOMY__ENABLED=1`, `NMEM_SELF_ENGINEERING__ENABLED=1` (prefix `NMEM_`,
nested delimiter `__`).

> **Recommended rollout:** enable **skills** first (lowest risk, immediately
> useful), then **autonomy** (proactive surfacing), and only then **self-engineering**
> (it makes autonomous LLM calls — see §3). Each is independent; you can stop at any tier.

---

## 1. Skills (0.10)

A **skill** is a durable, embedded record of an approach that worked (or didn't),
retrievable by the *situation* it applies to. Skills reinforce over repeated use
(success/trial counts), decay when stale, and can be superseded by better ones.
They work standalone; when nmem-sym is attached they also mirror into its live
plasticity-scored procedure ledger.

### Enable

```python
config = NmemConfig(
    database_url="postgresql+asyncpg://…",
    skills={
        "enabled": True,
        "include_in_prompt": True,      # optional: auto-inject matching skills into context
        "include_in_briefing": True,    # optional: include skills in mem.briefing()
        # decay/dedup ride the consolidation cycle; opt in if you want them:
        "decay_enabled": True,
        "dedup_enabled": True,
    },
)
```

### Python API (`mem.skills`)

```python
info = await mem.skills.record(
    "roll out blue-green behind a feature flag",   # `what` = process + retrieval trigger
    outcome="zero downtime", worked=True, name="blue-green", agent_id="dj")
# → SkillInfo(id, name, what, outcome, worked, success_count, trial_count, status, …)

hits = await mem.skills.find("deploying with zero downtime", agent_id="dj")   # ranked by relevance + reliability
await mem.skills.reinforce(info.id, success=True)      # LTP on success / LTD on failure
await mem.skills.supersede(old_id, new_id)             # forward-pointer supersede
open_ = await mem.skills.list("active")
```

`record()` coalesces near-duplicates (reinforces the existing skill instead of
duplicating). `find()` is scoped to the instance's `project_scope` (pass
`project_scope="*"` for cross-scope). All calls are inert no-ops if `skills.enabled`
is False.

### MCP tools

`memory_skill_record(what, outcome="", worked=True, name=None, agent_id="default")`,
`memory_skill_find(query, limit=3, agent_id="default")`,
`memory_skill_reinforce(skill_id, success)`. Each returns a friendly "skills disabled"
message when off, so registering them is harmless.

### Events

`skill.recorded`, `skill.reinforced`, `skill.superseded` — and, when nmem-sym reports
plasticity transitions, `skill.myelinated` / `skill.retired` (the `skill.*` family).

### Context injection

With `skills.include_in_prompt`, `mem.prompt.build(agent_id, query=…)` adds a
`## Relevant Skills` section (near-relevant skills with reliability). With
`include_in_briefing`, `mem.briefing(agent_id, query=…)` adds a `### Relevant Skills`
block. Both are query-relevant and bounded; with the flags off, prompt/briefing output
is byte-for-byte unchanged.

---

## 2. Autonomy (0.10)

nmem watches its own event stream and, on qualifying journal writes, can **capture a
skill** and/or **proactively surface** relevant memory + skills — *offering* them to
the host via a single event. nmem never forces injection; it only offers.

All autonomy work runs **off the write path** (backgrounded), is per-agent
cooldown-limited, and tags its own searches so it can't feed back into a storm.

### Enable

```python
config = NmemConfig(
    database_url="postgresql+asyncpg://…",
    skills={"enabled": True},           # capture writes skills, so enable skills too
    autonomy={
        "enabled": True,
        "auto_capture_skills": True,    # capture skills from qualifying journal entries
        "skill_entry_types": ["decision", "outcome", "retro", "lesson"],
        "proactive_retrieve": True,     # emit memory.surfaced on qualifying writes
        "surface_recognition_threshold": 0.5,   # how "known" a hit must be to surface
        "novelty_threshold": 0.6,       # only surface for genuinely new triggers
        "cooldown_seconds": 120,        # per-agent min interval between surfaces
        "surface_top_k": 5,
    },
)
```

### The event the host subscribes to

```python
@mem.on("memory.surfaced")
async def _on_surfaced(data):
    # data = {agent_id, trigger, source, reason, results:[…], skills:[…]}
    # results: [{tier, id, title, content, recognition, recognition_score}, …]
    # skills:  [{id, name, what, outcome, worked, success_count, trial_count}, …]
    dj.offer_context(data["results"], data["skills"])   # the host decides whether to use it
```

nmem *offers*; the host decides. This is the whole contract — nothing is injected behind
your back.

### On-demand surfacing

```python
offered = await mem.request_surface("how did we handle the last outage?", agent_id="dj")
# returns True iff something was offered (also emits memory.surfaced)
```

### MCP

`memory_autonomy_surface(query, agent_id="default")` triggers the same proactive
surface on demand.

---

## 3. Self-engineering (0.11)

This is the tier that makes nmem **call the LLM autonomously** (single-turn) during
nightly consolidation to turn proven experience into reusable artifacts. Two halves:

- **Context recipes** — advisory prompt fragments distilled from reliable skills,
  injected into nmem's own context as `## Learned Guidance (advisory)` (placed last,
  explicitly subordinate to policy and direct memory).
- **Sub-agent proposals** — rich, **propose-only** sub-agent specs. nmem never runs
  them; the host inspects proposals and instantiates the ones it wants.

> **Cost & safety.** Self-engineering runs on the nightly consolidation cycle, so the host
> must have consolidation running (`mem.start_consolidation()`, or the MCP server with
> `NMEM_START_CONSOLIDATION` set). Each run is bounded by a hard `max_llm_calls_per_run`
> **and** per-prompt input-size caps. Recipes are auto-active but tagged, near-exact-match
> gated, decay when stale, and any recipe is one `disable()` away from gone (which also
> **suppresses re-distillation** of that cluster). A non-noop LLM provider must be
> configured (`llm.provider`) or this tier is a clean no-op.

### Enable

```python
config = NmemConfig(
    database_url="postgresql+asyncpg://…",
    llm={"provider": "anthropic", "model": "claude-…"},   # required — noop → no-op
    skills={"enabled": True},
    self_engineering={
        "enabled": True,
        "include_in_prompt": True,      # inject matching recipes as advisory guidance
        "max_llm_calls_per_run": 3,     # hard cap per nightly run
        "min_reliability": 0.7,         # skill success/trial bar to seed a recipe
        "min_trials": 3,
        "min_match_similarity": 0.6,    # near-exact gate for injecting a recipe
        # sub-agent proposals (2B):
        "propose_subagents": True,
        "subagent_min_reliability": 0.8,
    },
)
```

### Context recipes — Python API (`mem.self_engineering`)

Distillation happens automatically on the nightly cycle. the host's job is to **inspect
and veto**:

```python
recipes = await mem.self_engineering.list("active")     # inspect what's live
await mem.self_engineering.disable(recipe_id, reason="too broad")   # veto + tombstone the cluster
```

`disable()` both removes the recipe from injection and stops nightly consolidation from
re-distilling that source cluster (a cooldown that grows on repeated disables).

With `include_in_prompt`, matching recipes appear in `mem.prompt.build(...)` as an
advisory block; off ⇒ prompt output unchanged.

### Sub-agent proposals — Python API

```python
proposals = await mem.self_engineering.list_proposals("proposed")
# each: {id, name, system_prompt, trigger_conditions, context_recipe,
#        suggested_tools, source_skill_ids, reliability_evidence, status}

# the host instantiates/runs the ones it wants, then records feedback:
await mem.self_engineering.resolve_proposal(proposal_id, accepted=True)   # or False to dismiss
```

**nmem never executes a proposal** — it's inert data. the host owns instantiation and
execution. The `context_recipe` is a snapshot (copied in), so an accepted proposal stays
valid even if the source recipe is later disabled.

### MCP tools

`memory_recipe_list(status="active")`, `memory_recipe_disable(recipe_id, reason="")`,
`memory_subagent_proposals(status="proposed")`, `memory_subagent_resolve(proposal_id, accepted)`.

### Events

`recipe.distilled` (a recipe went live), `recipe.rejected` (failed the acceptance gate),
`recipe.disabled`; `subagent.proposed` (a spec is ready for the host), `subagent.rejected`.

```python
@mem.on("subagent.proposed")
async def _on_proposal(data):        # {id, name, source_skill_ids, project_scope}
    proposals = await mem.self_engineering.list_proposals("proposed")   # full rich specs
    spec = next(p for p in proposals if p["id"] == data["id"])
    dj.review_subagent_spec(spec)
```

---

## 4. Events reference (new in 0.10–0.11)

| Event | When | Payload |
|-------|------|---------|
| `skill.recorded` | a skill is recorded | `{id, name, worked, agent_id}` |
| `skill.reinforced` | a skill is reinforced | `{id, success}` |
| `skill.superseded` | a skill superseded by another | `{id, superseded_by}` |
| `skill.*` (`myelinated`/`retired`/…) | nmem-sym reports a plasticity transition | `{id, sym_procedure_id, …}` |
| `memory.surfaced` | autonomy offers relevant memory + skills | `{agent_id, trigger, source, reason, results, skills}` |
| `recipe.distilled` | a context recipe went active | `{id, name, source_skill_ids, project_scope}` |
| `recipe.rejected` | a distilled recipe failed the gate | `{source_skill_ids, reason}` |
| `recipe.disabled` | a recipe was vetoed | `{id, reason}` |
| `subagent.proposed` | a sub-agent spec is ready | `{id, name, source_skill_ids, project_scope}` |
| `subagent.rejected` | a proposed spec failed the gate | `{source_skill_ids, reason}` |

Subscribe with `@mem.on("<event>")`. Handlers may be sync or async; exceptions in a
handler are swallowed and never break a write.

## 5. New MCP tools

`memory_skill_record`, `memory_skill_find`, `memory_skill_reinforce`,
`memory_autonomy_surface`, `memory_recipe_list`, `memory_recipe_disable`,
`memory_subagent_proposals`, `memory_subagent_resolve`. All registered unconditionally;
each returns a "disabled" message when its feature's config flag is off. Enable via the
`NMEM_*__ENABLED` env vars on the MCP server process.

---

## 6. the host adoption checklist

1. **Upgrade** to nmem 0.11.0 (and nmem-sym 0.10.0 if you use the cognitive backend).
   No code changes required to keep 0.9.x behavior.
2. **Skills first.** Set `skills.enabled=True`. Have the host call
   `mem.skills.record(...)` at natural "that worked / that didn't" moments, and
   `mem.skills.find(...)` (or `include_in_prompt`) when starting a task. Call
   `reinforce()` on re-use.
3. **Autonomy next.** Set `autonomy.enabled` (+ `auto_capture_skills`,
   `proactive_retrieve`) and subscribe to `memory.surfaced`. the host decides whether to
   inject what nmem offers.
4. **Self-engineering last.** Configure a real `llm.provider`, ensure consolidation is
   running, set `self_engineering.enabled` (+ `include_in_prompt`, and
   `propose_subagents` if you want specs). Subscribe to `subagent.proposed`; wire an
   admin surface to `list()`/`disable()` recipes and `list_proposals()`/`resolve_proposal()`.
5. **Watch the cost.** Self-engineering LLM spend shows up in `mem.stats` /
   `query_token_summary` under the `context_recipe` and `subagent_proposal` operations.
   Tune `max_llm_calls_per_run` and the input-size caps.

## Reference

- nmem `[0.10.0]` (skills + autonomy) and `[0.11.0]` (self-engineering) —
  [../CHANGELOG.md](../CHANGELOG.md)
- Config surface — [configuration.md](configuration.md), and the `SkillsConfig` /
  `AutonomyConfig` / `SelfEngineeringConfig` classes in
  [../src/nmem/config.py](../src/nmem/config.py)
- 0.9.x host changes (SQLite drop + commitments) — [upgrading-0.9.md](upgrading-0.9.md)
