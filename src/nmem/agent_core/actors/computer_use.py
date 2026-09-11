"""Computer-use actuator — a host-declared actor for agent_core.

A general computer-use SANDBOX (a VLM-driven desktop in a separate container): the agent POSTs an
NL objective and POLLS the session — it never runs the loop itself. This is the agent's *hands*
for SEEKING KNOWLEDGE (reading live sources, verifying how something actually behaves) rather than
confabulating an unknown.

Packaging (design: nmem-sym ``docs/goal-planning-design.md`` §33): this is a HOST-DECLARED actor,
**not** a built-in. ``httpx`` is imported LAZILY inside :class:`SandboxClient`, so a vanilla
``nmem`` install that never instantiates the client pulls in nothing extra — install the
``computer-use`` extra (``pip install nmem[computer-use]``) to use it.

Dispatch (§33.3): this module ships the **direct path** — the host builds
:func:`build_research_action` into its own ``ReferenceRunner`` / ``GoalPursuit``, where the runner
ENFORCES the attached ``Verifier`` (so the handler cannot self-certify). The selector
(``ToolCallingExecutor``) path is DEFERRED until a composite-evidence rule exists (a selector
``Done(True)`` must not convert a verifier-rejected result into proof), so there is deliberately
**no** ``assemble_registry`` branch here — that would make it selector-visible in the generic path.

Surface:
  * :class:`SandboxClient` — an INSTANCE (not a module global): config → HTTP session lifecycle.
  * :func:`drive_sandbox` — the poll loop → ``(status, note, steps)``; ``enabled`` is a real gate.
  * :func:`build_research_action` — the RESEARCH ``Action`` (intrinsic structural ``Verifier``,
    ``min_chars=16``; optional host ``judge`` for Layer-2). NOT "all-purpose computer use": a
    generic desktop action whose success is "opened settings" would use a different builder.
  * :func:`register_computer_use_capability` — the catch-all capability descriptor for the
    feasibility planner; when the client is disabled it supersedes the descriptor UNAVAILABLE.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)

# Defaults mirror the host sandbox config; `computer_use_max_per_day` is a runaway ceiling, not yet
# a gate (a pre-existing non-enforcement carried over verbatim — see §33 P2).
_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "url": "http://localhost:8080",
    "request_timeout": 15.0,
    "max_steps": 20,
    "wall_clock_s": 300.0,
    "computer_use_max_per_day": 20,
}

_RESEARCH_MIN_CHARS = 16   # a knowledge finding is a few words minimum; reject trivial "?"/one-token
_RESEARCH_TIMEOUT_S = 450.0


class SandboxClient:
    """HTTP client for the computer-use sandbox. An INSTANCE, captured by the action/registrar that
    use it — so two runtimes in one process (different sandboxes) never clobber each other's config.
    Config-gated (``enabled`` kill-switch) and fail-open: a sandbox outage must never crash a cycle.
    """

    def __init__(self, config: dict | None = None) -> None:
        self._cfg = dict(_DEFAULTS)
        if config:
            self._cfg.update(config)

    def configure(self, config: dict | None) -> None:
        if config:
            self._cfg.update(config)
        log.info("[computer_use] configured: url=%s enabled=%s",
                 self._cfg.get("url"), self._cfg.get("enabled"))

    def is_enabled(self) -> bool:
        return bool(self._cfg.get("enabled"))

    def cfg(self, key: str, default: Any = None) -> Any:
        return self._cfg.get(key, default)

    @staticmethod
    def _httpx():
        """Lazy import so the dep is optional (host-declared actor). Clear error if missing."""
        try:
            import httpx
        except ModuleNotFoundError as e:  # pragma: no cover - packaging guard
            raise RuntimeError(
                "the computer-use actor needs httpx — install `pip install nmem[computer-use]`") from e
        return httpx

    async def start_session(self, task: str, mode: str = "read_only",
                            max_steps: int | None = None) -> str | None:
        """Kick off a sandbox session. Returns session_id, or None on failure/disabled/busy (409)."""
        if not self.is_enabled():
            return None   # gate (defence-in-depth; drive_sandbox classifies disabled as infra first)
        try:
            httpx = self._httpx()   # inside try: a missing optional dep degrades to infra, never burns the goal
            payload = {"task": task, "mode": mode,
                       "max_steps": max_steps or self._cfg.get("max_steps", 20),
                       "wall_clock_s": self._cfg.get("wall_clock_s", 300.0)}
            async with httpx.AsyncClient(timeout=self._cfg.get("request_timeout", 15.0)) as client:
                r = await client.post(f"{self._cfg['url']}/session", json=payload)
                if r.status_code == 409:
                    log.info("[computer_use] busy — a session is already running")
                    return None
                r.raise_for_status()
                return r.json().get("session_id")
        except Exception as e:  # noqa: BLE001
            log.warning("[computer_use] start_session failed: %s", e)
            return None

    async def get_session(self, session_id: str) -> dict | None:
        try:
            httpx = self._httpx()
            async with httpx.AsyncClient(timeout=self._cfg.get("request_timeout", 15.0)) as client:
                r = await client.get(f"{self._cfg['url']}/session/{session_id}")
                r.raise_for_status()
                return r.json()
        except Exception as e:  # noqa: BLE001
            log.debug("[computer_use] get_session failed: %s", e)
            return None

    async def abort_session(self, session_id: str) -> bool:
        """Kill-switch a running session (best-effort) so it doesn't run orphaned and hold the one
        sandbox slot. Returns True on 2xx."""
        if not session_id:
            return False
        try:
            httpx = self._httpx()
            async with httpx.AsyncClient(timeout=self._cfg.get("request_timeout", 15.0)) as client:
                r = await client.post(f"{self._cfg['url']}/session/{session_id}/abort")
                r.raise_for_status()
                return True
        except Exception as e:  # noqa: BLE001
            log.debug("[computer_use] abort_session %s failed: %s", session_id, e)
            return False

    async def aclose(self) -> None:
        """No persistent connection (per-call AsyncClient), so nothing to close — present for the
        single-owner lifecycle contract (§33) so a host can treat the client uniformly."""
        return None


async def drive_sandbox(client: SandboxClient, task: str,
                        *, timeout_s: float = _RESEARCH_TIMEOUT_S) -> tuple[str, str, list]:
    """Drive the sandbox to completion for one objective.

    Returns ``(status, note, steps)`` where status is ``'achieved'`` (done), ``'failed'`` (a
    COMPLETED session that didn't succeed — max_steps/error), or ``'infra'`` (the session never
    really ran: disabled/busy/unavailable/stuck) so the caller can RETRY the goal instead of
    burning it. ``enabled`` is a real gate: disabled → ``infra`` BEFORE any dispatch.
    """
    if not client.is_enabled():
        return "infra", "computer-use disabled", []
    sid = await client.start_session(task, mode="read_only")
    if not sid:
        return "infra", "sandbox busy/unavailable", []
    polls = max(1, int(timeout_s // 6))
    try:
        for _ in range(polls):
            await asyncio.sleep(6)
            s = await client.get_session(sid)
            if s and s.get("status") not in ("queued", "running"):
                st = s.get("status")
                res = (s.get("result") or "")[:500]
                return ("achieved" if st == "done" else "failed"), f"[{st}] {res}", (s.get("steps") or [])
    except asyncio.CancelledError:
        # Shutdown mid-pursuit — kill the desktop session so it doesn't run orphaned.
        await client.abort_session(sid)
        raise
    # Stopped waiting but the session is likely still running: abort so it doesn't hold the slot.
    await client.abort_session(sid)
    return "infra", "timeout waiting for sandbox", []


def build_research_action(client: SandboxClient, *, judge: Any = None,
                          action_name: str = "pursue_knowledge",
                          min_chars: int = _RESEARCH_MIN_CHARS,
                          timeout_s: float = _RESEARCH_TIMEOUT_S,
                          capability_class: Any = None):
    """The knowledge-seeking computer-use Action. Drives the sandbox for one objective and returns a
    RAW :class:`ActionResult` — it does NOT self-certify: a structural :class:`Verifier`
    (``min_chars``) is attached INTRINSICALLY and ENFORCED by the runner (``ReferenceRunner._apply_
    verifier``) after the handler, before any sink. An optional host ``judge`` adds the bounded
    single-judge Layer-2 (§32). The result contract here is tuned for RESEARCH (a substantive
    finding); a generic desktop action whose success is "opened settings" would use a different
    builder + verifier policy (§33.2)."""
    from nmem_act import Action, ActionResult, CapabilityClass, Verifier

    async def _handler(params: dict) -> ActionResult:
        obj = params.get("objective", "")
        lessons = params.get("lessons", "")
        task = (lessons + "\n\n" if lessons else "") + (
            f"Seek knowledge to resolve this: {obj}. Read live sources, verify what's actually "
            f"true, and report concisely what you found. Do not guess — if you can't verify, say so.")
        status, note, steps = await drive_sandbox(client, task, timeout_s=timeout_s)
        # RAW observations (no `verified`): the runner's Verifier stamps verified/sandbox_status/
        # verification_status. `infra` is preserved so GoalPursuit retries rather than burning the
        # goal. `success` here is provisional — the runner overrides it with the verdict.
        return ActionResult(
            success=(status == "achieved"),
            outcome=note,
            observations={"status": status, "steps": steps, "infra": status == "infra",
                          "objective": obj, "goal_id": params.get("goal_id"),
                          "procedure_ids": params.get("procedure_ids") or []},
            task_success=1.0 if status == "achieved" else 0.0,
            info_gain=1.0 if status == "achieved" else 0.0,
        )

    return Action(
        name=action_name, handler=_handler,
        capability_class=capability_class or CapabilityClass.READ_ONLY,
        description="Drive the computer-use sandbox to seek and verify knowledge for a goal.",
        parameters={"type": "object",
                    "properties": {"objective": {"type": "string"}},
                    "required": ["objective"]},
        verifier=Verifier(judge=judge, min_chars=min_chars))


def build_research_runner(*, mem, backend, agent_id: str, bridge, client: "SandboxClient",
                          action_name: str = "pursue_knowledge", tool_tag: str | None = None,
                          verify_judge: bool = False, reflect_enabled: bool = True,
                          gate=None, cap: int = 4):
    """Assemble the DIRECT knowledge-seeking executor: a ``ReferenceRunner`` over the research
    :class:`~nmem_act.Action` (:func:`build_research_action`) wrapped with the experiential +
    reflective outcome sink. This is the §33.3 **direct path** — the runner ENFORCES the action's
    intrinsic ``Verifier`` after the handler (so the actuator cannot self-certify), which is why
    computer-use uses this rather than the selector ``ToolCallingExecutor``. Returns an object
    implementing the nmem-act executor protocol (``execute(proposal) -> Outcome``), ready to drop
    into ``AgentRuntime(build_executor=…)`` / ``GoalPursuit``.

    Every input is a PRIMITIVE (``mem`` / ``backend`` / ``agent_id`` / ``bridge`` / ``client``), so
    this stays host-shape-agnostic — a thin host reads them off its own ctx, the studio appliance
    off ``ctx``/``ctx.runtime``. ``tool_tag`` scopes reflective skill capture to the agent's
    actuator namespace (pre-action lesson recall keys on the SAME tag). ``verify_judge`` adds the
    bounded Layer-2 strict judge (default OFF — needs held-out calibration first); ``reflect_enabled``
    (default ON) captures ``[DO]``/``[AVOID]`` tool-use skills from the sandbox step trace. Nothing
    here is a new mechanism — it is the assembly of pieces that already live in nmem-act + agent_core,
    graduated verbatim from the reference agent's ``service/actuation.build_runner``.

    ``gate`` is the nmem-act ``AutonomyGate`` the runner enforces before dispatch (None →
    ReferenceRunner's read-only default, which permits the read-only research action); a host that
    exposes an autonomy policy passes its own gate so an explicitly denied action is BLOCKED."""
    from nmem_act import ActionRegistry, ReferenceRunner, make_reflective_sink, make_strict_judge

    from nmem.agent_core import build_experiential_sink

    # Layer-2 judge is generic (nmem-act.make_strict_judge); the host only injects its backend's chat.
    judge = make_strict_judge(backend.chat) if verify_judge else None
    reg = ActionRegistry()
    reg.register(build_research_action(client, judge=judge, action_name=action_name))
    log.info("[computer_use] research action %r registered (READ_ONLY, structural verify intrinsic%s)",
             action_name, " + judge" if judge else "")

    async def _reflect(messages):
        return await backend.chat(messages, max_tokens=320)

    async def _record_skill(what, **kw):
        return await mem.skills.record(what, **kw)

    # The experiential loop (episode + procedure reward + honest discharge + merit finding memory)
    # is the graduated agent_core sink; nmem-act's make_reflective_sink wraps it to reflect on the
    # sandbox step trace and capture tool-use skills (reflect LLM + skill writer injected here).
    sink = make_reflective_sink(
        build_experiential_sink(bridge, mem, agent_id),
        reflect=_reflect, record_skill=_record_skill,
        agent_id=agent_id, enabled=reflect_enabled, cap=cap, tool_tag=tool_tag or "")
    runner = ReferenceRunner(reg, outcome_sink=sink, gate=gate)
    log.info("[computer_use] research runner built (action=%s, reflect=%s, gated=%s)",
             action_name, reflect_enabled, gate is not None)
    return runner


async def register_computer_use_capability(bridge, *, goal_scope: str | None,
                                           client: SandboxClient,
                                           action_name: str = "pursue_knowledge") -> str:
    """Declare the computer-use sandbox to the nmem-sym capability registry as a CATCH-ALL knowledge
    actuator, so the feasibility planner can bind eligible (decomposition) goals to it.

    Scope (§33.5): ``goal_scope`` is the runtime's hive agent_id (``None`` for an isolated agent
    whose goals are ``owner_agent=NULL``) — NOT the memory/display identity; if they diverge,
    ``live_descriptors`` + the feasibility fingerprint can't resolve the actuator.

    Disabled ≠ skip (§33.2): a descriptor from an earlier enabled boot stays ``available`` in the
    PERSISTENT registry and the planner keeps binding goals to a dead sandbox. So register
    ``availability='available'`` when enabled and ``'unavailable'`` when disabled — ``availability``
    is part of the descriptor hash, so flipping it supersedes the stale row. Returns the availability
    written. Fail-open (additive); the caller logs."""
    avail = "available" if client.is_enabled() else "unavailable"
    await bridge.register_actuators([{
        "name": action_name,
        "action_schema": {"type": "object",
                          "properties": {"objective": {"type": "string"}},
                          "required": ["objective"]},
        "catch_all": True,
        "parameters": {"accepts_source_types": ["decomposition"]},
        "authority": "read-only web/desktop knowledge-seeking via the computer-use sandbox",
        "outcome_verifier": "finding recorded (non-finding → honest failure)",
        "approval_required": False,
        "availability": avail,
    }], agent_id=goal_scope)
    return avail
