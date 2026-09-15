#!/usr/bin/env bash
# The studio process (a supervisord program, autorestart=true). Waits for Postgres — embedded
# (127.0.0.1) or the compose service ($POSTGRES_HOST) — then runs the appliance entrypoint, which
# sources the agent's capabilities.env/secrets.env in agent mode and execs studio_server. On the
# create→boot self-SIGTERM, supervisord relaunches this (same seam as a compose container restart).
set -euo pipefail

until pg_isready -h "${POSTGRES_HOST:-127.0.0.1}" -p "${POSTGRES_PORT:-5432}" -U "${POSTGRES_USER:-nmem}" >/dev/null 2>&1; do
  echo "[nmem-studio] waiting for postgres at ${POSTGRES_HOST:-127.0.0.1}:${POSTGRES_PORT:-5432}…"
  sleep 1
done
exec /entrypoint.sh
