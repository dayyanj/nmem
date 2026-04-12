# Changelog

All notable changes to nmem are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.2.0] — 2026-04-12

### Added
- `memory_write_entity` MCP tool — write typed entity records with
  explicit record_type and grounding lifecycle.
- `memory_write_policy` MCP tool — write governance policies with
  upsert semantics on (scope, key).
- `memory_check_conflicts` MCP tool — list conflicts detected by the
  scanner, with status/agent/scope/time filters and `all_scopes` param.
- `memory_mark_grounding` MCP tool — transition an entity record's
  grounding value (inferred → confirmed / disputed) with audit trail.
- `memory_search` now accepts "policy" in its `tiers` parameter.
- `list_conflicts()` function in `nmem.conflicts`.
- `EntityTier.update_grounding()` method.
- `PolicyTier.search()` method (FTS on postgres, LIKE on sqlite).
- Default policy writers expanded to include "default" and "mcp" for
  out-of-box MCP tool usage.
- `project_scope` column on `nmem_memory_conflicts` table — conflicts
  are now scoped to the project that produced them. Populated
  automatically by the scanner at write time.

### Changed
- `nmem setup` CLAUDE.md and AGENTS.md snippets restructured into
  Retrieval / Writes / Integrity groups, with a "which tier" decision
  tree. `memory_linked` now documented (was missing in 0.1.x).
- `cross_tier_search` accepts optional `policy` keyword argument.

### Upgrade notes for existing PostgreSQL installations
- Run: `ALTER TABLE nmem_memory_conflicts ADD COLUMN project_scope VARCHAR(300);`
- Run: `CREATE INDEX ix_nmem_conflict_project_scope ON nmem_memory_conflicts (project_scope);`
- Existing conflict rows will have `project_scope = NULL` (treated as global).

### Notes
- All changes are additive. Existing 8 MCP tools and their parameters
  are unchanged. Agents built against 0.1.x continue to work.
- Policy tier has no embedding column — search uses FTS (postgres) or
  LIKE (sqlite) instead of hybrid vector+FTS.
- Policy tier is globally scoped (no project_scope column). Searching
  with `tiers="policy"` returns the same policies regardless of the
  current project scope.
