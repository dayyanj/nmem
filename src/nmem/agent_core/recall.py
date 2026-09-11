"""Consumer for nmem autonomy's ``memory.surfaced`` events (the recall loop).

nmem's autonomy layer surfaces relevant memory/skills — proactively on journal
writes (``NMEM_AUTONOMY__PROACTIVE_RETRIEVE``) and on demand via ``request_surface``
(the nmem-sym recall drive's reverse channel) — by emitting a single
``memory.surfaced`` event. That event otherwise goes into the void: nmem surfaces,
but nothing consumes it. This module is the reusable consumer: it stashes surfaced
items in a small rolling buffer and, on request, formats the ones relevant to a
query for injection into the agent's next task/context.

    seed/observe -> autonomy surfaces -> `memory.surfaced` -> HERE (stash)
                 -> agent calls context_for(query) before acting -> inject

Fail-open throughout: a recall hiccup must never break a cognitive cycle. The buffer
is process-local (one agent per process), so nothing leaks across agents.

Graduated from the reference agent (``service/recall.py``) after it ran live (upstream plan
Phase 3).
"""
from __future__ import annotations

import logging
import time
from collections import deque

log = logging.getLogger(__name__)

# Rolling buffer of what autonomy has surfaced, newest last. Bounded so a chatty
# drive can't grow memory without bound; recall is enrichment, not a store.
_BUF: deque = deque(maxlen=32)
_installed = False


def install(mem) -> bool:
    """Subscribe to nmem's ``memory.surfaced``. Idempotent; returns True if wired."""
    global _installed
    if _installed:
        return True
    try:
        mem.on("memory.surfaced")(_on_surfaced)
        _installed = True
        log.info("[recall] memory.surfaced consumer wired")
        return True
    except Exception:  # noqa: BLE001
        log.warning("[recall] could not wire memory.surfaced consumer", exc_info=True)
        return False


def _on_surfaced(data: dict) -> None:
    """Stash a surfaced offer. Sync handler (nmem dispatches sync or async)."""
    try:
        if not isinstance(data, dict):
            return
        results = data.get("results") or []
        skills = data.get("skills") or []
        if not results and not skills:
            return
        _BUF.append({
            "trigger": (data.get("trigger") or "").strip(),
            "reason": data.get("reason") or "",
            "results": results,
            "skills": skills,
            "ts": time.time(),
        })
        log.info("[recall] surfaced for '%s': %d memories, %d skills",
                 (data.get("trigger") or "")[:60], len(results), len(skills))
    except Exception:  # noqa: BLE001
        log.warning("[recall] stashing surfaced event failed", exc_info=True)


def _tokens(s: str) -> set:
    return {w for w in "".join(c.lower() if c.isalnum() else " " for c in (s or "")).split()
            if len(w) > 3}


def context_for(query: str, *, max_items: int = 3, consume: bool = True) -> str:
    """Format surfaced memory relevant to ``query`` for injection into a task.

    Ranks buffered offers by trigger<->query token overlap (recency breaks ties),
    takes the best ``max_items``, and — when ``consume`` — removes them so each
    surfaced offer is injected at most once. Returns "" when nothing is buffered
    (the common case until autonomy/the recall drive fires)."""
    if not _BUF:
        return ""
    try:
        qt = _tokens(query)
        scored = []
        for i, item in enumerate(_BUF):
            overlap = len(qt & _tokens(item["trigger"])) if qt else 0
            scored.append((overlap, item["ts"], i, item))
        scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
        chosen = [t for t in scored[:max_items] if (t[3]["results"] or t[3]["skills"])]
        if not chosen:
            return ""

        lines: list[str] = []
        for _ov, _ts, _i, item in chosen:
            for r in item["results"][:4]:
                title = (r.get("title") or "").strip()
                content = (r.get("content") or "").strip()
                snippet = (f"{title}: " if title else "") + content
                if snippet:
                    lines.append(f"- {snippet[:220]}")
            for s in item["skills"][:3]:
                what = (s.get("what") or s.get("name") or "").strip()
                if what:
                    verb = "DO" if s.get("worked", True) else "AVOID"
                    lines.append(f"- [{verb}] {what[:180]}")

        if consume:
            for _ov, _ts, _i, item in chosen:
                try:
                    _BUF.remove(item)
                except ValueError:
                    pass

        if not lines:
            return ""
        return ("Memory your recall surfaced for this — build on what you already "
                "know, don't re-derive it:\n" + "\n".join(lines))
    except Exception:  # noqa: BLE001
        log.warning("[recall] context_for failed", exc_info=True)
        return ""


def buffer_size() -> int:
    """Introspection for health/validation."""
    return len(_BUF)
