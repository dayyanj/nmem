#!/usr/bin/env bash
# The studio process for the all-in-one image (a supervisord program, autorestart=true). Waits for
# the in-container Postgres, then runs the normal appliance entrypoint (which sources the agent's
# capabilities.env/secrets.env in agent mode, then execs studio_server). When the studio restarts
# itself on the create→boot seam (SIGTERM), supervisord relaunches this — same seam as a compose restart.
set -euo pipefail

until pg_isready -h 127.0.0.1 -p "${POSTGRES_PORT:-5432}" -U "${POSTGRES_USER:-nmem}" >/dev/null 2>&1; do
  echo "[aio] waiting for postgres…"
  sleep 1
done
exec /entrypoint.sh
