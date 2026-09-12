"""Continuity / wake-snapshot assembly.

Continuity is a *projection across* the existing tiers + the nmem-sym seam, not a
seventh storage tier. This module holds the pure, DB-free logic — salience
ranking, the unified open-loop merge, and token-budgeted section assembly — so it
can be unit-tested without a database. ``MemorySystem.wake()`` does the async
gather and hands the results here.

Two ideas do the load-bearing work:

* **Unified open loops** — commitments (prospective obligations) and curiosity
  signals (epistemic gaps) are ranked on one salience scale. The most pressing
  unresolved threads surface regardless of kind, with a hard ``k`` so the wake
  state stays a snapshot, not a graveyard of everything ever half-finished.
* **Drive state as prose, never numbers** — internal state arrives from the sym
  seam already rendered as a consequence; this module only places it.

The snapshot is assembled fresh every call (nothing here caches) and is present
even with an empty query — that is what makes continuity *living* rather than a
post-dreamstate refresh.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Sequence

from nmem.types import ContinuityResult, OpenLoop, SymContinuityInputs

if TYPE_CHECKING:  # avoid runtime import cycles; the functions are duck-typed
    from nmem.commitments import CommitmentInfo
    from nmem.types import CuriositySignalInfo


# Priority order of lanes in the snapshot (design: identity → trajectory →
# commitments/loops → goals → self-model → internal state → relevant).
_SECTION_ORDER = (
    "identity",
    "narrative",
    "gap",          # "returning after a gap" reorientation frame (only on a long gap)
    "immediate",
    "delta",        # what changed while away (only on a long gap)
    "warnings",
    "recent",
    "open_loops",
    "goals",
    "self_model",
    "internal_state",
    "relational_self",   # "who I am to my world", situation-conditioned (sym seam)
    "relevant",
)

# A gap since the agent's last turn longer than this flips the snapshot into
# "returning after a gap" mode: the immediate state is age-qualified (it is no longer
# safely "current"), a reorientation frame is added, and the delta lane is populated.
_LONG_GAP_SECONDS = 12 * 3600


def _age_phrase(seconds: float) -> str:
    """A compact human age for an elapsed duration ("~3 days", "~5 hours", "~20 minutes")."""
    s = max(0.0, seconds)
    if s >= 86400:
        n = round(s / 86400)
        return f"~{n} day{'s' if n != 1 else ''}"
    if s >= 3600:
        n = round(s / 3600)
        return f"~{n} hour{'s' if n != 1 else ''}"
    n = max(1, round(s / 60))
    return f"~{n} minute{'s' if n != 1 else ''}"

# Commitments are prospective obligations to others; even a low-"importance" one
# should not sink below curiosity noise. Floor its salience so it stays visible.
_COMMITMENT_SALIENCE_FLOOR = 0.4

_WARNING_KEYWORDS = frozenset(
    {"never", "do not", "avoid", "warning", "critical", "must not", "forbidden"}
)


def _as_utc(dt: datetime | None) -> datetime | None:
    """Coerce a possibly-naive datetime to tz-aware UTC (mixed tz-awareness in
    the DB layer would otherwise raise on subtraction)."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def _deadline_urgency(deadline: datetime | None, now: datetime) -> float:
    """Deadline pressure on 0..1: overdue > imminent > soon > distant > none."""
    if deadline is None:
        return 0.3  # a standing obligation with no deadline still has some pull
    hours = (deadline - now).total_seconds() / 3600.0
    if hours < 0:
        return 1.0  # overdue — most pressing
    if hours <= 48:
        return 0.8
    if hours <= 168:
        return 0.5
    return 0.2


def commitment_salience(commitment: Any, now: datetime) -> float:
    """Salience of an open commitment on 0..1.

    A blend of importance (the model's native 0..1 scale — see the commitment
    detector's ``"importance": number 0..1``) and deadline urgency, so both
    dimensions move the ranking and neither saturates the other: a max-importance
    obligation still sorts by how soon it is due. Floored so a prospective
    obligation never sinks below curiosity noise.
    """
    imp = _clamp01(getattr(commitment, "importance", 1.0))
    urgency = _deadline_urgency(_as_utc(getattr(commitment, "deadline", None)), now)
    return _clamp01(max(_COMMITMENT_SALIENCE_FLOOR, 0.5 * imp + 0.5 * urgency))


def _render_commitment(commitment: Any, now: datetime) -> str:
    desc = (getattr(commitment, "description", "") or "").strip()
    requester = getattr(commitment, "requester", None)
    parts = [f"[commitment] {desc}"]
    if requester:
        parts.append(f"(to {requester})")
    deadline = _as_utc(getattr(commitment, "deadline", None))
    if deadline is not None:
        overdue = deadline < now
        parts.append(
            f"— {'OVERDUE' if overdue else 'due'} {deadline.strftime('%Y-%m-%d')}"
        )
    return " ".join(parts)


def _render_curiosity(signal: Any) -> str:
    trigger = (getattr(signal, "trigger_type", "") or "open").strip()
    summary = (getattr(signal, "summary", "") or "").strip()
    return f"[{trigger}] {summary}"


def merge_open_loops(
    commitments: Sequence[Any],
    curiosity: Sequence[Any],
    now: datetime,
    k: int,
) -> tuple[list[OpenLoop], int]:
    """Merge commitments + curiosity into one salience-ranked list.

    Returns ``(top_k, total)`` — the top-k loops for the wake state and the total
    available before the cut, so callers can report what was dropped rather than
    silently truncating.
    """
    loops: list[OpenLoop] = []
    for c in commitments:
        loops.append(
            OpenLoop(
                kind="commitment",
                salience=commitment_salience(c, now),
                text=_render_commitment(c, now),
                due=_as_utc(getattr(c, "deadline", None)),
            )
        )
    for s in curiosity:
        loops.append(
            OpenLoop(
                kind="curiosity",
                salience=_clamp01(float(getattr(s, "composite_score", 0.0) or 0.0)),
                text=_render_curiosity(s),
                due=None,
            )
        )
    # Stable, deterministic order: salience desc, then commitments before
    # curiosity at a tie (prospective obligations first), then text.
    loops.sort(key=lambda l: (-l.salience, 0 if l.kind == "commitment" else 1, l.text))
    total = len(loops)
    if k >= 0:
        loops = loops[:k]
    return loops, total


def _is_warning(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in _WARNING_KEYWORDS)


def applicable_policies(policies: Sequence[Any], agent_id: str) -> list[Any]:
    """Keep only policies that apply to this agent — ``global`` or
    ``agent:{agent_id}`` — mirroring ``PolicyTier.build_prompt``.

    ``PolicyTier.list()`` returns every policy regardless of scope; surfacing a
    foreign agent's or an entity-scoped rule as this agent's *standing warning*
    would hand it inapplicable instructions and crowd out its real ones.
    """
    allowed = {"global", f"agent:{agent_id}"}
    return [p for p in policies if getattr(p, "scope", "global") in allowed]


def assemble_continuity(
    *,
    agent_id: str,
    now: datetime,
    identity: str | None,
    recent: Sequence[Any],
    commitments: Sequence[Any],
    curiosity: Sequence[Any],
    policies: Sequence[Any],
    relevant: Sequence[Any],
    sym: SymContinuityInputs | None,
    max_tokens: int,
    k_open_loops: int,
    curiosity_total: int | None = None,
    narrative: dict | None = None,
    checkpoint: dict | None = None,
    elapsed_seconds: float | None = None,
    delta: dict | None = None,
) -> ContinuityResult:
    """Assemble the wake snapshot from already-gathered inputs. Pure — no I/O.

    All sequences hold duck-typed objects: ``recent`` → JournalEntry (.title,
    .entry_type), ``commitments`` → CommitmentInfo, ``curiosity`` →
    CuriositySignalInfo, ``policies`` → PolicyEntry (.key, .content), ``relevant``
    → SearchResult (.content, .title).

    ``curiosity_total`` is the true pending-curiosity backlog size (counted
    independently of the bounded candidate fetch); when omitted it falls back to
    the length of the fetched ``curiosity`` list.
    """
    max_chars = max(1, max_tokens * 4)  # honor small budgets; no silent floor
    header_prefix = f"## Continuity — {agent_id}\n\n"
    sections: list[str] = []
    section_names: list[str] = []
    used = len(header_prefix)  # global running budget, incl. header + separators

    # Per-section SOFT ceilings so no single lane hogs the snapshot. The HARD cap
    # is the global ``used <= max_chars`` invariant enforced in ``_emit`` — the
    # ceilings may sum past 100% precisely because the global cap, not their sum,
    # is what bounds the result.
    soft = {
        "identity": int(max_chars * 0.12),
        "narrative": int(max_chars * 0.20),
        "gap": int(max_chars * 0.10),
        "immediate": int(max_chars * 0.12),
        "delta": int(max_chars * 0.18),
        "warnings": int(max_chars * 0.15),
        "recent": int(max_chars * 0.15),
        "open_loops": int(max_chars * 0.30),
        "goals": int(max_chars * 0.10),
        "self_model": int(max_chars * 0.12),
        "internal_state": int(max_chars * 0.10),
        "relevant": int(max_chars * 0.16),
    }

    # "Returning after a gap" mode: the agent's last turn is old enough that its
    # immediate state is no longer safely current (G5). Drives the age qualifier on the
    # checkpoint, the reorientation frame, and whether the delta lane (G4) renders.
    long_gap = elapsed_seconds is not None and elapsed_seconds >= _LONG_GAP_SECONDS
    age = _age_phrase(elapsed_seconds) if elapsed_seconds is not None else ""

    def _emit(name: str, header: str, lines: Sequence[str], *, fair: bool = False) -> int:
        """Render a section within both its soft ceiling and the global budget.
        Returns the number of content lines actually kept (0 if the section was
        dropped), so callers report surfaced content, not raw inputs.

        ``fair``: give every line an equal share of the *actual* body budget
        (truncating each to a bounded preview) instead of first-come-first-served.
        Used by the immediate/checkpoint lane so a stale long field can't crowd out
        a freshly-written one — every populated field survives as a preview."""
        nonlocal used
        if not lines:
            return 0
        # Room left is the smaller of this lane's ceiling and global remaining,
        # minus the header and the "\n\n" separator this block will cost.
        avail = min(soft.get(name, int(max_chars * 0.1)), max_chars - used)
        body_budget = avail - len(header) - 1 - 2  # header + "\n" + separator
        if body_budget <= 0:
            return 0
        if fair and len(lines) > 1 and sum(len(ln) + 1 for ln in lines) > body_budget:
            # The fields don't all fit. Reserve a FLOOR preview for each populated field
            # first (priority order), THEN hand leftover budget to the highest-priority
            # fields. Floors-first matters because these fields are written by DIFFERENT
            # seams at different times (chat/peer write last_interaction_summary, comms
            # writes last_action): a pure priority-greedy fill would let a long stale
            # summary consume the whole lane and hide an action the agent JUST completed
            # (codex P2). With floors, every field that fits at all shows at least a
            # preview; priority only decides who gets the surplus and, when the budget is
            # too tight for every floor, which lowest-priority fields drop. Each line costs
            # len+1 (content + "\n"). Skipped entirely when everything fits.
            _FLOOR = 48   # min chars for a meaningful preview (> the loop's 25 guard)
            caps = [0] * len(lines)
            remaining = body_budget
            floored: list[int] = []
            for i, ln in enumerate(lines):               # pass 1: floors, priority order
                need = min(len(ln), _FLOOR)
                if need + 1 <= remaining:
                    caps[i] = need
                    remaining -= need + 1
                    floored.append(i)
                else:
                    break                                # budget exhausted; the rest drop
            for i in floored:                            # pass 2: surplus, priority order
                grow = min(len(lines[i]) - caps[i], remaining)
                if grow > 0:
                    caps[i] += grow
                    remaining -= grow
            fitted = [lines[i] if len(lines[i]) <= caps[i]
                      else lines[i][: caps[i] - 1].rstrip() + "…"
                      for i in floored]
            # If the budget couldn't floor even one field, don't blank the lane — fall back
            # to the top-priority field and let the loop's oversized-first-item path preview it.
            lines = fitted if fitted else [lines[0]]
        kept: list[str] = []
        chars = 0
        for line in lines:
            if chars + len(line) + 1 <= body_budget:
                kept.append(line)
                chars += len(line) + 1
            elif not kept and body_budget >= 25:
                # Oversized first item: keep a bounded preview rather than drop
                # the whole lane (a single long commitment must not blank the
                # snapshot).
                kept.append(line[: body_budget - 1].rstrip() + "…")
                break
            else:
                break
        if not kept:
            return 0
        block = header + "\n" + "\n".join(kept)
        sections.append(block)
        section_names.append(name)
        used += len(block) + 2  # +2 for the "\n\n" join
        return len(kept)

    # 1. Identity / self-kernel (supplied by the runtime's persona; nmem core
    #    stays decoupled from agent_core).
    if identity:
        _emit("identity", "### Who I am", [identity.strip()])

    # 1b. Autobiographical narrative — the durable "what has been happening to
    #     me" (written by dreamstate, re-grounded from episodes).
    has_narrative = False
    if narrative and (narrative.get("current_period") or "").strip():
        lines = [narrative["current_period"].strip()]
        traj = (narrative.get("longer_trajectory") or "").strip()
        if traj:
            lines.append(traj)
        # Temporal qualification: a current-period narrative written weeks ago
        # must not read as current. Stamp the grounding date and mark it
        # historical once it is older than the period it describes.
        label = "### Where I've been"
        grounded = _as_utc(narrative.get("grounded_at"))
        if grounded is not None:
            date_str = grounded.strftime("%Y-%m-%d")
            if (now - grounded).days > 21:
                label = f"### Where I've been (historical — last updated {date_str})"
            else:
                label = f"### Where I've been (as of {date_str})"
        has_narrative = _emit("narrative", label, lines) > 0

    # 1b-gap. Returning-after-a-gap reorientation (G5): when the last turn is old, say so
    #     plainly and tell the agent to re-orient before acting on stale immediate state.
    if long_gap and age:
        _emit("gap", "### Returning after a gap",
              [f"Your last turn was {age} ago — things may have moved on. Re-orient against "
               f"the current state below before acting on where you left off."])

    # 1c. Immediate continuity — "where I left off", for resuming across a gap.
    #     Freshest-first order (interaction → interrupted work → last action → next): the
    #     checkpoint is a partial upsert, so a stale long ``last_action`` from an earlier
    #     writer can linger; rendering it ahead of a just-written interaction summary would
    #     let it displace the summary (codex P2). Each field also gets a fair bounded share
    #     of the lane so *every* present field renders a preview rather than the first few
    #     crowding out the rest. On a long gap the header is age-qualified so the state
    #     does not read as current (present evidence still wins — it is shown, just dated).
    has_checkpoint = False
    if checkpoint:
        ck_lines = []
        if checkpoint.get("last_interaction_summary"):
            ck_lines.append(f"- Last interaction: {checkpoint['last_interaction_summary']}")
        if checkpoint.get("interrupted_work"):
            ck_lines.append(f"- Was in the middle of: {checkpoint['interrupted_work']}")
        if checkpoint.get("last_action"):
            ck_lines.append(f"- Last action: {checkpoint['last_action']}")
        if checkpoint.get("expected_next_action"):
            ck_lines.append(f"- Expected next: {checkpoint['expected_next_action']}")
        if ck_lines:
            ck_header = f"### Picking up from ({age} ago)" if long_gap and age else "### Picking up from"
            has_checkpoint = _emit("immediate", ck_header, ck_lines, fair=True) > 0

    # 1d. Delta (G4) — what changed while the agent was away. Only on a long gap (so it
    #     costs nothing in continuous operation and never adds noise mid-session).
    if long_gap and delta:
        delta_lines = []
        # External — what others / the world changed.
        for item in (delta.get("shared_new") or [])[:5]:
            key = (item.get("key") or "").strip()
            by = (item.get("by") or "another agent").strip()
            if key:
                delta_lines.append(f"- {by} added to shared knowledge: {key}")
        # Introspective — what the agent's own dreamstate did while away.
        if delta.get("narrative_regrounded"):
            delta_lines.append("- your self-narrative was re-grounded by consolidation")
        lp = int(delta.get("ltm_promoted") or 0)
        if lp:
            # "at least" — this is a conservative floor (see continuity_store.delta_since:
            # promotions into a pre-existing key keep their old created_at and aren't counted).
            delta_lines.append(f"- at least {lp} memor{'y' if lp == 1 else 'ies'} consolidated "
                               f"into long-term memory")
        # Activity volume.
        jn = int(delta.get("journal_new") or 0)
        if jn:
            delta_lines.append(f"- {jn} new entr{'y' if jn == 1 else 'ies'} accrued in your "
                               f"journal while you were away")
        if delta_lines:
            _emit("delta", "### Since you were last active", delta_lines)

    # 2. Warnings — governance red-lines, surfaced regardless of stimulus.
    warnings = [
        f"[!] {getattr(p, 'key', '')}: {(getattr(p, 'content', '') or '')[:140]}"
        for p in policies
        if _is_warning(f"{getattr(p, 'key', '')} {getattr(p, 'content', '')}")
    ]
    _emit("warnings", "### Standing warnings", warnings)

    # 3. Recent trajectory — what I have been doing lately.
    recent_lines = []
    for e in recent:
        title = (getattr(e, "title", None) or getattr(e, "content", "") or "").strip()
        if title:
            recent_lines.append(f"- {title[:120]}")
    _emit("recent", "### Recently", recent_lines)

    # 4. Unified open loops — commitments ∪ curiosity, salience-ranked. The
    #    ranked list is drawn from the (bounded) fetched candidates, but the
    #    reported total uses the true backlog count so the omission notice never
    #    understates what is unresolved.
    loops, _considered = merge_open_loops(commitments, curiosity, now, k_open_loops)
    fetched_curiosity = curiosity_total if curiosity_total is not None else len(curiosity)
    loops_total = len(commitments) + fetched_curiosity
    loop_lines = [f"- {l.text}" for l in loops]
    n_loops_shown = _emit("open_loops", "### Open loops (what's unresolved)", loop_lines)
    # Omission notice reflects BOTH the k-cut and any budget truncation, and is
    # only appended when it genuinely fits — so the count never lies.
    dropped = loops_total - n_loops_shown
    if dropped > 0 and section_names and section_names[-1] == "open_loops":
        note = f"- (+{dropped} more unresolved, not shown)"
        if used + len(note) + 1 <= max_chars:
            sections[-1] = sections[-1] + "\n" + note
            used += len(note) + 1

    # 5. Active goals (sym seam).
    n_goals = 0
    if sym is not None and sym.active_goals:
        n_goals = _emit("goals", "### Active goals", [f"- {g}" for g in sym.active_goals])

    # 6. Self-model (sym seam).
    has_self_model = False
    if sym is not None and sym.self_model_summary:
        has_self_model = _emit("self_model", "### About myself", [sym.self_model_summary.strip()]) > 0

    # 7. Internal state — drive consequence as prose, never numbers.
    has_drive_state = False
    if sym is not None and sym.drive_state_prose:
        has_drive_state = _emit("internal_state", "### Right now", [sym.drive_state_prose.strip()]) > 0

    # 7b. Relational self — who I am to my world, situation-conditioned (sym seam).
    if sym is not None and sym.relational_self_prose:
        _emit("relational_self", "### My world", [sym.relational_self_prose.strip()])

    # 8. Query-relevant recall (only when a stimulus is present).
    rel_lines = []
    for r in relevant:
        text = (getattr(r, "title", None) or getattr(r, "content", "") or "").strip()
        if text:
            rel_lines.append(f"- {text[:120]}")
    _emit("relevant", "### Relevant to now", rel_lines)

    if not sections:
        content = header_prefix + "(no continuity state yet)"
    else:
        content = header_prefix + "\n\n".join(sections)
    # Hard guarantee: never exceed the caller's char budget (covers the tiny-
    # budget empty-state fallback and any separator-accounting off-by-one).
    if len(content) > max_chars:
        content = content[:max_chars]

    # Order section_names by the canonical lane order for a stable report.
    ordered = tuple(n for n in _SECTION_ORDER if n in section_names)

    return ContinuityResult(
        content=content,
        token_estimate=len(content) // 4,
        sections=ordered,
        n_commitments=len(commitments),
        n_open_loops_shown=n_loops_shown,
        n_open_loops_total=loops_total,
        n_goals=n_goals,
        has_self_model=has_self_model,
        has_drive_state=has_drive_state,
        has_narrative=has_narrative,
        has_checkpoint=has_checkpoint,
    )
