# nmem-studio single-agent appliance.
#
# Build context is the apps/ monorepo root (so the sibling nmem-* libs are reachable):
#   docker build -f nmem/docker/studio/Dockerfile -t nmem-studio .
# or just `docker compose up` from nmem/docker/studio/ (compose sets the context).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    STUDIO_DATA_DIR=/data \
    STUDIO_PORT=8080 \
    HF_HOME=/opt/hf

# build-essential for asyncpg/torch native bits; curl for the compose healthcheck.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential curl \
 && rm -rf /var/lib/apt/lists/*

# The four libraries this appliance needs. nmem[openai] = OpenAI-compatible endpoints (no CUDA);
# nmem-sym embeds locally, so install the CPU torch wheel FIRST (else sentence-transformers pulls
# the multi-GB CUDA build). nmem-exchange is imported only if peering is enabled, installed so it
# never crashes a fresh env.
COPY nmem /src/nmem
COPY nmem-sym /src/nmem-sym
COPY nmem-act /src/nmem-act
COPY nmem-exchange /src/nmem-exchange

RUN pip install --upgrade pip \
 && pip install torch --index-url https://download.pytorch.org/whl/cpu \
 && pip install "/src/nmem[openai,anthropic]" /src/nmem-sym /src/nmem-act /src/nmem-exchange \
 && pip install "uvicorn[standard]" fastapi asyncpg pyyaml \
 && pip install mcp    # actor layer: MCP client (webhook/a2a/plugins need no extra deps)

# Preload the bundled embedder so the first agent boot is fast and fully offline.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

COPY nmem/docker/studio/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8080
VOLUME ["/data"]
ENTRYPOINT ["/entrypoint.sh"]
