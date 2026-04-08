"""
nmem — Cognitive memory for AI agents.

Hierarchical, self-refining, framework-agnostic memory that makes agents
genuinely learn from experience.

6 memory tiers:
  1. Working Memory  — ephemeral per-session context
  2. Journal         — 30-day activity log with auto-promotion
  3. Long-Term Memory — permanent, versioned, per-agent knowledge
  4. Shared Knowledge — cross-agent canonical facts
  5. Entity Memory   — collaborative workspace per business object
  6. Policy Memory   — governance rules with permissions

Quick start:
    from nmem import MemorySystem, NmemConfig

    mem = MemorySystem(NmemConfig(
        database_url="postgresql+asyncpg://localhost/mydb",
        embedding={"provider": "sentence-transformers"},
    ))
    await mem.initialize()

    await mem.journal.add(agent_id="agent1", entry_type="note",
                          title="First memory", content="Hello world")
    results = await mem.search(agent_id="agent1", query="hello")
"""

from nmem._version import __version__
from nmem.config import NmemConfig
from nmem.memory import MemorySystem
from nmem.types import (
    ConsolidationStats,
    CuriositySignalInfo,
    DelegationRecord,
    EntityRecord,
    JournalEntry,
    LTMEntry,
    MemoryConflictInfo,
    PolicyEntry,
    PromptContext,
    SearchResult,
    SharedEntry,
    WorkingSlot,
)

__all__ = [
    # Core
    "MemorySystem",
    "NmemConfig",
    "__version__",
    # Types
    "ConsolidationStats",
    "CuriositySignalInfo",
    "DelegationRecord",
    "EntityRecord",
    "JournalEntry",
    "LTMEntry",
    "MemoryConflictInfo",
    "PolicyEntry",
    "PromptContext",
    "SearchResult",
    "SharedEntry",
    "WorkingSlot",
]
