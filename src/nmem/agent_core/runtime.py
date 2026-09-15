"""AgentRuntime — the headless cognitive runtime for an nmem agent.

Owns the whole boot -> run -> shutdown lifecycle of an agent's MIND: memory + symbol
graph, the reasoning backend, persona seeding, the nmem-sym bridge and its plugins,
and the always-on loops (consolidation, drives, and — if the agent actuates — goal
pursuit). It is deliberately **I/O-agnostic**: it owns no web server, no CLI, no voice
pipeline. A host wraps it with whatever interface it wants (e.g. FastAPI; a future
voice host) and reaches into ``runtime.mem`` / ``.graph`` / ``.bridge`` / ``.backend``.

The thesis in one object: a new agent is ``AgentRuntime(config, persona, executor=...)``
plus that host I/O. Everything cognitive is here and toggled by the ``capabilities.env``
the process already loaded (nmem/nmem-sym read their flags from env).

Agent-specific seams (all optional except config+persona):
  * ``build_executor`` — ``(bridge) -> ActionExecutor``: build the agent's hands once the
                         bridge exists (the executor's outcome sink usually needs it).
                         Without it, the pursuit loop is simply not started (a pure thinker).
  * ``build_proposal`` — turns a :class:`~nmem_act.PursuitGoal` into an ActionProposal; paired with
                         the executor. OPTIONAL: if omitted while an executor is present, the runtime
                         defaults it to ``agent_core.default_proposal`` (context-injecting, configured
                         via ``pursuit.{action_type,tool_tag,lessons_query}``) — a thin host gets pursuit
                         for free. Pass one only to customize beyond those knobs.
  * ``comms_sink``     — a :class:`~nmem.agent_core.comms.ChannelSink` for the communication
                         drive. Without it, comms is not wired even if the drive flag is on.
  * ``skill_chronic``  — handler for nmem's ``skill.chronic`` event (a recurring lesson). OPTIONAL:
                         defaults to ``agent_core.default_skill_chronic`` when omitted, so every agent
                         turns recurring lessons into stored memory for free. Pass one only to override.
  * ``peer`` + delegation seams — plug the brokerless A2A delegation queue (P1d) onto the host-owned
                         ``PeerExchange`` bus. ``peer`` is that exchange; ``delegation_executor`` +
                         ``task_registry`` make this agent a WORKER (durable inbox); ``channel_for``
                         makes it a REQUESTER (durable outbox); ``delegation_approve`` gates MUTATING
                         task types; ``on_delegation_complete`` is the requester result callback. All
                         optional, gated by ``config['delegation'].enabled`` (DEFAULT-OFF) — unset ⇒
                         byte-identical to today. See ``nmem.agent_core.delegation``.
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


def install_continuity_provider(mem, bridge) -> bool:
    """Wire a SymbolBridge's ``continuity_inputs()`` into ``mem.wake()``'s
    continuity provider, so the wake snapshot surfaces the agent's goals /
    self-model / drive-state from the live bridge.

    Generic runtime glue (every AgentRuntime-based agent gets it), kept as a
    standalone function so it is unit-testable without a full runtime. Guarded
    and fail-open: returns False (no-op) when either side predates the continuity
    layer, and never raises into startup.
    """
    reg = getattr(mem, "register_continuity_provider", None)
    if reg is None or not hasattr(bridge, "continuity_inputs"):
        return False
    try:
        from nmem.types import SymContinuityInputs

        async def _continuity_provider(agent_id):
            d = await bridge.continuity_inputs()
            return SymContinuityInputs(
                self_model_summary=d.get("self_model_summary"),
                drive_state_prose=d.get("drive_state_prose"),
                active_goals=tuple(d.get("active_goals") or ()),
                relational_self_prose=d.get("relational_self_prose"),
                relational_self_role=d.get("relational_self_role"),
            )

        reg(_continuity_provider)
        return True
    except Exception:  # noqa: BLE001 — wiring must never break startup
        return False


def install_control_provider(mem, bridge) -> bool:
    """Wire a SymbolBridge's ``control_recommendations()`` into ``mem`` as the
    metacognitive-control provider (Level 4), so a host can fetch lever recommendations
    for a decision point via ``mem.control_recommendations(context)``.

    Generic runtime glue (every AgentRuntime-based agent gets it), kept standalone so it
    is unit-testable without a full runtime. Guarded and fail-open: returns False (no-op)
    when either side predates the control seam, and never raises into startup. Note the
    seam is INERT until ``NMEM_SYM_METACOG_ENABLED`` — wiring it is always safe."""
    reg = getattr(mem, "register_control_provider", None)
    if reg is None or not hasattr(bridge, "control_recommendations"):
        return False
    try:
        async def _control_provider(context):
            return await bridge.control_recommendations(context)

        reg(_control_provider)
        return True
    except Exception:  # noqa: BLE001 — wiring must never break startup
        return False


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
        peer: Any | None = None,
        delegation_executor: Any | None = None,
        task_registry: Any | None = None,
        channel_for: Callable[[str], str] | None = None,
        delegation_approve: Callable | None = None,
        on_delegation_complete: Callable | None = None,
    ) -> None:
        self._config = config
        self._persona = persona
        self._strict_caps = strict_capabilities
        self._build_executor = build_executor
        self._build_proposal = build_proposal
        self._comms_sink = comms_sink
        self._skill_chronic = skill_chronic
        # Delegation (P1d) — brokerless A2A work queue over the host-owned PeerExchange bus.
        # All optional; wired only when config['delegation'].enabled AND a started peer is present
        # (default-off → byte-identical when unset). ``delegation_executor``+``task_registry`` make
        # this agent a WORKER (inbox); ``channel_for`` makes it a REQUESTER (client). Either, both,
        # or neither. ``delegation_approve`` gates MUTATING task types before the executor runs.
        self._peer = peer
        self._delegation_executor = delegation_executor
        self._task_registry = task_registry
        self._channel_for = channel_for
        self._delegation_approve = delegation_approve
        self._on_delegation_complete = on_delegation_complete
        self._deleg_inbox = None
        self._deleg_client = None
        self._deleg_inbox_task = None
        self._deleg_client_task = None
        self._deleg_registered: dict = {}   # kind -> the exact handler THIS runtime installed on the peer
        self._deleg_drain_grace = 10.0   # bounded shutdown drain before cancel (set from config on wire)
        # Reserve the delegation kinds on the (host-owned, already-started) bus at the EARLIEST point
        # — construction — so a task.* retry that lands before/while we wire (or if wiring later bails
        # on shared_world / provisioning failure) is DROPPED, never journaled as cognitive evidence
        # (codex). Reserve only the roles we'd route, only when the operator enabled delegation.
        self._reserve_delegation_kinds()

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
        # Chat-path tools: the host may attach its drop-in actors registry + autonomy gate
        # here so a *conversation* (chat.converse / converse_stream) can call the same tools
        # the /act executor uses, under the same policy. None ⇒ chat still gets the always-on
        # built-in memory_search (see chat_tools.build_chat_registry).
        self.tools = None
        self.gate = None
        self._pursuit = None
        self._planned_pursuit = None          # §30.4: optional 2nd pursuit over planned goals
        self._consol_task = None
        self._drive_task = None
        self._pursue_task = None
        self._planned_pursue_task = None
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
            # Inject the MemorySystem's shared embedding provider so the graph
            # reuses one model instance per process (no duplicate MiniLM load).
            self.graph = await build_symbol_graph(
                self._config, embedder=getattr(self.mem, "embedding", None))
        if self.backend is None:
            from nmem.agent_core.backend import build_backend
            self.backend = build_backend(self._config)
        self._wire_usage_recorder()
        await seed_persona(self.mem, self.graph, self._persona, owner_agent=self.hive.agent_id)
        await self._elect_keeper()
        await self._wire_cognition()
        return self.status

    def _wire_usage_recorder(self) -> None:
        """Attach real-token-usage recording (Tier B, viz token stats).

        Persists true provider token counts to ``nmem_metadata`` (daily +
        lifetime keys) for every real LLM call — from BOTH nmem core's backend
        (conversation / comms / chat tools) AND nmem-sym's cognition (which uses
        its own client and reports usage via ``viz_record_usage`` → the recorder
        we register here). The backend path also emits ``llm.usage`` for the
        overlay's live counter; the nmem-sym path emits it itself, so the
        registered recorder only persists (no double emit). All fire-and-forget +
        fail-open. No-op when the backend predates the hook or memory has no DB."""
        db = getattr(self.mem, "_db", None)
        if db is None:
            return
        agent_id = self.agent_id

        def _persist(inp: int, out: int, model: str | None = None) -> None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return  # no running loop — skip silently
            from nmem.token_stats import record_real_usage
            loop.create_task(record_real_usage(db, agent_id, inp, out, model=model))

        # nmem-sym cognition path: persist only (it emits llm.usage on its own).
        try:
            from nmem_sym.viz_events import viz_set_usage_recorder
            viz_set_usage_recorder(_persist)
        except Exception:  # noqa: BLE001 — nmem-sym viz is optional/absent on some installs
            pass

        # nmem core backend path: persist + emit the live counter event.
        be = self.backend
        if be is not None and hasattr(be, "on_usage"):
            def _record(inp: int, out: int, model: str | None = None) -> None:
                _persist(inp, out, model)
                try:
                    loop = asyncio.get_running_loop()
                    from nmem_sym.viz_events import viz_emit
                    loop.create_task(viz_emit("llm.usage", {
                        "agent_id": agent_id, "input": inp, "output": out,
                        "total": inp + out, "model": model,
                    }))
                except Exception:  # noqa: BLE001 — viz optional/absent
                    pass
            be.on_usage = _record

        log.info("[runtime] real token-usage recorder wired (backend + nmem-sym cognition)")

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
        # Delegation loops (P1d): DRAIN before cancel (codex P2). stop() only sets the loop's event,
        # so a task mid-execution keeps running; give it a BOUNDED grace to finish the current task
        # and durably record its result, then cancel as a backstop. Without the wait, a clean restart
        # would interrupt an in-flight executor → the row sits 'executing' until lease recovery (up to
        # ~lease seconds). Fail-open: a stopped/absent component never blocks teardown.
        # Delegation teardown — STAGED so in-flight work can complete cleanly (codex). The host-owned
        # bus OUTLIVES this runtime, so we must also detach handlers or a stray task.* would hit a dead
        # inbox / closed pool. But ORDER matters: an executing worker may itself be awaiting a SUBTASK
        # via the client (delegate_and_wait), so task.result MUST stay attached until the inbox drains.
        # (1) stop NEW intake: detach task.request + stop the inbox loop claiming more.
        grace = max(0.0, self._deleg_drain_grace)
        _unreg = getattr(self._peer, "unregister_kind", None) if self._peer is not None else None

        def _detach(*kinds):
            """Detach ONLY the handlers this runtime installed, identity-checked, so we never remove a
            handler the host pre-registered or a REPLACEMENT runtime has since installed on the shared
            bus. The kind stays RESERVED → late retries are dropped, not journaled."""
            if _unreg is None:
                return
            for k in kinds:
                h = self._deleg_registered.pop(k, None)
                if h is None:
                    continue
                try:
                    _unreg(k, h)
                except Exception:  # noqa: BLE001
                    pass

        from nmem.agent_core import delegation as _dg
        _detach(_dg.KIND_REQUEST)
        if self._deleg_inbox is not None:
            try:
                await self._deleg_inbox.stop()
            except Exception:  # noqa: BLE001
                pass
        # (2) drain the inbox — its in-flight executor can finish, and any subtask it awaits is still
        #     resolved because task.result remains attached.
        if self._deleg_inbox_task is not None:
            try:
                await asyncio.wait([self._deleg_inbox_task], timeout=grace)
            except Exception:  # noqa: BLE001
                pass
        # (3) NOW the requester side can go: detach result/progress + stop the client loop + drain it.
        _detach(_dg.KIND_RESULT, _dg.KIND_PROGRESS)
        if self._deleg_client is not None:
            try:
                await self._deleg_client.stop()
            except Exception:  # noqa: BLE001
                pass
        if self._deleg_client_task is not None:
            try:
                await asyncio.wait([self._deleg_client_task], timeout=grace)
            except Exception:  # noqa: BLE001
                pass
        for t in (self._pursue_task, self._planned_pursue_task, self._drive_task,
                  self._lifecycle_task, self._deleg_inbox_task, self._deleg_client_task):
            if t is not None:
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        self._drive_task = self._pursue_task = self._planned_pursue_task = None
        self._lifecycle_task = self._pursuit = self._planned_pursuit = None
        self._deleg_inbox_task = self._deleg_client_task = None
        self._deleg_inbox = self._deleg_client = None
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

    # ── cognition wiring (graduated from the reference agent's init_cognition) ──
    def _wire_skill_chronic(self, mem) -> None:
        """Subscribe the skill.chronic → lesson handler. Thin host: default it to
        ``agent_core.default_skill_chronic`` when none was passed, so every agent turns recurring
        lessons into stored memory for free; a host may still pass its own to override. Harmless if
        the event never fires."""
        sc = self._skill_chronic
        if sc is None:
            try:
                from nmem.agent_core.lessons import default_skill_chronic
                sc = default_skill_chronic(mem, self.agent_id)
            except Exception:  # noqa: BLE001
                sc = None
        if sc is not None:
            try:
                mem.on("skill.chronic")(sc)
            except Exception:  # noqa: BLE001
                log.warning("[runtime] could not subscribe skill.chronic", exc_info=True)

    async def _wire_cognition(self) -> None:
        mem, graph = self.mem, self.graph
        self._wire_skill_chronic(mem)
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
        if install_continuity_provider(mem, self.bridge):
            log.info("[runtime] continuity provider wired (wake <- bridge)")
        if install_control_provider(mem, self.bridge):
            log.info("[runtime] control provider wired (metacog <- bridge)")

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
        # the flag is the atomic cutover, protecting live isolated agents from a double-tick.
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

        # Loop #5/#6: delegation — the brokerless A2A work queue (P1d), gated delegation.enabled
        # (DEFAULT-OFF, additive). Registers task.* on the host's peer bus + starts the inbox
        # (worker) / client (requester) loops. No-op when off or no started peer.
        deleg = await self._wire_delegation()

        self.status = {
            "enabled": True, "plugins": plugins, "prediction": pred_on,
            "drives": drives_on, "consolidation": self._consol_task is not None,
            "actuation": pursuit_on, "lifecycle": lifecycle_on,
            "hive": self.hive.mode, "keeper": self.is_keeper, "delegation": deleg,
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

    def _wrap_build_proposal(self, build):
        """Wrap the host proposal-builder so symbol surfacing + the participation ledger
        live in agent_core (generic to every agent), not in each host.

        When the nmem-sym surfacing ledger is off — or no symbol graph is wired — this is
        a passthrough (byte-identical to the raw host builder). When on: mint a per-pursuit
        ``turn_id``, surface graph hypotheses for the goal (which writes the ledger row keyed
        to that turn_id), merge the surfaced context into the proposal's ``lessons`` so the
        executor actually uses it, and stamp ``turn_id`` into ``params`` so the outcome sink
        can credit the exact surfaced set. Fully fail-open: any error returns the host
        proposal unchanged — surfacing must never break a pursuit."""
        import inspect
        from uuid import uuid4

        async def wrapped(goal):
            # Working memory: record what the agent is working on RIGHT NOW (the active goal),
            # in the agent-level autonomous lane — BEFORE building, so default_proposal reads THIS
            # goal's focus (not the prior cycle's). Bounded so a long objective can't blow the
            # working-memory prompt budget and suppress the whole lane. Fail-open + gated.
            try:
                from nmem.tiers.working import AUTONOMOUS_SESSION
                if self.mem._config.working.enabled:
                    obj = (getattr(goal, "objective", "") or "").strip()
                    if obj:
                        await self.mem.working.set(
                            AUTONOMOUS_SESSION, self.agent_id, "current_focus", obj[:280], priority=1)
            except Exception:  # noqa: BLE001 — working memory is additive, never a blocker
                log.warning("[runtime] working-memory current_focus write failed (non-fatal)", exc_info=True)
            # Build the host proposal (always returned even if surfacing fails).
            built = build(goal)
            proposal = await built if inspect.isawaitable(built) else built
            try:
                if self.bridge is None:
                    return proposal
                from nmem_sym import config as sym_config
                if not sym_config.settings.surfacing_ledger_enabled:
                    return proposal
                turn_id = uuid4().hex
                # Use runtime.agent_id (the populated scope, e.g. "agent-1") — NOT
                # hive.agent_id, which stays "" for an isolated (non-shared-world) agent.
                agent_id = getattr(self, "agent_id", None) or getattr(self.hive, "agent_id", None)
                surfaced = await self.bridge.augment_search(
                    getattr(goal, "objective", "") or "",
                    turn_id=turn_id, agent_id=agent_id)
                params = getattr(proposal, "params", None)
                if proposal is not None and params is not None:
                    params["turn_id"] = turn_id
                    if surfaced and surfaced.strip():
                        prior = params.get("lessons", "") or ""
                        block = ("Graph hypotheses (speculative — weigh, don't assume):\n"
                                 + surfaced.strip())
                        params["lessons"] = (block + "\n\n" + prior) if prior else block
            except Exception:  # noqa: BLE001 — surfacing is additive, never a blocker
                log.warning("[runtime] surfacing wrapper failed (non-fatal)", exc_info=True)
            return proposal

        return wrapped

    def _default_proposal(self, pcfg: dict):
        """Build the graduated default proposal builder from this runtime's own mem/graph/bridge/id +
        the pursuit config knobs. Used when a thin host supplies an executor but no build_proposal."""
        from nmem.agent_core.proposal import default_proposal
        cc, capability_class = pcfg.get("capability_class"), None
        if cc:
            try:
                from nmem_act import CapabilityClass
                capability_class = CapabilityClass(str(cc).lower())
            except Exception:  # noqa: BLE001 — unknown value falls back to the builder's READ_ONLY default
                capability_class = None
        return default_proposal(
            self.mem, self.graph, self.bridge, agent_id=self.agent_id,
            action_type=pcfg.get("action_type", "llm_tool_call"), capability_class=capability_class,
            tool_tag=pcfg.get("tool_tag"), lessons_query=pcfg.get("lessons_query"))

    def _start_pursuit(self) -> bool:
        """Start the goal-pursuit loop if the agent actuates. Gated by
        config['pursuit']['enabled'] (default True when an executor is present)."""
        pcfg = (self._config.get("pursuit", {}) or {})
        if self._runner is None or not pcfg.get("enabled", True):
            return False
        from nmem_act import GoalPursuit
        from nmem.agent_core.goal_store import SymbolGoalStore
        # Thin host: an executor but no host proposal builder → default it, so every actuating agent
        # inherits the standard context-injecting builder (lessons/prior/recall/continuity), configured
        # via pursuit.{action_type,tool_tag,lessons_query,capability_class}. A host may still pass its own.
        build = self._build_proposal or self._default_proposal(pcfg)
        # Phase 1: surfacing + ledger are injected here (agent_core), so the builder stays lean and
        # every agent inherits the self-improvement loop. Both pursuits share the one wrapped builder.
        build_proposal = self._wrap_build_proposal(build)
        self._pursuit = GoalPursuit(
            SymbolGoalStore(self.graph.pool,
                            source_type=pcfg.get("source_type", "drive_intent"),
                            owner_agent=self.hive.agent_id),   # B-i: pursue only our own goals (None=isolated)
            self._runner, build_proposal)
        interval = float(pcfg.get("interval_seconds", 300))
        cap = max(1, int(pcfg.get("max_per_cycle", 1)))
        self._pursue_task = asyncio.create_task(self._pursue_loop(self._pursuit, interval, cap))
        log.info("[runtime] pursuit loop started (cap %d/cycle, every %ss)", cap, interval)
        # §30.4: a SECOND pursuit over PLANNED goals (any source_type), sharing the same executor +
        # proposal builder, so validated-but-unexecuted plans (e.g. an agent's "Establish: X"
        # decomposition goals) actually get pursued. Gated by pursuit.planned.enabled (default off).
        plcfg = (pcfg.get("planned", {}) or {})
        if plcfg.get("enabled", False):
            self._planned_pursuit = GoalPursuit(
                SymbolGoalStore(self.graph.pool, source_type=None,
                                owner_agent=self.hive.agent_id, dispatch="planned"),
                self._runner, build_proposal)
            p_interval = float(plcfg.get("interval_seconds", interval))
            p_cap = max(1, int(plcfg.get("max_per_cycle", cap)))
            self._planned_pursue_task = asyncio.create_task(
                self._pursue_loop(self._planned_pursuit, p_interval, p_cap))
            log.info("[runtime] PLANNED pursuit loop started (cap %d/cycle, every %ss)",
                     p_cap, p_interval)
        return True

    def _reserve_delegation_kinds(self) -> None:
        """Reserve (don't-journal) the delegation kinds this agent would route, on the host-owned
        peer bus, at construction — the earliest the runtime can act. Idempotent + fail-open; a peer
        without ``reserve_kind`` (or delegation disabled) is a no-op. See __init__ for the why."""
        try:
            dcfg = (self._config.get("delegation", {}) or {})
            if not dcfg.get("enabled", False) or self._peer is None:
                return
            if not hasattr(self._peer, "reserve_kind"):
                return
            from nmem.agent_core import delegation as dg
            if self._delegation_executor is not None and self._task_registry is not None:
                self._peer.reserve_kind(dg.KIND_REQUEST)
            if self._channel_for is not None:
                self._peer.reserve_kind(dg.KIND_RESULT)
                self._peer.reserve_kind(dg.KIND_PROGRESS)
        except Exception:  # noqa: BLE001 — reservation must never break construction
            pass

    async def _wire_delegation(self) -> dict:
        """P1d: wire the brokerless A2A delegation queue onto the host's PeerExchange bus.

        Additive + fail-open. Returns a small status dict (``{enabled, worker, requester}``).
        Gated OFF unless ``config['delegation'].enabled`` AND a STARTED peer is present — so an
        agent with no exchange, or the flag off, is byte-identical to today. This agent is a
        WORKER when an executor+registry were injected (starts the inbox loop, routes
        ``task.request``), and a REQUESTER when a ``channel_for`` was injected (starts the client
        loop, routes ``task.result``/``task.progress``). Ledgers self-provision in the agent's OWN
        graph DB (``graph.pool`` — the same pool the goal store uses), preserving isolation."""
        off = {"enabled": False, "worker": False, "requester": False}
        dcfg = (self._config.get("delegation", {}) or {})
        if not dcfg.get("enabled", False):
            return off
        if self._peer is None:
            log.warning("[runtime] delegation.enabled but no peer bus supplied — skipping")
            return off
        if not getattr(self._peer, "started", False):
            log.warning("[runtime] delegation.enabled but exchange not started "
                        "(needs exchange.enabled) — skipping")
            return off
        pool = getattr(self.graph, "pool", None)
        if pool is None:
            log.warning("[runtime] delegation.enabled but no graph DB pool — skipping")
            return off
        # Isolation invariant (codex P1): the two ledgers are UNSCOPED tables in the graph DB, so
        # they are the agent's private queue ONLY when it owns that DB. Under shared_world hive mode
        # the graph is shared across agents → a shared inbox would let one agent claim + run another
        # agent's task with the wrong executor/approval. Delegation is for isolated-DB appliances
        # (the fleet-migration end-state); fail CLOSED on a shared graph until owner-scoped ledgers
        # exist (P1 non-goal). The michelle↔DJ-AI P1g pair are solo/isolated → unaffected.
        if self.hive.is_shared_world:
            log.warning("[runtime] delegation.enabled but hive=shared_world (graph DB is shared) — "
                        "refusing: the delegation ledgers require an isolated DB. Skipping.")
            return off
        self._deleg_drain_grace = float(dcfg.get("drain_grace", 10.0))
        # A WORKER MUST declare its accept-list + capability classes (codex P1): with no registry,
        # an empty one would reject all NEW types but still CLAIM a task already queued from before a
        # restart, and _gate() reads its now-missing spec as READ_ONLY → a MUTATING task runs without
        # approval. Fail CLOSED: no registry ⇒ no worker role (a requester-only agent still wires).
        has_executor = self._delegation_executor is not None
        if has_executor and self._task_registry is None:
            log.warning("[runtime] delegation worker needs a task_registry (accept-list + capability "
                        "classes) — refusing the worker role (fail-closed; would bypass approval)")
        is_worker = has_executor and self._task_registry is not None
        is_requester = self._channel_for is not None
        if not (is_worker or is_requester):
            log.warning("[runtime] delegation.enabled but nothing wireable (worker needs "
                        "executor+registry; requester needs channel_for) — skipping")
            return off
        try:
            from nmem.agent_core import delegation as dg

            async def _send(channel, kind, body, *, in_reply_to=None):
                # PeerExchange.send(channel, kind, text, **kw) forwards in_reply_to to the exchange.
                return await self._peer.send(channel, kind, body, in_reply_to=in_reply_to)

            if is_worker:
                self._deleg_inbox = dg.DelegationInbox(
                    pool, self._delegation_executor, self._task_registry, send=_send,
                    task_timeout=float(dcfg.get("task_timeout", 900.0)),
                    max_attempts=int(dcfg.get("max_attempts", 3)),
                    retry_backoff=float(dcfg.get("retry_backoff", 1.0)),
                    retry_backoff_max=float(dcfg.get("retry_backoff_max", 300.0)),
                    poll_interval=float(dcfg.get("poll_interval", 1.0)),
                    approve=self._delegation_approve)
            if is_requester:
                self._deleg_client = dg.DelegationClient(
                    pool, send=_send, channel_for=self._channel_for, agent_id=self.agent_id,
                    resend_interval=float(dcfg.get("resend_interval", 30.0)),
                    resend_backoff=float(dcfg.get("resend_backoff", 2.0)),
                    resend_max_interval=float(dcfg.get("resend_max_interval", 600.0)),
                    default_deadline=float(dcfg.get("default_deadline", 3600.0)),
                    poll_interval=float(dcfg.get("client_poll_interval", 5.0)),
                    on_complete=self._on_delegation_complete)
        except Exception as e:  # noqa: BLE001 — wiring must never break startup
            log.warning("[runtime] delegation wiring failed (non-fatal): %s", e, exc_info=True)
            self._deleg_inbox = self._deleg_client = None
            return off

        # Provision BOTH ledgers FIRST (the only awaits → the only failure points), so a partial
        # init is impossible (codex P2): if outbox setup fails after inbox setup, NOTHING has been
        # registered or started yet, so we just drop the components and report OFF — never a live
        # inbox with a dead client, nor a callable delegate() with no result handlers.
        try:
            if self._deleg_inbox is not None:
                await self._deleg_inbox.setup()
            if self._deleg_client is not None:
                await self._deleg_client.setup()
        except Exception as e:  # noqa: BLE001
            log.warning("[runtime] delegation provisioning failed (non-fatal): %s", e, exc_info=True)
            self._deleg_inbox = self._deleg_client = None
            return off
        # GO LIVE — atomically (no await between): start the loops then register the kinds. Provision
        # happened above, so an inbound task.request the instant we register never hits a missing
        # table (run_forever also calls setup(), idempotently).
        # Capture each bound method ONCE — a fresh `x.method` access yields a new wrapper each time
        # (== but not `is`), and teardown detaches by IDENTITY, so register + track must be the SAME
        # object.
        if self._deleg_inbox is not None:
            self._deleg_inbox_task = asyncio.create_task(self._deleg_inbox.run_forever())
            on_request = self._deleg_inbox.on_request
            self._peer.register_kind(dg.KIND_REQUEST, on_request)
            self._deleg_registered[dg.KIND_REQUEST] = on_request
        if self._deleg_client is not None:
            self._deleg_client_task = asyncio.create_task(self._deleg_client.run_forever())
            on_result, on_progress = self._deleg_client.on_result, self._deleg_client.on_progress
            self._peer.register_kind(dg.KIND_RESULT, on_result)
            self._peer.register_kind(dg.KIND_PROGRESS, on_progress)
            self._deleg_registered[dg.KIND_RESULT] = on_result
            self._deleg_registered[dg.KIND_PROGRESS] = on_progress
        log.info("[runtime] delegation wired (worker=%s, requester=%s)", is_worker, is_requester)
        return {"enabled": True, "worker": is_worker, "requester": is_requester}

    @property
    def delegation_client(self):
        """The requester-side client (or None if delegation is off / this agent isn't a requester)."""
        return self._deleg_client

    async def delegate(self, target: str, task_type: str, payload: dict, **kw) -> str:
        """Durably delegate a task to ``target`` (returns the task_id). Requires delegation wired
        as a requester (``channel_for`` injected + ``delegation.enabled``)."""
        if self._deleg_client is None:
            raise RuntimeError("delegation client not wired (delegation.enabled? channel_for supplied?)")
        return await self._deleg_client.delegate(target, task_type, payload, **kw)

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

    async def _pursue_loop(self, pursuit, interval: float, cap: int) -> None:
        if pursuit is not None:
            await pursuit.recover()   # un-strand goals from a prior hard stop (selector-aware, §30.6)
        while True:
            try:
                if pursuit is not None:
                    await pursuit.run_once(cap)
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
