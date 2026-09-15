# nmem-studio

**Pull an image, open a web page, and have a live cognitive agent in ~10 minutes** — no config files,
no PhD in the stack. nmem-studio is the onboarding + management UX over the
[nmem](https://github.com/dayyanj/nmem) cognitive-memory engine: 6-tier memory, a symbol-graph world
model, optional drives / goals / pursuit, grounded chat, and a live 3D "brain" you can watch think.

## Quick start — one line

```bash
docker run -p 127.0.0.1:8080:8080 -p 127.0.0.1:5174:5174 -v nmem:/data docker.io/dayyanj/nmem-studio
```

Open **http://localhost:8080** for the setup wizard, and **http://localhost:5174** for the brain viz.
That single container is the whole appliance — **Postgres + pgvector, the studio, and the viz** run
inside it, with all state on the `nmem` volume, so your agent survives `docker rm` and comes back.

> Ports bind to **127.0.0.1** on purpose: the admin surface (wizard / create / act) is unauthenticated
> by default. To reach it from another machine, tunnel over SSH — or set `STUDIO_AUTH_PASSWORD` (login
> + CSRF) and bind `0.0.0.0` deliberately.

## What you do in the wizard

1. **Pick a persona** — a starter template (Researcher, Assistant, Companion, …) you can edit, or the
   full-suite **Everything** preset.
2. **Point it at an LLM** and *test the connection* — a local endpoint
   (`http://host.docker.internal:PORT/v1` for vLLM / Ollama / LM Studio) or a hosted API (OpenAI,
   Anthropic, OpenRouter, …). **Bring your own** — the key is used server-side and never written to
   config or shown in the browser.
3. **Choose how much of a mind it has** — presets, or the advanced pill grid (dependencies auto-satisfy).
4. **Create.** The agent boots; talk to it, watch it think, and drive cognition cycles from the
   dashboard. Change its settings anytime with **⚙ Edit** (memory is preserved).

## Supported tags

- `latest` — the current release
- `X.Y.Z` — a pinned release (recommended for reproducible upgrades)

## Image facts

- **Platform:** `linux/amd64`
- **Ports:** `8080` (wizard / dashboard / API), `5174` (brain viz)
- **Volume:** `/data` — agent config **and** the database; back it up, and it's all that persists
- Sizable first pull (bundles Postgres, an embedder, and CPU PyTorch) — subsequent starts are fast

## Advanced — multi-agent, identity, perception, hives

The **same image** also runs as the studio-only service in a multi-service stack. Grab the
`docker-compose.yml` (from the [source repo](https://github.com/dayyanj/nmem), under
`docker/studio/`) for:

- several agents / a **hive** (a shared world-model),
- **writing-style identity** (`--profile identity` — recognise a returning person by how they write),
- **embodied perception** (`--profile perception` — a computer-use sandbox the agent drives).

Companion images (same namespace): `nmem-viz`, `nmem-identity`, `nmem-sandbox`.

## License

The engine libraries are AGPL; the **studio image is BUSL-1.1** (`org.opencontainers.image.licenses =
BUSL-1.1 AND MIT`). Image: **https://hub.docker.com/r/dayyanj/nmem-studio** · Source & docs:
**https://github.com/dayyanj/nmem**.
