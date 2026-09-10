"""Memory bootstrap — construct an agent's nmem MemorySystem + nmem-sym SymbolGraph.

The order-sensitive, DSN-fiddly boot recipe every agent needs, once, as config.
Graduated from michelle-ai's ``service/memory.py``.

Bootstrap facts (do not reorder):
  - ``MemorySystem(cfg)`` builds engines only; ``await mem.initialize()`` creates tables.
  - ``SymbolGraph(...)`` builds nothing; ``await graph.connect()`` runs migrations.
  - ORDER MATTERS: ``mem.initialize()`` BEFORE ``graph.connect()`` (a sym migration
    ALTERs an nmem-owned table).
  - Embedding dimension MUST match nmem-sym's ``vector(N)`` (384 for all-MiniLM-L6-v2).
  - DSN prefixes differ: nmem wants ``postgresql+asyncpg://``, nmem-sym wants raw
    ``postgresql://`` — ``build_symbol_graph`` strips the ``+asyncpg`` automatically.

Capability toggles (skills, autonomy, commitment_detection, …) are deliberately NOT
passed as kwargs: ``NmemConfig`` is a pydantic BaseSettings that reads them from env
(``NMEM_<SECTION>__<FIELD>``), so a ``capabilities.env`` manifest stays the single
source of truth. Passing them here would override the env and split-brain the manifest.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)


def _db_url(config: dict, *, env_key: str | None = None, config_key: str = "default") -> str:
    url = None
    if env_key:
        url = os.environ.get(env_key)
    url = url or (config.get("databases", {}).get(config_key, {}) or {}).get("url")
    if not url:
        raise RuntimeError(
            f"agent DB DSN not configured (env {env_key or '-'} / databases.{config_key}.url)")
    return url


async def build_memory(config: dict):
    """Construct + initialize an agent's nmem MemorySystem from ``config['nmem']``.

    Reads structural config (DSN, embedding/LLM endpoints, belief/policy) from the
    ``config`` dict; capability flags come from env (see module docstring)."""
    from nmem import MemorySystem, NmemConfig

    ncfg = config.get("nmem", {}) or {}
    db = config.get("db", {}) or {}
    emb = ncfg.get("embedding", {}) or {}
    llm = ncfg.get("llm", {}) or {}

    def _key(d: dict, default: str = "") -> str:
        # Resolve the provider key: an inline api_key, else the env var NAMED by api_key_env
        # (the studio keeps keys in secrets.env, not agent.yaml — the writer strips inline keys,
        # so a hosted LLM / remote embedder would otherwise reach here with no credential).
        k = d.get("api_key")
        if not k and d.get("api_key_env"):
            k = os.environ.get(d["api_key_env"], "")
        return k or default

    cfg = NmemConfig(
        database_url=_db_url(config, env_key=db.get("env_key"),
                             config_key=db.get("config_key", "default")),
        embedding={
            "provider": emb.get("provider", "sentence-transformers"),
            "model": emb.get("model", "all-MiniLM-L6-v2"),
            "base_url": emb.get("base_url"),
            "api_key": _key(emb),
            "dimensions": emb.get("dimensions", 384),
        },
        llm={
            "provider": llm.get("provider", "openai"),
            "base_url": llm.get("base_url"),
            "model": llm.get("model", ""),
            "api_key": _key(llm, "not-needed"),
        },
        belief=ncfg.get("belief", {"default_trust": 0.5}),
        policy=ncfg.get("policy", {"writers": {"system"}, "max_chars_in_prompt": 4000}),
    )
    mem = MemorySystem(cfg)
    await mem.initialize()   # bootstraps nmem tables + pgvector + indexes
    log.info("nmem MemorySystem initialized (isolated store)")
    return mem


async def build_symbol_graph(config: dict, embedder=None):
    """Construct + connect an agent's nmem-sym SymbolGraph from ``config['symbol_graph']``.

    Returns None when disabled. MUST be called AFTER build_memory (a sym migration
    alters an nmem table). Pass ``embedder`` (the MemorySystem's shared
    EmbeddingProvider) so the graph reuses one model instance per process instead
    of loading its own — see docs/proposals/nmem-sym-embedding-seam-unification.md."""
    sg = config.get("symbol_graph", {}) or {}
    if not sg.get("enabled", False):
        log.info("[sym] symbol graph disabled")
        return None
    from nmem_sym import SymbolGraph

    db = config.get("db", {}) or {}
    raw_dsn = _db_url(config, env_key=db.get("env_key"),
                      config_key=db.get("config_key", "default")).replace(
        "postgresql+asyncpg://", "postgresql://")
    graph = SymbolGraph(
        db_dsn=raw_dsn,
        domain=sg.get("domain", "agent-cognition"),
        edge_types=set(sg.get("edge_types", ["causes", "enables", "blocks", "relates_to"])),
        vllm_backends=sg.get("vllm_backends"),
        vllm_model=sg.get("vllm_model"),
        embed_model=sg.get("embed_model", "sentence-transformers/all-MiniLM-L6-v2"),
        embedder=embedder,
    )
    await graph.connect()   # bootstraps symbol_* tables (migrations)
    log.info("[sym] symbol graph connected (domain=%s)", sg.get("domain"))
    return graph
