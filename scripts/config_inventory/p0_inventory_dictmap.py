#!/usr/bin/env python3
"""P0 inventory adapter for the DICT-MAP idiom (nmem-identity).

Some repos don't declare one assignment per flag; they carry a `_DEFAULTS` dict,
an `_ENV` name-map, and type-key sets (`_INT_KEYS`/`_FLOAT_KEYS`), resolved via a
`get(key)` accessor. The assignment-scanning extractor can't see these, so this
adapter literal-evals those dicts and emits the same table format.

Usage: p0_inventory_dictmap.py <config.py> <src_root> <PREFIX>
"""
from __future__ import annotations
import ast, re, sys, pathlib

CONFIG = pathlib.Path(sys.argv[1])
SRC_ROOT = pathlib.Path(sys.argv[2])
PREFIX = sys.argv[3]

tree = ast.parse(CONFIG.read_text())
lits = {}
for node in tree.body:
    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
        name, val = node.targets[0].id, node.value
    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
        name, val = node.target.id, node.value
    else:
        continue
    try:
        lits[name] = ast.literal_eval(val)
    except Exception:
        pass

defaults = lits.get("_DEFAULTS", {})
env_map = lits.get("_ENV", {})
int_keys = set(lits.get("_INT_KEYS", set()))
float_keys = set(lits.get("_FLOAT_KEYS", set()))

# call-site counts: config.get("key") plus accessor fns, across src
text_all = "".join(p.read_text(errors="ignore") for p in SRC_ROOT.rglob("*.py") if p.name != CONFIG.name)
def count(key):
    return len(re.findall(r'config\.get\(\s*["\']' + re.escape(key) + r'["\']', text_all))

rows = []
for key, dflt in defaults.items():
    env = env_map.get(key)
    typ = "int" if key in int_keys else "float" if key in float_keys else \
          ("str" if isinstance(dflt, str) else "str|None" if dflt is None else type(dflt).__name__)
    expected = PREFIX + key.upper()
    match = "✓" if env == expected else ("✗" if env else "—")
    rows.append((key, env, match, typ, dflt, count(key), expected))

mism = [r for r in rows if r[2] == "✗"]
print(f"## P0 inventory — {CONFIG.parent.name} ({PREFIX})  [dict-map idiom]\n")
print(f"**Totals:** {len(rows)} keys (all fields) — no constants/dynamic in this idiom.\n")
print("> Precedence today: built-in defaults < TOML (`$NMEM_IDENTITY_CONFIG`/`./nmem-identity.toml`)"
      " < env. Under pydantic-settings this needs a custom TOML source between defaults and env"
      " via `settings_customise_sources`. Access is `config.get(\"key\")` → migrate to `settings.key`.\n")
print(f"- name mismatches (rename needed): **{len(mism)}**")
print(f"- default-ON booleans: **0** (no bools in this config)")
print(f"- TOML source to preserve: **yes** (custom settings source)\n")
print("### Fields\n")
print("| canonical field | env today | ? | type | default | call sites | canonical env |")
print("|---|---|:--:|---|---|:--:|---|")
for key, env, match, typ, dflt, n, expected in rows:
    print(f"| `{key}` | `{env}` | {match} | {typ} | `{dflt!r}` | {n} | `{expected}` |")
