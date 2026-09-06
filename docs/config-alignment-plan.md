# nmem-family config alignment — design & execution plan

**Status:** design-only (no code yet). **Owner:** next session (execution). **Written:** 2026-09-07.
**Why now:** building michelle-ai surfaced that each nmem repo reads settings a different
way. That cost a real debug cycle (skills silently defaulted OFF), forces a hand-maintained
capability manifest (`michelle-ai/config/capabilities.env`), and is the reason nmem-sym has
the "env must be set before Python starts" footgun.

Goal: **one settings convention across the family**, so config is consistent, self-documenting,
introspectable (→ the capability map auto-generates), and free of import-time ordering traps.

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
list rendered.

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
- **Booleans are `true/false`** (pydantic parses `1/0/true/false/yes/no`, so **existing `=1`
  values keep working** — no env break for consumers).
- **Every field typed, defaulted, and `Field(description=...)`** — the description IS the manifest doc.
- **`env_nested_delimiter="__"`** for grouped sub-configs (nmem already does this).
- Access via the **`settings` singleton**, not scattered `os.environ` reads.

### Why this specific target
- nmem already uses it → it's the proven reference, not a new invention.
- `1/0` env values still parse → **backward compatible** for every current consumer (michelle, DJ-AI).
- `model_json_schema()` makes the whole surface introspectable → **auto-generate the manifest** (§4).

---

## 3. Migration strategy (staged; blast radius increases per phase)

### P1 — the easy repos: immune, viz, identity  *(low risk; off michelle's hot path)*
Convert `config.py` to a `Settings(BaseSettings)`. Keep a thin back-compat surface:
- **immune**: replace the `_bool/_float` globals with `Settings` fields; keep module-level names
  (`SKEPTIC_ENABLED = settings.skeptic_enabled`) so `config.SKEPTIC_ENABLED` call sites don't change.
- **identity**: fold `_DEFAULTS`/`_ENV`/TOML into `Settings` (pydantic-settings supports a TOML
  source via `settings_customise_sources`); keep `get(key)` as a shim over `settings`.
- **viz**: consolidate the scattered `os.environ.get` into one `Settings`.
Acceptance: `import <pkg>.config` works, all prior env names honored, `1/0` still parses,
existing tests green.

### P2 — nmem-sym  *(the hard one; ~200 flags, `config.X` read at import, PROD depends on it)*
Do **not** change the ~hundreds of `config.X` call sites. Instead:
1. Add `class SymSettings(BaseSettings)` with one described field per current flag (mechanical;
   can be generated from the current `config.py` — each `X = os.environ.get(...)` line → a field).
2. Replace the module body with `settings = SymSettings()` **plus a compatibility layer** that
   preserves `config.X` module-attribute access AND import-time availability:
   ```python
   settings = SymSettings()
   _g = globals()
   for name, val in settings.model_dump().items():
       _g[name.upper()] = val          # config.DRIVES_ENABLED etc. — unchanged for callers
   ```
   (Or `module __getattr__`; the explicit bind above keeps import-time semantics identical.)
3. **Preserve current effective defaults exactly** — audit every field's default against the
   present `os.environ.get(..., "d")` string so behavior is byte-identical when no env is set.
   Pay special attention to the ~28 flags that default ON.
4. Keep `BridgeConfig`'s env-fallback mapping working (it reads `config.DRIVES_ENABLED` etc.).

**Coordination:** nmem-sym is imported by **DJ-AI (production)** and **michelle**. Both consume
it as installs (michelle = editable at `/mnt/nas_projects/apps/nmem-sym/src`; DJ-AI may be a
built wheel — verify per venv, see the "nmem install mechanics" note). Rollout = change source →
rebuild/reinstall the wheel in each venv → restart each service. Stage: michelle first (isolated,
low-stakes), then DJ-AI after a clean bake.

### P3 — auto-generate the manifest, retire hand-maintenance
Once every repo is a `BaseSettings`, generate `capabilities.env` + the capability map from the
merged schemas (see §4). Delete the hand-curated manifest; the generator becomes the source of truth.

---

## 4. Payoff — the capability manifest auto-generates

With introspectable settings, one script replaces the hand-written `capabilities.env`:
```python
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

---

## 5. Acceptance criteria (per phase)
- **No behavior change with empty env**: every field's default reproduces today's effective value
  (write a test that asserts defaults for the ~28 default-ON flags especially).
- **Back-compat env**: existing `NMEM_*=1` / `=0` values still parse (pydantic bool coercion).
- **Import-time parity** (sym): `config.X` available at import, same values, no ordering change.
- **Consumers green**: michelle boots + full loop still works; DJ-AI prod boots (bake before deploy).
- **Introspectable**: `Settings.model_json_schema()` returns descriptions for every flag.

## 6. Risks & rollback
- **Silent default drift** (highest risk): a mis-transcribed default flips a live capability.
  Mitigation: generate the field list mechanically from the current `config.py`; add the
  defaults-parity test before merging.
- **Import-time semantics** (sym): keep the explicit global-bind so nothing lazy-loads later.
- **Prod (DJ-AI)**: land + bake on michelle first; deploy DJ-AI only after michelle is clean.
- **Rollback**: each repo change is self-contained + env-compatible, so reverting one repo's
  `config.py` + reinstalling the prior wheel restores prior behavior without touching consumers.

## 7. Suggested order for tomorrow
1. Agree the target (§2). 2. P1 immune → viz → identity (+ tests). 3. P2 nmem-sym behind the
compat shim, michelle-only, bake. 4. P3 generator + retire hand manifest. 5. DJ-AI rollout last.

## Appendix — key file references
- Reference impl: `nmem/src/nmem/config.py` (`model_config` L751).
- `nmem-sym/src/nmem_sym/config.py` (~200 globals; `BridgeConfig` env-map in `bridge.py` L152+).
- `nmem-immune/src/nmem_immune/config.py` (L8+ helper globals).
- `nmem-identity/src/nmem_identity/config.py` (L115+ `_resolved()`/`get()`).
- Current hand manifest + rendered map: `michelle-ai/config/capabilities.env`,
  `michelle-ai/docs/capabilities-map.html`.
