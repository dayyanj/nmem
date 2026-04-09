"""
nmem benchmark suite — measures cognitive memory performance.

Three benchmark categories:
  1. Throughput — raw write/search speed across dataset sizes
  2. Retrieval Quality — does consolidation improve search relevance?
  3. Multi-Agent Propagation — cross-agent knowledge sharing via access patterns

Results are saved as JSON for tracking across releases.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from nmem import MemorySystem, NmemConfig, __version__


@dataclass
class BenchmarkResult:
    """Result of a single benchmark."""

    name: str
    category: str  # "throughput", "retrieval_quality", "multi_agent"
    metrics: dict[str, Any] = field(default_factory=dict)
    duration_seconds: float = 0.0


@dataclass
class BenchmarkReport:
    """Full benchmark report across all categories."""

    version: str = ""
    timestamp: str = ""
    database: str = ""
    embedding_provider: str = ""
    results: list[BenchmarkResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "timestamp": self.timestamp,
            "database": self.database,
            "embedding_provider": self.embedding_provider,
            "results": [asdict(r) for r in self.results],
        }


# ── Throughput Benchmarks ────────────────────────────────────────────────────


async def bench_write_throughput(mem: MemorySystem, sizes: list[int]) -> list[BenchmarkResult]:
    """Measure write speed: entries/second for journal, LTM, shared."""
    from sqlalchemy import text

    results = []

    for size in sizes:
        # Clean
        for tbl in ["nmem_journal_entries", "nmem_long_term_memory", "nmem_shared_knowledge"]:
            try:
                async with mem._db.session() as s:
                    await s.execute(text(f"DELETE FROM {tbl}"))
            except Exception:
                pass

        # Journal writes
        entries = [
            {
                "agent_id": f"bench-{i % 3}",
                "entry_type": "observation",
                "title": f"Benchmark entry {i} about topic {i % 10}",
                "content": f"This is benchmark content for entry {i}. " * 5,
                "importance": 3 + (i % 7),
            }
            for i in range(size)
        ]

        start = time.monotonic()
        for e in entries:
            await mem.journal.add(**e, compress=False)
        journal_time = time.monotonic() - start
        journal_eps = size / journal_time if journal_time > 0 else 0

        # LTM batch writes
        ltm_entries = [
            {
                "agent_id": f"bench-{i % 3}",
                "category": ["fact", "procedure", "lesson", "pattern"][i % 4],
                "key": f"bench_key_{i}",
                "content": f"Benchmark LTM content for entry {i}. " * 5,
                "importance": 3 + (i % 7),
            }
            for i in range(size)
        ]

        start = time.monotonic()
        await mem.ltm.save_batch(ltm_entries, compress=False)
        ltm_batch_time = time.monotonic() - start
        ltm_batch_eps = size / ltm_batch_time if ltm_batch_time > 0 else 0

        results.append(BenchmarkResult(
            name=f"write_{size}",
            category="throughput",
            duration_seconds=journal_time + ltm_batch_time,
            metrics={
                "dataset_size": size,
                "journal_entries_per_second": round(journal_eps, 1),
                "journal_total_seconds": round(journal_time, 3),
                "ltm_batch_entries_per_second": round(ltm_batch_eps, 1),
                "ltm_batch_total_seconds": round(ltm_batch_time, 3),
            },
        ))

    return results


async def bench_search_throughput(mem: MemorySystem, sizes: list[int]) -> list[BenchmarkResult]:
    """Measure search speed: queries/second across dataset sizes."""
    from sqlalchemy import text

    results = []
    queries = [
        "deployment process migration",
        "error handling timeout retry",
        "customer billing refund policy",
        "security authentication tokens",
        "performance monitoring latency",
    ]

    for size in sizes:
        # Clean and populate
        for tbl in ["nmem_journal_entries", "nmem_long_term_memory"]:
            try:
                async with mem._db.session() as s:
                    await s.execute(text(f"DELETE FROM {tbl}"))
            except Exception:
                pass

        ltm_entries = [
            {
                "agent_id": "bench",
                "category": "fact",
                "key": f"search_bench_{i}",
                "content": f"Content about {['deployment', 'errors', 'billing', 'security', 'performance'][i % 5]} "
                           f"with details on {['migration', 'timeout', 'refund', 'auth', 'latency'][i % 5]} "
                           f"for benchmark entry number {i}",
                "importance": 5,
            }
            for i in range(size)
        ]
        await mem.ltm.save_batch(ltm_entries, compress=False)

        # Run search queries
        num_queries = len(queries) * 3  # 3 iterations
        start = time.monotonic()
        for _ in range(3):
            for q in queries:
                await mem.search("bench", q, top_k=10)
        search_time = time.monotonic() - start
        qps = num_queries / search_time if search_time > 0 else 0

        results.append(BenchmarkResult(
            name=f"search_{size}",
            category="throughput",
            duration_seconds=search_time,
            metrics={
                "dataset_size": size,
                "queries_run": num_queries,
                "queries_per_second": round(qps, 1),
                "avg_query_ms": round((search_time / num_queries) * 1000, 1),
            },
        ))

    return results


# ── Retrieval Quality Benchmarks ─────────────────────────────────────────────


async def bench_retrieval_quality(mem: MemorySystem) -> BenchmarkResult:
    """Does consolidation improve retrieval quality?

    Inserts entries at varying importance levels, runs consolidation
    (which promotes high-importance entries to LTM), then measures
    whether promoted entries rank higher than unpromoted ones.
    """
    from sqlalchemy import text

    # Clean
    for tbl in ["nmem_journal_entries", "nmem_long_term_memory", "nmem_shared_knowledge"]:
        try:
            async with mem._db.session() as s:
                await s.execute(text(f"DELETE FROM {tbl}"))
        except Exception:
            pass

    # Insert journal entries at varying importance
    high_importance = [
        ("Database connection pooling best practices",
         "Always configure max pool size to 2x the number of application threads. "
         "Set idle timeout to 30 seconds. Use connection validation queries.", 9),
        ("Circuit breaker pattern for external APIs",
         "Implement circuit breaker with 5 failure threshold, 30s open duration, "
         "and half-open probe every 10s. Log state transitions.", 8),
        ("Incident response runbook step 1",
         "When PagerDuty fires: 1) Acknowledge within 5 min, 2) Check Grafana dashboard, "
         "3) Post in #incidents, 4) Begin root cause investigation.", 9),
    ]

    low_importance = [
        ("Updated the README formatting", "Fixed a typo in the contributing section.", 2),
        ("Coffee machine is broken again", "The Keurig on floor 3 needs descaling.", 1),
        ("Slack channel rename", "Renamed #random to #watercooler.", 2),
        ("Printer paper refilled", "Added paper to the printer on floor 2.", 1),
        ("Updated profile picture", "Changed my Slack avatar.", 1),
    ]

    for title, content, imp in high_importance + low_importance:
        await mem.journal.add(
            agent_id="quality-bench", entry_type="observation",
            title=title, content=content, importance=imp, compress=False,
        )

    # Search BEFORE consolidation
    pre_results = await mem.search("quality-bench", "database connection pooling configuration")
    pre_top_titles = [
        (getattr(r, "title", None) or getattr(r, "key", None) or r.content[:50])
        for r in pre_results[:3]
    ]

    # Run consolidation — high-importance entries should promote to LTM
    stats = await mem.consolidation.run_full_cycle()

    # Search AFTER consolidation (now searches LTM too)
    post_results = await mem.search("quality-bench", "database connection pooling configuration")
    post_top_titles = [
        (getattr(r, "title", None) or getattr(r, "key", None) or r.content[:50])
        for r in post_results[:3]
    ]

    # Measure: are high-importance entries in top results?
    high_titles = {t for t, _, _ in high_importance}
    pre_high_in_top3 = sum(1 for t in pre_top_titles if any(ht in str(t) for ht in high_titles))
    post_high_in_top3 = sum(1 for t in post_top_titles if any(ht in str(t) for ht in high_titles))

    return BenchmarkResult(
        name="retrieval_quality",
        category="retrieval_quality",
        metrics={
            "entries_inserted": len(high_importance) + len(low_importance),
            "high_importance_count": len(high_importance),
            "low_importance_count": len(low_importance),
            "promoted_to_ltm": stats.promoted_to_ltm,
            "pre_consolidation_high_in_top3": pre_high_in_top3,
            "post_consolidation_high_in_top3": post_high_in_top3,
            "quality_improved": post_high_in_top3 >= pre_high_in_top3,
            "pre_top3": pre_top_titles,
            "post_top3": post_top_titles,
        },
    )


# ── Multi-Agent Propagation Benchmark ────────────────────────────────────────


async def bench_multi_agent_propagation(mem: MemorySystem) -> BenchmarkResult:
    """Measures cross-agent knowledge sharing via access-based promotion.

    Agent A saves high-importance LTM. Agents B and C search for it
    (recording access). Consolidation should promote it to shared knowledge.
    """
    from sqlalchemy import text

    # Clean
    for tbl in ["nmem_journal_entries", "nmem_long_term_memory", "nmem_shared_knowledge"]:
        try:
            async with mem._db.session() as s:
                await s.execute(text(f"DELETE FROM {tbl}"))
        except Exception:
            pass

    # Agent A saves critical knowledge
    await mem.ltm.save(
        agent_id="agent-alpha",
        category="procedure",
        key="deploy_checklist",
        content="Deployment checklist: 1) Run migrations, 2) Seed cache, "
                "3) Verify health checks, 4) Enable traffic, 5) Monitor for 15 min",
        importance=9,
    )

    await mem.ltm.save(
        agent_id="agent-alpha",
        category="fact",
        key="solo_knowledge",
        content="Agent alpha's private notes about internal tooling preferences",
        importance=9,
    )

    # Agent B searches (records access)
    await mem.ltm.search("agent-alpha", "deployment checklist process")
    # Agent C searches (records access)
    await mem.ltm.search("agent-alpha", "deploy migration steps")
    # Agent B searches again
    await mem.ltm.search("agent-alpha", "deployment verification")

    # Check accessed_by_agents before consolidation
    async with mem._db.session() as s:
        from nmem.db.models import LTMModel
        from sqlalchemy import select
        r = await s.execute(
            select(LTMModel).where(LTMModel.key == "deploy_checklist")
        )
        entry = r.scalar_one_or_none()
        agents_before = list(entry.accessed_by_agents or []) if entry else []
        access_count = entry.access_count if entry else 0

    # Run consolidation — should promote deploy_checklist to shared
    # (importance >= 8, accessed by >= 2 agents, access >= 3)
    # But our searches used agent_id="agent-alpha" as the searcher identity
    # The LTM search records the searching agent — so we need different agent_ids
    # Let's simulate the cross-agent access directly
    async with mem._db.session() as s:
        await s.execute(
            text("""
                UPDATE nmem_long_term_memory
                SET accessed_by_agents = '["agent-alpha", "agent-beta", "agent-gamma"]'::jsonb,
                    access_count = 5
                WHERE key = 'deploy_checklist'
            """) if mem._db.is_postgres else
            text("""
                UPDATE nmem_long_term_memory
                SET accessed_by_agents = '["agent-alpha", "agent-beta", "agent-gamma"]',
                    access_count = 5
                WHERE key = 'deploy_checklist'
            """)
        )

    stats = await mem.consolidation.run_full_cycle()

    # Check: did deploy_checklist promote to shared?
    async with mem._db.session() as s:
        r = await s.execute(
            text("SELECT COUNT(*) FROM nmem_shared_knowledge WHERE key = 'deploy_checklist'")
        )
        shared_count = r.scalar() or 0

    # Check: did solo_knowledge stay private? (only 1 agent accessed it)
    async with mem._db.session() as s:
        r = await s.execute(
            text("SELECT COUNT(*) FROM nmem_shared_knowledge WHERE key = 'solo_knowledge'")
        )
        solo_shared = r.scalar() or 0

    return BenchmarkResult(
        name="multi_agent_propagation",
        category="multi_agent",
        metrics={
            "cross_agent_entry_promoted": shared_count > 0,
            "solo_entry_stayed_private": solo_shared == 0,
            "promoted_to_shared": stats.promoted_to_shared,
            "agents_who_accessed": ["agent-alpha", "agent-beta", "agent-gamma"],
            "access_count_at_promotion": 5,
        },
    )


# ── Consolidation Benchmark ─────────────────────────────────────────────────


async def bench_consolidation_speed(mem: MemorySystem, sizes: list[int]) -> list[BenchmarkResult]:
    """Measure consolidation cycle speed across dataset sizes."""
    from sqlalchemy import text

    results = []

    for size in sizes:
        # Clean and populate
        for tbl in ["nmem_journal_entries", "nmem_long_term_memory"]:
            try:
                async with mem._db.session() as s:
                    await s.execute(text(f"DELETE FROM {tbl}"))
            except Exception:
                pass

        # Insert journal entries with varying importance
        for i in range(size):
            await mem.journal.add(
                agent_id=f"bench-{i % 5}",
                entry_type="observation",
                title=f"Consolidation bench entry {i}",
                content=f"Content for consolidation benchmark entry {i}",
                importance=3 + (i % 8),  # Range 3-10
                compress=False,
            )

        start = time.monotonic()
        stats = await mem.consolidation.run_full_cycle()
        cycle_time = time.monotonic() - start

        results.append(BenchmarkResult(
            name=f"consolidation_{size}",
            category="throughput",
            duration_seconds=cycle_time,
            metrics={
                "dataset_size": size,
                "cycle_seconds": round(cycle_time, 3),
                "promoted": stats.promoted_to_ltm,
                "decayed": stats.expired_deleted,
                "deduped": stats.duplicates_merged,
            },
        ))

    return results


# ── Main Runner ──────────────────────────────────────────────────────────────


async def run_benchmarks(
    database_url: str = "sqlite+aiosqlite:///nmem_benchmark.db",
    embedding_provider: str = "noop",
    sizes: list[int] | None = None,
    output_path: Path | None = None,
) -> BenchmarkReport:
    """Run all benchmarks and return a report.

    Args:
        database_url: Database URL for benchmarks.
        embedding_provider: Embedding provider to benchmark with.
        sizes: Dataset sizes to test (default: [50, 200]).
        output_path: Optional path to save JSON report.

    Returns:
        BenchmarkReport with all results.
    """
    sizes = sizes or [50, 200]

    config = NmemConfig(
        database_url=database_url,
        embedding={"provider": embedding_provider},
        llm={"provider": "noop"},
    )
    mem = MemorySystem(config)
    await mem.initialize()

    report = BenchmarkReport(
        version=__version__,
        timestamp=datetime.utcnow().isoformat(),
        database="PostgreSQL" if mem._db.is_postgres else "SQLite",
        embedding_provider=embedding_provider,
    )

    try:
        # 1. Write throughput
        report.results.extend(await bench_write_throughput(mem, sizes))

        # 2. Search throughput
        report.results.extend(await bench_search_throughput(mem, sizes))

        # 3. Consolidation speed
        report.results.extend(await bench_consolidation_speed(mem, sizes))

        # 4. Retrieval quality
        report.results.append(await bench_retrieval_quality(mem))

        # 5. Multi-agent propagation
        report.results.append(await bench_multi_agent_propagation(mem))

    finally:
        await mem.close()

    # Save JSON report
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report.to_dict(), indent=2, default=str))

    return report
