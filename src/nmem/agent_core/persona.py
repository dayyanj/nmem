"""Persona — the per-agent DATA an agent supplies, and the generic loader that seeds it.

An agent's *identity* is data, not code: its standing objectives, its initial
knowledge-seeking mandate, and the durable baseline facts it should start life
knowing. `Persona` holds that data; `seed_persona` plants it (idempotently) into the
agent's nmem shared tier + nmem-sym goal queue. Both graduated from the reference agent's
`service/identity.py` (the seed_goals / seed_world_goals / seed_baseline_kb logic) so
a new agent supplies a `Persona` value + its prompt files, and writes no seed code.
"""
from __future__ import annotations

import logging
import os
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

    @classmethod
    def from_dict(cls, d: dict) -> "Persona":
        """Build a Persona from plain data (a studio-written persona.yaml / json). Lenient:
        objectives accept [label, statement] pairs OR bare statement strings; world_seed_topics
        accept [label, objective, priority]; baseline_kb maps key -> [content, category]."""
        d = d or {}

        def _pairs(v):
            out = []
            for i, item in enumerate(v or []):
                if isinstance(item, (list, tuple)):
                    out.append((str(item[0]), str(item[1])))
                else:  # bare statement -> synthesize a label
                    out.append((f"obj{i+1}", str(item)))
            return out

        def _topics(v):
            out = []
            for item in v or []:
                if isinstance(item, (list, tuple)):
                    lbl, obj = str(item[0]), str(item[1])
                    pri = float(item[2]) if len(item) > 2 else 0.8
                    out.append((lbl, obj, pri))
            return out

        kb = {}
        for k, val in (d.get("baseline_kb") or {}).items():
            if isinstance(val, (list, tuple)):
                kb[k] = (str(val[0]), str(val[1]) if len(val) > 1 else "fact")
            else:
                kb[k] = (str(val), "fact")

        return cls(
            agent_id=d["agent_id"],
            objectives=_pairs(d.get("objectives")),
            goal_priorities={str(k): float(v) for k, v in (d.get("goal_priorities") or {}).items()},
            world_seed_topics=_topics(d.get("world_seed_topics")),
            baseline_kb=kb,
            capabilities=d.get("capabilities", ""),
            capabilities_key=d.get("capabilities_key", ""),
            world_entities=d.get("world_entities", ""),
        )

    @classmethod
    def from_yaml(cls, path: str) -> "Persona":
        """Load a Persona from a persona.yaml on disk — the dockerized-appliance pattern: a host
        ships config/persona.yaml + prompt files and writes no seed/loader code. Thin wrapper over
        from_dict (yaml lazy-imported, as elsewhere in agent_core)."""
        import yaml
        with open(path) as f:
            return cls.from_dict(yaml.safe_load(f) or {})

    def to_dict(self) -> dict:
        """Serialise to plain data (for a studio-written persona.yaml). Round-trips with from_dict."""
        return {
            "agent_id": self.agent_id,
            "objectives": [[l, s] for l, s in self.objectives],
            "goal_priorities": dict(self.goal_priorities),
            "world_seed_topics": [[l, o, p] for l, o, p in self.world_seed_topics],
            "baseline_kb": {k: [c, cat] for k, (c, cat) in self.baseline_kb.items()},
            "capabilities": self.capabilities,
            "capabilities_key": self.capabilities_key,
            "world_entities": self.world_entities,
        }


async def seed_persona(mem, graph, persona: Persona, *, owner_agent: str | None = None) -> None:
    """Idempotently seed a persona's objectives, world mandate, and baseline KB.

    Safe to call every boot: objectives + world topics dedup by objective text, and
    baseline-KB writes are upserts by key. Fail-open per section — a seed hiccup must
    not abort startup.

    Path B / B-i: `owner_agent` stamps (and dedup-scopes) the seeded goals so a shared-graph
    agent's owner-scoped pursuit sees its OWN seed goals; `None` = unscoped/legacy (additive)."""
    await _seed_objectives(graph, persona, owner_agent)
    await _seed_world_goals(graph, persona, owner_agent)
    await _seed_baseline_kb(mem, persona)


async def _seed_objectives(graph, persona: Persona, owner_agent: str | None = None) -> None:
    """Standing objectives -> `external` goals (deduped by text, owner-scoped in a hive)."""
    if graph is None or not persona.objectives:
        return
    try:
        from nmem_sym.goals import GOALS_TABLE_SQL, create_goal
        pool, embedder = graph.pool, graph._embedder
        # Ensure symbol_goals has the full shape create_goal needs — even when the goals PLUGIN is OFF
        # (a memory-only agent still seeds its persona objectives as external goals). The plugin
        # normally applies this on init; without it the table can lack columns like plan_state and the
        # create below fails. Idempotent DDL (CREATE/ALTER/INDEX ... IF NOT EXISTS); best-effort.
        try:
            await pool.execute(GOALS_TABLE_SQL)
        except Exception as e:  # noqa: BLE001 — a real problem surfaces on the create below
            log.debug("seed_persona: goals-schema ensure skipped: %s", e)
        # DECLARATIVE reconciliation (OPT-IN): treat the persona's objectives as the authoritative set
        # of external standing goals — so editing an objective in the wizard actually adds/retires the
        # matching goal. OFF by default because `create_goal` defaults source_type='external', so a host
        # that mints external goals outside its persona (some do) must not have them reaped. The
        # nmem-studio appliance sets NMEM_PERSONA_RECONCILE_OBJECTIVES=1 (its external goals come only
        # from the persona). The empty-objectives early-return above means it can never reap everything.
        reconcile = os.environ.get("NMEM_PERSONA_RECONCILE_OBJECTIVES", "").strip().lower() in ("1", "true", "yes", "on")
        active = ["pending", "active", "decomposed", "pursuing"]
        seeded = reactivated = 0
        for label, text in persona.objectives:
            row = await pool.fetchrow(
                "SELECT id, status FROM symbol_goals WHERE source_type='external' AND objective=$1 "
                "AND ($2::text IS NULL OR owner_agent=$2) ORDER BY id LIMIT 1", text, owner_agent)
            if row is None:
                await create_goal(pool, embedder, text,
                                  priority=persona.goal_priorities.get(label, 0.5),
                                  source_type="external", owner_agent=owner_agent)
                seeded += 1
            elif reconcile and row["status"] == "abandoned":
                # a RE-ADDED objective (dedup matches regardless of status): reactivate its retired
                # goal instead of leaving it dead. 'pending' so pursuit re-plans it from a clean state.
                await pool.execute("UPDATE symbol_goals SET status='pending' WHERE id=$1", row["id"])
                reactivated += 1
        retired = 0
        if reconcile:
            desired = [text for _l, text in persona.objectives]
            # Abandon every external goal the persona no longer lists AND its owned decomposition
            # SUBTREE — else planned pursuit keeps executing the children of a removed objective
            # (actionable() doesn't re-check a child's parent status).
            retired = await pool.fetchval(
                "WITH RECURSIVE removed AS ("
                "    SELECT id FROM symbol_goals WHERE source_type='external' "
                "      AND ($1::text IS NULL OR owner_agent=$1) AND status = ANY($3::text[]) "
                "      AND objective <> ALL($2::text[]) "
                "  UNION ALL "
                "    SELECT g.id FROM symbol_goals g JOIN removed r ON g.parent_id = r.id "
                "      WHERE g.status = ANY($3::text[])) "
                "  , u AS (UPDATE symbol_goals SET status='abandoned' "
                "      WHERE id IN (SELECT id FROM removed) RETURNING 1) "
                "SELECT count(*) FROM u",
                owner_agent, desired, active)
        log.info("seed_persona: %d new, %d reactivated, %d retired (of %d objective(s))",
                 seeded, reactivated, retired or 0, len(persona.objectives))
    except Exception as e:  # noqa: BLE001
        log.warning("seed_persona objectives failed: %s", e)


async def _seed_world_goals(graph, persona: Persona, owner_agent: str | None = None) -> None:
    """Knowledge-seeking mandate -> `drive_intent` goals the pursuit loop actuates."""
    if graph is None or not persona.world_seed_topics:
        return
    try:
        from nmem_sym.goals import create_goal
        pool, embedder = graph.pool, graph._embedder
        seeded = 0
        for _label, text, priority in persona.world_seed_topics:
            exists = await pool.fetchval(
                "SELECT 1 FROM symbol_goals WHERE objective=$1 "
                "AND ($2::text IS NULL OR owner_agent=$2) LIMIT 1", text, owner_agent)
            if not exists:
                await create_goal(pool, embedder, text, priority=priority,
                                  source_type="drive_intent", owner_agent=owner_agent)
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
