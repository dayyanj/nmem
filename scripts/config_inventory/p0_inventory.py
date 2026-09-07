#!/usr/bin/env python3
"""P0 config inventory extractor (reusable across the nmem family).

Parses a repo's config.py via AST and classifies every module-level assignment:
  field     — reads os.environ.get(...) → becomes a Settings field
  constant  — literal, no env read → stays a module constant
  dict/set  — dict/frozenset/comprehension literal → stays a module constant
  dynamic   — value is a call to a local helper (env discovery / derived) → custom source
Flags: name mismatch (env suffix != prefix+SYMBOL), chained-fallback (alias smell),
derived-default (default comes from another symbol/preset, not a literal).

Emits a markdown inventory + a summary. Call-site counts are grepped from src/.
"""
from __future__ import annotations
import ast, re, sys, pathlib

CONFIG = pathlib.Path(sys.argv[1])
SRC_ROOT = pathlib.Path(sys.argv[2])
PREFIX = sys.argv[3]  # e.g. NMEM_SYM_
# Optional 4th arg: this repo's env-reader wrapper helpers, name:type[:invariant]
# e.g. "_bool:bool,_float:float,_int:int,_pos_int:int:ge=1"
READERS = {}
if len(sys.argv) > 4 and sys.argv[4]:
    for spec in sys.argv[4].split(","):
        parts = spec.split(":")
        READERS[parts[0]] = (parts[1], parts[2] if len(parts) > 2 else "")

src = CONFIG.read_text()
tree = ast.parse(src)

def env_calls(node):
    """Env reads within a node: os.environ.get(...) OR a repo reader helper.
    Returns list of (kind, call) where kind is 'os' or the helper name."""
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if (isinstance(f, ast.Attribute) and f.attr == "get"
                    and isinstance(f.value, ast.Attribute) and f.value.attr == "environ"):
                out.append(("os", n))
            elif isinstance(f, ast.Name) and f.id in READERS:
                out.append((f.id, n))
    return out

def clamp_note(node):
    """Detect a min()/max() wrapper (silent clamp) and its numeric bound."""
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("min", "max"):
            bounds = [literal(a) for a in n.args if isinstance(literal(a), (int, float))]
            if bounds:
                return f"⚠ clamp {n.func.id}({bounds[0]}) — preserve via field_validator"
    return ""

def literal(n):
    try:
        return ast.literal_eval(n)
    except Exception:
        return None

def classify(sym, value):
    calls = env_calls(value)  # [(kind, call), ...]
    if calls:
        # ── field: reads env (via os.environ.get or a repo reader helper) ──
        kind, primary = calls[0]
        env = literal(primary.args[0])
        os_calls = [c for k, c in calls if k == "os"]
        # A true env-fallback alias = TWO env reads (os.environ.get(X, os.environ.get(Y,...))).
        # A single call whose default is a computed expr (os.path.abspath(...)) is NOT an alias.
        chained = len(os_calls) > 1
        last = calls[-1][1]
        deflt = literal(last.args[1]) if len(last.args) > 1 else None
        # type: reader helper declares it; else infer from wrapper
        typ, inv = "str", ""
        if kind in READERS:
            typ, inv = READERS[kind]
        elif isinstance(value, ast.Compare) and any(isinstance(o, ast.Eq) for o in value.ops):
            typ = "bool"
        elif isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
            typ = {"int": "int", "float": "float"}.get(value.func.id, "str")
        elif isinstance(value, ast.ListComp):
            typ = "list[str]"
        notes = []
        if inv:
            notes.append(f"invariant {inv}")
        cn = clamp_note(value)
        if cn:
            notes.append(cn)
        if chained:
            notes.append(f"⚠ chained-fallback (alias): {[literal(c.args[0]) for k,c in calls]}")
        return dict(cls="field", env=env, default=deflt, typ=typ, note="; ".join(notes))
    # ── not a normal env read: local helper (derived/dynamic) or plain constant ──
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) \
            and value.func.id not in ("int", "float", "str", "bool", "frozenset", "set", "dict"):
        args = [literal(a) for a in value.args]
        env = args[0] if args and isinstance(args[0], str) and args[0].startswith(PREFIX) else None
        if env is None:
            return dict(cls="dynamic", env=None, default="(discovered)", typ="—",
                        note=f"⚠ dynamic via {value.func.id}() — custom source, not a scalar field")
        deflt = f"preset[{args[1]}]" if len(args) > 1 else "(derived)"
        return dict(cls="field*", env=env, default=deflt, typ="float",
                    note=f"⚠ derived-default via {value.func.id}() — needs model_validator")
    if isinstance(value, (ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)) \
            or (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                and value.func.id in ("frozenset", "set", "dict")) \
            or isinstance(value, ast.BinOp):
        return dict(cls="dict/set", env=None, default=None, typ="—", note="not env-driven")
    return dict(cls="constant", env=None, default=repr(literal(value)), typ="—",
                note="hardcoded, not env-driven")

rows = []
seen = set()
for node in tree.body:
    if isinstance(node, ast.Assign):
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        sym, value = node.targets[0].id, node.value
    elif isinstance(node, ast.AnnAssign):
        if not isinstance(node.target, ast.Name) or node.value is None:
            continue
        sym, value = node.target.id, node.value
    else:
        continue
    if sym.startswith("_") or sym in seen:
        continue
    seen.add(sym)
    info = classify(sym, value)
    expected_env = PREFIX + sym
    mism = ""
    if info["env"] and info["env"] != expected_env:
        mism = "✗"
    elif info["env"]:
        mism = "✓"
    rows.append(dict(sym=sym, line=node.lineno, **info, match=mism, expected=expected_env))

# call-site counts across src (excluding config.py), approximate
counts = {}
for p in SRC_ROOT.rglob("*.py"):
    if p.name == "config.py":
        continue
    text = p.read_text(errors="ignore")
    for r in rows:
        counts.setdefault(r["sym"], 0)
        counts[r["sym"]] += len(re.findall(r"\b" + re.escape(r["sym"]) + r"\b", text))

# ── output ──
fields = [r for r in rows if r["cls"].startswith("field")]
consts = [r for r in rows if r["cls"] == "constant"]
dicts_ = [r for r in rows if r["cls"] == "dict/set"]
dynamic = [r for r in rows if r["cls"] == "dynamic"]
mismatches = [r for r in fields if r["match"] == "✗"]
default_on = [r for r in fields if r["typ"] == "bool" and str(r["default"]) == "1"]
chained = [r for r in fields if "chained" in r["note"]]
derived = [r for r in fields if r["cls"] == "field*"]
unwired = [r for r in fields if counts.get(r["sym"], 0) == 0]

print(f"## P0 inventory — {CONFIG.parent.name} ({PREFIX})\n")
print(f"**Totals:** {len(rows)} module symbols → "
      f"**{len(fields)} fields** · {len(consts)} constants · {len(dicts_)} dicts/sets "
      f"· {len(dynamic)} dynamic\n")
print("> Note: call-site counts are grepped from THIS repo's src/ only. A 0 does NOT mean dead —"
      " host-driven flags (BridgeConfig, drive_loop, prediction plugin) are read by the CONSUMER"
      " (michelle/DJ-AI), not by nmem-sym itself. Verify against consumers before treating as unwired.\n")
print(f"- name mismatches (rename needed): **{len(mismatches)}**")
print(f"- default-ON booleans: **{len(default_on)}**")
print(f"- chained-fallback aliases (collapse to one name): **{len(chained)}**")
print(f"- derived-default fields (need model_validator): **{len(derived)}**")
print(f"- unwired fields (0 call sites in src): **{len(unwired)}**\n")

print("### Fields\n")
print("| line | canonical field | env today | ? | type | default | call sites | note |")
print("|---|---|---|:--:|---|---|:--:|---|")
for r in sorted(fields, key=lambda r: r["line"]):
    print(f"| {r['line']} | `{r['sym'].lower()}` | `{r['env']}` | {r['match']} "
          f"| {r['typ']} | `{r['default']}` | {counts.get(r['sym'],0)} | {r['note']} |")

if dynamic:
    print("\n### Dynamic sources (variable/indexed env — need a custom settings source)\n")
    for r in dynamic:
        print(f"- `{r['sym']}` (line {r['line']}) — {r['note']}")

print("\n### Constants (stay module-level; NOT fields)\n")
print(", ".join(f"`{r['sym']}`" for r in sorted(consts, key=lambda r: r['line'])) or "(none)")
print("\n### Dicts / sets / derived (stay module-level)\n")
print(", ".join(f"`{r['sym']}`" for r in sorted(dicts_, key=lambda r: r['line'])) or "(none)")
