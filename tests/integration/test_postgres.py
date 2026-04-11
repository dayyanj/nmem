"""
Integration tests against real PostgreSQL + pgvector.

Requires: docker compose up -d (nmem-db on port 5433)
Run: pytest tests/integration/ -v
"""

import asyncio
import os
from datetime import datetime, timedelta

import pytest

from nmem import MemorySystem, NmemConfig

# Skip if no database available
NMEM_TEST_DSN = os.environ.get(
    "NMEM_TEST_DSN", "postgresql+asyncpg://nmem:nmem@localhost:5433/nmem"
)


def _can_connect() -> bool:
    """Check if PostgreSQL is reachable."""
    try:
        import asyncpg
    except ImportError:
        return False

    async def check():
        try:
            conn = await asyncpg.connect("postgresql://nmem:nmem@localhost:5433/nmem")
            await conn.close()
            return True
        except Exception:
            return False

    try:
        return asyncio.run(check())
    except Exception:
        return False


_pg_available = _can_connect()

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

    async def test_salience_decay(self, mem: MemorySystem):
        """Stale LTM entries should have salience decayed."""
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

        decayed = await mem.consolidation._update_salience_scores()
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


class TestAutoImportanceScoring:
    """Phase 2 — heuristic importance scoring at consolidation time."""

    async def test_auto_importance_default_on_journal(self, mem: MemorySystem):
        """Journal entries with no explicit importance are auto-scored."""
        entry = await mem.journal.add(
            "test-agent", "observation", "Something happened",
            "Some event worth logging",
        )
        assert entry.auto_importance is True
        assert entry.importance == 5  # Default floor before rescoring

    async def test_explicit_importance_preserved_on_journal(self, mem: MemorySystem):
        """Explicit importance flips auto_importance off and sticks."""
        entry = await mem.journal.add(
            "test-agent", "observation", "Important event",
            "Critical observation",
            importance=8,
        )
        assert entry.auto_importance is False
        assert entry.importance == 8

    async def test_auto_importance_default_on_ltm(self, mem: MemorySystem):
        """LTM entries with no explicit importance are auto-scored."""
        entry = await mem.ltm.save(
            "test-agent", "fact", "auto_key",
            "Some fact the agent discovered",
        )
        assert entry.auto_importance is True
        assert entry.importance == 5

    async def test_explicit_importance_preserved_on_ltm(self, mem: MemorySystem):
        """Explicit LTM importance is never silently adjusted."""
        entry = await mem.ltm.save(
            "test-agent", "fact", "manual_key",
            "Fact with author-set importance",
            importance=9,
        )
        assert entry.auto_importance is False
        assert entry.importance == 9

    async def test_score_auto_importance_rescores_heuristic(self, mem: MemorySystem):
        """Heuristic scorer rescores auto entries, leaves manual alone."""
        from sqlalchemy import text

        # Manual entry — importance should NEVER move
        manual = await mem.ltm.save(
            "t", "fact", "manual_evidence",
            "High-grounding manual entry that should stay at 2",
            importance=2,
            record_type="evidence",
            grounding="source_material",
        )
        # Auto entry — importance should get rescored high (evidence + source)
        auto = await mem.ltm.save(
            "t", "fact", "auto_evidence",
            "High-grounding auto entry",
            record_type="evidence",
            grounding="source_material",
        )

        rescored = await mem.consolidation._score_auto_importance()
        assert rescored >= 1

        # Re-fetch both
        refreshed_manual = await mem.ltm.get("t", "manual_evidence")
        refreshed_auto = await mem.ltm.get("t", "auto_evidence")
        assert refreshed_manual.importance == 2, (
            "Manual importance must not change"
        )
        assert refreshed_manual.auto_importance is False
        assert refreshed_auto.importance >= 7, (
            f"Auto importance for evidence+source should be >=7, got {refreshed_auto.importance}"
        )
        assert refreshed_auto.auto_importance is True

    async def test_retroactive_boost_skips_manual(self, mem: MemorySystem):
        """Nightly _retroactive_boost must not touch author-set importance."""
        from sqlalchemy import text

        # Seed a manual journal entry
        await mem.journal.add(
            "t", "observation", "Manual observation", "content",
            importance=3,
        )

        # Simulate the _retroactive_boost SQL path directly: fake a pattern
        # that matches the entry's content and verify the guard holds.
        await mem.consolidation._retroactive_boost(
            patterns=[{"observation": "Manual observation content"}],
            since=datetime.utcnow() - timedelta(days=1),
        )

        # Manual importance should NOT be boosted
        async with mem._db.session() as session:
            result = await session.execute(
                text("SELECT importance, auto_importance FROM nmem_journal_entries "
                     "WHERE title = 'Manual observation'")
            )
            row = result.first()
            assert row is not None
            assert row[0] == 3, f"Manual importance was boosted to {row[0]}"
            assert row[1] is False

    async def test_rescore_batch_bounded(self, mem: MemorySystem):
        """Rescore respects rescore_batch_size (processes at most N rows per cycle)."""
        # Seed many auto entries
        for i in range(5):
            await mem.ltm.save(
                "batch-agent", "fact", f"batch_key_{i}",
                f"content {i}",
                record_type="observation",
                grounding="inferred",
            )

        # Temporarily lower the batch size
        original = mem.consolidation._config.importance.rescore_batch_size
        mem.consolidation._config.importance.rescore_batch_size = 2
        try:
            # First pass should process at most 2
            rescored1 = await mem.consolidation._score_auto_importance()
            assert rescored1 <= 2
        finally:
            mem.consolidation._config.importance.rescore_batch_size = original


class TestBeliefRevision:
    """Phase 3 — conflict resolution and belief revision."""

    async def _insert_conflict(
        self,
        mem: MemorySystem,
        *,
        record_a_table: str,
        record_a_id: int,
        record_b_table: str,
        record_b_id: int,
        agent_a: str,
        agent_b: str,
    ) -> int:
        """Helper: manually seed a conflict row to exercise the resolver
        without depending on the write-time detection heuristics."""
        from nmem.db.models import MemoryConflictModel

        async with mem._db.session() as session:
            conflict = MemoryConflictModel(
                record_a_table=record_a_table,
                record_a_id=record_a_id,
                record_b_table=record_b_table,
                record_b_id=record_b_id,
                agent_a=agent_a,
                agent_b=agent_b,
                similarity_score=0.5,
                description="seeded by test",
                status="open",
            )
            session.add(conflict)
            await session.flush()
            return conflict.id

    async def test_resolve_by_grounding_gap(self, mem: MemorySystem):
        """source_material beats inferred by a full rank gap → auto-resolve."""
        from sqlalchemy import text

        winner = await mem.ltm.save(
            "agent-a", "fact", "deploy_window",
            "Deployment window is Tuesdays 9am UTC",
            importance=5,
            record_type="fact",
            grounding="source_material",
        )
        loser = await mem.ltm.save(
            "agent-b", "fact", "deploy_window_alt",
            "Deployment window is Fridays 5pm UTC",
            importance=5,
            record_type="fact",
            grounding="inferred",
        )

        await self._insert_conflict(
            mem,
            record_a_table="nmem_long_term_memory", record_a_id=loser.id,
            record_b_table="nmem_long_term_memory", record_b_id=winner.id,
            agent_a="agent-b", agent_b="agent-a",
        )

        auto, review = await mem.consolidation._resolve_conflicts()
        assert auto == 1, f"Expected 1 auto-resolution, got {auto}"
        assert review == 0

        # Loser should be superseded; winner still validated
        async with mem._db.session() as session:
            result = await session.execute(text(
                "SELECT id, status, superseded_by_id FROM nmem_long_term_memory "
                "WHERE id IN (:w, :l)"
            ), {"w": winner.id, "l": loser.id})
            rows = {r[0]: r for r in result.all()}
            assert rows[winner.id][1] == "validated"
            assert rows[loser.id][1] == "superseded"
            assert rows[loser.id][2] == winner.id

    async def test_resolve_by_agent_trust_tiebreaker(self, mem: MemorySystem):
        """Equal grounding → higher trust wins."""
        from sqlalchemy import text

        # Bump config trust for opus; sonnet gets default
        mem._config.belief.agent_trust = {"opus": 0.9, "sonnet": 0.4}

        winner = await mem.ltm.save(
            "opus", "fact", "opus_version",
            "The correct answer is 42",
            importance=5,
            record_type="fact",
            grounding="inferred",
        )
        loser = await mem.ltm.save(
            "sonnet", "fact", "sonnet_version",
            "The correct answer is 99",
            importance=5,
            record_type="fact",
            grounding="inferred",
        )

        await self._insert_conflict(
            mem,
            record_a_table="nmem_long_term_memory", record_a_id=loser.id,
            record_b_table="nmem_long_term_memory", record_b_id=winner.id,
            agent_a="sonnet", agent_b="opus",
        )

        auto, review = await mem.consolidation._resolve_conflicts()
        assert auto == 1

        async with mem._db.session() as session:
            result = await session.execute(text(
                "SELECT status FROM nmem_long_term_memory WHERE id = :id"
            ), {"id": loser.id})
            assert result.scalar() == "superseded"

    async def test_tied_goes_to_needs_review(self, mem: MemorySystem):
        """Equal grounding + equal trust + equal recency + equal importance → needs_review."""
        from sqlalchemy import text

        mem._config.belief.agent_trust = {}  # everyone defaults to 0.5

        a = await mem.ltm.save(
            "agent-x", "fact", "ambiguous_a",
            "Pricing tier one is free",
            importance=5,
            record_type="fact",
            grounding="inferred",
        )
        b = await mem.ltm.save(
            "agent-y", "fact", "ambiguous_b",
            "Pricing tier one is free",  # same importance, grounding
            importance=5,
            record_type="fact",
            grounding="inferred",
        )
        # Force equal updated_at
        async with mem._db.session() as session:
            await session.execute(text(
                "UPDATE nmem_long_term_memory SET updated_at = NOW() "
                "WHERE id IN (:a, :b)"
            ), {"a": a.id, "b": b.id})

        await self._insert_conflict(
            mem,
            record_a_table="nmem_long_term_memory", record_a_id=a.id,
            record_b_table="nmem_long_term_memory", record_b_id=b.id,
            agent_a="agent-x", agent_b="agent-y",
        )

        auto, review = await mem.consolidation._resolve_conflicts()
        assert review >= 1, f"Expected at least 1 needs_review, got auto={auto} review={review}"

        # Neither record should be marked superseded
        async with mem._db.session() as session:
            result = await session.execute(text(
                "SELECT status FROM nmem_long_term_memory WHERE id IN (:a, :b)"
            ), {"a": a.id, "b": b.id})
            statuses = {r[0] for r in result.all()}
            assert statuses == {"validated"}, f"Both should stay validated, got {statuses}"

    async def test_superseded_excluded_from_search_by_default(self, mem: MemorySystem):
        """Default search filters out status='superseded' rows."""
        from sqlalchemy import text

        winner = await mem.ltm.save(
            "t", "fact", "winner_key", "Canonical widget behaviour",
            importance=5,
        )
        loser = await mem.ltm.save(
            "t", "fact", "loser_key", "Canonical widget behaviour variant",
            importance=5,
        )
        # Manually mark the loser superseded
        async with mem._db.session() as session:
            await session.execute(text(
                "UPDATE nmem_long_term_memory SET status='superseded', "
                "superseded_by_id=:w WHERE id=:l"
            ), {"w": winner.id, "l": loser.id})

        results = await mem.ltm.search("t", "widget")
        ids = {r.id for r in results}
        assert winner.id in ids
        assert loser.id not in ids

    async def test_superseded_included_with_flag(self, mem: MemorySystem):
        """include_superseded=True returns superseded rows for audit."""
        from sqlalchemy import text

        a = await mem.ltm.save(
            "t2", "fact", "audit_a", "Audit widget record",
            importance=5,
        )
        async with mem._db.session() as session:
            await session.execute(text(
                "UPDATE nmem_long_term_memory SET status='superseded' WHERE id=:id"
            ), {"id": a.id})

        default_results = await mem.ltm.search("t2", "audit widget")
        audit_results = await mem.ltm.search(
            "t2", "audit widget", include_superseded=True,
        )
        assert a.id not in {r.id for r in default_results}
        assert a.id in {r.id for r in audit_results}

    async def test_stale_conflict_marked_when_record_deleted(self, mem: MemorySystem):
        """If one of the referenced records is gone, the conflict goes stale."""
        from sqlalchemy import text

        a = await mem.ltm.save("t3", "fact", "stale_a", "content a", importance=5)
        b = await mem.ltm.save("t3", "fact", "stale_b", "content b", importance=5)

        conflict_id = await self._insert_conflict(
            mem,
            record_a_table="nmem_long_term_memory", record_a_id=a.id,
            record_b_table="nmem_long_term_memory", record_b_id=b.id,
            agent_a="t3", agent_b="t3",
        )
        # Delete one side
        async with mem._db.session() as session:
            await session.execute(text(
                "DELETE FROM nmem_long_term_memory WHERE id=:id"
            ), {"id": a.id})

        await mem.consolidation._resolve_conflicts()

        async with mem._db.session() as session:
            result = await session.execute(text(
                "SELECT status FROM nmem_memory_conflicts WHERE id=:id"
            ), {"id": conflict_id})
            assert result.scalar() == "stale"

    async def test_write_path_detects_conflict(self, mem: MemorySystem):
        """scan_conflicts fires on LTM writes — smoke test that the write
        path actually creates a conflict row when grounding diverges."""
        from sqlalchemy import text

        # Make scan thresholds permissive for the test
        mem._config.belief.text_similarity_threshold = 0.3
        mem._config.belief.vector_divergence_threshold = 0.99

        await mem.ltm.save(
            "agent-a", "fact", "divergent_first",
            "The payment processor is Stripe with v1 webhooks",
            importance=5,
            record_type="fact",
            grounding="source_material",
        )
        await mem.ltm.save(
            "agent-b", "fact", "divergent_second",
            "The payment processor is Adyen with v2 webhooks",
            importance=5,
            record_type="fact",
            grounding="inferred",
        )

        async with mem._db.session() as session:
            result = await session.execute(text(
                "SELECT COUNT(*) FROM nmem_memory_conflicts WHERE status='open'"
            ))
            count = result.scalar()
            assert count >= 1, f"Expected at least 1 conflict row, got {count}"


class TestProjectScope:
    """Tests for project-scoped memory isolation."""

    async def test_journal_scope_isolation(self, mem: MemorySystem):
        """Journal entries in different scopes should not interfere."""
        await mem.journal.add(
            "agent-a", "observation", "Project A fact",
            "This is specific to project A",
            importance=5, project_scope="project:A",
        )
        await mem.journal.add(
            "agent-a", "observation", "Project B fact",
            "This is specific to project B",
            importance=5, project_scope="project:B",
        )

        # Search within scope A should not find scope B
        results_a = await mem.journal.search(
            "agent-a", "project fact", project_scope="project:A"
        )
        assert all("Project A" in e.title or "Project B" not in e.title
                    for e in results_a)

    async def test_global_entries_visible_in_scope(self, mem: MemorySystem):
        """Global entries (no scope) should be visible in scoped searches."""
        await mem.journal.add(
            "agent-a", "rule", "Global rule",
            "This applies everywhere", importance=8,
            project_scope=None,
        )
        await mem.journal.add(
            "agent-a", "observation", "Scoped fact",
            "This is project-specific", importance=5,
            project_scope="project:X",
        )

        # Search within scope X should find both
        results = await mem.journal.search(
            "agent-a", "rule fact", project_scope="project:X"
        )
        assert len(results) >= 1  # At least the global rule

    async def test_ltm_scope_key_uniqueness(self, mem: MemorySystem):
        """Same key in different scopes should create separate entries."""
        await mem.ltm.save(
            "agent-a", "fact", "db_host",
            "Database is at db-a.example.com",
            importance=5, project_scope="project:A",
        )
        await mem.ltm.save(
            "agent-a", "fact", "db_host",
            "Database is at db-b.example.com",
            importance=5, project_scope="project:B",
        )

        entry_a = await mem.ltm.get("agent-a", "db_host", project_scope="project:A")
        entry_b = await mem.ltm.get("agent-a", "db_host", project_scope="project:B")

        assert entry_a is not None
        assert entry_b is not None
        assert entry_a.content != entry_b.content
        assert "db-a" in entry_a.content
        assert "db-b" in entry_b.content

    async def test_dedup_within_scope_only(self, mem: MemorySystem):
        """Dedup should only match entries within the same scope."""
        entry_a = await mem.journal.add(
            "agent-a", "observation", "Same title same content",
            "Exactly the same text here for dedup testing",
            importance=5, project_scope="project:A",
        )

        # Same content in different scope should NOT dedup
        entry_b = await mem.journal.add(
            "agent-a", "observation", "Same title same content",
            "Exactly the same text here for dedup testing",
            importance=5, project_scope="project:B",
        )

        # Both should have been created (different scopes)
        assert entry_a.id != entry_b.id

    async def test_cross_scope_search(self, mem: MemorySystem):
        """Searching with project_scope='*' should find entries across ALL scopes."""
        # Create entries in different scopes
        await mem.ltm.save(
            "agent-a", "lesson", "payment_timeout_cause_1",
            "Checkout timeout was due to stripe webhook delays",
            importance=7, project_scope="customer:acme",
        )
        await mem.ltm.save(
            "agent-a", "lesson", "payment_timeout_cause_2",
            "Checkout timeout was due to database lock contention",
            importance=7, project_scope="customer:beta",
        )
        await mem.ltm.save(
            "agent-a", "lesson", "payment_timeout_cause_3",
            "Checkout timeout was due to CDN routing issue",
            importance=7, project_scope="customer:gamma",
        )

        # Default search from within one scope — finds only that scope
        scoped_results = await mem.ltm.search(
            "agent-a", "checkout timeout cause", project_scope="customer:acme",
        )
        # Should find acme entry but not beta/gamma
        assert any("stripe" in r.content for r in scoped_results)
        assert not any("database lock" in r.content for r in scoped_results)

        # Cross-scope search — finds all three
        all_results = await mem.ltm.search(
            "agent-a", "checkout timeout cause", project_scope="*",
        )
        # Should find all three different causes
        contents = " ".join(r.content for r in all_results)
        assert "stripe" in contents
        assert "database lock" in contents
        assert "CDN routing" in contents

    async def test_backward_compat_no_scope(self, mem: MemorySystem):
        """Entries without scope should work identically to v0.1.0."""
        await mem.ltm.save(
            "agent-a", "fact", "global_fact",
            "This has no scope", importance=5,
            project_scope=None,
        )

        entry = await mem.ltm.get("agent-a", "global_fact", project_scope=None)
        assert entry is not None
        assert entry.project_scope is None


class TestEntityAutoJournal:
    """Tests for entity access auto-journaling."""

    async def test_entity_search_creates_journal(self, mem: MemorySystem):
        """Searching entities with agent_id should create a journal entry."""
        import asyncio

        # Save an entity record
        await mem.entity.save(
            "customer", "cust_001", "Acme Corp",
            "agent-support", "Acme Corp is a enterprise customer with 500 seats",
        )

        # Search with agent_id to trigger auto-journal
        results = await mem.entity.search(
            "Acme Corp enterprise", agent_id="agent-support",
        )
        assert len(results) >= 1

        # Give the background task time to complete
        await asyncio.sleep(0.5)

        # Check that a journal entry was created
        journal_entries = await mem.journal.recent("agent-support", days=1)
        entity_refs = [e for e in journal_entries if e.entry_type == "entity_reference"]
        assert len(entity_refs) >= 1
        assert "Acme Corp" in entity_refs[0].title
        assert entity_refs[0].importance == 3  # Low importance

    async def test_no_journal_without_agent_id(self, mem: MemorySystem):
        """Entity search without agent_id should NOT create journal entries."""
        import asyncio

        await mem.entity.save(
            "customer", "cust_002", "Beta Corp",
            "agent-sales", "Beta Corp is a prospect",
        )

        # Search WITHOUT agent_id
        results = await mem.entity.search("Beta Corp prospect")
        assert len(results) >= 1

        await asyncio.sleep(0.5)

        # No journal entries should exist for entity_reference
        journal_entries = await mem.journal.recent("agent-sales", days=1)
        entity_refs = [e for e in journal_entries if e.entry_type == "entity_reference"]
        assert len(entity_refs) == 0

    async def test_auto_journal_config_toggle(self, mem: MemorySystem):
        """Disabling auto_journal_on_search should prevent journaling."""
        import asyncio

        # Disable auto-journaling
        mem._config.entity.auto_journal_on_search = False

        await mem.entity.save(
            "customer", "cust_003", "Gamma Corp",
            "agent-eng", "Gamma Corp uses our API",
        )

        results = await mem.entity.search(
            "Gamma Corp API", agent_id="agent-eng",
        )
        await asyncio.sleep(0.5)

        journal_entries = await mem.journal.recent("agent-eng", days=1)
        entity_refs = [e for e in journal_entries if e.entry_type == "entity_reference"]
        assert len(entity_refs) == 0

        # Re-enable for other tests
        mem._config.entity.auto_journal_on_search = True


class TestKnowledgeLinks:
    """Tests for associative knowledge linking."""

    async def test_shared_entity_links(self, mem: MemorySystem):
        """Entries with the same entity tag should be linked."""
        # Create 3 journal entries all referencing the same entity
        e1 = await mem.journal.add(
            "agent-a", "observation", "First Acme note",
            "Customer Acme had an issue",
            tags=["entity:customer/acme_001"],
        )
        e2 = await mem.journal.add(
            "agent-a", "decision", "Second Acme note",
            "Decided to escalate Acme issue",
            tags=["entity:customer/acme_001"],
        )
        e3 = await mem.journal.add(
            "agent-a", "outcome", "Third Acme note",
            "Acme issue resolved successfully",
            tags=["entity:customer/acme_001"],
        )

        # Build links
        created = await mem.links.build_links()
        # 3 entries → 3 pairwise links (e1-e2, e1-e3, e2-e3)
        assert created >= 3

        # Verify e1 has 2 linked entries
        linked = await mem.links.get_linked(e1.id, "journal")
        entity_links = [l for l in linked if l.link_type == "shared_entity"]
        assert len(entity_links) >= 2

    async def test_shared_tag_links(self, mem: MemorySystem):
        """Entries sharing a non-meta tag should be linked."""
        e1 = await mem.journal.add(
            "agent-b", "observation", "Payment issue 1",
            "Stripe webhook failed", tags=["payment", "webhook"],
        )
        e2 = await mem.journal.add(
            "agent-b", "observation", "Payment issue 2",
            "Payment processing slow", tags=["payment", "performance"],
        )

        await mem.links.build_links()

        linked = await mem.links.get_linked(e1.id, "journal")
        tag_links = [l for l in linked if l.link_type == "shared_tag"]
        assert len(tag_links) >= 1

    async def test_temporal_proximity_links(self, mem: MemorySystem):
        """Journal entries in the same session within N minutes should be linked."""
        e1 = await mem.journal.add(
            "agent-c", "observation", "Event A",
            "Something happened", session_id="session-1",
        )
        e2 = await mem.journal.add(
            "agent-c", "observation", "Event B",
            "Something else happened", session_id="session-1",
        )

        await mem.links.build_links()

        linked = await mem.links.get_linked(e1.id, "journal")
        temporal_links = [l for l in linked if l.link_type == "temporal"]
        assert len(temporal_links) >= 1

    async def test_links_not_duplicated(self, mem: MemorySystem):
        """Running build_links twice should not create duplicates."""
        await mem.journal.add(
            "agent-d", "observation", "Entry one",
            "Content", tags=["entity:project/alpha"],
        )
        await mem.journal.add(
            "agent-d", "observation", "Entry two",
            "Content", tags=["entity:project/alpha"],
        )

        created_first = await mem.links.build_links()
        created_second = await mem.links.build_links()

        assert created_first >= 1
        assert created_second == 0  # No new links on second run

    async def test_search_expansion(self, mem: MemorySystem):
        """Knowledge links should expand search results with related entries."""
        # Create entries linked by entity tag
        e1 = await mem.journal.add(
            "agent-e", "observation", "Database migration started",
            "Running ALTER TABLE on users", importance=6,
            tags=["entity:project/migration_042"],
        )
        e2 = await mem.journal.add(
            "agent-e", "outcome", "Deployment pipeline update",
            "Changed Kubernetes manifests for rollout", importance=6,
            tags=["entity:project/migration_042"],
        )

        # Build links so they're connected
        await mem.links.build_links()

        # Use top_k=1 to force only e1 to come back from search
        results = await mem.journal.search(
            "agent-e", "database migration ALTER TABLE users", top_k=1,
        )
        assert len(results) == 1
        assert results[0].id == e1.id

        # Build SearchResult list for expansion test
        from nmem.types import SearchResult
        search_results = [
            SearchResult(tier="journal", id=r.id, score=r.relevance_score,
                        content=r.content, title=r.title)
            for r in results
        ]

        expanded = await mem.links.expand_search_results(search_results)
        # Should include the linked entry via knowledge link expansion
        expanded_flags = [r for r in expanded if r.metadata.get("expanded_via_link")]
        assert len(expanded_flags) >= 1
        assert expanded_flags[0].id == e2.id

    async def test_config_toggle_disables_linking(self, mem: MemorySystem):
        """Disabling knowledge_links config should prevent link creation."""
        mem._config.knowledge_links.enabled = False

        await mem.journal.add(
            "agent-f", "observation", "Test entry",
            "Content", tags=["entity:test/thing"],
        )
        await mem.journal.add(
            "agent-f", "observation", "Test entry 2",
            "Content", tags=["entity:test/thing"],
        )

        created = await mem.links.build_links()
        assert created == 0

        # Re-enable for other tests
        mem._config.knowledge_links.enabled = True
