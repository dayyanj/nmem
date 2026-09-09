# nmem-hive (multi-agent · shared world-model)

Several nmem agents that **share one symbol-graph world-model** but keep their **own** goals, drives,
and pursuit. It's the same appliance image as [`../studio`](../studio/) — the only difference is that
every member points `NMEM_AGENT_DB` at the **same** database, so they contribute to and read from one
graph. Exactly one member is the **keeper** that runs the heavy graph-global maintenance.

## The model

- **Shared graph + memory.** All members use `hive_world` on the shared Postgres. Agency
  (goals/drives/concerns/pursuit) is `owner_agent`-scoped, so each agent only pursues its *own* goals;
  the world-model (nodes/edges/hypotheses) is shared and each member's extraction feeds it.
- **One keeper, enforced.** The keeper runs clustering + dreamstate (the heavy generative cycle) for
  the whole graph; contributors feed + read but don't run it. The keeper is elected by a **Postgres
  advisory lock** (`agent_core.hive`), so it is enforced by the database, not by config discipline —
  mark two members "keeper" and only one wins; if the keeper dies, a willing contributor takes over
  **live** (no restart).
- **Fresh hive, no fleet caveat.** Every member is `owner_agent`-scoped from birth, so a brand-new hive
  is clean — none of the migration caveats that apply to retrofitting an existing shared graph.

## Run

```bash
# 1. build the images once — the studio compose builds BOTH nmem-studio:latest and nmem-viz:latest:
cd ../studio && docker compose build

# 2. bring the hive up (from this directory):
cd ../hive && docker compose up
```

Then configure each member through its wizard (bound to loopback by default):

| Member     | Wizard              | Create as (Step 05)          |
|------------|---------------------|------------------------------|
| `keeper`   | http://localhost:8080 | Hive member · **Keeper**     |
| `member-a` | http://localhost:8081 | Hive member · **Contributor**|
| `member-b` | http://localhost:8082 | Hive member · **Contributor**|

Point each at your LLM (a host-run vLLM/Ollama is `http://host.docker.internal:PORT/v1`), pick its
capabilities + persona, set **Step 05 → mode = Hive member** and the graph role, and Create. Each
restarts into agent mode and joins `hive_world`. The shared brain is at http://localhost:5174.

> Give members **distinct agent ids** — that id is the `owner_agent` their agency is scoped to. (The
> studio rejects a duplicate id within one appliance; across the hive, keep them different by hand.)

## Scaling / notes

- **More members:** copy a `member-*` service, give it a new port + data volume. All that matters is
  the shared `NMEM_AGENT_DB` + Postgres.
- **DB provisioning is concurrency-safe:** members race to `CREATE DATABASE hive_world` on first boot;
  the loser treats "already exists" as success (`provision_db`). Table/migration bootstrap is
  idempotent (`CREATE ... IF NOT EXISTS` + migration tracking), so simultaneous first-boots converge.
- **Keeper role vs the lock:** the wizard's "Keeper" choice is a *willingness* — it makes that member
  contend for the lock. The lock decides the actual keeper, so a hive with zero explicit keepers has
  no graph-global maintenance (fine for a pure contributor swarm), and one with several has exactly one.
- **Security:** each member's wizard/`/act` is an admin surface. Set `STUDIO_AUTH_PASSWORD` (applied to
  every member) to require login before exposing any of them; unset = unauthenticated (keep on
  `127.0.0.1`). See [`../studio`](../studio/) for the auth details.
