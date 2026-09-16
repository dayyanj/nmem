# nmem-studio — the pull-and-run appliance for one autonomous nmem agent.
#
# ONE image, two shapes (same binary, chosen by env):
#   docker run -p 127.0.0.1:8080:8080 -p 127.0.0.1:5174:5174 -v nmem:/data <registry>/nmem-studio
#       → embedded Postgres+pgvector + the studio + the nmem-viz brain, all here. The one-liner.
#   docker compose up   (NMEM_EMBEDDED_PG=0 / NMEM_EMBEDDED_VIZ=0)
#       → just the studio; Postgres + viz are separate services (needed for the identity/perception
#         profiles + hives). See docker-compose.yml.
#
# supervisord runs the enabled services; the studio's create→restart seam works IN-container. ALL
# state (agent config + database) lives on the single /data volume, so it survives `docker rm`.
# The LLM stays external (point the wizard at your endpoint). Build context = the apps/ monorepo root.

# ── stage 1: build the nmem-viz SPA ──────────────────────────────────────────────
FROM node:20-slim AS viz-spa
WORKDIR /build
COPY nmem-viz/package.json nmem-viz/package-lock.json ./
RUN npm ci
COPY nmem-viz/ .
RUN npm run build

# ── stage 2: the runtime (Postgres+pgvector base) ───────────────────────────────
FROM pgvector/pgvector:pg16

ARG VERSION=dev
LABEL org.opencontainers.image.title="nmem-studio" \
      org.opencontainers.image.description="Pull-and-run appliance for an autonomous nmem agent (studio + Postgres + viz)" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.source="https://github.com/dayyanj/nmem" \
      org.opencontainers.image.licenses="BUSL-1.1 AND MIT"

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    STUDIO_DATA_DIR=/data \
    STUDIO_PORT=8080 \
    NMEM_STUDIO_VERSION=${VERSION} \
    HF_HOME=/opt/hf \
    PGDATA=/data/pgdata \
    VENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    POSTGRES_HOST=127.0.0.1 \
    POSTGRES_PORT=5432 \
    POSTGRES_USER=nmem \
    POSTGRES_PASSWORD=nmem \
    NMEM_AGENT_DB=agent_nmem \
    NMEM_PERSONA_RECONCILE_OBJECTIVES=1 \
    NMEM_SYM_VIZ_ENABLED=1 \
    NMEM_VIZ_INGEST_URL=http://127.0.0.1:5174/ingest \
    NMEM_VIZ_PUBLIC_URL=http://localhost:5174/

# python + build tools + supervisor + gosu (run postgres as the postgres user). The pgvector base
# already ships the postgres-16 server + the vector extension.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 python3-venv python3-dev build-essential curl supervisor gosu redis-server \
 && rm -rf /var/lib/apt/lists/*

# App deps in a venv. CPU torch FIRST so sentence-transformers doesn't drag the multi-GB CUDA build.
# nmem[computer-use] = httpx for the sandbox client; nmem-sym-sensor[visual,inference] = the embodied
# visual-memory path (perception profile) which runs IN the agent process.
COPY nmem /src/nmem
COPY nmem-sym /src/nmem-sym
COPY nmem-act /src/nmem-act
COPY nmem-exchange /src/nmem-exchange
COPY nmem-sym-sensor /src/nmem-sym-sensor
RUN python3 -m venv "$VENV" \
 && pip install --upgrade pip \
 && pip install torch --index-url https://download.pytorch.org/whl/cpu \
 && pip install "/src/nmem[openai,anthropic,computer-use]" /src/nmem-sym /src/nmem-act /src/nmem-exchange \
 && pip install "/src/nmem-sym-sensor[visual,inference]" \
 && pip install "uvicorn[standard]" fastapi asyncpg pyyaml mcp \
 && pip install aiohttp websockets numpy pydantic-settings    # nmem-viz server deps

# Preload the bundled embedder so first agent boot is fast + offline.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# nmem-viz: the built SPA + its aiohttp server (run from /opt/viz).
COPY --from=viz-spa /build/dist /opt/viz/dist
COPY nmem-viz/server /opt/viz/server

# entrypoint.sh: the studio process (sources capabilities.env in agent mode, then runs the server).
# studio-supervisor.sh: the container entrypoint (pg init + generates the supervisord config from the
# embedded-services toggle). studio-service.sh: the studio supervisord program (waits for pg, execs entrypoint).
COPY nmem/docker/studio/entrypoint.sh /entrypoint.sh
COPY nmem/docker/studio/studio-service.sh /studio-service.sh
COPY nmem/docker/studio/studio-supervisor.sh /studio-supervisor.sh
RUN chmod +x /entrypoint.sh /studio-service.sh /studio-supervisor.sh

VOLUME ["/data"]
# 8080 studio · 5174 viz · 6390 embedded exchange broker (only served when NMEM_EMBEDDED_EXCHANGE=1;
# publish it to let hive peers on other hosts connect)
EXPOSE 8080 5174 6390
HEALTHCHECK --interval=15s --timeout=5s --start-period=45s --retries=10 \
  CMD curl -fsS http://127.0.0.1:8080/health >/dev/null 2>&1 || curl -fsS http://127.0.0.1:8080/studio/catalog >/dev/null 2>&1 || exit 1
ENTRYPOINT ["/studio-supervisor.sh"]
