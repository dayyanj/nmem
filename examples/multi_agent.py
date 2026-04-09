"""
nmem multi-agent example — shared memory across a team of agents.

Demonstrates:
  - Multiple agents writing to their own journals
  - Shared knowledge propagation
  - Entity memory (collaborative workspace per customer)
  - Cross-tier search from any agent's perspective
  - Consolidation promoting learnings across the team

Prerequisites:
    pip install nmem[postgres,st]
    docker compose up -d

Run:
    python examples/multi_agent.py
"""

import asyncio
from nmem import MemorySystem, NmemConfig


async def main():
    mem = MemorySystem(NmemConfig(
        database_url="postgresql+asyncpg://nmem:nmem@localhost:5433/nmem",
        embedding={"provider": "sentence-transformers"},
    ))
    await mem.initialize()

    # ── Agent 1: Support Agent ──────────────────────────────────────
    print("=== Support Agent ===")

    await mem.journal.add(
        agent_id="support",
        entry_type="interaction",
        title="Customer Jane Doe reported billing issue",
        content="Jane Doe (jane@example.com) reported double charge on order #5678. "
                "Verified in Stripe dashboard. Initiated refund for duplicate charge.",
        importance=6,
    )

    # Save entity memory about this customer
    await mem.entity.save(
        entity_type="customer",
        entity_id="cust-jane-doe",
        entity_name="Jane Doe",
        agent_id="support",
        content="Reported double charge on order #5678. Refund initiated.",
        record_type="evidence",
        grounding="source_material",
        confidence=1.0,
    )

    # ── Agent 2: Sales Agent ────────────────────────────────────────
    print("=== Sales Agent ===")

    await mem.journal.add(
        agent_id="sales",
        entry_type="interaction",
        title="Jane Doe interested in enterprise plan upgrade",
        content="Jane Doe expressed interest in upgrading from Business to Enterprise plan. "
                "Currently spending $79/mo, enterprise would be $149/mo. "
                "Key requirements: SSO integration and priority support.",
        importance=7,
    )

    # Sales also writes to Jane's entity memory
    await mem.entity.save(
        entity_type="customer",
        entity_id="cust-jane-doe",
        entity_name="Jane Doe",
        agent_id="sales",
        content="Interested in enterprise upgrade. Key needs: SSO + priority support.",
        record_type="evidence",
        grounding="source_material",
        confidence=1.0,
    )

    # ── Agent 3: Marketing Agent ────────────────────────────────────
    print("=== Marketing Agent ===")

    await mem.journal.add(
        agent_id="marketing",
        entry_type="analysis",
        title="Enterprise conversion patterns this quarter",
        content="3 of 5 enterprise conversions this quarter started with billing issues. "
                "Billing support interactions seem to correlate with upgrade interest.",
        importance=8,
    )

    # Share this insight across all agents
    await mem.shared.save(
        agent_id="marketing",
        category="insight",
        key="billing_to_enterprise_pattern",
        content="Customers who contact support about billing issues are 3x more likely "
                "to upgrade to enterprise within 30 days. Consider proactive outreach "
                "after billing resolution.",
        importance=8,
    )

    # ── Cross-agent search ──────────────────────────────────────────
    print("\n=== Cross-Agent Search: 'Jane Doe billing' ===")
    results = await mem.search("support", "Jane Doe billing enterprise")
    for r in results:
        agent = r.metadata.get("agent_id") or r.agent_id or "shared"
        print(f"  [{r.tier}|{agent}] {r.content[:80]}...")

    # ── Entity dossier ──────────────────────────────────────────────
    print("\n=== Entity Dossier: Jane Doe ===")
    dossier = await mem.entity.build_prompt("customer", "cust-jane-doe")
    print(dossier)

    # ── Each agent's prompt sees shared knowledge ───────────────────
    print("\n=== Support Agent's Context ===")
    ctx = await mem.prompt.build(agent_id="support", query="Jane Doe")
    print(ctx.full_injection[:500])

    print("\n=== Sales Agent's Context ===")
    ctx = await mem.prompt.build(agent_id="sales", query="Jane Doe enterprise upgrade")
    print(ctx.full_injection[:500])

    # ── Consolidation ───────────────────────────────────────────────
    print("\n=== Running Consolidation ===")
    stats = await mem.consolidation.run_full_cycle()
    print(f"Promoted: {stats.promoted_to_ltm}, Deduped: {stats.duplicates_merged}")

    await mem.close()
    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(main())
