"""Persona — the per-agent DATA an agent supplies, and the generic loader that seeds it.

An agent's *identity* is data, not code: its standing objectives, its initial
knowledge-seeking mandate, and the durable baseline facts it should start life
knowing. `Persona` holds that data; `seed_persona` plants it (idempotently) into the
agent's nmem shared tier + nmem-sym goal queue. Both graduated from michelle-ai's
`service/identity.py` (the seed_goals / seed_world_goals / seed_baseline_kb logic) so
a new agent supplies a `Persona` value + its prompt files, and writes no seed code.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class Persona:
    """Everything agent-specific about who an agent is, as data.

    Fields map to how each is seeded:
      * ``objectives``       -> standing ``external`` goals (nmem-sym symbol_goals)
      * ``world_seed_topics``-> actionable ``drive_intent`` goals (pursued immediately)
      * ``baseline_kb``      -> durable facts in the nmem SHARED tier (grounding='confirmed')
      * ``capabilities``     -> a shared-tier self-description (what this agent can do)
    """

    agent_id: str
    objectives: list[tuple[str, str]] = field(default_factory=list)          # (label, statement)
    goal_priorities: dict[str, float] = field(default_factory=dict)          # label -> priority
    world_seed_topics: list[tuple[str, str, float]] = field(default_factory=list)  # (label, objective, priority)
    baseline_kb: dict[str, tuple[str, str]] = field(default_factory=dict)    # key -> (content, category)
    capabilities: str = ""
    capabilities_key: str = ""
    world_entities: str = ""   # display names for goal-enrichment grounding (falls back to topic labels)


async def seed_persona(mem, graph, persona: Persona) -> None:
    """Idempotently seed a persona's objectives, world mandate, and baseline KB.

    Safe to call every boot: objectives + world topics dedup by objective text, and
    baseline-KB writes are upserts by key. Fail-open per section — a seed hiccup must
    not abort startup."""
    await _seed_objectives(graph, persona)
    await _seed_world_goals(graph, persona)
    await _seed_baseline_kb(mem, persona)


async def _seed_objectives(graph, persona: Persona) -> None:
    """Standing objectives -> `external` goals (deduped by text)."""
    if graph is None or not persona.objectives:
        return
    try:
        from nmem_sym.goals import create_goal
        pool, embedder = graph.pool, graph._embedder
        seeded = 0
        for label, text in persona.objectives:
            exists = await pool.fetchval(
                "SELECT 1 FROM symbol_goals WHERE source_type='external' AND objective=$1 LIMIT 1",
                text)
            if not exists:
                await create_goal(pool, embedder, text,
                                  priority=persona.goal_priorities.get(label, 0.5),
                                  source_type="external")
                seeded += 1
        log.info("seed_persona: %d new objective(s) (of %d)", seeded, len(persona.objectives))
    except Exception as e:  # noqa: BLE001
        log.warning("seed_persona objectives failed: %s", e)


async def _seed_world_goals(graph, persona: Persona) -> None:
    """Knowledge-seeking mandate -> `drive_intent` goals the pursuit loop actuates."""
    if graph is None or not persona.world_seed_topics:
        return
    try:
        from nmem_sym.goals import create_goal
        pool, embedder = graph.pool, graph._embedder
        seeded = 0
        for _label, text, priority in persona.world_seed_topics:
            exists = await pool.fetchval(
                "SELECT 1 FROM symbol_goals WHERE objective=$1 LIMIT 1", text)
            if not exists:
                await create_goal(pool, embedder, text, priority=priority,
                                  source_type="drive_intent")
                seeded += 1
        log.info("seed_persona: %d new world-seed goal(s) (of %d)", seeded, len(persona.world_seed_topics))
    except Exception as e:  # noqa: BLE001
        log.warning("seed_persona world goals failed: %s", e)


async def _seed_baseline_kb(mem, persona: Persona) -> None:
    """Durable baseline facts + a capabilities self-description -> nmem SHARED tier."""
    if mem is None or not (persona.baseline_kb or persona.capabilities):
        return
    try:
        for key, (content, category) in persona.baseline_kb.items():
            await mem.shared.save(key, content, category, persona.agent_id,
                                  importance=9, record_type="fact", grounding="confirmed")
        if persona.capabilities and persona.capabilities_key:
            await mem.shared.save(persona.capabilities_key, persona.capabilities, "fact",
                                  persona.agent_id, importance=9, record_type="fact",
                                  grounding="confirmed")
        log.info("seed_persona: %d baseline fact(s)%s", len(persona.baseline_kb),
                 " + capabilities" if persona.capabilities else "")
    except Exception as e:  # noqa: BLE001
        log.warning("seed_persona baseline KB failed: %s", e)
