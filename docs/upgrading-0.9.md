# Upgrading to nmem 0.9.x

This guide is for **host applications that embed nmem (and nmem-sym)** — e.g. the
Spwig Refinery — moving from the 0.8.x line to **nmem 0.9.2 + nmem-sym 0.9.1**.

Two things changed that a host needs to act on:

1. **0.9.2 (breaking):** SQLite is gone — nmem is PostgreSQL + pgvector only.
2. **0.9.0 / 0.9.1 (new interface):** nmem is now the single "conscious" front
   door to the cognitive system. External obligations are imposed on nmem as
   **commitments** and flow to nmem-sym automatically; the host reacts through
   nmem's event bus instead of wiring the bridge directly.

---

## TL;DR — what the host must change

| Area | Before (0.8.x) | After (0.9.x) |
|------|----------------|---------------|
| Database | PostgreSQL **or** SQLite | **PostgreSQL + pgvector only** — a non-postgres URL raises at startup |
| Install extra | `nmem[postgres]` / `nmem[sqlite]` | `asyncpg`+`pgvector` are core deps; `nmem[postgres]` is a no-op alias; **`nmem[sqlite]` removed** |
| CLI init | `nmem init --sqlite` | `nmem init` (Postgres only) |
| Backend wiring | host wires nmem-sym obligations directly | `bridge.connect(mem)` **auto-registers** nmem-sym as nmem's backend |
| Imposing an obligation | call the bridge | `await mem.commitments.impose(...)` |
| Reacting to an obligation | `bridge.on_obligation(...)` | `mem.on("commitment.acting")(...)` (single interface) |

Nothing else in the tier APIs (journal / LTM / shared / entity / policy) changed.

---

## 1. Database: PostgreSQL + pgvector only (0.9.2, breaking)

nmem is a concurrent multi-writer system (background consolidation, the
cognitive-backend commitment flush, the obligation reverse channel, drive ticks)
and its core feature is vector search. SQLite fit neither, so it was removed.

**Do this:**

- Point `database_url` at a PostgreSQL instance with the `pgvector` extension:
  ```python
  config = NmemConfig(
      database_url="postgresql+asyncpg://user:pass@host:5432/nmem",
  )
  ```
  A URL whose scheme isn't `postgresql*` now raises `ValueError` in
  `DatabaseManager.__init__` — fail-fast, at startup.
- Remove any `sqlite+aiosqlite://…` URLs, the `nmem[sqlite]` extra, and any
  `nmem init --sqlite` calls. `asyncpg` and `pgvector` install automatically with
  `nmem` now (`nmem[postgres]` still resolves, as a no-op alias).
- Ensure the extension exists once per database:
  `CREATE EXTENSION IF NOT EXISTS vector;` (nmem also attempts this on
  `initialize()`).

See [configuration.md](configuration.md) and the `[0.9.2]` entry in
[../CHANGELOG.md](../CHANGELOG.md).

---

## 2. nmem is the conscious interface (0.9.0 / 0.9.1)

The mental model: **nmem is the conscious layer, nmem-sym is the subconscious
slow brain.** The host talks to nmem; nmem forwards to nmem-sym and reports back.
External obligations ("I need to get X done for the founder by Friday") are the
first *extrinsic* driver, and they enter the system as **commitments on nmem**.

### 2a. Backend registration is automatic

When nmem-sym connects, it registers itself as nmem's cognitive backend — the
host does **not** call anything extra:

```python
graph = SymbolGraph(...)
await graph.connect()

bridge = SymbolBridge(graph)
bridge.connect(mem)     # ← self-registers as mem's cognitive backend (0.9.1)
```

From this point, any commitment imposed on nmem is mirrored into nmem-sym's live
obligation ledger (deadline pressure, meta-arbiter, temperament). Commitments
imposed *before* the bridge connects are queued and flushed on connect, so
ordering is not load-bearing. If nmem runs with no backend attached, commitments
are still recorded durably and mirror once a backend registers — nmem stays
standalone.

### 2b. Impose external obligations as commitments

Instead of poking the bridge, the host records the obligation on nmem:

```python
info = await mem.commitments.impose(
    requester="founder",
    description="Ship the Q3 benchmark report",
    deadline=datetime(2026, 8, 21, 17, 0, tzinfo=timezone.utc),  # tz-aware
    authority=0.9,        # how much weight this requester carries (0..1)
    importance=1.0,       # relative importance of this deliverable
    # project_scope defaults to the instance's configured scope
)
# info.id                → nmem's durable commitment id
# info.sym_obligation_id → the linked nmem-sym obligation (once mirrored)
```

`deadline` must be timezone-aware. `deadline=None` records a commitment with no
deadline pressure.

### 2c. Execute the deliverable via nmem events

When the obligation wins nmem-sym's meta-arbiter (it's the most pressing thing to
act on right now), nmem emits `commitment.acting`. **That is the host's execution
hook** — subscribe once, in one place:

```python
@mem.on("commitment.acting")
async def _do_the_work(data):
    # data = {"id", "sym_obligation_id", "description", "pressure"}
    await refinery.run_deliverable(data["description"])
    # when finished, close the loop:
    await mem.commitments.confirm(data["id"])
```

This replaces `bridge.on_obligation(...)`. The direct bridge handler still works
for direct-API deployments, but **wire only one execution path** — don't register
both `mem.on("commitment.acting")` and `bridge.on_obligation(...)` for the same
side-effect, or the deliverable runs twice.

### 2d. Lifecycle events

nmem-sym reports transitions back up; nmem re-emits them for the host:

| Event | Meaning | Payload |
|-------|---------|---------|
| `commitment.imposed` | recorded (host- or content-sourced) | `{id, requester, description, source}` |
| `commitment.acting` | selected to act on now | `{id, sym_obligation_id, description, pressure}` |
| `commitment.missed` | deadline passed, in grace | `{id, sym_obligation_id, ...}` |
| `commitment.breached` | grace expired, unmet | `{id, sym_obligation_id, ...}` |
| `commitment.fulfilled` | met (via `confirm()` or backend) | `{id, ...}` |

Subscribe to whichever the Refinery cares about (e.g. escalate on
`commitment.breached`).

### 2e. Managing commitments

```python
await mem.commitments.confirm(commitment_id)                 # → fulfilled
await mem.commitments.abandon(commitment_id)                 # drop it
await mem.commitments.renegotiate(commitment_id, new_deadline)
open_ = await mem.commitments.list("open")                   # list[CommitmentInfo]
```

`confirm` / `abandon` / `renegotiate` return `False` for an unknown id (they
don't silently succeed). `list()` is scoped to the instance's `project_scope`.

### 2f. Migrating from direct `bridge.on_obligation`

If the Refinery currently registers `bridge.on_obligation(handler)` and imposes
obligations straight on the bridge:

1. Replace obligation-creation calls with `mem.commitments.impose(...)`.
2. Replace `bridge.on_obligation(handler)` with `mem.on("commitment.acting")(handler)`
   (note the payload is a dict, not an `ObligationIntent`).
3. Call `mem.commitments.confirm(id)` when the deliverable completes, so the
   ledger and the narrative record both close.

---

## 3. Optional: commitment detection from content

nmem can also *discover* commitments the agent made in its own journal ("told the
founder I'd have it Friday") during nightly consolidation, and impose them
automatically — the mirror of curiosity flowing outward. Off by default:

```python
config = NmemConfig(
    database_url="postgresql+asyncpg://…",
    commitment_detection={
        "enabled": True,          # default False
        "lookback_hours": 24,
        "max_entries": 50,
        "min_confidence": 0.6,
        "default_authority": 0.5,
    },
)
```

Detected commitments carry `source="detected"` and are deduped against
recently-created commitments within the same `project_scope`.

---

## 4. Refinery-specific notes

- Keep using the tuned profile:
  `NmemConfig.from_profile("refinery", database_url="postgresql+asyncpg://…")`.
  Profile defaults deep-merge under your explicit kwargs. See
  [profiles.md](profiles.md).
- The nmem-sym side (drives, obligations, temperament) is documented in the
  **nmem-sym repo**: `docs/cognitive-drives.md` and
  `docs/bringing-the-system-online.md`.

## Reference

- nmem `[0.9.0]` (commitments interface) and `[0.9.2]` (SQLite drop) —
  [../CHANGELOG.md](../CHANGELOG.md)
- nmem-sym `[0.9.1]` (self-registration + relay) — nmem-sym `CHANGELOG.md`
- `mem.commitments` API — [../src/nmem/commitments.py](../src/nmem/commitments.py)
