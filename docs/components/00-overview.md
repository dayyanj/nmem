# nmem: System Overview

Persistent memory system for AI agents. A 6-tier hierarchy where entries earn promotion through importance, access frequency, and multi-agent confirmation. Designed for multi-agent systems where agents share knowledge, resolve conflicts, and learn from experience.

## System Diagram

```
┌────────────────���─────────────────────────────────────────────────────┐
│                        Agent / Application                           │
│                                                                      │
│  LangChain adapter │ CrewAI adapter │ Plain Python │ MCP server      │
└──���───────────────────────┬─────────────��─────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────���─────────────────────────────���─────┐
│                        MemorySystem                                  │
│                                                                      │
│  MEMORY TIERS (data flows upward through earned promotion)           │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │  Tier 6: POLICY        governance rules, scoped permissions    │  │
│  │  Tier 5: ENTITY        collaborative per-object workspace      │  │
│  │  Tier 4: SHARED        cross-agent canonical facts             │  │
│  │  Tier 3: LTM           permanent per-agent knowledge           │  │
│  │  Tier 2: JOURNAL       30-day agent observations               │  │
│  │  Tier 1: WORKING       ephemeral per-session scratchpad        │  │
│  └───────────────────────────────────���────────────────────────────┘  │
│                                                                      │
│  PROCESSING                                                          │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │  search         hybrid vector + FTS + recency                  │  │
│  │  consolidation  micro-cycles + full cycles + nightly synthesis  │  │
│  │  conflicts      belief revision with grounding hierarchy        │  │
│  │  cognitive      deja vu, curiosity, counterfactual reasoning    │  │
│  │  compression    LLM-based write-time distillation               │  │
│  │  links          knowledge graphs between entries                │  ���
│  │  importance     heuristic + LLM scoring                         │  │
│  └─────────────��──────────────────────────────────────────────────┘  │
│                                                                      │
│  INFRASTRUCTURE                                                      │
│  ┌───────��────────────────────���───────────────────────────────────���  │
│  │  db/session     PostgreSQL+pgvector or SQLite                   │  │
│  │  providers      embedding (ST/OpenAI) + LLM (OpenAI/Anthropic)  │  │
│  │  prompt         token-budgeted context builder                  │  │
│  │  token_stats    usage tracking per agent per day                │  │
│  └───────────────��────────────────────────────────────────────────┘  │
│                                                                      │
│  INTERFACES                                                          │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │  MCP server     Claude Code / Claude Desktop integration        ��  │
│  │  REST API       FastAPI with middleware + dependency injection   │  │
│  │  CLI            typer app: init, search, stats, consolidate     │  │
│  └─────────────���──────────────────────────��───────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
                           │
              SymbolBridge (opt-in, crash-safe)
                           │
                           ���
              ┌────────────────────────┐
              │       nmem-sym         │
              │  (symbolic cognition)  │
              └─────────��──────────────┘
```

## Component Documents

1. [Working Memory](01-working-memory.md) -- ephemeral per-session scratchpad (Tier 1)
2. [Journal](02-journal.md) -- agent observations with compression, dedup, auto-expiry (Tier 2)
3. [Long-Term Memory](03-long-term-memory.md) -- permanent per-agent knowledge with salience decay (Tier 3)
4. [Shared Knowledge](04-shared-knowledge.md) -- cross-agent canonical facts with change-log (Tier 4)
5. [Entity Memory](05-entity-memory.md) -- collaborative per-object workspace with grounding (Tier 5)
6. [Policy Memory](06-policy-memory.md) -- governance rules with permissions and approval (Tier 6)
7. [Search & Retrieval](07-search-retrieval.md) -- hybrid vector+FTS search, recognition signals, prompt building
8. [Consolidation](08-consolidation.md) -- background lifecycle: expiry, promotion, dedup, synthesis
9. [Conflict Resolution](09-conflict-resolution.md) -- belief revision with grounding hierarchy and agent trust
10. [Cognitive Engine](10-cognitive-engine.md) -- deja vu, curiosity signals, counterfactual reasoning, delegation
11. [Knowledge Links](11-knowledge-links.md) -- associative links between entries, search expansion
12. [Compression & Importance](12-compression-importance.md) -- write-time LLM distillation, importance scoring
13. [nmem-sym Integration](13-nmem-sym-integration.md) -- symbol bridge, search augmentation, dreamstate hooks

## Key Principles

- **Earned promotion** -- entries flow upward through tiers based on importance, access, and multi-agent confirmation
- **Social learning** -- when 2+ agents access the same LTM entry 3+ times, it promotes to shared knowledge
- **Conflict-aware** -- every write scans for contradictions; resolution uses grounding > trust > recency > importance
- **Write-time compression** -- LLM distills verbose content into factual statements at save time
- **Recognition signals** -- search results carry KNOWN/FAMILIAR/UNCERTAIN scores so the LLM knows what to trust
- **Token-tracked** -- automatic monitoring of prompt injection size and LLM costs per agent per day
- **Provider-pluggable** -- swappable embedding (sentence-transformers/OpenAI) and LLM (OpenAI-compat/Anthropic) backends
- **Multi-backend** -- PostgreSQL+pgvector for production, SQLite for development
- **Project-scoped** -- optional multi-tenant isolation via project_scope parameter
- **Framework-agnostic** -- plain Python API + LangChain/CrewAI adapters + MCP server + REST API

## Tier Promotion Flow

```
Working (session) ──flush_to_journal()──> Journal (30 days)
                                              |
                            importance >= 7 OR access_count >= 5
                                              |
                                              v
                                         LTM (permanent)
                                              |
                            2+ agents, 3+ accesses, importance >= 8
                                              |
                                              v
                                     Shared (cross-agent)
```

Entity and Policy tiers operate independently -- they are not part of the promotion chain.
