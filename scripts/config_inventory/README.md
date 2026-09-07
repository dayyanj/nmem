# P0 config-inventory tools

Mechanical extractors for the config-alignment project (see `docs/config-alignment-plan.md`).
They classify every config symbol in a repo (field / constant / dict-set / dynamic), derive the
canonical name (`env == PREFIX + FIELD.upper()`), flag name mismatches, default-ON booleans,
chained-fallback aliases, clamps/invariants, and grep approximate call-site counts. The field
list for each repo's future `Settings` class is meant to be generated from these, never hand-typed.

## Two idioms, two tools

**`p0_inventory.py`** — assignment-scanning. For repos that declare one assignment per flag.
Handles both `os.environ.get` and per-repo reader wrappers.

```bash
# sym: raw os.environ.get, no wrappers
python3 p0_inventory.py <config.py> <src_root> NMEM_SYM_

# immune: wraps env reads in helpers — declare them as name:type[:invariant]
python3 p0_inventory.py <config.py> <src_root> NMEM_IMMUNE_ "_bool:bool,_float:float,_int:int,_pos_int:int:ge=1"

# viz: raw os.environ.get
python3 p0_inventory.py <config.py> <src_root> NMEM_VIZ_
```

**`p0_inventory_dictmap.py`** — dict-map idiom. For repos with a `_DEFAULTS` dict + `_ENV`
name-map + `_INT_KEYS`/`_FLOAT_KEYS` type sets, resolved via `get(key)` (nmem-identity).

```bash
python3 p0_inventory_dictmap.py <config.py> <src_root> NMEM_IDENTITY_
```

## Known limitations (must hand-verify)

- **Call-site counts are grepped from the target repo's `src/` only.** A `0` does NOT mean dead:
  host-driven flags are read by the CONSUMER (michelle / DJ-AI), not the repo itself. Re-grep
  consumers before pruning anything.
- **Helper-embedded defaults** (`_bool("K")` whose default lives in the helper signature) show as
  `None`. Pull the real default from the helper when building the parity oracle.
- **Idiom must be declared.** Point the right tool at the repo and pass its reader wrappers, or
  fields get misread as derived/dynamic.
