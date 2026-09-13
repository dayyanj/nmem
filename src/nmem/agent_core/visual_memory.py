"""Generic visual memory — turn computer-use keyframes into sensory memories (agent_core).

GENERIC: given the agent's :class:`~nmem.agent_core.actors.computer_use.SandboxClient` (its *hands*)
plus a nmem-sym-sensor ``SensorGraph`` (its *visual cortex*), this closes the SEE → REMEMBER loop
for ANY acting agent whose executor drives the computer-use sandbox. Nothing here is agent-specific:
a host constructs one :class:`VisualMemory` (via :func:`build_visual_memory`) and composes its sink
onto the experiential/reflective outcome sink — the same shape as every other agent_core seam.

Gated + fail-open by construction: disabled, no sensor DSN, or a missing heavy dep
(opencv / torch / sentence-transformers) degrades to a **no-op**, never crashing a cycle.

Bar (founder): this is NOT a vanity graph. The worth-storing gate keeps only frames that carry
signal — a *changed* screen, or *any* frame from a SURPRISING (unverified/failed) outcome — and
drops near-duplicates of what we already hold (phash Hamming). A failure on a FAMILIAR screen is
kept on purpose (surprise overrides dedup) because that is exactly what Phase 2 read-back needs:
"I have seen a screen like this before, and last time this action FAILED." Each stored frame's ref
encodes the ``goal_id`` so a later visual-similarity lookup can join a past screen back to that
goal's recorded outcome in nmem-sym — no extra schema required.
"""
from __future__ import annotations

import logging
import os
import re
from collections import deque

log = logging.getLogger(__name__)


def _env_on(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def _hamming_hex(a: str, b: str) -> int:
    """Bit distance between two dhash hexes (0=identical, 64=opposite). 64 on bad input."""
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except Exception:  # noqa: BLE001
        return 64


def _mask_dsn(dsn: str | None) -> str | None:
    if not dsn:
        return dsn
    return re.sub(r"://([^:@/]+):[^@/]+@", r"://\1:***@", dsn)


class VisualMemory:
    """Ingest computer-use keyframes into a ``SensorGraph``, gated by a worth-storing policy.

    Construct via :func:`build_visual_memory` (which also ``connect()``s it). One instance per agent
    runtime, held on the host's ``ctx.state`` and closed on shutdown — the same single-owner
    lifecycle as the ``SandboxClient`` it reads from.
    """

    def __init__(self, sandbox_client, *, mem=None, agent_id: str | None = None,
                 db_dsn: str | None = None, sym_dsn: str | None = None, novelty_hamming: int = 8,
                 same_screen_hamming: int = 8, max_frames_per_outcome: int = 6,
                 phash_cache: int = 512, consolidate_every: int = 0,
                 readback_scan: int = 500, readback_enabled: bool = True) -> None:
        self._sandbox = sandbox_client
        self._mem = mem                          # nmem MemorySystem — for the working-memory read-back channel
        self._agent_id = agent_id                # for the startup baseline-clean (OFF arm)
        # A/B isolation: ingest + link-recording ALWAYS run (both arms accumulate the same measurement
        # data); this gates ONLY the behavioral intervention — the working-memory warning that steers
        # the next proposal. Off = baseline arm (frames still stored, links still recorded, no warning).
        self._readback_enabled = readback_enabled
        self._db_dsn = db_dsn or os.environ.get("NMEM_SENSOR_DB_DSN")
        self._sym_dsn = sym_dsn or os.environ.get("NMEM_SYM_DB_DSN")
        self._novelty_hamming = novelty_hamming
        # "Same screen" dhash Hamming radius: different UI screens measure ≥18 apart, same-screen ≤4,
        # so 8 cleanly separates them (novelty_hamming dedups near-identical frames within one store).
        self._same_screen_hamming = same_screen_hamming
        self._readback_scan = readback_scan       # bound the recent-links Hamming scan
        self._max_frames = max_frames_per_outcome
        self._consolidate_every = consolidate_every
        self._sg = None                          # SensorGraph, or None while disabled
        self._recent: deque[str] = deque(maxlen=phash_cache)  # recently-stored phashes (dedup)
        self._outcomes = 0

    async def connect(self) -> bool:
        """Lazily build + connect the ``SensorGraph`` (heavy deps import here, not at module load).
        ``SensorGraph.connect()`` runs the sensor migrations = appliance sensory-init. Returns True
        on success; on ANY failure logs + stays disabled (``enabled`` False) so the agent is
        unaffected."""
        if not self._db_dsn:
            log.warning("[visual-memory] no NMEM_SENSOR_DB_DSN — visual memory disabled")
            return False
        try:
            from nmem_sym_sensor import SensorGraph  # lazy: heavy (torch/opencv/sentence-transformers)
        except Exception:  # noqa: BLE001
            log.warning("[visual-memory] nmem-sym-sensor not importable — disabled", exc_info=True)
            return False
        sg = None
        try:
            sg = SensorGraph(db_dsn=self._db_dsn)
            await sg.connect()   # migrations + embedder load
            self._sg = sg
            log.info("[visual-memory] connected (sensor_db=%s, sym_grounding=%s, consolidate_every=%d)",
                     _mask_dsn(self._db_dsn), bool(self._sym_dsn), self._consolidate_every)
            # A/B baseline must start clean: if we boot into the OFF arm, clear any warning a prior
            # ON run left in the autonomous lane BEFORE the pursuit loop builds its first proposal
            # (the per-outcome clear runs too late for that first proposal). Restart applies the flag.
            if not self._readback_enabled and self._agent_id:
                await self._write_visual_lesson(self._agent_id, None)
            return True
        except Exception:  # noqa: BLE001
            log.warning("[visual-memory] SensorGraph.connect failed — disabled", exc_info=True)
            # connect() may have already opened the asyncpg pool before failing (e.g. embedder
            # load) — close the partial graph so we don't leak DB connections on the disabled path.
            if sg is not None:
                try:
                    await sg.close()
                except Exception:  # noqa: BLE001
                    log.debug("[visual-memory] partial-graph close failed", exc_info=True)
            self._sg = None
            return False

    async def aclose(self) -> None:
        sg, self._sg = self._sg, None
        if sg is not None:
            try:
                await sg.close()
            except Exception:  # noqa: BLE001
                log.debug("[visual-memory] close failed", exc_info=True)

    @property
    def enabled(self) -> bool:
        return self._sg is not None

    def _select(self, candidates: list[dict], surprising: bool) -> list[dict]:
        """Worth-storing gate: novelty (phash) × informativeness (screen_changed) × surprise.

        - An unchanged screen carries no new signal UNLESS the outcome was surprising.
        - A near-duplicate of a scene we already hold is dropped (don't re-mint) UNLESS surprising —
          a failure on a familiar screen is worth reinforcing the "this screen → failure" link.
        Capped at ``max_frames_per_outcome`` to bound cost.

        Dedup compares against the persistent ``_recent`` cache AND frames already picked THIS batch,
        but does NOT mutate ``_recent`` — the persistent cache is updated by :meth:`ingest_outcome`
        only AFTER a frame is actually stored, so a fetch/decode/ingest failure can't poison the
        cache into discarding a screen that was never saved."""
        kept: list[dict] = []
        batch_phashes: list[str] = []
        for f in candidates:
            if not isinstance(f, dict):
                continue
            ph = f.get("phash")
            if not ph:
                continue
            changed = bool(f.get("screen_changed"))
            if not changed and not surprising:
                continue
            near_dup = any(_hamming_hex(ph, seen) <= self._novelty_hamming
                           for seen in (*self._recent, *batch_phashes))
            if near_dup and not surprising:
                continue
            kept.append(f)
            batch_phashes.append(ph)
            if len(kept) >= self._max_frames:
                break
        return kept

    async def ingest_outcome(self, proposal, outcome, agent_id: str) -> None:
        """Read a completed pursuit's observation contract, gate its keyframes, fetch only the kept
        ones, decode → HWC uint8 BGR, and ``ingest_frame`` into the sensory graph. No-op on an infra
        outcome (the actuator never ran) or when disabled. Fail-open per frame."""
        if self._sg is None:
            return
        obs = getattr(outcome, "observations", None) or {}
        if obs.get("infra"):
            return
        sid = obs.get("session_id")
        steps = obs.get("steps") or []
        if not sid or not steps:
            return
        gid = obs.get("goal_id")
        objective = obs.get("objective", "")
        verified = bool(obs.get("verified"))
        surprising = not verified
        verdict = "v" if verified else "f"           # tags scene links + frame refs (Phase 2 read-back)
        candidates = [s.get("frame") for s in steps
                      if isinstance(s, dict) and isinstance(s.get("frame"), dict)]
        candidates = [f for f in candidates if f]

        stored = 0
        screens: list[str] = []                   # dhash of each distinct screen this pursuit stored
        # Store gated frames (may keep nothing — e.g. an unchanged verified pursuit; the read-back
        # below STILL runs so the no-op check fires and any stale lesson is cleared).
        kept = self._select(candidates, surprising) if candidates else []
        if kept:
            try:
                import cv2
                import numpy as np
            except Exception:  # noqa: BLE001
                log.warning("[visual-memory] opencv/numpy missing — cannot decode frames", exc_info=True)
                kept = []
        for f in kept:
            fid = f.get("frame_id")
            if fid is None:
                continue
            data = await self._sandbox.get_frame(sid, fid)  # bytes worth moving (already gated)
            if not data:
                continue
            arr = np.frombuffer(data, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)  # HWC uint8 BGR (what ingest_frame wants)
            if img is None:
                continue
            # The frame ref encodes goal + verdict so a later read-back knows a visually-similar past
            # screen belonged to a FAILED ('f') vs verified ('v') pursuit (objective is too long here).
            frame_ref = f"cu:{agent_id}:{gid}:{verdict}:{sid}:{fid}"
            try:
                await self._sg.ingest_frame(img, frame_id=frame_ref,
                                            scene_change=bool(f.get("screen_changed")))
                stored += 1
                # Cache the phash ONLY after a successful store, so a later identical screen is
                # correctly deduped — and a failed store never suppresses a screen we never saved.
                # Also collect this screen's dhash (distinct screens only) for the read-back.
                ph = f.get("phash")
                if ph:
                    self._recent.append(ph)
                    if not any(_hamming_hex(ph, s) <= self._same_screen_hamming for s in screens):
                        screens.append(ph)
            except Exception:  # noqa: BLE001
                log.warning("[visual-memory] ingest_frame failed", exc_info=True)

        if stored:
            log.info("[visual-memory] stored %d/%d keyframe(s) for goal %s (%s outcome)",
                     stored, len(candidates), gid, "surprising" if surprising else "verified")

        # Phase 2 — scene-level read-back into the NEXT proposal. ALWAYS runs (even when no frame was
        # kept) so the "success but screen never changed" warning can fire and a stale lesson clears.
        # Reads prior links BEFORE writing this pursuit's, so the current verdict can't contaminate
        # its own lookup. Fail-open: a read-back error must never affect the store above.
        try:
            await self._readback(agent_id, gid, objective, verdict, screens, steps)
        except Exception:  # noqa: BLE001
            log.warning("[visual-memory] read-back failed (non-fatal)", exc_info=True)

        # Throttled consolidation (cluster formation → occipital viz + eventual symbol grounding).
        # OFF by default (consolidate_every=0): consolidation is heavy and better run by a periodic
        # job than on the pursuit hot-path. When >0, run it every Nth outcome for a self-contained
        # agent with no separate sensor loop.
        self._outcomes += 1
        if self._consolidate_every and self._outcomes % self._consolidate_every == 0:
            try:
                await self._sg.consolidate()
            except Exception:  # noqa: BLE001
                log.warning("[visual-memory] consolidate failed", exc_info=True)

    async def _readback(self, agent_id, gid, objective, verdict, screens, steps) -> None:
        """Phase 2 SEE→REMEMBER read-back, keyed on the perceptual dhash (discriminative for UI
        screens, unlike the coarse-colour scene embedding — see migration 004). For the screens this
        pursuit visited, look up PRIOR FAILED pursuits on the SAME screen (dhash Hamming ≤
        same_screen_hamming, incl. earlier attempts of the same goal) and, if any, warn the NEXT
        proposal via the working-memory autonomous lane (which ``default_proposal`` already injects —
        no proposal plumbing change). Folds in a frame-diff "did the screen change" signal. Records
        THIS pursuit's screen→verdict links AFTER the lookup (so it can't match its own rows). The
        count of revisited-failed screens is the Phase-3 behavioral-advantage metric."""
        pool = self._sg.pool
        prior_fail = False
        matched = 0
        seen_objs: list[str] = []
        if screens:
            # Bounded scan of this agent's recent FAILED screen links; Hamming-match in Python
            # (portable — no reliance on a Postgres popcount). Runs BEFORE inserting this pursuit's
            # own links, so it never matches itself.
            rows = await pool.fetch(
                """SELECT phash, objective FROM screen_pursuit_links
                   WHERE agent_id = $1 AND verdict = 'f'
                   ORDER BY created_at DESC LIMIT $2""",
                agent_id, self._readback_scan)
            for r in rows:
                if any(_hamming_hex(cur, r["phash"]) <= self._same_screen_hamming for cur in screens):
                    matched += 1
                    # Collect up to 3 distinct objective texts for a legible warning — but the MATCH
                    # itself counts even when the objective is empty (else a failure with no objective
                    # would silently suppress the warning).
                    if r["objective"] and r["objective"] not in seen_objs and len(seen_objs) < 3:
                        seen_objs.append(r["objective"])
            prior_fail = matched > 0
            # Record this pursuit's screen links for future read-backs (one per distinct screen).
            # ALWAYS recorded (both A/B arms) + stamped with the arm active now, so the metric can
            # attribute a revisit's outcome to whether the agent was actually warned.
            for ph in screens:
                await pool.execute(
                    """INSERT INTO screen_pursuit_links
                           (phash, agent_id, goal_id, verdict, objective, readback)
                       VALUES ($1, $2, $3, $4, $5, $6)""",
                    ph, agent_id, gid, verdict, (objective or "")[:200], self._readback_enabled)

        # Frame-diff: fraction of steps that visibly changed the screen. A "verified" success that
        # never changed the screen is suspicious (the actuator may not have taken effect).
        total = len(steps)
        changed = sum(1 for s in steps if isinstance(s, dict)
                      and isinstance(s.get("frame"), dict) and s["frame"].get("screen_changed"))
        change_ratio = (changed / total) if total else 0.0

        lines = []
        if prior_fail:
            objs = "; ".join(seen_objs) or "a prior task"
            lines.append(
                f"You are on screen(s) you have visited during a FAILED attempt before "
                f"(e.g. “{objs[:160]}”). Do NOT repeat what failed there — try a different approach.")
        if verdict == "v" and total >= 3 and change_ratio == 0.0:
            lines.append(
                "Your last pursuit reported success but the screen never changed across its steps — "
                "confirm the action actually took effect before relying on it.")
        if prior_fail:
            log.info("[visual-memory] read-back: %d screen(s) revisited from FAILED pursuits (goal %s)"
                     " [readback %s]", matched, gid, "ON" if self._readback_enabled else "OFF (A/B baseline)")

        # Behavioral intervention — the ONLY thing gated by the A/B arm. In the OFF arm we still
        # CLEAR the slot so a lingering warning can't influence the baseline.
        if self._readback_enabled:
            await self._write_visual_lesson(agent_id, "\n".join(lines) if lines else None)
        else:
            await self._write_visual_lesson(agent_id, None)

    async def _write_visual_lesson(self, agent_id, text) -> None:
        """Set (or clear) the ``visual_lesson`` slot in the autonomous working lane so the next
        proposal surfaces it. Clearing on an empty lesson avoids a stale warning persisting."""
        mem = self._mem
        if mem is None:
            return
        try:
            if not getattr(mem._config.working, "enabled", False):
                return
            from nmem.tiers.working import AUTONOMOUS_SESSION
            if text:
                await mem.working.set(AUTONOMOUS_SESSION, agent_id, "visual_lesson",
                                      f"\U0001f441 Visual memory: {text}"[:500], priority=2)
            else:
                await mem.working.clear(AUTONOMOUS_SESSION, agent_id, slot="visual_lesson")
        except Exception:  # noqa: BLE001
            log.warning("[visual-memory] working-lesson write failed (non-fatal)", exc_info=True)

    def wrap(self, inner_sink, agent_id: str):
        """Compose this visual-memory ingest AFTER an inner ``OutcomeSink`` (the experiential/
        reflective learning). The inner sink runs first (the real learning must not be delayed or
        broken by perception storage); visual ingest is additive + fail-open."""
        async def sink(proposal, outcome) -> None:
            try:
                await inner_sink(proposal, outcome)
            finally:
                try:
                    await self.ingest_outcome(proposal, outcome, agent_id)
                except Exception:  # noqa: BLE001
                    log.warning("[visual-memory] ingest_outcome failed (non-fatal)", exc_info=True)
        return sink


async def build_visual_memory(sandbox_client, *, enabled: bool | None = None, **kw):
    """Gated factory: return a CONNECTED :class:`VisualMemory`, or ``None`` when disabled / the
    sandbox is off / deps or DSN are missing. ``enabled`` defaults to the ``NMEM_VISUAL_MEMORY_
    ENABLED`` env gate. Fail-open: any failure → ``None`` and the agent runs exactly as before.

    ``kw`` forwards to :class:`VisualMemory` (``db_dsn``, ``sym_dsn``, ``novelty_hamming``,
    ``max_frames_per_outcome``, ``phash_cache``, ``consolidate_every``, ``readback_enabled``).

    ``NMEM_VISUAL_MEMORY_ENABLED`` gates the whole feature (ingest + read-back). To A/B the
    behavioral advantage, keep that ON and toggle ``NMEM_VISUAL_READBACK_ENABLED`` (default on):
    OFF keeps ingest + link-recording running (same measurement data) but suppresses the warning."""
    if enabled is None:
        enabled = _env_on("NMEM_VISUAL_MEMORY_ENABLED")
    if not enabled:
        return None
    if sandbox_client is None or not sandbox_client.is_enabled():
        log.info("[visual-memory] sandbox disabled — visual memory inert")
        return None
    kw.setdefault("readback_enabled", _env_on("NMEM_VISUAL_READBACK_ENABLED", "true"))
    # P5: periodic consolidation (iconic→nodes→clusters, fills occipital + enables symbol grounding).
    # Default 0 = off (heavy); a modest N runs it every Nth stored outcome on the pursuit path.
    if "consolidate_every" not in kw:
        try:
            kw["consolidate_every"] = int(os.environ.get("NMEM_VISUAL_CONSOLIDATE_EVERY", "0") or 0)
        except ValueError:
            kw["consolidate_every"] = 0
    vm = VisualMemory(sandbox_client, **kw)
    ok = await vm.connect()
    if ok:
        log.info("[visual-memory] ready (read-back %s)",
                 "ON" if vm._readback_enabled else "OFF — A/B baseline arm")
    return vm if ok else None
