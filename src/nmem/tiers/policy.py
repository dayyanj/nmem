"""
Tier 6: Policy Memory — governance rules with writer/proposer permissions.

Scopes: "global", "agent:{id}", "entity_type:{type}".
Writers create active policies directly. Proposers create "proposed" entries
that require approval.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select, and_

from nmem.db.models import PolicyMemoryModel
from nmem.exceptions import PermissionError
from nmem.types import PolicyEntry

if TYPE_CHECKING:
    from nmem.db.session import DatabaseManager
    from nmem.config import NmemConfig

logger = logging.getLogger(__name__)


class PolicyTier:
    """Tier 6: Governance policy memory."""

    def __init__(self, db: DatabaseManager, config: NmemConfig):
        self._db = db
        self._config = config

    async def save(
        self,
        scope: str,
        category: str,
        key: str,
        content: str,
        agent_id: str,
    ) -> PolicyEntry:
        """Save or update a policy.

        Writers create active policies. Proposers create "proposed" status.
        Others are denied.

        Args:
            scope: Policy scope ("global", "agent:sales", "entity_type:lead").
            category: Category ("escalation", "approval", "autonomy", etc.).
            key: Unique key within scope.
            content: Policy content/rule text.
            agent_id: Agent creating the policy.

        Returns:
            The created/updated PolicyEntry.

        Raises:
            PermissionError: If agent has no write/propose permission.
        """
        writers = self._config.policy.writers
        proposers = self._config.policy.proposers

        if agent_id in writers:
            status = "active"
            approved_by = agent_id
        elif agent_id in proposers:
            status = "proposed"
            approved_by = None
        else:
            raise PermissionError(
                f"Agent '{agent_id}' cannot write or propose policies. "
                f"Writers: {writers}, Proposers: {proposers}"
            )

        async with self._db.session() as session:
            stmt = select(PolicyMemoryModel).where(
                and_(PolicyMemoryModel.scope == scope, PolicyMemoryModel.key == key)
            )
            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing:
                # Refresh to ensure all attributes are loaded (avoids
                # greenlet_spawn errors with aiosqlite on JSON columns).
                await session.refresh(existing)
                change = {
                    "agent": agent_id,
                    "date": datetime.now(timezone.utc).isoformat(),
                    "old_value": existing.content[:200],
                }
                # Must create a new list — SQLAlchemy JSON mutation tracking
                # does not detect in-place append on the same object.
                log = list(existing.change_log or [])
                log.append(change)

                existing.content = content
                existing.category = category
                existing.status = status
                existing.approved_by = approved_by
                existing.version += 1
                existing.change_log = log
                await session.flush()
                await session.refresh(existing)
                return self._row_to_entry(existing)
            else:
                record = PolicyMemoryModel(
                    scope=scope,
                    category=category,
                    key=key,
                    content=content,
                    created_by=agent_id,
                    approved_by=approved_by,
                    status=status,
                )
                session.add(record)
                await session.flush()
                return self._row_to_entry(record)

    async def get(self, scope: str, key: str) -> PolicyEntry | None:
        """Get a policy by scope and key. Returns only active policies.

        Args:
            scope: Policy scope.
            key: Policy key.

        Returns:
            PolicyEntry or None.
        """
        async with self._db.session() as session:
            stmt = select(PolicyMemoryModel).where(
                and_(
                    PolicyMemoryModel.scope == scope,
                    PolicyMemoryModel.key == key,
                    PolicyMemoryModel.status == "active",
                )
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return self._row_to_entry(row) if row else None

    async def list(
        self, scope: str | None = None, category: str | None = None
    ) -> list[PolicyEntry]:
        """List policies, optionally filtered.

        Args:
            scope: Filter by scope.
            category: Filter by category.

        Returns:
            List of PolicyEntry objects.
        """
        async with self._db.session() as session:
            stmt = select(PolicyMemoryModel).where(
                PolicyMemoryModel.status == "active"
            )
            if scope:
                stmt = stmt.where(PolicyMemoryModel.scope == scope)
            if category:
                stmt = stmt.where(PolicyMemoryModel.category == category)
            stmt = stmt.order_by(PolicyMemoryModel.scope, PolicyMemoryModel.key)
            result = await session.execute(stmt)
            return [self._row_to_entry(r) for r in result.scalars().all()]

    async def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        scope: str | None = None,
        category: str | None = None,
    ) -> list[tuple[PolicyEntry, float]]:
        """Search active policies by text relevance.

        Policy memory has no embedding column, so this uses FTS on
        PostgreSQL (``to_tsvector``/``ts_rank_cd``) and falls back
        to case-insensitive LIKE matching on SQLite.

        Args:
            query: Search query text.
            top_k: Maximum results.
            scope: Optional scope filter.
            category: Optional category filter.

        Returns:
            List of (PolicyEntry, score) tuples, ranked by relevance.
        """
        from sqlalchemy import text as sa_text

        where_parts = ["status = 'active'"]
        params: dict = {}
        if scope:
            where_parts.append("scope = :scope")
            params["scope"] = scope
        if category:
            where_parts.append("category = :category")
            params["category"] = category

        where_clause = " AND ".join(where_parts)
        params["query"] = query
        params["top_k"] = top_k

        if self._db.is_postgres:
            sql = sa_text(f"""
                SELECT id,
                       ts_rank_cd(
                           to_tsvector('english', key || ' ' || content),
                           plainto_tsquery('english', :query)
                       ) AS score
                FROM nmem_policy_memory
                WHERE {where_clause}
                  AND to_tsvector('english', key || ' ' || content)
                      @@ plainto_tsquery('english', :query)
                ORDER BY score DESC
                LIMIT :top_k
            """)
        else:
            # SQLite: simple LIKE matching with constant score
            sql = sa_text(f"""
                SELECT id, 0.5 AS score
                FROM nmem_policy_memory
                WHERE {where_clause}
                  AND (LOWER(key || ' ' || content) LIKE LOWER('%' || :query || '%'))
                LIMIT :top_k
            """)

        async with self._db.session() as session:
            result = await session.execute(sql, params)
            ranked = [(row[0], float(row[1])) for row in result.all()]

        if not ranked:
            return []

        ranked_ids = [r[0] for r in ranked]
        scores_by_id = {r[0]: r[1] for r in ranked}

        async with self._db.session() as session:
            result = await session.execute(
                select(PolicyMemoryModel).where(PolicyMemoryModel.id.in_(ranked_ids))
            )
            entries_by_id = {r.id: r for r in result.scalars().all()}
            return [
                (self._row_to_entry(entries_by_id[pid]), scores_by_id[pid])
                for pid in ranked_ids
                if pid in entries_by_id
            ]

    async def build_prompt(
        self, agent_id: str, max_chars: int | None = None
    ) -> str:
        """Build policy prompt section for an agent.

        Includes global policies + agent-specific policies.

        Returns:
            Formatted policy rules text.
        """
        max_chars = max_chars or self._config.policy.max_chars_in_prompt

        # Get global + agent-specific policies
        async with self._db.session() as session:
            stmt = (
                select(PolicyMemoryModel)
                .where(
                    and_(
                        PolicyMemoryModel.status == "active",
                        PolicyMemoryModel.scope.in_(["global", f"agent:{agent_id}"]),
                    )
                )
                .order_by(PolicyMemoryModel.scope, PolicyMemoryModel.category)
            )
            result = await session.execute(stmt)
            policies = result.scalars().all()

        if not policies:
            return ""

        lines: list[str] = []
        chars = 0
        for p in policies:
            stub = p.content[:150].replace("\n", " ")
            line = f"- [{p.scope}/{p.category}] {p.key}: {stub}"
            if chars + len(line) > max_chars:
                break
            lines.append(line)
            chars += len(line)
        return "\n".join(lines)

    @staticmethod
    def _row_to_entry(row: PolicyMemoryModel) -> PolicyEntry:
        return PolicyEntry(
            id=row.id,
            scope=row.scope,
            category=row.category,
            key=row.key,
            content=row.content,
            created_by=row.created_by,
            approved_by=row.approved_by,
            status=row.status,
            version=row.version,
            change_log=row.change_log,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
