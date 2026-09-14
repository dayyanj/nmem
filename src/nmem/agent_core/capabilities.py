"""Machine-readable capability dependency map + validator.

The single source of truth for which nmem / nmem-sym capability flags require which
others. **Pure data + a pure validator — no nmem/nmem-sym imports** — so the runtime's
fail-fast combo-check and the (future) nmem-studio UI share one map. This is the
machine-readable form of the prose table in ``docs/capability-activation-sweep.md``.

Two kinds of prerequisite:
  * ``requires``  — HARD flag→flag deps, checkable from the flag set alone. If a flag is
                    ON while a required flag is OFF, the capability **silently no-ops** —
                    exactly the failure this map exists to surface.
  * ``substrate`` — non-flag prerequisites (host wiring, an LLM endpoint, data, a running
                    loop, a config value). Advisory only; a flag-set check can't verify it.

Every capability here defaults OFF in the libraries (opt-in), so "enabled" simply means
the flag is set truthy in the environment (``capabilities.env``). That is what keeps this
module dependency-free and lets the same check run in the UI, in a test, or at boot.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

_OFF = {"", "0", "false", "no", "off"}


def _on(val: str | None) -> bool:
    """A flag is ON when set to a non-empty, non-false value (handles bool `true`/`1`
    AND non-bool values like the CSV ``DRIVES_OUTWARD_ACTIONS=explore``)."""
    return (val or "").strip().lower() not in _OFF


@dataclass(frozen=True)
class Capability:
    flag: str                         # canonical env var (what capabilities.env sets / the UI writes)
    group: str = ""                   # UI grouping
    requires: tuple[str, ...] = ()    # hard flag→flag deps (validated)
    substrate: str = ""               # non-flag prerequisites (advisory; not validated)
    summary: str = ""                 # one-line UI help


def _c(flag, group, requires=(), substrate="", summary=""):
    return Capability(flag, group, tuple(requires), substrate, summary)


# ── The map ────────────────────────────────────────────────────────────────────
# Grouped for the UI. Only flag→flag deps go in `requires`; everything a flag-set can't
# see (LLM endpoint, host sink, data, a running loop, a matching id) goes in `substrate`.
#
# CURATION (why not every nmem-sym `*_enabled` field is here): this map is the wizard's user-facing
# surface, and the env model is ENABLE-ONLY (we write the ON flags; a default omitted). So we surface
# the meaningful *default-OFF* capabilities and DELIBERATELY omit:
#   - default-TRUE internal shapes/tuning (the hypothesis shapes abductive/mechanistic/exception/
#     analogical_completion/competition, prediction_novelty_gate, prediction_dreamstate,
#     hypotheses_enabled, trace_persist, retention) — on by construction; the enable-only env can't
#     express "turn this OFF", and they're not decisions a builder should make in the wizard;
#   - nmem-sym's sensory_context_enabled — a TEXT agent pulling ambient sensory context into its
#     prompt; niche, out of scope for the text-agent appliance. Set it by hand if you wire that.
# The EMBODIED perception loop (visual memory: SEE→REMEMBER over a computer-use sandbox) IS declared
# below in the "perception" group — a first-class, wizard-selectable capability for sandbox agents.
_CAPS = [
    # drives
    _c("NMEM_SYM_DRIVES_ENABLED", "drives",
       summary="Intrinsic drives (novelty/coherence/uncertainty/…) that fire actions on pressure."),
    _c("NMEM_SYM_DRIVES_HONEST_DISCHARGE", "drives", requires=["NMEM_SYM_DRIVES_ENABLED"],
       summary="Pressure clears only on real work; outward actions defer relief to the host."),
    _c("NMEM_SYM_DRIVES_OUTWARD_ACTIONS", "drives",
       requires=["NMEM_SYM_DRIVES_ENABLED", "NMEM_SYM_DRIVES_HONEST_DISCHARGE"],
       substrate="host calls bridge.discharge_drive() on the real outcome",
       summary="CSV of drive actions the host actuates outward (e.g. explore)."),
    _c("NMEM_SYM_EMOTION_ENABLED", "drives", requires=["NMEM_SYM_DRIVES_ENABLED"],
       summary="Emotional modulation; ticks alongside the drive loop."),
    # concerns
    _c("NMEM_SYM_CONCERNS_ENABLED", "concerns", requires=["NMEM_SYM_DRIVES_ENABLED"],
       summary="Per-problem (object-bound) pressure instead of a diffuse scalar."),
    _c("NMEM_SYM_CURIOSITY_CONCERNS_ENABLED", "concerns", requires=["NMEM_SYM_CONCERNS_ENABLED"],
       substrate="nmem connected + curiosity signals ≥ CURIOSITY_CONCERN_MIN_COMPOSITE",
       summary="Mirror nmem curiosity signals into concerns."),
    _c("NMEM_SYM_CONCERN_PERSISTENCE_ENABLED", "concerns", requires=["NMEM_SYM_CONCERNS_ENABLED"],
       summary="Persist native concerns across restart (rumination)."),
    # goals
    _c("NMEM_SYM_GOALS_ENABLED", "goals",
       summary="Goal plugin: durable goals, progress, impasse detect/resolve."),
    _c("NMEM_SYM_DRIVES_CREATE_GOALS", "goals",
       requires=["NMEM_SYM_DRIVES_ENABLED", "NMEM_SYM_GOALS_ENABLED"],
       substrate="a targeting concern for a targeted (non-diffuse) goal",
       summary="A fired drive with a concern becomes a drive_intent goal (A5)."),
    _c("NMEM_SYM_DRIVES_GOAL_LLM_ENRICH", "goals", requires=["NMEM_SYM_DRIVES_CREATE_GOALS"],
       substrate="vllm_backends + bridge.set_goal_enrichment_context(objectives/entities/findings)",
       summary="LLM-write concrete, world-directed goal objectives instead of the template."),
    # experiential learning
    _c("NMEM_SYM_FAILURE_MEMORY_ENABLED", "learning",
       substrate="record_action_outcome flowing (the nmem-act actuation loop)",
       summary="Remember failed actions to avoid repeating them."),
    _c("NMEM_SYM_SELF_CAPABILITY_ENABLED", "learning",
       substrate="record_action_outcome flowing", summary="Track what the agent can/can't do."),
    _c("NMEM_SYM_WORLD_MODEL_ENABLED", "learning",
       substrate="record_action_outcome flowing", summary="Learn P(S'|S,A) state transitions."),
    _c("NMEM_SYM_CONSOLIDATION_ENABLED", "learning",
       substrate="episodes present (from the actuation loop)",
       summary="Consolidate episodes into durable structure."),
    _c("NMEM_SYM_UTILITY_PLASTICITY_ENABLED", "learning",
       substrate="record_action_outcome(procedure_ids=…) + procedures to credit",
       summary="Reward the procedures an action used by its real outcome (A2)."),
    _c("NMEM_SYM_STRATEGY_MEMORY_ENABLED", "learning",
       requires=["NMEM_SYM_UTILITY_PLASTICITY_ENABLED"],
       substrate="multi-edge procedures must exist (dormant until they do)",
       summary="Induce reusable strategies from rewarded procedures."),
    # surprise → communication
    _c("NMEM_SYM_OUTCOME_SURPRISE_ENABLED", "comms",
       substrate="record_action_outcome with `source` set (actuation loop)",
       summary="Appraise outcomes vs expectation; emit outcome.surprising."),
    _c("NMEM_SYM_PENDING_UTTERANCES_ENABLED", "comms",
       requires=["NMEM_SYM_OUTCOME_SURPRISE_ENABLED"],
       summary="Turn worth-saying surprises into pending utterances."),
    _c("NMEM_SYM_COMMUNICATION_DRIVE_ENABLED", "comms", requires=["NMEM_SYM_DRIVES_ENABLED"],
       substrate="a host ChannelSink (agent_core.CommsLoop + a peer/channel sink)",
       summary="A drive to communicate worth-saying utterances over a channel."),
    # recall
    _c("NMEM_SYM_RECALL_DRIVE_ENABLED", "recall",
       requires=["NMEM_SYM_DRIVES_ENABLED", "NMEM_SYM_CONCERNS_ENABLED", "NMEM_AUTONOMY__ENABLED"],
       substrate="RECALL_AGENT_ID must equal the agent's id + a memory.surfaced consumer "
                 "(agent_core.recall) + a recall-pressure source (bridge.seed_recall)",
       summary="A drive that asks nmem to proactively surface memory for a target."),
    # graph maintenance
    _c("NMEM_SYM_DREAMSTATE_BRIDGE_HOLES", "graph",
       substrate="dreamstate cycle running + vllm_backends for the LLM judge (else heuristic)",
       summary="Bridge detected structural holes into edges (self-organise the graph)."),
    _c("NMEM_SYM_EXTRACT_AUTOPROMOTE_EDGE_TYPES_ENABLED", "graph",
       substrate="dreamstate cycle running (reuses the proposals/canonical ledger)",
       summary="Promote frequently-proposed edge types into the domain vocabulary."),
    _c("NMEM_SYM_EXTRACT_MULTI_TURN_ENABLED", "graph", substrate="vllm_backends",
       summary="Chunked multi-turn extraction so large source documents aren't truncated."),
    _c("NMEM_SYM_DREAMSTATE_GAIN_BUDGET_ENABLED", "graph",
       substrate="dreamstate cycle running",
       summary="Track each offline op's rolling yield and skip low-return ones (compute self-regulation)."),
    # prediction + hypothesis shapes
    _c("NMEM_SYM_PREDICTION_ENABLED", "prediction",
       summary="Prediction plugin wired into dreamstate."),
    _c("NMEM_SYM_PREDICTION_GROUNDING_LLM_ENABLED", "prediction",
       requires=["NMEM_SYM_PREDICTION_ENABLED"], substrate="vllm_backends",
       summary="LLM-ground predictions."),
    _c("NMEM_SYM_PREDICTION_LLM_REASONING_ENABLED", "prediction",
       requires=["NMEM_SYM_PREDICTION_ENABLED"], substrate="vllm_backends",
       summary="LLM-mediated causal reasoning when generating predictions (deeper, costlier)."),
    _c("NMEM_SYM_HYPOTHESIS_POSTERIOR_ENABLED", "prediction",
       substrate="benefits from graph causal density", summary="Posterior-weighted hypotheses."),
    _c("NMEM_SYM_HYPOTHESIS_COUNTERFACTUAL_ENABLED", "prediction",
       substrate="benefits from graph causal density", summary="Counterfactual hypothesis shape."),
    # bridge plugins (independent; listed for the UI)
    _c("NMEM_SYM_SCHEMAS_ENABLED", "graph", summary="Schema induction plugin."),
    _c("NMEM_SYM_ANALOGY_ENABLED", "graph", summary="Analogy plugin."),
    _c("NMEM_SYM_SELF_MODEL_ENABLED", "graph", summary="Self-model plugin."),
    _c("NMEM_SYM_PROCEDURES_ENABLED", "graph", summary="Procedural (compiled-skill) plugin."),
    _c("NMEM_SYM_TEMPORAL_AWARENESS_ENABLED", "graph", summary="Temporal awareness (silence/overdue/staleness)."),
    # obligations (extrinsic motivation)
    _c("NMEM_SYM_OBLIGATIONS_ENABLED", "obligations", requires=["NMEM_SYM_DRIVES_ENABLED"],
       substrate="an external delegator/obligation source (a hive/host that imposes them)",
       summary="Deadline-driven commitments to external requestors."),
    _c("NMEM_SYM_OBLIGATION_PERSISTENCE_ENABLED", "obligations",
       requires=["NMEM_SYM_OBLIGATIONS_ENABLED"], summary="Persist obligations + requestors across restart."),
    # nmem meta layer (nested settings; `__` delimiter)
    _c("NMEM_AUTONOMY__ENABLED", "meta",
       summary="Autonomous memorize/retrieve layer (proactive surface + optional skill capture)."),
    _c("NMEM_AUTONOMY__PROACTIVE_RETRIEVE", "meta", requires=["NMEM_AUTONOMY__ENABLED"],
       summary="Surface relevant memory proactively on journal writes."),
    _c("NMEM_AUTONOMY__AUTO_CAPTURE_SKILLS", "meta", requires=["NMEM_AUTONOMY__ENABLED"],
       substrate="journal entry_types the agent actually produces",
       summary="Auto-capture skills from qualifying journal entries."),
    _c("NMEM_SELF_ENGINEERING__ENABLED", "meta",
       summary="Distil reliable skills into advisory context recipes (never touches code)."),
    _c("NMEM_SELF_ENGINEERING__INCLUDE_IN_PROMPT", "meta",
       requires=["NMEM_SELF_ENGINEERING__ENABLED"], summary="Inject matching recipes into assembled context."),
    _c("NMEM_SELF_ENGINEERING__PROPOSE_SUBAGENTS", "meta",
       requires=["NMEM_SELF_ENGINEERING__ENABLED"], substrate="a host that can spawn sub-agents",
       summary="Propose sub-agent specs from proven skills."),
    _c("NMEM_COMMITMENT_DETECTION__ENABLED", "meta",
       substrate="commitment-language journal entries + the nightly consolidation path",
       summary="Detect commitments in journal content and impose them as obligations."),
    # perception — embodied visual memory (agent_core.build_visual_memory over a sandbox)
    _c("NMEM_VISUAL_MEMORY_ENABLED", "perception",
       substrate="a computer-use sandbox (ctx.state['sandbox']) + a sensory Postgres "
                 "(NMEM_SENSOR_DB_DSN, pgvector) + nmem-sym-sensor[visual,inference] installed; "
                 "SensorGraph.connect() self-applies the sensory migrations",
       summary="SEE→REMEMBER: store sandbox keyframes as sensory memories (visual episodic memory)."),
    _c("NMEM_VISUAL_READBACK_ENABLED", "perception", requires=["NMEM_VISUAL_MEMORY_ENABLED"],
       substrate="accumulated screen↔pursuit-outcome links to match against",
       summary="Warn on revisiting a screen a prior pursuit failed on (dhash read-back)."),
]

CAPABILITIES: dict[str, Capability] = {c.flag: c for c in _CAPS}


@dataclass(frozen=True)
class Issue:
    flag: str
    missing: tuple[str, ...]

    @property
    def message(self) -> str:
        reqs = ", ".join(self.missing)
        return (f"{self.flag} is ON but its dependency {'flags are' if len(self.missing) > 1 else 'flag is'} "
                f"OFF ({reqs}) — {self.flag} will silently no-op. Enable {reqs}, or disable {self.flag}.")


# ── API (shared by the runtime check and the studio UI) ─────────────────────────
def enabled_flags(env=None) -> set[str]:
    """The set of KNOWN capability flags that are ON in `env` (default os.environ)."""
    e = os.environ if env is None else env
    return {flag for flag in CAPABILITIES if _on(e.get(flag))}


def validate(enabled: set[str]) -> list[Issue]:
    """Hard flag→flag check: for every enabled capability, report any `requires` flag that
    is OFF. Returns [] when the enabled set is dependency-complete. Pure — no I/O."""
    issues = []
    for flag in enabled:
        cap = CAPABILITIES.get(flag)
        if cap is None:
            continue
        missing = tuple(r for r in cap.requires if r not in enabled)
        if missing:
            issues.append(Issue(flag, missing))
    return issues


def check_env(env=None) -> list[Issue]:
    """Convenience: validate whatever is enabled in the environment."""
    return validate(enabled_flags(env))


def by_group() -> dict[str, list[Capability]]:
    """Capabilities grouped for the UI, preserving declaration order."""
    groups: dict[str, list[Capability]] = {}
    for c in _CAPS:
        groups.setdefault(c.group, []).append(c)
    return groups


def requires_closure(flag: str) -> set[str]:
    """Transitive set of flags `flag` depends on (for the UI to auto-enable a bundle)."""
    seen: set[str] = set()
    stack = list(CAPABILITIES.get(flag, Capability(flag)).requires)
    while stack:
        f = stack.pop()
        if f in seen:
            continue
        seen.add(f)
        stack.extend(CAPABILITIES.get(f, Capability(f)).requires)
    return seen


# ── Presets (the studio's "basic" view — dependency-complete flag bundles) ───────
PRESETS: dict[str, dict] = {
    "memory": {
        "label": "Memory",
        "blurb": "A remembering agent: tiered memory + consolidation. No drives or goals.",
        "flags": ["NMEM_SYM_CONSOLIDATION_ENABLED"],
    },
    "reflective": {
        "label": "Reflective",
        "blurb": "Adds learning: skills, proactive recall, and self-engineering recipes.",
        "flags": ["NMEM_SYM_CONSOLIDATION_ENABLED", "NMEM_AUTONOMY__ENABLED",
                  "NMEM_AUTONOMY__PROACTIVE_RETRIEVE", "NMEM_SELF_ENGINEERING__ENABLED",
                  "NMEM_SELF_ENGINEERING__INCLUDE_IN_PROMPT"],
    },
    "full_cognition": {
        "label": "Full cognition",
        "blurb": "The whole mind: drives, concerns, goals, pursuit, dreamstate, prediction, recall.",
        "flags": ["NMEM_SYM_DRIVES_ENABLED", "NMEM_SYM_DRIVES_HONEST_DISCHARGE",
                  "NMEM_SYM_CONCERNS_ENABLED", "NMEM_SYM_GOALS_ENABLED",
                  "NMEM_SYM_DRIVES_CREATE_GOALS", "NMEM_SYM_DRIVES_GOAL_LLM_ENRICH",
                  "NMEM_SYM_SCHEMAS_ENABLED", "NMEM_SYM_ANALOGY_ENABLED",
                  "NMEM_SYM_SELF_MODEL_ENABLED", "NMEM_SYM_PROCEDURES_ENABLED",
                  "NMEM_SYM_PREDICTION_ENABLED", "NMEM_SYM_CONSOLIDATION_ENABLED",
                  "NMEM_AUTONOMY__ENABLED", "NMEM_SYM_RECALL_DRIVE_ENABLED",
                  "NMEM_SELF_ENGINEERING__ENABLED", "NMEM_SELF_ENGINEERING__INCLUDE_IN_PROMPT"],
    },
    "perception": {
        "label": "Perception (embodied)",
        "blurb": "Visual memory over a computer-use sandbox: remember screens seen, warn on "
                 "revisiting a screen a past pursuit failed on. Needs a sandbox + a sensory DB.",
        "flags": ["NMEM_VISUAL_MEMORY_ENABLED", "NMEM_VISUAL_READBACK_ENABLED"],
    },
}


def preset_flags(name: str) -> set[str]:
    """A preset's flags expanded to include every transitive dependency (so a preset is
    always dependency-complete). Empty for an unknown preset."""
    base = set(PRESETS.get(name, {}).get("flags", ()))
    for f in list(base):
        base |= requires_closure(f)
    return base


# ── Enriched catalog for the UI (schema-introspected; keeps pills from drifting) ─
def catalog(*, env=None) -> list[dict]:
    """The capability list the studio renders: each map entry enriched with the LIVE
    pydantic ``Field(description=…, default=…)`` from the settings classes, plus the
    current enabled-state from ``env``. Enrichment is best-effort (lazy imports);
    falls back to the map's own ``summary`` + default-off. Import stays cheap unless
    this is called."""
    meta = _field_meta()
    on = enabled_flags(env)
    out = []
    for c in _CAPS:
        desc, default = meta.get(c.flag, ("", False))
        out.append({
            "flag": c.flag,
            "group": c.group,
            "summary": desc or c.summary,          # prefer the authoritative schema description
            "requires": list(c.requires),
            "substrate": c.substrate,
            "default": bool(default),
            "enabled": c.flag in on,
        })
    return out


def _field_meta() -> dict:
    """{env_var: (description, default)} introspected from the pydantic settings classes.
    nmem-sym fields are flat (NMEM_SYM_<NAME>); nmem sections are nested (NMEM_<SEC>__<FIELD>)."""
    meta: dict = {}

    def _default(fi):
        d = getattr(fi, "default", None)
        # pydantic uses a sentinel for "no default"; treat non-bools/sentinels as off
        return d if isinstance(d, bool) else False

    try:
        from nmem_sym import config as sym_config
        for name, fi in type(sym_config.settings).model_fields.items():
            meta["NMEM_SYM_" + name.upper()] = (getattr(fi, "description", "") or "", _default(fi))
    except Exception:  # noqa: BLE001
        pass
    try:
        from nmem.config import NmemConfig
        for sec, sfi in NmemConfig.model_fields.items():
            sub = getattr(sfi, "annotation", None)
            if hasattr(sub, "model_fields"):
                for fname, ffi in sub.model_fields.items():
                    meta["NMEM_" + sec.upper() + "__" + fname.upper()] = (
                        getattr(ffi, "description", "") or "", _default(ffi))
    except Exception:  # noqa: BLE001
        pass
    return meta
