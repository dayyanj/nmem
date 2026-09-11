"""The experiential outcome sink — close the act -> learn loop for a pursuit outcome.

This is the crown jewel of an acting agent, and it is entirely generic: given a
completed action's ``(proposal, outcome)``, it (1) records the nmem-sym EPISODE +
procedure reward + surprise appraisal, (2) honestly DISCHARGES the drive that pressed
for the action (verified outcomes only), and (3) writes a MERIT-based finding memory
whose importance emerges from grounding + use (never a hardcoded number). Only the
*executor* that produces the observations is agent-specific — so this sink graduates
verbatim from the reference agent's ``actuation._make_sink`` and any acting agent reuses it.

Reads the pursuit observation contract (same one nmem-act's ``GoalPursuit`` reads):
``infra`` (actuator never ran -> nothing to learn), ``verified`` (stand-behind result),
plus ``goal_id`` / ``objective`` / ``status`` / ``procedure_ids`` the executor sets.

Compose with nmem-act's ``make_reflective_sink`` (reflect on the step trace -> skills)
for the full learning sink; that wrap stays with the agent since its reflect-LLM +
skill-writer are injected. See the reference agent's ``service/actuation.build_runner``.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def build_experiential_sink(bridge, mem, agent_id: str):
    """Return an nmem-act ``OutcomeSink`` closing the experiential loop into ``bridge``
    + ``mem`` for ``agent_id``. No-op on an infra outcome (the actuator never ran)."""

    async def sink(proposal, outcome) -> None:
        obs = outcome.observations or {}
        if obs.get("infra"):
            return  # actuator never ran; caller unclaims + retries. Nothing to learn.
        gid = obs.get("goal_id")
        obj = obs.get("objective", "")
        note = outcome.actual_outcome or ""
        status = obs.get("status", "")
        # Single source of truth — the executor classified this once (verified/infra).
        verified = bool(obs.get("verified"))

        # 1. experiential loop -> nmem-sym: episode + procedure reward + surprise appraisal.
        # (record_action_outcome does NOT discharge drives — that is step 2.)
        try:
            await bridge.record_action_outcome(
                proposal_id=proposal.id, status="success" if verified else "failure",
                action_type=proposal.action_type, source=proposal.source, goal_id=gid,
                outcome_strength=1.0 if verified else 0.0,
                utility={"task_success": 1.0 if verified else 0.0}, actual_outcome=note[:2000],
                # A2 utility-plasticity: reward the procedures the executor recalled/used
                # (no-op unless NMEM_SYM_UTILITY_PLASTICITY_ENABLED).
                procedure_ids=obs.get("procedure_ids") or [])
        except Exception:  # noqa: BLE001
            log.warning("[experiential-sink] record_action_outcome failed", exc_info=True)

        # 1b. Phase 1 credit assignment: stamp this graded outcome onto the surfacing-ledger
        # row(s) for the turn — keyed by the turn_id the agent_core surfacing wrapper stamped
        # into proposal.params — so the offline dreamstate credit step reinforces/depresses the
        # exact edges that were surfaced. Generic: every acting agent using this sink inherits
        # it. No-op unless NMEM_SYM_SURFACING_LEDGER_ENABLED. Best-effort.
        try:
            turn_id = (getattr(proposal, "params", None) or {}).get("turn_id")
            if turn_id and hasattr(bridge, "record_surfacing_outcome"):
                await bridge.record_surfacing_outcome(
                    turn_id=turn_id, goal_id=gid,
                    outcome=1.0 if verified else -1.0, source="goal",
                    produced_answer=note[:4000])
        except Exception:  # noqa: BLE001
            log.warning("[experiential-sink] record_surfacing_outcome failed", exc_info=True)

        # 2. honest discharge -> nmem-sym: this action IS the real outward work the drive
        # pressed for, so report it via discharge_drive (record_action_outcome only
        # appraises surprise). With the drive in NMEM_SYM_DRIVES_OUTWARD_ACTIONS the
        # internal handler retains the pressure, so this is the ONLY real relief — a
        # non-finding (verified False) discharges nothing and the need re-fires. Keyed on
        # the proposal's "drive:<name>" source.
        if verified and (proposal.source or "").startswith("drive:"):
            try:
                await bridge.discharge_drive(proposal.source.split(":", 1)[1], outcome_strength=1.0)
            except Exception:  # noqa: BLE001
                log.warning("[experiential-sink] discharge_drive failed", exc_info=True)

        # 3. merit-based finding memory (importance emerges from record_type/grounding + use).
        # §32.7 P2-1: journal the CLASSIFIED verification_status (honest), not the raw actuator
        # status — else a verifier-rejected finding reads "Outcome (achieved):" to recall/audit
        # consumers. Falls back to the raw status when no verifier ran (byte-identical).
        vstatus = obs.get("verification_status") or status
        vreason = obs.get("verification_reason")
        try:
            await mem.journal.add(
                agent_id=agent_id, entry_type="pursuit",
                title=f"pursued goal #{gid}",
                content=(f"{obj}\n\nOutcome ({vstatus}): {note}"
                         + (f"\n[verify: {vreason}]" if vreason else "")),
                importance=None, record_type="fact" if verified else "observation",
                grounding="confirmed" if verified else "inferred")
        except Exception:  # noqa: BLE001
            pass

        # 4. continuity: pursuing a goal is an ACTION — record it as "where I left off" so a
        # wake snapshot reflects what the agent last DID autonomously, not only what it was
        # asked. (Generic: every acting agent that uses this sink inherits it.)
        try:
            from nmem.agent_core.continuity import record_action_checkpoint
            verb = "pursued" if verified else "attempted"
            await record_action_checkpoint(
                mem, agent_id, f"{verb}: {obj}" + (f" — {note[:160]}" if note else ""))
        except Exception:  # noqa: BLE001
            pass
        # NB: tool-use skill capture is not here — wrap this sink with nmem-act's
        # make_reflective_sink (reflect on observations["steps"] -> skills) for that.

    return sink
