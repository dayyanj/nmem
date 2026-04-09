"""
Integration tests against real PostgreSQL + pgvector.

Requires: docker compose up -d (nmem-db on port 5433)
Run: pytest tests/integration/ -v
"""

import asyncio
import os
import pytest

from nmem import MemorySystem, NmemConfig

# Skip if no database available
NMEM_TEST_DSN = os.environ.get(
    "NMEM_TEST_DSN", "postgresql+asyncpg://nmem:nmem@localhost:5433/nmem"
)


def _can_connect() -> bool:
    """Check if PostgreSQL is reachable."""
    import asyncpg

    async def check():
        try:
            conn = await asyncpg.connect("postgresql://nmem:nmem@localhost:5433/nmem")
            await conn.close()
            return True
        except Exception:
            return False

    return asyncio.get_event_loop().run_until_complete(check())


try:
    _pg_available = _can_connect()
except Exception:
    _pg_available = False

pytestmark = pytest.mark.skipif(not _pg_available, reason="PostgreSQL not available")


async def _clean_tables(system: MemorySystem):
    """Delete all rows from nmem tables."""
    from sqlalchemy import text
    async with system._db.session() as session:
        for table in [
            "nmem_working_memory",
            "nmem_journal_entries",
            "nmem_long_term_memory",
            "nmem_shared_knowledge",
            "nmem_entity_memory",
            "nmem_delegations",
            "nmem_curiosity_signals",
        ]:
            try:
                await session.execute(text(f"DELETE FROM {table}"))
            except Exception:
                pass


@pytest.fixture
async def mem():
    """Create a MemorySystem connected to real PostgreSQL."""
    config = NmemConfig(database_url=NMEM_TEST_DSN)
    system = MemorySystem(config)
    await system.initialize()
    # Clean BEFORE tests to avoid stale data
    await _clean_tables(system)
    # Reset thread centroids cache
    from nmem.search import _thread_centroids
    _thread_centroids.clear()
    yield system
    await _clean_tables(system)
    await system.close()


class TestJournalWithPostgres:
    """Test journal tier with real pgvector hybrid search."""

    async def test_add_creates_entry(self, mem: MemorySystem):
        entry = await mem.journal.add(
            "test-agent", "observation", "Server restarted",
            "The server restarted at 3am due to OOM killer",
            importance=6,
        )
        assert entry.id is not None
        assert entry.title == "Server restarted"
        assert entry.importance == 6
        assert entry.context_thread_id is not None

    async def test_hybrid_search(self, mem: MemorySystem):
        """Test that hybrid search returns vector+FTS ranked results."""
        await mem.journal.add(
            "test-agent", "observation", "Database migration completed",
            "Successfully migrated the PostgreSQL database from v15 to v16",
            importance=7,
        )
        await mem.journal.add(
            "test-agent", "decision", "Chose Redis for caching",
            "After benchmarking, Redis was selected for session caching",
            importance=5,
        )
        await mem.journal.add(
            "test-agent", "observation", "API latency spike",
            "Observed 500ms p99 latency on the /search endpoint",
            importance=8,
        )

        # Search for database-related entries
        results = await mem.journal.search("test-agent", "database migration")
        assert len(results) > 0
        # The database migration entry should be IN the results (FTS ensures this)
        titles = [r.title.lower() for r in results]
        assert any("migration" in t for t in titles), f"Expected 'migration' in {titles}"

    async def test_dedup_skips_duplicates(self, mem: MemorySystem):
        """Test that near-identical entries are deduplicated."""
        entry1 = await mem.journal.add(
            "test-agent", "observation", "Server crashed due to OOM",
            "The production server crashed because of out-of-memory",
            importance=8,
        )
        # Add a near-identical entry
        entry2 = await mem.journal.add(
            "test-agent", "observation", "Server crashed due to OOM",
            "The production server crashed because of out-of-memory killer",
            importance=6,
        )
        # Second entry should be the same ID (deduplicated)
        assert entry2.id == entry1.id

    async def test_tsvector_populated(self, mem: MemorySystem):
        """Test that content_tsv is populated for hybrid search."""
        entry = await mem.journal.add(
            "test-agent", "observation", "Deployment completed",
            "Deployed version 2.5.0 to production cluster",
            importance=5,
        )
        # Verify TSVECTOR was populated
        from sqlalchemy import text

        async with mem._db.session() as session:
            result = await session.execute(
                text("SELECT content_tsv IS NOT NULL FROM nmem_journal_entries WHERE id = :id"),
                {"id": entry.id},
            )
            has_tsv = result.scalar()
            assert has_tsv is True

    async def test_context_threading(self, mem: MemorySystem):
        """Test that similar entries get assigned to the same context thread."""
        e1 = await mem.journal.add(
            "test-agent", "observation", "Database backup started running",
            "Automated PostgreSQL database backup started running at midnight",
            importance=5,
        )
        e2 = await mem.journal.add(
            "test-agent", "observation", "Database backup finished running",
            "Automated PostgreSQL database backup finished running successfully",
            importance=5,
        )
        # These share most words so hash embeddings cluster them together
        assert e1.context_thread_id == e2.context_thread_id


class TestLTMWithPostgres:
    """Test LTM tier with real pgvector."""

    async def test_save_and_hybrid_search(self, mem: MemorySystem):
        await mem.ltm.save(
            "test-agent", "procedure", "deploy_process",
            "Always run migrations before deploying new code to production",
            importance=8,
        )
        await mem.ltm.save(
            "test-agent", "lesson", "cache_invalidation",
            "Redis cache must be flushed after schema changes",
            importance=7,
        )

        # Search with terms that overlap with deploy_process content
        results = await mem.ltm.search("test-agent", "deploy migrations code production")
        assert len(results) > 0
        keys = [r.key for r in results]
        assert "deploy_process" in keys, f"Expected 'deploy_process' in {keys}"

    async def test_tsvector_on_save(self, mem: MemorySystem):
        await mem.ltm.save(
            "test-agent", "fact", "team_size",
            "Engineering team has 12 members across 3 squads",
            importance=5,
        )
        from sqlalchemy import text

        async with mem._db.session() as session:
            result = await session.execute(
                text("SELECT content_tsv IS NOT NULL FROM nmem_long_term_memory WHERE key = 'team_size'"),
            )
            assert result.scalar() is True


class TestSharedWithPostgres:
    """Test shared knowledge with real pgvector."""

    async def test_save_and_search(self, mem: MemorySystem):
        await mem.shared.save(
            "system", "company_policy", "remote_work",
            "Remote work is allowed 3 days per week",
            importance=7,
        )
        results = await mem.shared.search("work from home policy")
        assert len(results) > 0


class TestWorkingMemoryWithPostgres:
    """Test working memory with real PostgreSQL."""

    async def test_set_get_clear(self, mem: MemorySystem):
        await mem.working.set("session-1", "agent-1", "current_task", "Reviewing PR #42")
        slots = await mem.working.get("session-1", "agent-1")
        assert len(slots) == 1
        assert slots[0].content == "Reviewing PR #42"

        await mem.working.clear("session-1", "agent-1")
        slots = await mem.working.get("session-1", "agent-1")
        assert len(slots) == 0


class TestConsolidation:
    """Test consolidation engine against real database."""

    async def test_promote_high_importance(self, mem: MemorySystem):
        """High-importance journal entries should promote to LTM."""
        await mem.journal.add(
            "test-agent", "lesson", "Critical: always validate inputs",
            "Input validation failure caused a production outage",
            importance=9,
        )

        # Run promotion
        stats = await mem.consolidation.run_micro_cycle("test")
        assert stats.promoted_to_ltm >= 1

        # Verify it exists in LTM
        ltm_entries = await mem.ltm.search("test-agent", "input validation")
        assert len(ltm_entries) > 0

    async def test_confidence_decay(self, mem: MemorySystem):
        """Stale LTM entries should have confidence decayed."""
        from sqlalchemy import text

        await mem.ltm.save(
            "test-agent", "fact", "old_fact",
            "This is an old fact that nobody accesses",
            importance=5,
        )
        # Artificially age the entry
        async with mem._db.session() as session:
            await session.execute(
                text("""
                    UPDATE nmem_long_term_memory
                    SET created_at = NOW() - INTERVAL '60 days',
                        access_count = 0
                    WHERE key = 'old_fact'
                """)
            )

        decayed = await mem.consolidation._update_confidence_scores()
        assert decayed >= 1

    async def test_ltm_to_shared_promotion(self, mem: MemorySystem):
        """LTM entries accessed by multiple agents should promote to shared."""
        from sqlalchemy import text

        # Save a high-importance LTM entry
        await mem.ltm.save(
            "agent-alpha", "procedure", "deploy_checklist",
            "Always run migrations, seed cache, then verify health checks",
            importance=8,
        )

        # Simulate access from multiple agents by updating accessed_by_agents
        async with mem._db.session() as session:
            await session.execute(
                text("""
                    UPDATE nmem_long_term_memory
                    SET accessed_by_agents = '["agent-alpha", "agent-beta", "agent-gamma"]'::jsonb,
                        access_count = 5
                    WHERE key = 'deploy_checklist'
                """)
            )

        # Run consolidation — should promote to shared
        stats = await mem.consolidation.run_full_cycle()
        assert stats.promoted_to_shared >= 1

        # Verify it exists in shared knowledge
        results = await mem.shared.search("deploy migrations checklist")
        assert len(results) > 0
        assert any("deploy_checklist" in r.key for r in results)

    async def test_ltm_not_promoted_without_cross_agent_access(self, mem: MemorySystem):
        """LTM entries accessed by only one agent should NOT promote."""
        from sqlalchemy import text

        await mem.ltm.save(
            "agent-solo", "fact", "solo_knowledge",
            "Only one agent cares about this particular piece of knowledge",
            importance=9,
        )

        # Only 1 agent accessed it
        async with mem._db.session() as session:
            await session.execute(
                text("""
                    UPDATE nmem_long_term_memory
                    SET accessed_by_agents = '["agent-solo"]'::jsonb,
                        access_count = 10
                    WHERE key = 'solo_knowledge'
                """)
            )

        stats = await mem.consolidation.run_full_cycle()
        # Should NOT promote — only 1 agent accessed it
        assert stats.promoted_to_shared == 0

    async def test_full_cycle(self, mem: MemorySystem):
        """Full consolidation cycle should complete without errors."""
        stats = await mem.consolidation.run_full_cycle()
        assert stats.duration_seconds >= 0
