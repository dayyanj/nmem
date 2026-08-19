"""
Autonomy — nmem decides *when* to memorize and *when* to retrieve.

Today retrieval is entirely host-initiated. This layer adds two self-initiated
behaviors, both OFF by default (`config.autonomy.enabled`):

  • capture   — turn a qualifying journal entry into a skill without being asked
  • surface   — proactively search on a journal write and OFFER relevant memory
                (and skills) to the host via a single `memory.surfaced` event

nmem only *offers* — it never forces injection; the host subscribes and decides.

Safety rails (these are load-bearing — see the codex review notes in the plan):

  • Never in the write path. `journal.add` awaits `_emit` serially, so the
    `journal.added` handler does only cheap checks and hands real work to a
    background task (bounded semaphore + timeout). A slow autonomy pass can
    never delay a journal write.
  • No recursion / storm. A proactive search can trigger entity auto-journaling,
    which emits another `journal.added`. We guard against the loop three ways:
    a per-agent in-flight set, skipping machinery-generated entry types
    (`entity_reference`), and tagging our searches `source="autonomy"` so drive
    ingestion ignores them. A per-agent cooldown bounds frequency.
  • Refetch. The `journal.added` payload is title-only; we refetch the row by id
    to classify/search on real content.
"""
from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

# Entry types produced by nmem's own machinery — never re-process these, or a
# proactive search that auto-journals an entity access would loop.
_SKIP_ENTRY_TYPES = {"entity_reference"}

_MAX_CONCURRENT = 4
_TASK_TIMEOUT_S = 10.0


class AutonomyManager:
    """Self-initiated memorize/retrieve, driven off nmem's event bus."""

    def __init__(self, mem, config):
        self._mem = mem                 # MemorySystem (search / journal / skills / emit)
        self._config = config
        self._sem = asyncio.Semaphore(_MAX_CONCURRENT)
        self._inflight: set[str] = set()          # agents currently being handled
        self._cooldown_until: dict[str, float] = {}   # agent → monotonic deadline
        self._last_trigger_emb: dict[str, list[float]] = {}  # agent → last surfaced trigger
        self._tasks: set[asyncio.Task] = set()
        self._closing = False

    # ── config helpers ────────────────────────────────────────

    @property
    def _cfg(self):
        return getattr(self._config, "autonomy", None)

    @property
    def _enabled(self) -> bool:
        return bool(getattr(self._cfg, "enabled", False))

    # ── attach to the event bus ───────────────────────────────

    def attach(self) -> None:
        """Subscribe to the event bus. No-op (no subscription) when disabled, so
        a default instance registers no handlers and behaves exactly as before."""
        if not self._enabled:
            return
        self._mem.on("journal.added")(self._on_journal_added)

    async def aclose(self) -> None:
        """Cancel and await any in-flight background work — called on
        MemorySystem.close() so tasks don't run against a closed DB or leak
        pending-task warnings at loop teardown. Sets a closing flag first so a
        `journal.added` racing in after the snapshot can't schedule a new task."""
        self._closing = True
        # Loop: a task could be scheduled between snapshot and cancel; keep
        # draining until none remain (the closing flag stops new ones).
        while self._tasks:
            tasks = list(self._tasks)
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        self._inflight.clear()

    # ── journal.added → background handler ────────────────────

    async def _on_journal_added(self, data: dict) -> None:
        """Cheap gate only — real work is backgrounded so this never sits in the
        journal.add write path."""
        if not self._enabled or self._closing:
            return
        entry_type = data.get("entry_type")
        agent_id = data.get("agent_id")
        entry_id = data.get("id")
        if agent_id is None or entry_id is None:
            return
        if entry_type in _SKIP_ENTRY_TYPES:      # machinery-generated → ignore
            return
        # Claim the agent synchronously (no await between check and add, so this
        # is atomic w.r.t. other coroutines) BEFORE scheduling — a burst of
        # same-agent writes then enqueues at most one task, keeping "cheap checks
        # then bounded work" true even under load.
        if agent_id in self._inflight:
            return
        self._inflight.add(agent_id)
        task = asyncio.create_task(self._handle(agent_id, entry_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _handle(self, agent_id: str, entry_id: int) -> None:
        try:
            async with self._sem:
                await asyncio.wait_for(
                    self._process(agent_id, entry_id), timeout=_TASK_TIMEOUT_S)
        except Exception as e:   # never propagate — autonomy is best-effort
            logger.debug("Autonomy handler failed for %s/%s: %s", agent_id, entry_id, e)
        finally:
            self._inflight.discard(agent_id)

    async def _process(self, agent_id: str, entry_id: int) -> None:
        entry = await self._refetch(entry_id)
        if entry is None:
            return
        content = (entry.get("content") or entry.get("title") or "").strip()
        if not content:
            return
        if getattr(self._cfg, "auto_capture_skills", False):
            await self._maybe_capture_skill(agent_id, entry)
        if getattr(self._cfg, "proactive_retrieve", False):
            await self._maybe_surface(agent_id, content, project_scope=entry.get("project_scope"),
                                      exclude_journal_id=entry_id)

    # ── autonomous memorize (skill capture) ───────────────────

    async def _maybe_capture_skill(self, agent_id: str, entry: dict) -> None:
        types = set(getattr(self._cfg, "skill_entry_types", ()) or ())
        rt = entry.get("record_type")
        et = entry.get("entry_type")
        if rt not in types and et not in types:
            return
        what = (entry.get("content") or entry.get("title") or "").strip()
        if not what:
            return
        # Heuristic capture only (LLM classification is Phase 1.5). worked=True:
        # the entry records something the agent did; reinforcement adjusts later.
        await self._mem.skills.record(
            what, name=entry.get("title"), worked=True, agent_id=agent_id,
            project_scope=entry.get("project_scope"))

    # ── autonomous retrieve (proactive surface) ───────────────

    async def surface_now(self, query: str, agent_id: str, *,
                          reason: str = "request") -> bool:
        """Explicit proactive surface (used by the recall drive's request_surface
        reverse channel). Bypasses the cooldown/novelty gates a journal trigger
        uses, but still emits at most one `memory.surfaced`. Returns True iff
        something was offered — the caller (e.g. a drive) uses this for
        deferred-relief so pressure isn't discharged for a no-op."""
        if not self._enabled:
            return False
        return await self._maybe_surface(agent_id, query, gated=False, reason=reason)

    async def _maybe_surface(self, agent_id: str, query: str, *,
                             project_scope=..., exclude_journal_id: int | None = None,
                             gated: bool = True, reason: str = "journal") -> bool:
        cfg = self._cfg
        now = time.monotonic()
        if gated and self._cooldown_until.get(agent_id, 0.0) > now:
            return False

        # Novelty: skip if this trigger is ~identical to the last one we searched
        # for this agent (avoids re-offering the same context repeatedly).
        emb = await self._embed(query)
        if gated and emb is not None:
            last = self._last_trigger_emb.get(agent_id)
            if last is not None:
                from nmem.search import cosine_similarity
                if cosine_similarity(emb, last) >= getattr(cfg, "novelty_threshold", 0.6):
                    return False

        # Commit to a proactive search THIS tick → arm the cooldown + record the
        # trigger now, before searching. Cooldown must bound search *frequency*,
        # not just successful offers — otherwise a stream of no-offer journal
        # writes would run a proactive search (and possible entity auto-journal)
        # on every write. (surface_now/gated=False is an explicit request and is
        # neither rate-limited nor allowed to poison the journal-path cooldown.)
        if gated:
            self._cooldown_until[agent_id] = now + getattr(cfg, "cooldown_seconds", 120)
            if emb is not None:
                self._last_trigger_emb[agent_id] = emb

        top_k = getattr(cfg, "surface_top_k", 5)
        threshold = getattr(cfg, "surface_recognition_threshold", 0.5)
        try:
            results = await self._mem.search(
                agent_id, query, top_k=top_k, bump_access=False,
                project_scope=project_scope, source="autonomy")
        except Exception as e:
            logger.debug("Autonomy proactive search failed: %s", e)
            return False

        surfaced = []
        for r in results:
            if exclude_journal_id is not None and r.tier == "journal" and r.id == exclude_journal_id:
                continue
            if r.recognition_score >= threshold:
                surfaced.append({
                    "tier": r.tier, "id": r.id, "title": r.title,
                    "content": r.content[:500],
                    "recognition": r.recognition,
                    "recognition_score": round(r.recognition_score, 3),
                })

        skills = []
        try:
            skill_hits = await self._mem.skills.find(query, project_scope=project_scope)
            skills = [{"id": s.id, "name": s.name, "what": s.what,
                       "outcome": s.outcome, "worked": s.worked,
                       "success_count": s.success_count, "trial_count": s.trial_count}
                      for s in skill_hits]
        except Exception as e:
            logger.debug("Autonomy skill find failed: %s", e)

        if not surfaced and not skills:
            return False

        await self._mem._emit("memory.surfaced", {
            "agent_id": agent_id, "trigger": query[:300], "source": "autonomy",
            "reason": reason, "results": surfaced, "skills": skills,
        })
        return True

    # ── helpers ───────────────────────────────────────────────

    async def _embed(self, text: str):
        emb = getattr(self._mem, "_embedding", None)
        if emb is None or not text:
            return None
        try:
            return await asyncio.to_thread(emb.embed, text)
        except Exception:
            return None

    async def _refetch(self, entry_id: int) -> dict | None:
        from sqlalchemy import select
        from nmem.db.models import JournalEntryModel
        try:
            async with self._mem._db.session() as session:
                row = (await session.execute(
                    select(JournalEntryModel).where(JournalEntryModel.id == entry_id))
                ).scalar_one_or_none()
                if row is None:
                    return None
                return {
                    "id": row.id, "agent_id": row.agent_id, "title": row.title,
                    "content": row.content, "entry_type": row.entry_type,
                    "record_type": row.record_type, "project_scope": row.project_scope,
                }
        except Exception as e:
            logger.debug("Autonomy refetch failed for %s: %s", entry_id, e)
            return None
