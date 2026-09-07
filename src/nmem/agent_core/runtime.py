"""AgentRuntime — the headless cognitive runtime for an nmem agent.

Owns the whole boot -> run -> shutdown lifecycle of an agent's MIND: memory + symbol
graph, the reasoning backend, persona seeding, the nmem-sym bridge and its plugins,
and the always-on loops (consolidation, drives, and — if the agent actuates — goal
pursuit). It is deliberately **I/O-agnostic**: it owns no web server, no CLI, no voice
pipeline. A host wraps it with whatever interface it wants (michelle: FastAPI; a future
twin: voice) and reaches into ``runtime.mem`` / ``.graph`` / ``.bridge`` / ``.backend``.

The thesis in one object: a new agent is ``AgentRuntime(config, persona, executor=...)``
plus that host I/O. Everything cognitive is here and toggled by the ``capabilities.env``
the process already loaded (nmem/nmem-sym read their flags from env).

Agent-specific seams (all optional except config+persona):
  * ``executor``       — an nmem-act :class:`ActionExecutor` (the agent's hands). Without
                         it, the pursuit loop is simply not started (a pure-thinker agent).
  * ``build_proposal`` — turns a :class:`~nmem_act.PursuitGoal` into an ActionProposal;
                         paired with the executor (it shapes the params the executor wants).
  * ``comms_sink``     — a :class:`~nmem.agent_core.comms.ChannelSink` for the communication
                         drive. Without it, comms is not wired even if the drive flag is on.
  * ``skill_chronic``  — handler for nmem's ``skill.chronic`` event (a recurring lesson).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from nmem.agent_core.memory import build_memory, build_symbol_graph
from nmem.agent_core.persona import Persona, seed_persona

log = logging.getLogger(__name__)


class AgentRuntime:
    def __init__(
        self,
        config: dict,
        persona: Persona,
        *,
        executor: Any | None = None,
        build_proposal: Callable | None = None,
        comms_sink: Any | None = None,
        skill_chronic: Callable[[dict], Awaitable[None]] | None = None,
        backend: Any | None = None,
    ) -> None:
        self._config = config
        self._persona = persona
        self._executor = executor
        self._build_proposal = build_proposal
        self._comms_sink = comms_sink
        self._skill_chronic = skill_chronic

        self.mem = None
        self.graph = None
        self.backend = backend
        self.bridge = None
        self._prediction = None
        self._pursuit = None
        self._consol_task = None
        self._drive_task = None
        self._pursue_task = None
        self.status: dict = {"enabled": False}

    # ── lifecycle ─────────────────────────────────────────────────
    async def start(self) -> dict:
        """Bring the mind up: memory + graph + backend + persona + cognition + loops."""
        self.mem = await build_memory(self._config)
        self.graph = await build_symbol_graph(self._config)
        if self.backend is None:
            from nmem.agent_core.backend import build_backend
            self.backend = build_backend(self._config)
        await seed_persona(self.mem, self.graph, self._persona)
        await self._wire_cognition()
        return self.status

    async def stop(self) -> None:
        """Tear the mind down cleanly (idempotent). Cancel loops, stop consolidation,
        close memory + graph."""
        for t in (self._pursue_task, self._drive_task):
            if t is not None:
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        self._drive_task = self._pursue_task = self._pursuit = None
        try:
            if self.mem is not None and hasattr(self.mem, "stop_consolidation"):
                self.mem.stop_consolidation()
        except Exception as e:  # noqa: BLE001
            log.warning("[runtime] stop_consolidation: %s", e)
        if self._consol_task is not None:
            try:
                await self._consol_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._consol_task = None
        for name, obj in (("sym", self.graph), ("nmem", self.mem)):
            try:
                if obj is not None and hasattr(obj, "close"):
                    await obj.close()
            except Exception as e:  # noqa: BLE001
                log.warning("[runtime] close %s: %s", name, e)
        self.graph = self.mem = self.bridge = None
        log.info("[runtime] stopped")

    # ── cognition wiring (graduated from michelle init_cognition) ──
    async def _wire_cognition(self) -> None:
        mem, graph = self.mem, self.graph
        if self._skill_chronic is not None:
            try:
                mem.on("skill.chronic")(self._skill_chronic)
            except Exception:  # noqa: BLE001
                log.warning("[runtime] could not subscribe skill.chronic", exc_info=True)
        if graph is None:
            log.info("[runtime] symbol graph disabled — no cognitive loops")
            self.status = {"enabled": False}
            return

        from nmem_sym import BridgeConfig, SymbolBridge, config as sym_config
        s = sym_config.settings
        self.bridge = SymbolBridge(graph, BridgeConfig(
            drives_enabled=s.drives_enabled, emotion_enabled=s.emotion_enabled,
            temporal_awareness_enabled=s.temporal_awareness_enabled,
            schemas_enabled=s.schemas_enabled, analogy_enabled=s.analogy_enabled,
            self_model_enabled=s.self_model_enabled, procedures_enabled=s.procedures_enabled,
            goals_enabled=s.goals_enabled, extract_max_parallel=s.extract_max_parallel,
        ))
        self.bridge.connect(mem)

        self._wire_goal_enrichment()
        self._wire_actuation()
        self._wire_comms(s)
        self._wire_recall(s)

        plugins = {}
        try:
            plugins = await self.bridge.initialize_plugins()
            log.info("[runtime] plugins wired: %s", plugins)
        except Exception as e:  # noqa: BLE001
            log.warning("[runtime] initialize_plugins failed (non-fatal): %s", e, exc_info=True)

        pred_on = self._wire_prediction(s)

        # Loop #1: consolidation (its full-cycle/nightly hooks drive cluster + dreamstate).
        self._consol_task = mem.start_consolidation()
        log.info("[runtime] consolidation loop started")

        # Loop #2: drive tick (emotion/temporal piggyback).
        drives_on = False
        if s.drives_enabled:
            tick = float(s.drive_tick_seconds)
            self._drive_task = asyncio.create_task(self._drive_loop(tick))
            drives_on = True
            log.info("[runtime] drive tick loop started (%ss)", tick)

        # Loop #3: goal pursuit — only if the agent gave us hands + a proposal builder.
        pursuit_on = self._start_pursuit()

        self.status = {
            "enabled": True, "plugins": plugins, "prediction": pred_on,
            "drives": drives_on, "consolidation": self._consol_task is not None,
            "actuation": pursuit_on,
        }

    def _wire_goal_enrichment(self) -> None:
        """DRIVES_GOAL_LLM_ENRICH grounding: persona objectives + entities + a recent-
        findings provider, so the native goal producer writes concrete objectives."""
        try:
            p = self._persona
            entities = self._config.get("world_entities", "") or ", ".join(
                lbl for lbl, *_ in p.world_seed_topics)
            obj_text = "\n".join(f"- {t}" for _l, t in p.objectives)

            async def _recent_findings() -> str:
                try:
                    hits = await self.mem.search(p.agent_id, entities, top_k=6)
                    return "\n".join(f"- {(getattr(h, 'content', '') or '')[:200]}"
                                     for h in (hits or []) if getattr(h, "content", ""))
                except Exception:  # noqa: BLE001
                    return ""

            self.bridge.set_goal_enrichment_context(obj_text, entities, _recent_findings)
        except Exception as e:  # noqa: BLE001
            log.warning("[runtime] goal-enrichment registration failed: %s", e)

    def _wire_actuation(self) -> None:
        """Register the agent's executor as the nmem-act runner (if it exposes a build
        hook), so pursuit + the outcome sink close the experiential loop."""
        ex = self._executor
        if ex is None:
            return
        try:
            # An executor may need the bridge (e.g. to build its outcome sink). Support
            # a `build(bridge)` hook; otherwise the executor is used directly.
            if hasattr(ex, "build"):
                ex.build(self.bridge)
        except Exception as e:  # noqa: BLE001
            log.warning("[runtime] actuation build failed: %s", e, exc_info=True)

    def _wire_comms(self, s) -> None:
        if not (getattr(s, "communication_drive_enabled", False)
                and self._comms_sink is not None and getattr(self.bridge, "_drives", None)):
            return
        try:
            from nmem.agent_core.comms import CommsLoop
            comms = CommsLoop(self.bridge, self.mem, self.backend,
                              self._comms_sink, agent_id=self._persona.agent_id)
            self.bridge._drives.on_intent(comms.on_intent)
            log.info("[runtime] comms loop wired")
        except Exception as e:  # noqa: BLE001
            log.warning("[runtime] comms loop wiring failed: %s", e, exc_info=True)

    def _wire_recall(self, s) -> None:
        if not getattr(s, "recall_drive_enabled", False):
            return
        try:
            from nmem.agent_core import recall
            recall.install(self.mem)
            log.info("[runtime] recall consumer wired")
        except Exception as e:  # noqa: BLE001
            log.warning("[runtime] recall consumer wiring failed: %s", e, exc_info=True)

    def _wire_prediction(self, s) -> bool:
        if not s.prediction_enabled:
            return False
        try:
            from nmem_sym import PredictionConfig, PredictionPlugin
            self._prediction = PredictionPlugin(graph=self.graph, config=PredictionConfig(
                llm_reasoning_enabled=s.prediction_llm_reasoning_enabled,
                grounding_llm_enabled=s.prediction_grounding_llm_enabled,
                llm_reasoning_timeout_ms=s.prediction_llm_reasoning_timeout_ms,
                min_confidence_to_store=s.prediction_min_confidence_to_store,
            ))
            self._prediction.register_dreamstate(self.bridge)
            log.info("[runtime] PredictionPlugin wired into dreamstate")
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("[runtime] PredictionPlugin init failed (non-fatal): %s", e)
            return False

    def _start_pursuit(self) -> bool:
        """Start the goal-pursuit loop if the agent actuates. Gated by
        config['pursuit']['enabled'] (default True when an executor is present)."""
        pcfg = (self._config.get("pursuit", {}) or {})
        if self._executor is None or self._build_proposal is None or not pcfg.get("enabled", True):
            return False
        runner = getattr(self._executor, "runner", None) or self._executor
        from nmem_act import GoalPursuit
        from nmem.agent_core.goal_store import SymbolGoalStore
        self._pursuit = GoalPursuit(
            SymbolGoalStore(self.graph.pool,
                            source_type=pcfg.get("source_type", "drive_intent")),
            runner, self._build_proposal)
        interval = float(pcfg.get("interval_seconds", 300))
        cap = max(1, int(pcfg.get("max_per_cycle", 1)))
        self._pursue_task = asyncio.create_task(self._pursue_loop(interval, cap))
        log.info("[runtime] pursuit loop started (cap %d/cycle, every %ss)", cap, interval)
        return True

    # ── loops ─────────────────────────────────────────────────────
    async def _drive_loop(self, tick: float) -> None:
        # Event-wake drives (DRIVES_WAKE_MODE=event + honest discharge) fire only on
        # real pressure, so ticking on a sparse graph doesn't spin.
        while True:
            try:
                await self.bridge.tick_drives(dt=tick)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.warning("[runtime] drive tick failed", exc_info=True)
            await asyncio.sleep(tick)

    async def _pursue_loop(self, interval: float, cap: int) -> None:
        if self._pursuit is not None:
            await self._pursuit.recover()   # un-strand goals from a prior hard stop
        while True:
            try:
                if self._pursuit is not None:
                    await self._pursuit.run_once(cap)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.warning("[runtime] pursuit cycle failed", exc_info=True)
            await asyncio.sleep(interval)
