"""
Curiosity signal recurrence / dedup (0.8.0 Bug 2).

Re-emitting the same problem must reinforce one signal — accumulating
recurrence and lifting composite — rather than spawn duplicate rows that each
only ever decay.
"""
import pytest
from sqlalchemy import text


async def _count(mem) -> int:
    async with mem._db.session() as session:
        return (
            await session.execute(text("SELECT COUNT(*) FROM nmem_curiosity_signals"))
        ).scalar()


@pytest.mark.asyncio
async def test_repeated_curiosity_reinforces_instead_of_duplicating(mem):
    cog = mem.cognitive
    s1 = await cog.emit_curiosity(
        source_agent="tester", trigger_type="contradiction",
        summary="X conflicts with Y",
        novelty_score=0.5, uncertainty_score=0.5, conflict_score=0.4,
        business_impact=0.5, entity_type="fact", entity_id="42",
    )
    assert s1.recurrence_score == 0.0
    first_composite = s1.composite_score

    s2 = await cog.emit_curiosity(
        source_agent="tester", trigger_type="contradiction",
        summary="X conflicts with Y (seen again)",
        novelty_score=0.6, uncertainty_score=0.5, conflict_score=0.4,
        business_impact=0.5, entity_type="fact", entity_id="42",
    )

    assert s2.id == s1.id                              # same row reinforced
    assert s2.recurrence_score == pytest.approx(0.25)  # recurrence accrued
    assert s2.composite_score > first_composite        # ...and it got more salient
    assert s2.novelty_score == pytest.approx(0.6)      # took the stronger component
    assert await _count(mem) == 1                       # no duplicate row


@pytest.mark.asyncio
async def test_recurrence_saturates_and_composite_climbs(mem):
    cog = mem.cognitive
    last = 0.0
    for _ in range(6):
        s = await cog.emit_curiosity(
            source_agent="t", trigger_type="unusual_pattern",
            summary="weird", novelty_score=0.3, uncertainty_score=0.3,
            conflict_score=0.0, business_impact=0.3,
            entity_type="topic", entity_id="7",
        )
        last = s.composite_score
    assert s.recurrence_score == pytest.approx(1.0)  # capped
    assert last > (0.3 * 0.3 + 0.3 * 0.2 + 0.3 * 0.3)  # above the base-only score
    assert await _count(mem) == 1


@pytest.mark.asyncio
async def test_distinct_entities_do_not_merge(mem):
    cog = mem.cognitive
    await cog.emit_curiosity(source_agent="t", trigger_type="gap", summary="a",
                             entity_type="fact", entity_id="1")
    await cog.emit_curiosity(source_agent="t", trigger_type="gap", summary="b",
                             entity_type="fact", entity_id="2")
    assert await _count(mem) == 2


@pytest.mark.asyncio
async def test_entityless_signals_dedup_on_summary(mem):
    cog = mem.cognitive
    await cog.emit_curiosity(source_agent="t", trigger_type="gap",
                             summary="same text")
    s2 = await cog.emit_curiosity(source_agent="t", trigger_type="gap",
                                  summary="same text")
    assert s2.recurrence_score == pytest.approx(0.25)
    assert await _count(mem) == 1


# ── Phase 2: read / resolve surface for consumers (nmem-sym) ─────

@pytest.mark.asyncio
async def test_list_pending_curiosity_filters_and_orders(mem):
    cog = mem.cognitive
    await cog.emit_curiosity(source_agent="t", trigger_type="gap", summary="low",
                             novelty_score=0.1, uncertainty_score=0.1,
                             conflict_score=0.0, business_impact=0.1,
                             entity_type="f", entity_id="1")   # composite ~0.08
    await cog.emit_curiosity(source_agent="t", trigger_type="contradiction",
                             summary="high", novelty_score=0.9, uncertainty_score=0.9,
                             conflict_score=0.9, business_impact=0.9,
                             entity_type="f", entity_id="2")   # composite ~0.9
    got = await cog.list_pending_curiosity(min_composite=0.5, limit=10)
    assert [g.summary for g in got] == ["high"]               # low filtered out
    assert got[0].trigger_type == "contradiction"


@pytest.mark.asyncio
async def test_resolve_curiosity_marks_resolved_and_is_idempotent(mem):
    cog = mem.cognitive
    s = await cog.emit_curiosity(source_agent="t", trigger_type="gap", summary="x",
                                 entity_type="f", entity_id="9")
    assert await cog.resolve_curiosity(s.id, resolved_by="nmem-sym") is True
    pend = await cog.list_pending_curiosity(min_composite=0.0)
    assert all(p.id != s.id for p in pend)                    # no longer served
    assert await cog.resolve_curiosity(s.id) is False         # already resolved
