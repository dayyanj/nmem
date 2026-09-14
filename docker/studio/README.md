# nmem-studio (single-agent appliance)

Pull-and-run image that stands up **one** nmem agent from a web wizard — no config files by
hand. One image, one process, two modes:

- **First boot → wizard.** Open <http://localhost:8080>, pick a persona, point it at your LLM
  (and *test the connection* before you build), choose how much of a mind it has, and click
  **Create**. That writes the agent's `agent.yaml` + `capabilities.env` + `persona.yaml` to a
  data volume, provisions its Postgres database, and restarts the container.
- **Every boot after → the agent runs.** Its capability flags + DB DSN are already in the
  environment (the entrypoint sources them before Python starts — mandatory, since nmem reads
  its settings from env at import), so the runtime boots the mind you designed. Inspect it at
  `/health` and drive cycles via the `/admin/*` ops endpoints.

## Run

**Run the released images** (open, free — no login to pull). The compose file references
`registry.spwig.com/nmem-studio` + `registry.spwig.com/nmem-viz`, so a fresh checkout just pulls them:

```bash
cd nmem/docker/studio
docker compose pull        # fetches BOTH the studio + viz images from the registry
docker compose up
# check what you got:
docker inspect --format '{{ index .Config.Labels "org.opencontainers.image.version" }}' \
  registry.spwig.com/nmem-studio:latest
```

**Or build from source** (needs the sibling `nmem-*` repos side-by-side; installs CPU torch + the
embedder — a few minutes):

```bash
cd nmem/docker/studio
docker compose up --build
```

Then open <http://localhost:8080>. (If 8080 is taken: `STUDIO_HOST_PORT=18080 docker compose up`.)

> **Releasing (maintainers):** `./docker/publish.sh --push` builds all four appliance images from the
> monorepo — `nmem-studio`, `nmem-viz`, and (for the optional profiles) `nmem-identity` +
> `nmem-sandbox` — version-stamps them (`VERSION` → the `org.opencontainers.image.version` label), and
> pushes `:<version>` + `:latest` to `$REGISTRY` (default `registry.spwig.com`). CI does this on a `v*`
> tag (`.github/workflows/publish-images.yml`).

> **Security — the admin surface can create + run agents and register container-executing tools.**
> By default it is **unauthenticated** and the compose binds the studio + viz ports to **loopback
> (`127.0.0.1`)**. To expose it beyond localhost, turn on **login** first:
> ```bash
> STUDIO_AUTH_PASSWORD=change-me STUDIO_BIND=0.0.0.0 docker compose up
> ```
> That gates the whole surface (wizard, `/studio/*`, `/admin/*`, `/act`, `/chat`, `/tools`) behind a
> username + password → an HttpOnly, SameSite=Strict session cookie, with CSRF on every mutation.
> Default user is `admin` (`STUDIO_AUTH_USER` to change); set `STUDIO_COOKIE_SECURE=1` when serving over
> HTTPS. With no password set the surface stays open — keep it on loopback, or front it with your own
> auth proxy.

The **LLM is external** — you bring your own endpoint and key:

- **Local model** (vLLM / Ollama / LM Studio on your host): in the wizard set the endpoint to
  `http://host.docker.internal:PORT/v1` (the compose file already maps `host.docker.internal`).
- **Hosted API** (OpenAI / Anthropic / OpenRouter / …): pick the provider, paste your key. The
  key is posted to the studio backend, used server-side to test + run, stored as a secret on the
  data volume, and **never written to `agent.yaml` or exposed in the browser**.

Everything else runs in the compose stack: the studio, **Postgres + pgvector**, the bundled
**all-MiniLM-L6-v2** embedder (in-process, CPU), and Redis (only used if you later enable
peering/comms).

## Optional capabilities (compose profiles)

Two heavier capabilities ship in the box but stay **off by default** — bring them up with a compose
profile when you want them. The matching wizard pills tell you when a capability needs one.

- **Writing-style identity** (`--profile identity`) — two LUAR sidecars (a Postgres-backed matcher +
  the text-embed service; the `rrivera1849/LUAR-MUD` model is baked into the image for offline boot).
  They let a conversational agent recognise a *returning* person by how they write, not just by a
  declared name. Turn it on in the wizard with the **CHAT · TEXT_IDENTITY** pill (which also needs
  **CHAT · SPEAKER**). The client fails **open** — identity just stays off — if the sidecars aren't
  running, so it's safe to leave the pill off and the profile down.
  ```bash
  docker compose --profile identity up
  ```

- **Embodied perception** (`--profile perception`) — the bundled **nmem-sandbox**: a headless
  browser/desktop the agent drives over an HTTP action API (watch it work at
  `http://localhost:6080/vnc.html`). Point a **research/perception** agent's *Research sandbox* step
  at `http://sandbox:8080`. The sandbox has its **own vision model** (separate from the agent's brain)
  — you must give it one:
  ```bash
  SANDBOX_VLM_URL=http://host.docker.internal:8003/v1/chat/completions \
  SANDBOX_VLM_MODEL=your-vlm docker compose --profile perception up
  ```
  With the sandbox attached, the **Perception (embodied)** preset / visual-memory pills become live
  (the agent remembers screens it has seen and warns when it revisits one a past attempt failed on;
  the sensory store reuses the agent DB and self-migrates).

Combine them: `docker compose --profile identity --profile perception up`.

## What persists

Named volumes survive `docker compose down`:

- `agent_data` → `/data/<agent_id>/` — the agent's config + secrets. Delete it to start the wizard over.
- `pg_data` — the agent's memory + symbol graph (and, if you use them, the `nmem_identity` DB and the
  reused sensory store — all in the one Postgres volume).
- `redis_data` — reserved for comms.
- `sandbox_artifacts` — files the perception sandbox produces (only with `--profile perception`).

## Backup, restore & upgrade

**Back up / restore** the two pieces of state that aren't in the image — the database (memory + symbol
graph) and the agent config volume (`/data`, incl. `secrets.env`) — with `./backup.sh` (stack must be up):

```bash
./backup.sh backup                     # → ./backups/nmem-studio-agent_nmem-<UTC>.tgz
./backup.sh restore ./backups/nmem-studio-agent_nmem-<UTC>.tgz    # drop-and-restore DB + /data, then restart
```

The archive contains `secrets.env` — keep it at least as protected as the appliance. (For a hive, back
up the shared DB + each member's `/data` the same way.)

**Upgrade** — the agent's schema self-migrates (forward-only) on boot, so upgrading is pull-and-restart:

```bash
./backup.sh backup                     # always snapshot first
docker compose pull && docker compose up -d      # new image, SAME volumes → migrations run at startup
```

State lives in the named volumes, so a new image reuses your existing agent + memory. Roll back by
restoring the pre-upgrade archive against the previous image tag. Pin a version
(`registry.spwig.com/nmem-studio:1.0.0`) rather than `:latest` if you want upgrades to be deliberate.

## Notes

- **One agent per appliance.** Capability flags are process-global (read from env at import), so
  one container runs one capability-set. For several differently-configured agents, run several
  appliances (distinct data volumes + ports) — or use the control-plane topology (a later step).
- **Pure thinker by default.** The wizard's agent consolidates, dreams, forms drives/goals, and
  grows its graph. It takes no *outward* action unless you give it tools: the wizard's **Actors**
  step authors webhook/OpenAPI, UTCP, MCP, A2A, or plugin-mount tools plus an autonomy tier
  (default `read_only`, so a fresh agent still can't act), which the appliance wires into a gated
  executor — no derived image needed. Drive it from the dashboard's **Act** panel or `POST /act`.
- **Chat + memory-viz** ship in agent mode: a grounded `/chat` panel on the dashboard and a live
  3D "brain" (the bundled nmem-viz service) alongside `/health` + `/admin/*`. The wizard's
  **Conversation & identity** pills add tool-calling in chat (the agent can call `memory_search` +
  your tools mid-turn), speaker recognition, person-alias convergence, chat-lifted obligations, and
  (with the `identity` profile) writing-style identity. The **Conversational** preset bundles a safe
  default set.
- **Build context** is the `apps/` monorepo root so the image can install the sibling `nmem-*`
  libraries from local source (they are not yet on PyPI).
