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

```bash
cd nmem/docker/studio
docker compose up --build        # first run builds the image (installs CPU torch + the embedder)
```

Then open <http://localhost:8080>. (If 8080 is taken: `STUDIO_HOST_PORT=18080 docker compose up`.)

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

## What persists

Named volumes survive `docker compose down`:

- `agent_data` → `/data/<agent_id>/` — the agent's config + secrets. Delete it to start the wizard over.
- `pg_data` — the agent's memory + symbol graph.
- `redis_data` — reserved for comms.

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
  3D "brain" (the bundled nmem-viz service) alongside `/health` + `/admin/*`.
- **Build context** is the `apps/` monorepo root so the image can install the sibling `nmem-*`
  libraries from local source (they are not yet on PyPI).
