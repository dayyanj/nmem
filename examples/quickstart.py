"""
nmem quickstart — minimal example showing core features.

Prerequisites (PostgreSQL):
    pip install nmem[postgres,st,cli]
    docker compose up -d   # PostgreSQL + pgvector
    nmem init

Prerequisites (SQLite, no Docker needed):
    pip install nmem[sqlite,st,cli]
    nmem init --sqlite

Run:
    python examples/quickstart.py
"""

import asyncio
from nmem import MemorySystem, NmemConfig


async def main():
    # Initialize with sentence-transformers (local, no API key needed)
    # Switch to sqlite+aiosqlite:///nmem.db for SQLite
    mem = MemorySystem(NmemConfig.from_profile("neutral",
        database_url="postgresql+asyncpg://nmem:nmem@localhost:5433/nmem",
        embedding={"provider": "sentence-transformers"},
        # Optional: wire an LLM for content compression + nightly synthesis
        # llm={"provider": "openai", "base_url": "http://localhost:11434/v1", "model": "qwen3"},
    ))
    await mem.initialize()

    # ── Working Memory (ephemeral, per-session) ─────────────────────
    await mem.working.set("session-1", "support-agent", "current_task",
                          "Handling refund request for order #1234")
    slots = await mem.working.get("session-1", "support-agent")
    print(f"Working memory: {slots[0].content}")

    # ── Journal (30-day activity log) ───────────────────────────────
    entry = await mem.journal.add(
        agent_id="support-agent",
        entry_type="lesson_learned",
        title="Refund over $100 requires manager approval",
        content="Customer requested refund for $150 order. Discovered that refunds "
                "over $100 require manager approval per company policy. Escalated to "
                "manager who approved the refund within 2 hours.",
        importance=7,  # importance >= 7 triggers auto-promotion to LTM
    )
    print(f"Journal entry #{entry.id}: {entry.title}")

    # ── Long-Term Memory (permanent knowledge) ──────────────────────
    await mem.ltm.save(
        agent_id="support-agent",
        category="procedure",
        key="refund_escalation",
        content="Refunds over $100 require manager approval. Submit via #refunds channel.",
        importance=8,
    )

    # ── Shared Knowledge (cross-agent) ──────────────────────────────
    await mem.shared.save(
        agent_id="system",
        category="policy",
        key="refund_policy",
        content="30-day refund window. Over $100 needs manager approval. "
                "Digital products are non-refundable.",
        importance=9,
    )

    # ── Search across all tiers ─────────────────────────────────────
    results = await mem.search("support-agent", "refund process approval")
    print(f"\nCross-tier search found {len(results)} results:")
    for r in results:
        print(f"  [{r.tier}] score={r.score:.3f}: {r.content[:80]}...")

    # ── Build prompt injection ──────────────────────────────────────
    ctx = await mem.prompt.build(
        agent_id="support-agent",
        session_id="session-1",
        query="How do I process a refund?",
    )
    print(f"\nPrompt injection ({len(ctx.full_injection)} chars):")
    print(ctx.full_injection[:500])

    # ── Start background consolidation (optional) ───────────────────
    # In a real app, this runs continuously:
    # mem.start_consolidation()
    #
    # Or run a single cycle manually:
    stats = await mem.consolidation.run_full_cycle()
    print(f"\nConsolidation: promoted={stats.promoted_to_ltm}, "
          f"deduped={stats.duplicates_merged}, "
          f"duration={stats.duration_seconds:.1f}s")

    # Cleanup
    await mem.close()
    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(main())
