"""
Background consolidation engine.

Three trigger modes:
  1. Micro-cycles  — reactive, triggered by high-importance journal entries
  2. Full cycles   — every N hours (default 6), runs all consolidation steps
  3. Nightly synthesis — daily, extracts cross-cutting patterns from journal

Consolidation steps:
  - Decay expired journal entries (delete or promote)
  - Promote high-importance journal entries to LTM
  - Deduplicate similar entries (union-find clustering + LLM merge)
  - Decay confidence on stale LTM entries
  - Decay curiosity signal scores
  - Nightly synthesis (extract patterns, retroactive significance boost)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable, Awaitable

from nmem.types import ConsolidationStats

if TYPE_CHECKING:
    from nmem.db.session import DatabaseManager
    from nmem.config import NmemConfig
    from nmem.providers.embedding.base import EmbeddingProvider
    from nmem.providers.llm.base import LLMProvider

logger = logging.getLogger(__name__)


class Consolidator:
    """Background memory consolidation engine."""

    def __init__(
        self,
        db: DatabaseManager,
        config: NmemConfig,
        embedding: EmbeddingProvider,
        llm: LLMProvider,
    ):
        self._db = db
        self._config = config
        self._embedding = embedding
        self._llm = llm

        # Signal for reactive micro-cycles
        self._signal = asyncio.Event()
        self._signal_reason: str = ""

        # Custom consolidation hooks
        self._full_cycle_hooks: list[tuple[str, Callable[[], Awaitable[None]]]] = []
        self._nightly_hooks: list[tuple[str, Callable[[], Awaitable[None]]]] = []

        # Background task reference
        self._task: asyncio.Task | None = None
        self._running = False

        # Last cycle timestamps
        self._last_full_cycle: datetime | None = None
        self._last_micro_cycle: datetime | None = None
        self._last_nightly: datetime | None = None

    def signal(self, reason: str = "high_importance_entry") -> None:
        """Signal the consolidator to wake for a micro-cycle."""
        self._signal_reason = reason
        self._signal.set()

    def register_full_cycle_step(
        self, name: str, fn: Callable[[], Awaitable[None]]
    ) -> None:
        """Register a custom step to run during full consolidation cycles.

        Args:
            name: Step name (for logging).
            fn: Async callable to execute.
        """
        self._full_cycle_hooks.append((name, fn))

    def register_nightly_step(
        self, name: str, fn: Callable[[], Awaitable[None]]
    ) -> None:
        """Register a custom step to run during nightly synthesis.

        Args:
            name: Step name (for logging).
            fn: Async callable to execute.
        """
        self._nightly_hooks.append((name, fn))

    def start(self) -> asyncio.Task:
        """Start the background consolidation loop.

        Returns:
            The asyncio.Task running the loop.
        """
        if self._task and not self._task.done():
            return self._task
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="nmem-consolidator")
        logger.info("Consolidator started (interval=%dh)", self._config.consolidation.interval_hours)
        return self._task

    def stop(self) -> None:
        """Stop the background consolidation loop."""
        self._running = False
        self._signal.set()  # Wake the loop so it can exit
        if self._task:
            self._task.cancel()
            self._task = None
        logger.info("Consolidator stopped")

    async def run_full_cycle(self) -> ConsolidationStats:
        """Run a full consolidation cycle manually.

        Returns:
            Statistics from the cycle.
        """
        import time

        start = time.monotonic()
        stats = ConsolidationStats()

        logger.info("Starting full consolidation cycle")

        # Step 1: Decay expired journal entries
        # TODO: Phase 4 — port from memory_consolidator.py

        # Step 2: Promote high-importance journal entries to LTM
        # TODO: Phase 4 — port promotion logic

        # Step 3: Deduplicate similar entries
        # TODO: Phase 4 — port union-find clustering + LLM merge

        # Step 4: Confidence decay on stale LTM
        # TODO: Phase 4 — port confidence decay

        # Step 5: Custom hooks
        for name, fn in self._full_cycle_hooks:
            try:
                await fn()
            except Exception as e:
                logger.warning("Custom consolidation step '%s' failed: %s", name, e)

        # Step 6: Curiosity signal decay
        # TODO: Phase 4 — port curiosity decay

        stats.duration_seconds = time.monotonic() - start
        self._last_full_cycle = datetime.now(timezone.utc)
        logger.info("Full consolidation completed in %.1fs: %s", stats.duration_seconds, stats)
        return stats

    async def run_micro_cycle(self, reason: str = "") -> ConsolidationStats:
        """Run a fast micro-cycle (promotion pass only).

        Args:
            reason: Why this micro-cycle was triggered.

        Returns:
            Statistics from the cycle.
        """
        logger.debug("Micro-cycle triggered: %s", reason)
        stats = ConsolidationStats()
        # TODO: Phase 4 — fast promotion pass
        self._last_micro_cycle = datetime.now(timezone.utc)
        return stats

    async def run_nightly_synthesis(self) -> ConsolidationStats:
        """Run nightly synthesis — extract patterns from journal entries.

        Returns:
            Statistics from the synthesis.
        """
        logger.info("Starting nightly synthesis")
        stats = ConsolidationStats()
        # TODO: Phase 4 — port nightly synthesis (journal aggregate + LLM pattern extraction)

        # Custom nightly hooks
        for name, fn in self._nightly_hooks:
            try:
                await fn()
            except Exception as e:
                logger.warning("Custom nightly step '%s' failed: %s", name, e)

        self._last_nightly = datetime.now(timezone.utc)
        return stats

    async def _loop(self) -> None:
        """Main consolidation loop."""
        interval = self._config.consolidation.interval_hours * 3600
        cooldown = self._config.consolidation.micro_cycle_cooldown_minutes * 60

        while self._running:
            try:
                # Wait for signal or timeout (full cycle interval)
                try:
                    await asyncio.wait_for(self._signal.wait(), timeout=interval)
                    # Signal received — run micro-cycle
                    self._signal.clear()
                    if not self._running:
                        break
                    await self.run_micro_cycle(self._signal_reason)
                    await asyncio.sleep(cooldown)
                except asyncio.TimeoutError:
                    # Timeout — run full cycle
                    if not self._running:
                        break
                    await self.run_full_cycle()

                    # Check if nightly synthesis is due
                    now = datetime.now(timezone.utc)
                    target_hour = self._config.consolidation.nightly_synthesis_hour_utc
                    if now.hour == target_hour and (
                        not self._last_nightly
                        or (now - self._last_nightly).total_seconds() > 23 * 3600
                    ):
                        await self.run_nightly_synthesis()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Consolidation loop error: %s", e)
                await asyncio.sleep(60)  # Back off on error
