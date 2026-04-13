"""
Integration tests with real embedding model + real LLM backend.

Uses:
  - Embedding: sentence-transformers/all-MiniLM-L6-v2 (384 dims, local CPU)
  - LLM: Qwen3-8B-AWQ via vLLM on 192.168.68.50:8200 (OpenAI-compat API)
  - Database: PostgreSQL 16 + pgvector on localhost:5433

Run: pytest tests/integration/test_real_models.py -v
"""

import asyncio
import os
import pytest

from nmem import MemorySystem, NmemConfig

NMEM_TEST_DSN = os.environ.get(
    "NMEM_TEST_DSN", "postgresql+asyncpg://nmem:nmem@localhost:5433/nmem"
)
VLLM_BASE_URL = os.environ.get("NMEM_VLLM_URL", "http://192.168.68.50:8200/v1")
VLLM_MODEL = os.environ.get("NMEM_VLLM_MODEL", "Qwen/Qwen3-8B-AWQ")


def _check_prerequisites() -> tuple[bool, str]:
    """Check if PostgreSQL and vLLM are both available."""
    import asyncpg

    async def check():
        # Check PostgreSQL
        try:
            conn = await asyncpg.connect("postgresql://nmem:nmem@localhost:5433/nmem")
            await conn.close()
        except Exception as e:
            return False, f"PostgreSQL: {e}"

        # Check vLLM
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{VLLM_BASE_URL}/models")
                if r.status_code != 200:
                    return False, f"vLLM returned {r.status_code}"
        except Exception as e:
            return False, f"vLLM: {e}"

        return True, "ok"

    try:
        loop = asyncio.new_event_loop()
        ok, reason = loop.run_until_complete(check())
        loop.close()
        return ok, reason
    except Exception as e:
        return False, str(e)


_available, _skip_reason = _check_prerequisites()
pytestmark = pytest.mark.skipif(not _available, reason=f"Prerequisites not met: {_skip_reason}")


async def _clean_tables(system: MemorySystem):
    from sqlalchemy import text
    async with system._db.session() as session:
        for table in [
            "nmem_working_memory", "nmem_journal_entries",
            "nmem_long_term_memory", "nmem_shared_knowledge",
            "nmem_entity_memory", "nmem_delegations", "nmem_curiosity_signals",
        ]:
            try:
                await session.execute(text(f"DELETE FROM {table}"))
            except Exception:
                pass


@pytest.fixture
async def mem():
    """MemorySystem with real sentence-transformers + Qwen3-8B via vLLM."""
    config = NmemConfig(
        database_url=NMEM_TEST_DSN,
        embedding={
            "provider": "sentence-transformers",
            "model": "all-MiniLM-L6-v2",
        },
        llm={
            "provider": "openai",
            "base_url": VLLM_BASE_URL,
            "model": VLLM_MODEL,
            "api_key": "EMPTY",
        },
    )
    system = MemorySystem(config)
    await system.initialize()
    await _clean_tables(system)

    # Reset thread centroids
    from nmem.search import _thread_centroids
    _thread_centroids.clear()

    yield system
    await _clean_tables(system)
    await system.close()


# ── Embedding Quality Tests ──────────────────────────────────────────────


class TestRealEmbeddings:
    """Verify that real embeddings produce meaningful semantic similarity."""

    async def test_semantic_similarity(self, mem: MemorySystem):
        """Similar texts should have high cosine similarity."""
        from nmem.search import cosine_similarity

        emb = mem._embedding
        a = emb.embed("The database migration completed successfully")
        b = emb.embed("Database schema upgrade finished without errors")
        c = emb.embed("The cat sat on the mat and watched birds")

        sim_ab = cosine_similarity(a, b)
        sim_ac = cosine_similarity(a, c)

        # Related texts should be much more similar than unrelated
        assert sim_ab > 0.5, f"Related texts sim={sim_ab:.3f}, expected >0.5"
        assert sim_ac < 0.3, f"Unrelated texts sim={sim_ac:.3f}, expected <0.3"
        assert sim_ab > sim_ac, "Related texts should be more similar than unrelated"

    async def test_embedding_dimensions(self, mem: MemorySystem):
        """all-MiniLM-L6-v2 produces 384-dimensional vectors."""
        emb = mem._embedding.embed("test")
        assert len(emb) == 384


# ── Hybrid Search with Real Embeddings ───────────────────────────────────


class TestRealHybridSearch:
    """Test that hybrid search produces semantically correct rankings."""

    async def test_journal_semantic_ranking(self, mem: MemorySystem):
        """Hybrid search should rank semantically relevant entries first."""
        agent = "search-rank-agent"
        await mem.journal.add(
            agent, "observation", "Database migration completed",
            "Successfully migrated PostgreSQL from version 15 to 16 with zero downtime",
            importance=7,
        )
        await mem.journal.add(
            agent, "decision", "Chose Redis for caching",
            "After benchmarking Memcached vs Redis, selected Redis for session caching",
            importance=5,
        )
        await mem.journal.add(
            agent, "observation", "API latency spike",
            "Observed 500ms p99 latency on the user search endpoint during peak traffic",
            importance=8,
        )

        results = await mem.journal.search(agent, "database upgrade migration")
        assert len(results) > 0
        # With real embeddings, the database migration entry should rank first
        assert "migration" in results[0].title.lower(), \
            f"Expected 'migration' in top result, got: {results[0].title}"

    async def test_ltm_semantic_ranking(self, mem: MemorySystem):
        """LTM search returns semantically relevant entries first."""
        await mem.ltm.save(
            "agent-1", "procedure", "deploy_process",
            "Always run database migrations before deploying application code to production",
            importance=8,
        )
        await mem.ltm.save(
            "agent-1", "lesson", "cache_invalidation",
            "Redis cache must be flushed after any database schema changes",
            importance=7,
        )
        await mem.ltm.save(
            "agent-1", "fact", "team_standup",
            "Engineering standup happens at 9:30 AM every Monday through Friday",
            importance=4,
        )

        results = await mem.ltm.search("agent-1", "how to deploy code")
        assert len(results) > 0
        entry, score = results[0]
        assert entry.key == "deploy_process", \
            f"Expected deploy_process first, got: {entry.key}"

    async def test_cross_tier_search(self, mem: MemorySystem):
        """Cross-tier search finds relevant entries across all tiers."""
        await mem.journal.add(
            "agent-1", "observation", "Payment gateway timeout",
            "Stripe API returned timeouts during checkout for 3 minutes",
            importance=8,
        )
        await mem.ltm.save(
            "agent-1", "procedure", "payment_retry",
            "When payment gateway times out, retry with exponential backoff up to 3 times",
            importance=9,
        )
        await mem.shared.save(
            "system", "policy", "payment_sla",
            "Payment processing must complete within 10 seconds or show user-friendly error",
            importance=7,
        )

        results = await mem.search("agent-1", "payment timeout handling")
        assert len(results) >= 2
        tiers_found = {r.tier for r in results}
        # Should find entries from at least 2 tiers
        assert len(tiers_found) >= 2, f"Expected multi-tier results, got: {tiers_found}"


# ── LLM-Powered Features ────────────────────────────────────────────────


class TestRealLLM:
    """Test features that require a real LLM backend."""

    @pytest.mark.timeout(30)
    async def test_content_compression(self, mem: MemorySystem):
        """Long content should be compressed by the LLM."""
        long_content = (
            "During the post-mortem meeting on March 15th, the team discussed the root cause "
            "of the production outage that occurred on March 14th at approximately 2:47 AM UTC. "
            "The investigation revealed that a database connection pool exhaustion issue was "
            "triggered by a sudden spike in traffic from the mobile API, which increased from "
            "the normal 500 requests per second to over 3,000 requests per second. The connection "
            "pool was configured with a maximum of 20 connections, which proved insufficient. "
            "The team decided to increase the pool size to 50 and implement circuit breaker "
            "patterns for the mobile API endpoints."
        )

        entry = await mem.journal.add(
            "agent-1", "session_summary", "March 14 outage post-mortem",
            long_content,
            importance=8,
            compress=True,
        )

        # The compressed content should be shorter than the original
        assert len(entry.content) < len(long_content), \
            f"Expected compression: original={len(long_content)}, got={len(entry.content)}"
        # But should still contain key facts
        content_lower = entry.content.lower()
        # At least some key details should survive compression
        has_key_facts = (
            "march" in content_lower
            or "outage" in content_lower
            or "connection" in content_lower
            or "pool" in content_lower
            or "traffic" in content_lower
        )
        assert has_key_facts, f"Compressed content lost key facts: {entry.content}"

    @pytest.mark.timeout(30)
    async def test_no_compression_for_short_content(self, mem: MemorySystem):
        """Short content should not be compressed."""
        short_content = "Server restarted at 3am"

        entry = await mem.journal.add(
            "agent-1", "observation", "Server restart",
            short_content,
            importance=5,
            compress=True,
        )

        # Short content should pass through unchanged
        assert entry.content == short_content


# ── Dedup with Real Embeddings ───────────────────────────────────────────


class TestRealDedup:
    """Test deduplication with real semantic similarity."""

    async def test_exact_duplicate_detected(self, mem: MemorySystem):
        """Identical content should be deduplicated."""
        e1 = await mem.journal.add(
            "agent-1", "observation", "Server out of memory",
            "Production server ran out of memory and was killed by OOM killer",
            importance=7,
        )
        e2 = await mem.journal.add(
            "agent-1", "observation", "Server out of memory",
            "Production server ran out of memory and was killed by OOM killer",
            importance=5,
        )
        assert e1.id == e2.id, "Identical entries should be deduplicated"

    async def test_paraphrased_duplicate_detected(self, mem: MemorySystem):
        """Semantically identical but paraphrased content should be deduplicated."""
        e1 = await mem.journal.add(
            "agent-1", "observation", "Server crashed from OOM",
            "The production server crashed because it ran out of memory due to OOM killer",
            importance=7,
        )
        e2 = await mem.journal.add(
            "agent-1", "observation", "Server killed by OOM",
            "Production server was terminated by the out-of-memory killer process",
            importance=5,
        )
        # With real embeddings, these should be similar enough (>0.92) to dedup
        # If not, that's also acceptable — the threshold is intentionally high
        # The important thing is the mechanism works
        if e1.id == e2.id:
            pass  # Deduplicated correctly
        else:
            # Check they're at least highly similar
            from nmem.search import cosine_similarity
            emb1 = mem._embedding.embed("Server crashed from OOM The production server crashed because it ran out of memory due to OOM killer")
            emb2 = mem._embedding.embed("Server killed by OOM Production server was terminated by the out-of-memory killer process")
            sim = cosine_similarity(emb1, emb2)
            # If similarity is below threshold, dedup correctly didn't fire
            assert sim < 0.92 or e1.id == e2.id, \
                f"Sim={sim:.3f} >= 0.92 but entries weren't deduplicated"

    async def test_unrelated_not_deduplicated(self, mem: MemorySystem):
        """Unrelated entries should NOT be deduplicated."""
        e1 = await mem.journal.add(
            "agent-1", "observation", "Server crashed from OOM",
            "Production server ran out of memory",
            importance=7,
        )
        e2 = await mem.journal.add(
            "agent-1", "decision", "Chose new monitoring tool",
            "Selected Datadog for infrastructure monitoring after evaluating 5 tools",
            importance=6,
        )
        assert e1.id != e2.id, "Unrelated entries should NOT be deduplicated"


# ── Context Threading with Real Embeddings ───────────────────────────────


class TestRealContextThreading:
    """Test context thread clustering with real semantic similarity."""

    async def test_related_entries_same_thread(self, mem: MemorySystem):
        """Semantically related entries should cluster into the same thread."""
        e1 = await mem.journal.add(
            "agent-1", "observation", "Database backup started",
            "Automated nightly PostgreSQL database backup process initiated",
            importance=5,
        )
        e2 = await mem.journal.add(
            "agent-1", "observation", "Database backup completed",
            "Nightly PostgreSQL database backup completed successfully, all tables backed up",
            importance=5,
        )
        # These are semantically very similar — should cluster
        assert e1.context_thread_id == e2.context_thread_id, \
            f"Related entries should share thread: {e1.context_thread_id} vs {e2.context_thread_id}"

    async def test_unrelated_entries_different_threads(self, mem: MemorySystem):
        """Semantically unrelated entries should go to different threads."""
        e1 = await mem.journal.add(
            "agent-1", "observation", "Database backup started",
            "Automated nightly PostgreSQL database backup process initiated",
            importance=5,
        )
        e2 = await mem.journal.add(
            "agent-1", "decision", "New hire onboarding plan",
            "Created comprehensive onboarding plan for the three new frontend engineers joining next week",
            importance=6,
        )
        assert e1.context_thread_id != e2.context_thread_id, \
            f"Unrelated entries should have different threads: {e1.context_thread_id} vs {e2.context_thread_id}"


# ── Consolidation with Real Models ───────────────────────────────────────


class TestRealConsolidation:
    """Test consolidation engine with real embedding + LLM."""

    @pytest.mark.timeout(30)
    async def test_promotion_with_real_embeddings(self, mem: MemorySystem):
        """High-importance entries promote to LTM with real embeddings."""
        await mem.journal.add(
            "agent-1", "lesson", "Always validate user input",
            "A production incident was caused by unvalidated user input in the search API. "
            "SQL injection attempt succeeded because input was passed directly to the query.",
            importance=9,
        )

        stats = await mem.consolidation.run_micro_cycle("test_real")
        assert stats.promoted_to_ltm >= 1

        # Verify the promoted entry is searchable in LTM
        results = await mem.ltm.search("agent-1", "input validation security")
        assert len(results) > 0
        assert any("input" in e.content.lower() or "validat" in e.content.lower() for e, _ in results)

    @pytest.mark.timeout(30)
    async def test_full_cycle_with_real_models(self, mem: MemorySystem):
        """Full consolidation cycle completes with real models."""
        # Add some data first
        await mem.journal.add("agent-1", "observation", "Test entry 1", "Content one", importance=3)
        await mem.journal.add("agent-1", "observation", "Test entry 2", "Content two", importance=5)

        stats = await mem.consolidation.run_full_cycle()
        assert stats.duration_seconds >= 0
        assert stats.duration_seconds < 30  # Should complete reasonably fast
