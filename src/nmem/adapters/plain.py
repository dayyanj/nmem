"""
Plain synchronous wrapper for nmem.

Wraps the async API using asyncio.run() for users who don't need async.

Usage:
    from nmem.adapters.plain import SyncMemorySystem

    mem = SyncMemorySystem(NmemConfig(database_url="sqlite+aiosqlite:///memory.db"))
    mem.initialize()
    mem.journal.add(agent_id="agent1", entry_type="note", title="Hello", content="World")
"""

from __future__ import annotations

import asyncio
from typing import Any

from nmem.config import NmemConfig


class _SyncTierProxy:
    """Wraps an async tier, making all methods synchronous."""

    def __init__(self, tier: Any, loop: asyncio.AbstractEventLoop):
        self._tier = tier
        self._loop = loop

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._tier, name)
        if asyncio.iscoroutinefunction(attr):
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                return self._loop.run_until_complete(attr(*args, **kwargs))
            sync_wrapper.__name__ = name
            sync_wrapper.__doc__ = attr.__doc__
            return sync_wrapper
        return attr


class SyncMemorySystem:
    """Synchronous wrapper around MemorySystem.

    Creates its own event loop. Not thread-safe.
    """

    def __init__(self, config: NmemConfig | None = None, **kwargs: Any):
        from nmem.memory import MemorySystem

        self._loop = asyncio.new_event_loop()
        self._mem = MemorySystem(config, **kwargs)

    def initialize(self) -> None:
        """Initialize the memory system (create tables, etc.)."""
        self._loop.run_until_complete(self._mem.initialize())

    @property
    def working(self) -> _SyncTierProxy:
        return _SyncTierProxy(self._mem.working, self._loop)

    @property
    def journal(self) -> _SyncTierProxy:
        return _SyncTierProxy(self._mem.journal, self._loop)

    @property
    def ltm(self) -> _SyncTierProxy:
        return _SyncTierProxy(self._mem.ltm, self._loop)

    @property
    def shared(self) -> _SyncTierProxy:
        return _SyncTierProxy(self._mem.shared, self._loop)

    @property
    def entity(self) -> _SyncTierProxy:
        return _SyncTierProxy(self._mem.entity, self._loop)

    @property
    def policy(self) -> _SyncTierProxy:
        return _SyncTierProxy(self._mem.policy, self._loop)

    def search(self, *args: Any, **kwargs: Any) -> Any:
        return self._loop.run_until_complete(self._mem.search(*args, **kwargs))

    def close(self) -> None:
        self._loop.run_until_complete(self._mem.close())
        self._loop.close()
