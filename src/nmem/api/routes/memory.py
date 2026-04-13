"""
Memory tier endpoints — journal, LTM, shared, entity, search, context.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from nmem import MemorySystem
from nmem.api.deps import get_mem
from nmem.api.schemas import (
    ContextRequest,
    ContextResponse,
    EntityRecordCreate,
    EntityRecordResponse,
    EntitySearchRequest,
    JournalEntryCreate,
    JournalEntryResponse,
    LTMEntryCreate,
    LTMEntryResponse,
    SearchRequest,
    SearchResultItem,
    SharedEntryCreate,
    SharedEntryResponse,
    to_dict,
)

router = APIRouter(prefix="/v1", tags=["memory"])


# ── Journal ───────────────────────────────────────────────────────────────────


@router.post("/journal", response_model=JournalEntryResponse)
async def create_journal_entry(
    body: JournalEntryCreate,
    mem: MemorySystem = Depends(get_mem),
) -> dict[str, Any]:
    entry = await mem.journal.add(
        agent_id=body.agent_id,
        entry_type=body.entry_type,
        title=body.title,
        content=body.content,
        importance=body.importance,
        session_id=body.session_id,
        tags=body.tags,
        record_type=body.record_type,
        grounding=body.grounding,
        compress=body.compress,
        project_scope=body.project_scope if body.project_scope is not None else ...,
    )
    return to_dict(entry)


@router.get("/journal/recent", response_model=list[JournalEntryResponse])
async def recent_journal_entries(
    agent_id: str = Query(...),
    days: int = Query(7, ge=1, le=365),
    limit: int = Query(10, ge=1, le=100),
    project_scope: str | None = Query(None),
    mem: MemorySystem = Depends(get_mem),
) -> list[dict[str, Any]]:
    entries = await mem.journal.recent(
        agent_id=agent_id, days=days, limit=limit,
        project_scope=project_scope if project_scope is not None else ...,
    )
    return [to_dict(e) for e in entries]


# ── Long-Term Memory ──────────────────────────────────────────────────────────


@router.put("/ltm/{agent_id}/{key}", response_model=LTMEntryResponse)
async def save_ltm_entry(
    agent_id: str,
    key: str,
    body: LTMEntryCreate,
    mem: MemorySystem = Depends(get_mem),
) -> dict[str, Any]:
    entry = await mem.ltm.save(
        agent_id=agent_id,
        category=body.category,
        key=key,
        content=body.content,
        importance=body.importance,
        source=body.source,
        record_type=body.record_type,
        grounding=body.grounding,
        compress=body.compress,
        project_scope=body.project_scope if body.project_scope is not None else ...,
    )
    return to_dict(entry)


@router.get("/ltm/{agent_id}/{key}", response_model=LTMEntryResponse)
async def get_ltm_entry(
    agent_id: str,
    key: str,
    project_scope: str | None = Query(None),
    mem: MemorySystem = Depends(get_mem),
) -> dict[str, Any]:
    entry = await mem.ltm.get(
        agent_id=agent_id, key=key,
        project_scope=project_scope if project_scope is not None else ...,
    )
    if entry is None:
        raise HTTPException(status_code=404, detail=f"LTM entry not found: {agent_id}/{key}")
    return to_dict(entry)


@router.get("/ltm/{agent_id}")
async def list_ltm_keys(
    agent_id: str,
    category: str | None = Query(None),
    project_scope: str | None = Query(None),
    mem: MemorySystem = Depends(get_mem),
) -> dict[str, list[str]]:
    keys = await mem.ltm.list_keys(
        agent_id=agent_id, category=category,
        project_scope=project_scope if project_scope is not None else ...,
    )
    return {"keys": keys}


# ── Shared Knowledge ──────────────────────────────────────────────────────────


@router.put("/shared/{key}", response_model=SharedEntryResponse)
async def save_shared_entry(
    key: str,
    body: SharedEntryCreate,
    mem: MemorySystem = Depends(get_mem),
) -> dict[str, Any]:
    entry = await mem.shared.save(
        key=key,
        content=body.content,
        category=body.category,
        agent_id=body.agent_id,
        importance=body.importance,
        record_type=body.record_type,
        grounding=body.grounding,
        project_scope=body.project_scope if body.project_scope is not None else ...,
    )
    return to_dict(entry)


# ── Entity Memory ─────────────────────────────────────────────────────────────


@router.post("/entity", response_model=EntityRecordResponse)
async def create_entity_record(
    body: EntityRecordCreate,
    mem: MemorySystem = Depends(get_mem),
) -> dict[str, Any]:
    record = await mem.entity.save(
        entity_type=body.entity_type,
        entity_id=body.entity_id,
        entity_name=body.entity_name,
        agent_id=body.agent_id,
        content=body.content,
        record_type=body.record_type,
        confidence=body.confidence,
        grounding=body.grounding,
        tags=body.tags,
        evidence_refs=body.evidence_refs,
        project_scope=body.project_scope if body.project_scope is not None else ...,
    )
    return to_dict(record)


@router.post("/entity/search", response_model=list[EntityRecordResponse])
async def search_entity(
    body: EntitySearchRequest,
    mem: MemorySystem = Depends(get_mem),
) -> list[dict[str, Any]]:
    records = await mem.entity.search(
        query=body.query,
        entity_type=body.entity_type,
        entity_id=body.entity_id,
        top_k=body.top_k,
        agent_id=body.agent_id,
        project_scope=body.project_scope if body.project_scope is not None else ...,
    )
    return [to_dict(r) for r, _score in records]


# ── Cross-Tier Search ─────────────────────────────────────────────────────────


@router.post("/search", response_model=list[SearchResultItem])
async def search(
    body: SearchRequest,
    mem: MemorySystem = Depends(get_mem),
) -> list[dict[str, Any]]:
    # Resolve scope: all_scopes=True → "*", explicit project_scope → that,
    # otherwise use config default (sentinel)
    if body.all_scopes:
        scope: Any = "*"
    elif body.project_scope is not None:
        scope = body.project_scope
    else:
        scope = ...

    results = await mem.search(
        agent_id=body.agent_id,
        query=body.query,
        tiers=tuple(body.tiers) if body.tiers else None,
        top_k=body.top_k,
        project_scope=scope,
    )
    return [to_dict(r) for r in results]


# ── Prompt Context ────────────────────────────────────────────────────────────


@router.post("/context", response_model=ContextResponse)
async def build_context(
    body: ContextRequest,
    mem: MemorySystem = Depends(get_mem),
) -> dict[str, Any]:
    ctx = await mem.prompt.build(agent_id=body.agent_id, query=body.query)
    return {
        "working": ctx.working,
        "journal": ctx.journal,
        "ltm": ctx.ltm,
        "shared": ctx.shared,
        "entity": ctx.entity,
        "policy": ctx.policy,
        "deja_vu": ctx.deja_vu,
        "full_injection": ctx.full_injection,
        "token_estimate": ctx.token_estimate,
    }
