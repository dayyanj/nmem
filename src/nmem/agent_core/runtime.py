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
  * ``build_executor`` — ``(bridge) -> ActionExecutor``: build the agent's hands once the
                         bridge exists (the executor's outcome sink usually needs it).
                         Without it, the pursuit loop is simply not started (a pure thinker).
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

from nmem.agent_core.hive import HiveConfig
from nmem.agent_core.memory import build_memory, build_symbol_graph
from nmem.agent_core.persona import Persona, seed_persona

log = logging.getLogger(__name__)


class AgentRuntime:
    def __init__(
        self,
        config: dict,
        persona: Persona,
        *,
        build_executor: Callable[[Any], Any] | None = None,
        build_proposal: Callable | None = None,
        comms_sink: Any | None = None,
        skill_chronic: Callable[[dict], Awaitable[None]] | None = None,
        backend: Any | None = None,
        mem: Any | None = None,
        graph: Any | None = None,
        strict_capabilities: bool = False,
    ) -> None:
        self._config = config
        self._persona = persona
        self._strict_caps = strict_capabilities
        self._build_executor = build_executor
        self._build_proposal = build_proposal
        self._comms_sink = comms_sink
        self._skill_chronic = skill_chronic

        # Accept pre-built mem/graph/backend (a host that already owns its boot passes
        # them; the runtime then does ONLY the cognition wiring and does NOT close them
        # on stop). Otherwise the runtime builds + owns + closes them.
        self.mem = mem
        self.graph = graph
        self.backend = backend
        self._owns_mem = mem is None
        self._owns_graph = graph is None
        self.bridge = None
        self._prediction = None
        self._runner = None
        self._pursuit = None
        self._consol_task = None
        self._drive_task = None
        self._pursue_task = None
        self._lifecycle_task = None
        self._keeper_watch_task = None
        self._keeper_dsn = self._keeper_key = None
        self.status: dict = {"enabled": False}

        # Hive (Path B): isolated (default) = today, byte-identical. shared_world elects a single
        # graph-keeper by advisory lock (see agent_core.hive); is_keeper gates the graph-global loops.
        self.hive = HiveConfig.from_dict(config.get("hive"))
        # Fail CLOSED (codex B-i finding 3): shared_world WITHOUT an owner identity would forward
        # owner_agent=None everywhere → unscoped agency = table-wide recovery + claiming EVERY
        # agent's goals. A shared-world agent MUST have an agent_id; default None to the persona's.
        if self.hive.is_shared_world and not self.hive.agent_id:
            self.hive.agent_id = persona.agent_id
            if not self.hive.agent_id:
                raise RuntimeError("hive shared_world requires a non-empty agent_id (owner identity)")
        self.is_keeper = False
        self._keeper_lock = None

    @property
    def runs_graph_global(self) -> bool:
        """Whether THIS process runs the graph-global cognitive cycles — clustering + dreamstate
        (and dreamstate's post-hooks: schema induction, analogy, self-model concept clustering).
        These operate on the WHOLE shared graph, so exactly one process per graph may run them.
        Solo/isolated agents own their graph outright → always run them (byte-identical to today);
        under shared_world authority is tied to ACTUAL live lock possession — ``_keeper_lock.alive()``,
        not the ``is_keeper`` status mirror — so a keeper whose lock connection dies de-authorizes
        immediately (before the watch tick flips the flag), which is what prevents two keepers running
        global maintenance after a lock-session loss (codex 2026-09-09 P1). B-ii step-3."""
        if not self.hive.is_shared_world:
            return True
        return self._keeper_lock is not None and self._keeper_lock.alive()

    def _set_keeper(self, flag: bool) -> None:
        """Update keeper state + the PUBLISHED status in lockstep (so /health never reports a stale
        role after a live takeover/revoke — codex 2026-09-09 P2)."""
        self.is_keeper = flag
        self.status["keeper"] = flag

    @property
    def agent_id(self) -> str:
        return self._persona.agent_id

    def _check_capabilities(self) -> None:
        """Fail-fast on an invalid capability combo (a flag ON while a required flag is
        OFF → it would silently no-op). Warns loudly by default; raises if the runtime
        was built with strict_capabilities=True."""
        from nmem.agent_core.capabilities import check_env
        issues = check_env()
        if not issues:
            return
        for i in issues:
            log.warning("[runtime] capability dependency unmet: %s", i.message)
        if self._strict_caps:
            raise ValueError(
                "invalid capability combination: "
                + "; ".join(i.message for i in issues))

    # ── lifecycle ─────────────────────────────────────────────────
    async def start(self) -> dict:
        """Bring the mind up: memory + graph + backend + persona + cognition + loops.
        mem/graph/backend passed to the constructor are used as-is; anything not passed
        is built here."""
        self._check_capabilities()
        if self.mem is None:
            self.mem = await build_memory(self._config)
        if self.graph is None:
            self.graph = await build_symbol_graph(self._config)
        if self.backend is None:
            from nmem.agent_core.backend import build_backend
            self.backend = build_backend(self._config)
        await seed_persona(self.mem, self.graph, self._persona, owner_agent=self.hive.agent_id)
        await self._elect_keeper()
        await self._wire_cognition()
        return self.status

    async def _elect_keeper(self) -> None:
        """Shared-world graph-keeper election (§4.2). A process willing to keep (``graph_role:
        keeper``) tries the advisory lock on the SHARED graph DB; exactly one wins. The winner's
        ``is_keeper`` authorizes the graph-global loops; everyone else is a contributor. Isolated
        agents and contributors never try → no behavior change. Election failure ⇒ contributor
        (never fatal). The graph-global cycles are gated on this in ``_wire_cognition`` via
        ``runs_graph_global`` (the two BridgeConfig flags); the remaining open item is the per-agent
        goal lifecycle that rides dreamstate (docs/path-b-hive-scoping-design.md §12.5 step 3)."""
        if not self.hive.wants_keeper or self.graph is None:
            return
        from nmem.agent_core.hive import become_keeper, keeper_key
        from nmem.agent_core.memory import _db_url
        try:
            db = self._config.get("db", {})
            graph_dsn = _db_url(self._config, env_key=db.get("env_key"),
                                config_key=db.get("config_key", "default"))
            # Key on the SHARED graph's DB NAME (not the per-agent domain) so every contender on
            # the same graph computes the SAME key — else each would win its own lock.
            dbname = graph_dsn.rsplit("/", 1)[-1].split("?")[0]
            self._keeper_dsn, self._keeper_key = graph_dsn, keeper_key(dbname)  # reused by the retry tick
            self._keeper_lock = await become_keeper(self._keeper_dsn, self._keeper_key)
        except Exception as e:  # noqa: BLE001
            log.warning("[runtime] keeper election failed (running as contributor): %s", e)
            self._keeper_lock = None
        self.is_keeper = self._keeper_lock is not None
        log.info("[runtime] hive=shared_world graph_role=%s keeper=%s (graph-global loops %s)",
                 self.hive.graph_role, self.is_keeper,
                 "authorized" if self.is_keeper else "suppressed (contributor)")

    async def converse(self, message: str, **kw):
        """Hold one memory-grounded conversation turn with this agent (see
        ``nmem.agent_core.chat.converse``). The reusable core behind any chat UI."""
        from nmem.agent_core.chat import converse
        return await converse(self, message, **kw)

    async def stop(self) -> None:
        """Tear the mind down cleanly (idempotent). Cancel loops, release the keeper lock,
        stop consolidation, close memory + graph."""
        # Cancel the watch tick FIRST — else it could re-acquire the lock we're about to release.
        if self._keeper_watch_task is not None:
            self._keeper_watch_task.cancel()
            try:
                await self._keeper_watch_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._keeper_watch_task = None
        if self._keeper_lock is not None:
            try:
                await self._keeper_lock.release()   # frees the graph-keeper role for another process
            except Exception as e:  # noqa: BLE001
                log.warning("[runtime] keeper release: %s", e)
            self._keeper_lock = None
            self.is_keeper = False
        for t in (self._pursue_task, self._drive_task, self._lifecycle_task):
            if t is not None:
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        self._drive_task = self._pursue_task = self._lifecycle_task = self._pursuit = None
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
        # Only close mem/graph the runtime itself built; a host that passed them in
        # owns their teardown.
        for name, obj, owned in (("sym", self.graph, self._owns_graph),
                                 ("nmem", self.mem, self._owns_mem)):
            try:
                if owned and obj is not None and hasattr(obj, "close"):
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
        # B-ii step-3: gate the graph-global cycles (clustering + dreamstate + its post-hooks) on keeper
        # state via nmem-sym's D1 callable seam (bridge.py:223). The bridge registers the hooks
        # unconditionally and consults is_keeper() IN the hook body, so this re-evaluates LIVE — a
        # contributor that later wins the lock (see _keeper_retry_loop) starts running them with no
        # restart. Pass the callable ONLY in shared_world; isolated ⇒ is_keeper=None ⇒ nmem-sym keeps
        # its static-flag behavior (honors any hand-set cluster/dreamstate flag) = byte-identical to
        # today. (Supersedes c4ea564's boot-snapshot flags, which couldn't fail over — codex 2026-09-09.)
        is_keeper_cb = (lambda: self.runs_graph_global) if self.hive.is_shared_world else None
        self.bridge = SymbolBridge(graph, BridgeConfig(
            drives_enabled=s.drives_enabled, emotion_enabled=s.emotion_enabled,
            temporal_awareness_enabled=s.temporal_awareness_enabled,
            schemas_enabled=s.schemas_enabled, analogy_enabled=s.analogy_enabled,
            self_model_enabled=s.self_model_enabled, procedures_enabled=s.procedures_enabled,
            goals_enabled=s.goals_enabled, extract_max_parallel=s.extract_max_parallel,
        ), agent_id=self.hive.agent_id,   # B-i: owner-stamp this agent's agency writes (None=isolated)
           is_keeper=is_keeper_cb)
        if self.hive.is_shared_world:
            log.info("[runtime] graph-global cycles %s (keeper=%s, live-gated)",
                     "ON" if self.runs_graph_global else "SUPPRESSED (contributor)", self.is_keeper)
        self.bridge.connect(mem)

        self._wire_goal_enrichment()
        if self._build_executor is not None:
            try:
                self._runner = self._build_executor(self.bridge)
            except Exception as e:  # noqa: BLE001
                log.warning("[runtime] build_executor failed: %s", e, exc_info=True)
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

        # Loop #3b: goal-lifecycle (B-ii Decision 2) — per-agent, EVERY mode (owner_agent=None=isolated
        # =today). This REPLACES the dreamstate-embedded lifecycle, which nmem-sym removes in the same
        # co-land round; running BOTH double-ticks impasse_cycles (hive doc §23). So it is DEFAULT-OFF
        # (goal_lifecycle.loop_enabled) until that removal + this loop are deployed together — flipping
        # the flag is the atomic cutover, protecting live isolated agents (michelle) from a double-tick.
        lcfg = (self._config.get("goal_lifecycle", {}) or {})
        lifecycle_on = False
        if s.goals_enabled and lcfg.get("loop_enabled", False):
            lc_interval = float(lcfg.get("tick_seconds", 3600))
            self._lifecycle_task = asyncio.create_task(self._lifecycle_loop(lc_interval))
            lifecycle_on = True
            log.info("[runtime] goal-lifecycle loop started (every %ss, owner=%s)",
                     lc_interval, self.hive.agent_id)

        # Loop #4: keeper watch — every WILLING process (graph_role=keeper) runs it, the elected keeper
        # to self-check its lock liveness and contributors to take over live if it dies (see
        # _keeper_watch_loop). A pure contributor (graph_role=contributor) never runs it → no change.
        if self.hive.wants_keeper:
            retry = float(self._config.get("hive", {}).get("keeper_retry_seconds", 30))
            self._keeper_watch_task = asyncio.create_task(self._keeper_watch_loop(retry))
            log.info("[runtime] keeper-watch loop started (willing process, every %ss)", retry)

        self.status = {
            "enabled": True, "plugins": plugins, "prediction": pred_on,
            "drives": drives_on, "consolidation": self._consol_task is not None,
            "actuation": pursuit_on, "lifecycle": lifecycle_on,
            "hive": self.hive.mode, "keeper": self.is_keeper,
        }

    def _wire_goal_enrichment(self) -> None:
        """DRIVES_GOAL_LLM_ENRICH grounding: persona objectives + entities + a recent-
        findings provider, so the native goal producer writes concrete objectives."""
        try:
            p = self._persona
            entities = (p.world_entities or self._config.get("world_entities", "")
                        or ", ".join(lbl for lbl, *_ in p.world_seed_topics))
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
        if self._runner is None or self._build_proposal is None or not pcfg.get("enabled", True):
            return False
        from nmem_act import GoalPursuit
        from nmem.agent_core.goal_store import SymbolGoalStore
        self._pursuit = GoalPursuit(
            SymbolGoalStore(self.graph.pool,
                            source_type=pcfg.get("source_type", "drive_intent"),
                            owner_agent=self.hive.agent_id),   # B-i: pursue only our own goals (None=isolated)
            self._runner, self._build_proposal)
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

    async def _lifecycle_loop(self, interval: float) -> None:
        """B-ii Decision 2: drive THIS agent's goal-lifecycle tick (impasse-tick → decompose → detect/
        resolve impasses → abandon stale → reap orphaned) via nmem-sym's owner-scoped entrypoint, for
        EVERY mode. ``owner_agent=self.hive.agent_id`` — None (isolated/legacy) = the exact unscoped set
        that ran inside dreamstate before D2 de-dreamstated it. Gated OFF by default during the co-land
        (see run()); once live it is the SOLE lifecycle driver."""
        while True:
            try:
                await self.bridge.run_goal_lifecycle(owner_agent=self.hive.agent_id)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.warning("[runtime] goal-lifecycle tick failed", exc_info=True)
            await asyncio.sleep(interval)

    async def _keeper_watch_loop(self, interval: float) -> None:
        """Keeper liveness + live failover for any WILLING process (graph_role=keeper). Each tick:
        (1) if we hold the lock, ``verify()`` it still lives — a keeper whose dedicated PG session died
            while the process survives must REVOKE its own authority (PG already released the lock), or
            it + whoever takes over would both run global maintenance = split-brain (codex 2026-09-09 P1);
        (2) if we don't hold it (lost it, or lost the boot election), try to (re)acquire — on success the
            bridge's in-hook ``is_keeper()`` activates the already-registered graph-global hooks next
            cycle, no restart. Runs on the keeper too (to self-check), not just contributors."""
        from nmem.agent_core.hive import become_keeper
        while True:
            try:
                await asyncio.sleep(interval)
                if self._keeper_dsn is None:
                    return
                if self._keeper_lock is not None and not await self._keeper_lock.verify():
                    self._keeper_lock = None
                    self._set_keeper(False)
                    log.warning("[runtime] keeper lock lost (session died) — revoked authority; global cycles halt")
                if self._keeper_lock is None:           # never had it, or just lost it → compete for the freed lock
                    lock = await become_keeper(self._keeper_dsn, self._keeper_key)
                    if lock is not None:
                        self._keeper_lock = lock
                        self._set_keeper(True)
                        log.info("[runtime] keeper acquired (boot loss/failover) — graph-global cycles authorized")
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.warning("[runtime] keeper watch failed", exc_info=True)
