# nmem-family config alignment — design & execution plan

**Status:** design-only (no code yet). **Owner:** next session (execution). **Written:** 2026-09-07.
**Revised:** 2026-09-07 — dropped backward-compat/alias approach (pre-1.0, breaking changes OK);
canonical single-name policy; added naming inventory, line classification, and invariant tests.
**Why now:** building michelle-ai surfaced that each nmem repo reads settings a different
way. That cost a real debug cycle (skills silently defaulted OFF), forces a hand-maintained
capability manifest (`michelle-ai/config/capabilities.env`), and is the reason nmem-sym has
the "env must be set before Python starts" footgun.

Goal: **one settings convention across the family**, so config is consistent, self-documenting,
introspectable (→ the capability map auto-generates), and free of import-time ordering traps.

**Framing (pre-1.0):** we do **not** carry backward compatibility. There are no external
consumers pinned to the current env names — a fleet grep found the legacy immune names set
*nowhere* except the generated manifest itself. So this is a clean canonicalization, not a
migration-with-shims. We change names freely and update every call site + the fleet env files
in lockstep. The bar is a **consistent** end state before 1.0, not a compatible transition.

---

## 1. Current state — five mechanisms across the family

| Repo | Mechanism | File | Truthy | Read at | Introspectable |
|---|---|---|---|---|---|
| **nmem** | pydantic `BaseSettings` + nested `BaseModel` sub-configs | `src/nmem/config.py` (`model_config` L751: `env_prefix="NMEM_"`, `env_nested_delimiter="__"`) | `true/false` | runtime (instance) | ✅ `model_json_schema()` |
| **nmem-sym** | ~200 module globals `X = os.environ.get("NMEM_SYM_…", "d") == "1"` accessed as `config.X` | `src/nmem_sym/config.py` | `1/0` | **import time** | ❌ |
| **nmem-immune** | helper globals `_bool()/_int()/_float()/_str("NMEM_IMMUNE_…", d)` | `src/nmem_immune/config.py` (L8+) | `1/0` | import | ❌ |
| **nmem-identity** | `_DEFAULTS` + TOML + `_ENV` map + `os.environ`, `@lru_cache _resolved()`, `get(key)` | `src/nmem_identity/config.py` (L115+) | strings | first-`get()` | ❌ |
| **nmem-viz** | mixed `os.environ.get` at module + service level | various | mixed | mixed | ❌ |

Counts (from the michelle-ai extraction, 2026-09-07): **90 capability toggles + ~274 tuning
knobs**. nmem-sym alone: 188 flags. See `michelle-ai/docs/capabilities-map.html` for the full
list rendered. **Caveat:** that count conflates true env-flags with hardcoded constants, derived
values, and dicts — see §3.0, line classification. Not every line becomes a `Field`.

### Consequences
- **Inconsistent truthiness** (`1/0` vs `true/false`) — the manifest carries both.
- **Import-time footgun** (nmem-sym): `config.X` is bound at import, so env MUST be set before
  Python starts. This is why `michelle-ai.service` loads `EnvironmentFile` and forbids setting
  these from inside Python.
- **Not introspectable** → the capability manifest is hand-maintained and drifts from source.
- **Different access patterns** (`config.X` attr vs `config.get("x")` vs a Settings instance)
  make cross-repo code and docs harder.

---

## 2. Target convention

Every repo exposes a **pydantic-settings `BaseSettings`** class, matching nmem's existing pattern:

```python
# <repo>/src/<pkg>/config.py
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NMEM_SYM_", env_nested_delimiter="__",
                                      extra="ignore")
    drives_enabled: bool = Field(False, description="Fire drive pressure into the loop.")
    drive_tick_seconds: float = Field(5.0, description="Drive accumulator tick (s).")
    # …one typed, defaulted, DESCRIBED field per flag…

settings = Settings()          # module singleton
```

Rules:
- **Per-repo `env_prefix`**: `NMEM_` (core), `NMEM_SYM_`, `NMEM_IMMUNE_`, `NMEM_IDENTITY_`, `NMEM_VIZ_`.
- **Booleans are `true/false`.** We standardize the wire format; we are *not* preserving `1/0`.
- **Every field typed, defaulted, and `Field(description=...)`** — the description IS the manifest doc.
- **`env_nested_delimiter="__"`** for grouped sub-configs (nmem already does this).
- **Access via the `settings` singleton** (`from <pkg>.config import settings; settings.field`) —
  not scattered `os.environ` reads, and not module-level uppercase globals.

### 2.1 Naming policy — ONE canonical name, no aliases

This is the load-bearing decision. **Each flag has exactly one name.** The env var is derived
mechanically from the field, with no hand-maintained mapping:

```
env var  ==  env_prefix + FIELD_NAME.upper()
field    ==  the single source of truth (snake_case)
```

We reject `validation_alias` / dual-name mappings entirely. Rationale (owner's call): an alias
table is a second, hand-authored name space that drifts — rename a field and the old env name
lingers unless someone remembers to update it. Instead: **if a name changes, we change the field,
update every call site, update the fleet env files, and verify — one name, everywhere.**

Consequence for today's code: several current env names do **not** equal `prefix + attr.upper()`
and MUST be renamed to their canonical form (examples in immune, [config.py:38-41](../../nmem-immune/src/nmem_immune/config.py#L38)):

| Today (attr / env) | Canonical field | Canonical env |
|---|---|---|
| `SKEPTIC_TEXT_OVERLAP_THRESHOLD` / `NMEM_IMMUNE_TEXT_OVERLAP_THRESHOLD` | `skeptic_text_overlap_threshold` | `NMEM_IMMUNE_SKEPTIC_TEXT_OVERLAP_THRESHOLD` |
| `SKEPTIC_TRUST_DELTA_THRESHOLD` / `NMEM_IMMUNE_TRUST_DELTA` | `skeptic_trust_delta_threshold` | `NMEM_IMMUNE_SKEPTIC_TRUST_DELTA_THRESHOLD` |
| `SKEPTIC_POISON_PATTERN_THRESHOLD` / `NMEM_IMMUNE_POISON_THRESHOLD` | `skeptic_poison_pattern_threshold` | `NMEM_IMMUNE_SKEPTIC_POISON_PATTERN_THRESHOLD` |
| `QUARANTINE_CORROBORATION_MIN_SOURCES` / `NMEM_IMMUNE_CORROBORATION_MIN` | `quarantine_corroboration_min_sources` | `NMEM_IMMUNE_QUARANTINE_CORROBORATION_MIN_SOURCES` |

(…full list produced by the §3.0 inventory.) Because no consumer sets these today, the rename is
free — the only edits are in-repo attr references and the (soon-to-be-generated) manifest.

### Why this specific target
- nmem already uses it → it's the proven reference, not a new invention.
- `model_json_schema()` makes the whole surface introspectable → **auto-generate the manifest** (§4),
  and because env == `prefix + FIELD.upper()` with no aliases, the generator's `env_name()` is a
  pure string transform with nothing to look up.
- Single access pattern (`settings.field`) across the family → cross-repo code and docs converge.

---

## 3. Migration strategy (staged; blast radius increases per phase)

### P0 — naming + line inventory (do this first, all repos)  *(no code change; produces the worklist)*
For each repo, mechanically extract a table of every current config line with three columns:
**Python symbol · env var read · classification**. Classification is one of:
- **field** — genuinely env-configurable → becomes a `Settings` field.
- **constant** — hardcoded, never read from env (e.g. sym `MAX_ACTIVATED_NODES = 50`,
  `MERGE_THRESHOLD = 0.90`, `EMBED_DIMENSIONS = 384`) → stays a plain module constant, NOT a field.
- **derived/dict** — computed values or literal dicts (immune `GROUNDING_TRUST`, `SOURCE_TRUST`) →
  stays as-is unless a sub-part is genuinely env-driven.
- **dynamic** — variable-length env discovery (sym `_discover_backends()` walking
  `NMEM_SYM_VLLM_0/1/2…`, [config.py:13](../../nmem-sym/src/nmem_sym/config.py#L13)) → cannot be a
  static field; keep a custom source or `@model_validator`.

This table is also the canonical-name list (§2.1) AND the parity oracle for §5 (every "field" row
carries its present effective default and any invariant — clamp/min/max/`>=1`). The field list for
`Settings` is generated *from this table*, not hand-typed, so transcription drift is eliminated at
the source.

**Settled policies (from the immune worked example, apply to all repos):**
- **Bounded ints that today silently clamp** (`min(x, N)`) → preserve exact parity with a
  `@field_validator` that re-applies `min(x, N)`, NOT a `le=N` constraint. Rationale: reject-on-
  out-of-range is a behavior change; we keep byte-identical behavior and revisit fail-loud
  hardening as a separate pass. (`>=1` guards from `_pos_int`, which already *raise* today, DO map
  to `Field(ge=1)` — same reject semantics as current code.)
- **Unwired symbols** (declared but read nowhere — the inventory's call-site column = 0) → keep as
  `Settings` fields, annotated `⚠ UNWIRED` in the description. Do not drop during migration; a
  zero-reference symbol may be reserved/not-yet-wired. Flag for a separate cleanup decision.
  (immune surfaced 6, incl. `db_dsn` — a DSN read *nowhere* is a smell worth investigating before
  1.0, independent of this refactor.)

**P0 tooling + generated inventories.** A reusable AST extractor produces the per-repo table
mechanically (the field list is generated, never hand-typed — §6 top risk). Generated worklists:
`docs/config-inventory-nmem-sym.md`, `docs/config-inventory-nmem-immune.md`. Tooling caveats the
worked examples surfaced:
- **Per-repo env-read idiom.** sym uses raw `os.environ.get`; immune wraps it in
  `_bool/_float/_int/_pos_int`; identity uses `get()/_resolved`. The extractor must be told each
  repo's reader helpers (name→type, plus invariants like `_pos_int`→`ge=1`) or it misreads fields
  as "derived/dynamic". Not a one-size script — a per-repo adapter line.
- **Helper-embedded defaults.** A default living in the helper signature (`_bool("K")` → `True`)
  isn't at the call site; the extractor reports `None`. The parity oracle must pull these from the
  helper default (immune's `skeptic_enabled` is the one default-ON flag hidden this way).

**nmem-sym scope (from the generated inventory) — the real P2 numbers:**
275 module symbols → **208 env fields**, **56 hardcoded constants** (NOT env-driven — the earlier
"188 flags" count was inflated; these stay module constants), **10 dicts/sets** (edge-type
frozensets), **1 dynamic source** (`VLLM_BACKENDS` via `_discover_backends()` walking
`NMEM_SYM_VLLM_0/1/2…` — custom settings source). Of the 208 fields: **25 need renaming** (mostly
an `_ENABLED`/word dropped in the env name, e.g. `NMEM_SYM_WORLD_MODEL` → `world_model_enabled`),
**24 default-ON booleans** (the parity-test hotlist), **4 derived-default** temperament axes
(`conscientiousness` etc. default from the preset dict selected by `NMEM_SYM_TEMPERAMENT` → a
`model_validator` fills unset axes from the preset), and **1 chained-fallback alias to kill**:
`PREDICTION_DEADLINE_FACTOR` falls back to `NMEM_SYM_TEMPORAL_PREDICTION_DEADLINE_FACTOR` AND has a
back-compat alias symbol `TEMPORAL_PREDICTION_DEADLINE_FACTOR = PREDICTION_DEADLINE_FACTOR`
([config.py:823-830](../../nmem-sym/src/nmem_sym/config.py#L823)) — the exact dual-name pattern
§2.1 forbids; collapse to one name, drop the fallback and the alias. Also `recipient_interests`
(comma-split `list[str]`) needs a field_validator to preserve parse semantics.
**Call-site caveat:** the inventory's call-site counts are grepped from `nmem-sym/src` only, so
host-driven flags (`drive_tick_seconds`, `prediction_enabled`, `self_model_path_observation`)
read by michelle/DJ-AI show 0 — NOT dead. Re-grep against consumers before treating any as unwired.

### P1 — the easy repos: immune, viz, identity  *(low risk; off michelle's hot path)*
Convert `config.py` to a `Settings(BaseSettings)` using the P0 table:
- **immune**: replace the `_bool/_float/_pos_int` globals with typed fields. Preserve invariants as
  field constraints/validators — `SKEPTIC_SCAN_LIMIT = min(_int(...), 200)` →
  `Field(20, le=200)`; `_pos_int` guards → `Field(..., ge=1)`. Canonicalize the mismatched names
  (§2.1 table). Update the ~handful of `config.SKEPTIC_*` call sites to `settings.skeptic_*`.
- **identity**: fold `_DEFAULTS`/`_ENV`/TOML into `Settings` (pydantic-settings supports a TOML
  source via `settings_customise_sources`). Replace `get(key)` call sites with `settings.field`.
- **viz**: consolidate the scattered `os.environ.get` into one `Settings`; update call sites.
Acceptance: `import <pkg>.config` works; every field's default reproduces today's effective value
(§5 parity test); every invariant preserved (clamp/ge tests); existing tests green; **grep proves
zero remaining `os.environ.get`/legacy-name references** outside the dynamic-source shim.

### P2 — nmem-sym  *(the hard one; ~200 flags, `config.X` read at import, PROD depends on it)*
1. From the P0 table, generate `class SymSettings(BaseSettings)` — one described field per **field**
   row (constants/dicts/dynamic rows are excluded and left as module-level code).
2. **Migrate call sites** `config.X` → `settings.x` (mechanical find/replace, verified). This is the
   largest single diff in the project and the main risk surface — see coordination below. There is
   **no uppercase-global compat shim**: keeping `config.DRIVES_ENABLED` alive alongside
   `settings.drives_enabled` would be exactly the second-name-space we rejected in §2.1.
3. This also **kills the import-time footgun**: `settings` resolves env when `Settings()` is
   constructed at import of the singleton, but callers read `settings.field` at *use* time, so
   there is no "must set env before this specific global binds" ordering trap.
4. **Preserve current effective defaults exactly** — the P0 table's default column is the oracle;
   the §5 parity test asserts them, with special attention to the ~28 flags that default ON.
5. Keep `BridgeConfig`'s env-fallback mapping working; update it to read `settings.field`
   ([bridge.py:152+](../../nmem-sym/src/nmem_sym/bridge.py#L152)).

**Coordination:** nmem-sym is imported by **DJ-AI (production)** and **michelle**. Both consume
it as installs (michelle = editable at `/mnt/nas_projects/apps/nmem-sym/src`; DJ-AI may be a
built wheel — verify per venv). Rollout = change source → rebuild/reinstall the wheel in each venv
→ restart each service. Stage: michelle first (isolated, low-stakes), then DJ-AI after a clean bake.
Because there is no compat surface, the whole call-site migration lands atomically per repo —
a half-migrated tree won't import. That's intentional: it makes "did we miss a call site?" a
hard import/test failure, not a silent runtime `AttributeError` in prod.

### P3 — auto-generate the manifest, retire hand-maintenance
Once every repo is a `BaseSettings`, generate `capabilities.env` + the capability map from the
merged schemas (see §4). Delete the hand-curated manifest; the generator becomes the source of truth.

---

## 4. Payoff — the capability manifest auto-generates

With introspectable settings and the no-alias naming law, one script replaces the hand-written
`capabilities.env`:
```python
def env_name(settings_cls, field):
    # No aliases → pure transform, nothing to look up.
    return settings_cls.model_config["env_prefix"] + field.upper()

for repo_settings in (nmem.Settings, sym.SymSettings, immune.Settings,
                      identity.Settings, viz.Settings):
    schema = repo_settings.model_json_schema()
    for field, meta in schema["properties"].items():
        emit(f"# {meta['description']}  [default={meta.get('default')}]")
        emit(f"{env_name(repo_settings, field)}={current_or_default}")
```
This yields: always-in-sync manifest, `--diff` against a live agent's env (what's non-default),
and the HTML map regenerated on demand. michelle's `config/capabilities.env` becomes a generated
artifact + a small hand-authored "michelle's chosen overrides" delta.

**Round-trip test (guards the naming law):** for every field, assert that setting
`env_name(cls, field)` in the environment actually changes `settings.field` — i.e. the generated
env name really resolves to that field. This is the mechanical guarantee that replaces alias
bookkeeping: if a name ever drifts, this test fails instead of the manifest silently lying.

---

## 5. Acceptance criteria (per phase)
- **No behavior change with empty env**: every field's default reproduces today's effective value
  (parity test driven by the P0 table; assert the ~28 default-ON sym flags explicitly).
- **Invariants preserved**: every clamp/min/max/`>=1` from the P0 table has a test — a dropped
  clamp passes a defaults-only test but is still a regression.
- **Single name, resolvable**: the §4 round-trip test passes for every field (env → field).
- **No stragglers**: grep proves zero `os.environ.get` / legacy-name / `config.UPPER` references
  remain outside declared dynamic sources; a half-migrated tree fails to import.
- **Consumers green**: michelle boots + full loop still works; DJ-AI prod boots (bake before deploy).
- **Introspectable**: `Settings.model_json_schema()` returns descriptions for every flag.

## 6. Risks & rollback
- **Silent default drift** (highest risk): a mis-transcribed default flips a live capability.
  Mitigation: the `Settings` field list is *generated from the P0 table*, not hand-typed; the
  defaults-parity + invariants tests gate the merge.
- **Missed call site** (new top risk, replaces alias-drift): renaming with no compat shim means a
  forgotten `config.X` is a hard failure. Mitigation: atomic per-repo migration + grep gate +
  test/import must be green before the wheel is rebuilt. Prefer a loud `AttributeError` at import
  over a silent fallback — this is why we deliberately keep no shim.
- **Import-time semantics** (sym): resolved by moving reads to use-time `settings.field`; verify no
  remaining module-scope code depends on a value being bound at import.
- **Prod (DJ-AI)**: land + bake on michelle first; deploy DJ-AI only after michelle is clean.
- **Rollback**: each repo change is self-contained; reverting one repo's `config.py` + call sites
  and reinstalling the prior wheel restores prior behavior without touching consumers. (No env
  files to roll back, since we don't rely on env-name compat.)

## 7. Suggested order for execution
1. Agree the target (§2) + naming law (§2.1). 2. P0 inventory for all repos (produces the worklist,
   canonical-name table, and parity oracle). 3. P1 immune → viz → identity (+ parity/invariant/
   round-trip tests). 4. P2 nmem-sym: generate `SymSettings`, migrate call sites, michelle-only,
   bake. 5. P3 generator + retire hand manifest. 6. DJ-AI rollout last.

## Appendix A — P0 results across the family (generated 2026-09-07)

Tools: `scripts/config_inventory/` (`p0_inventory.py` for the assignment idiom,
`p0_inventory_dictmap.py` for identity's dict-map idiom). Generated worklists live at
`docs/config-inventory-nmem-{sym,immune,viz,identity}.md`. Three distinct config idioms across the
family confirmed the "per-repo adapter" prediction: raw `os.environ.get` (sym, viz), wrapper
helpers (immune), dict-map + TOML (identity).

| Repo | idiom | fields | constants | dicts/sets | dynamic | renames | default-ON | special |
|---|---|--:|--:|--:|--:|--:|--:|---|
| **nmem-sym** | os.environ.get | 208 | 56 | 10 | 1 | 25 | 24 | 1 alias to kill; 4 temperament validators; VLLM dynamic source |
| **nmem-immune** | `_bool/_float/…` | 18 | 0 | 2 | 0 | 10 | 1* | 2 clamps, 3 `ge=1` (*default-ON hidden in helper sig) |
| **nmem-viz** | os.environ.get | 19 | 0 | 0 | 0 | 9 | 1 | `static_dir` computed default |
| **nmem-identity** | dict-map + TOML | 15 | 0 | 0 | 0 | 4 | 0 | TOML source → `settings_customise_sources`; access via `get()`/`dsn()` |
| **totals** | | **260** | 56 | 12 | 1 | **48** | 26 | |

Reading: the migration touches **260 real env fields** (not the "~364 knobs / 188 sym flags"
headline — 56 sym symbols are hardcoded constants that stay module-level). **48 fields get renamed**
to canonical form. **26 default-ON booleans** are the parity-test hotlist. nmem (core) is already
`BaseSettings` — it's the reference target, not a migration.

## Appendix B — key file references
- Reference impl: `nmem/src/nmem/config.py` (`model_config` L751).
- `nmem-sym/src/nmem_sym/config.py` (~200 globals; `BridgeConfig` env-map in `bridge.py` L152+);
  note hardcoded constants (`MAX_ACTIVATED_NODES`, `MERGE_THRESHOLD`) and the dynamic
  `_discover_backends()` at L13 — neither becomes a plain field.
- `nmem-immune/src/nmem_immune/config.py` (L8+ helper globals; name mismatches at L38-41;
  clamps/`_pos_int` invariants at L42/L46-48).
- `nmem-identity/src/nmem_identity/config.py` (L115+ `_resolved()`/`get()`).
- Current hand manifest + rendered map: `michelle-ai/config/capabilities.env`,
  `michelle-ai/docs/capabilities-map.html`.
