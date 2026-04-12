"""
Token usage statistics — tracks prompt injection sizes and LLM costs over time.

Records are stored in the nmem_metadata table as JSON blobs keyed by
agent_id + date. This avoids schema changes and keeps stats queryable
via the existing KV infrastructure.

Key format: ``token_stats:{agent_id}:{YYYY-MM-DD}``
Value: JSON with per-section token estimates and LLM usage.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, text as sa_text

from nmem.db.models import NmemMetadata

if TYPE_CHECKING:
    from nmem.db.session import DatabaseManager
    from nmem.types import PromptContext

logger = logging.getLogger(__name__)

_KEY_PREFIX = "token_stats"


def _day_key(agent_id: str, date: datetime | None = None) -> str:
    """Build the metadata key for a given agent + day."""
    day = (date or datetime.utcnow()).strftime("%Y-%m-%d")
    return f"{_KEY_PREFIX}:{agent_id}:{day}"


async def record_prompt_stats(
    db: "DatabaseManager",
    agent_id: str,
    ctx: "PromptContext",
) -> None:
    """Record a prompt injection event for token trend tracking.

    Accumulates into a daily bucket per agent. Each call increments
    the call count and adds the per-section token sizes.
    """
    key = _day_key(agent_id)

    try:
        async with db.session() as session:
            row = (await session.execute(
                select(NmemMetadata).where(NmemMetadata.key == key)
            )).scalar_one_or_none()

            sections = ctx.section_tokens
            total = ctx.token_estimate

            if row is None:
                data = {
                    "calls": 1,
                    "total_tokens": total,
                    "sections": sections,
                }
                session.add(NmemMetadata(
                    key=key,
                    value=json.dumps(data),
                ))
            else:
                existing = json.loads(row.value)
                existing["calls"] = existing.get("calls", 0) + 1
                existing["total_tokens"] = existing.get("total_tokens", 0) + total
                # Accumulate per-section
                existing_sections = existing.get("sections", {})
                for sec, tokens in sections.items():
                    existing_sections[sec] = existing_sections.get(sec, 0) + tokens
                existing["sections"] = existing_sections
                row.value = json.dumps(existing)
    except Exception as e:
        logger.debug("Failed to record prompt stats: %s", e)


async def record_llm_usage(
    db: "DatabaseManager",
    operation: str,
    token_estimate: int,
) -> None:
    """Record an LLM token usage event (compression, synthesis, etc.).

    Stored under a system-level daily key (agent_id="system").
    """
    key = _day_key("_system_llm")

    try:
        async with db.session() as session:
            row = (await session.execute(
                select(NmemMetadata).where(NmemMetadata.key == key)
            )).scalar_one_or_none()

            if row is None:
                data = {
                    "calls": 1,
                    "total_tokens": token_estimate,
                    "operations": {operation: token_estimate},
                }
                session.add(NmemMetadata(
                    key=key,
                    value=json.dumps(data),
                ))
            else:
                existing = json.loads(row.value)
                existing["calls"] = existing.get("calls", 0) + 1
                existing["total_tokens"] = existing.get("total_tokens", 0) + token_estimate
                ops = existing.get("operations", {})
                ops[operation] = ops.get(operation, 0) + token_estimate
                existing["operations"] = ops
                row.value = json.dumps(existing)
    except Exception as e:
        logger.debug("Failed to record LLM usage: %s", e)


async def query_token_trends(
    db: "DatabaseManager",
    *,
    days: int = 30,
    agent_id: str | None = None,
) -> list[dict[str, Any]]:
    """Query token trends for the last N days.

    Returns a list of daily records sorted by date, each with:
        date, agent_id, calls, total_tokens, avg_tokens, sections
    """
    cutoff = datetime.utcnow() - timedelta(days=days)
    cutoff_str = cutoff.strftime("%Y-%m-%d")

    try:
        async with db.session() as session:
            if agent_id:
                pattern = f"{_KEY_PREFIX}:{agent_id}:%"
            else:
                pattern = f"{_KEY_PREFIX}:%"

            result = await session.execute(
                select(NmemMetadata).where(
                    NmemMetadata.key.like(pattern),
                    NmemMetadata.updated_at >= cutoff,
                )
            )
            rows = result.scalars().all()
    except Exception as e:
        logger.error("Failed to query token trends: %s", e)
        return []

    records: list[dict[str, Any]] = []
    for row in rows:
        parts = row.key.split(":")
        if len(parts) != 3:
            continue
        _, aid, date_str = parts
        if date_str < cutoff_str:
            continue

        try:
            data = json.loads(row.value)
        except (json.JSONDecodeError, TypeError):
            continue

        calls = data.get("calls", 0)
        total = data.get("total_tokens", 0)
        records.append({
            "date": date_str,
            "agent_id": aid,
            "calls": calls,
            "total_tokens": total,
            "avg_tokens": total // calls if calls else 0,
            "sections": data.get("sections", {}),
            "operations": data.get("operations", {}),
        })

    records.sort(key=lambda r: (r["date"], r["agent_id"]))
    return records


async def query_token_summary(
    db: "DatabaseManager",
    *,
    days: int = 30,
) -> dict[str, Any]:
    """Aggregate token stats across all agents for the last N days.

    Returns a summary dict with totals, per-agent breakdown, and
    per-section breakdown.
    """
    records = await query_token_trends(db, days=days)

    agent_totals: dict[str, dict[str, int]] = defaultdict(
        lambda: {"calls": 0, "tokens": 0}
    )
    section_totals: dict[str, int] = defaultdict(int)
    llm_totals: dict[str, int] = defaultdict(int)
    total_calls = 0
    total_tokens = 0

    for rec in records:
        aid = rec["agent_id"]
        if aid == "_system_llm":
            for op, tokens in rec.get("operations", {}).items():
                llm_totals[op] += tokens
            continue

        agent_totals[aid]["calls"] += rec["calls"]
        agent_totals[aid]["tokens"] += rec["total_tokens"]
        total_calls += rec["calls"]
        total_tokens += rec["total_tokens"]

        for sec, tokens in rec.get("sections", {}).items():
            section_totals[sec] += tokens

    return {
        "period_days": days,
        "total_prompt_calls": total_calls,
        "total_prompt_tokens": total_tokens,
        "avg_tokens_per_call": total_tokens // total_calls if total_calls else 0,
        "agents": dict(agent_totals),
        "sections": dict(section_totals),
        "llm_operations": dict(llm_totals),
    }
